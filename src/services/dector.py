import threading 
import time
import os
import sys
import json
import uuid
import numpy as np 
import logging
import src.global_ctx as ctx 

from src.utils.logger import sys_logger as logger, log_event
from src.services.process_detector import ProcessDetector

# 导入核心库
try:
    import cv2
    import onnxruntime
    AI_READY = True
except ImportError as e:
    AI_READY = False
    MISSING_LIB = str(e)
    
# 尝试导入树莓派摄像头
try:
    from picamera2 import Picamera2
    SOURCE_TYPE = "PI_CAM"
except ImportError:
    # 如果没有树莓派摄像头，尝试导入 mss 用于截屏
    try:
        import mss
        SOURCE_TYPE = "PC_SCREEN"
    except ImportError:
        SOURCE_TYPE = "MOCK" # 进入模拟     
    

class DECTOR_ser( threading.Thread ):
    def __init__( self ):
        super().__init__()
        
        import src.global_ctx as ctx 
        self.ctx = ctx
        
        self.daemon = True
        self.picam2 = None     # 树莓派的摄像头
        self.sct = None        # 电脑进行截屏
        self.sess = None
        self.input_name = None
        self.logger = logger
        
        self.classes = []   # 防止加载失败时报错
        
        self.mode = not AI_READY
        
        self.latest_frame = None            # 只保留最新的图片，防止出现大幅度不同步
        self.result_frame = None            # 存放结果
        self.frame_lock = threading.Lock()  # 线程锁，防止又读又写
        self.stop_capture = False
        
        # 当在树莓派上时，不显示图像
        self.show_window = bool(os.environ.get("DISPLAY")) and bool(ctx.config.get("dector", {}).get("show_window", False))
        
        # 用于避免同一个违规情况在每一帧都触发保存导致磁盘爆炸
        self._last_violation_ts = 0.0
        
        # 选择后端
        self.backend = str(ctx.config.get("dector", {}).get("backend", "onnx")).lower() # onnx\process\ncnn
        self.proc_det = None # ProcessDetector实例，只有在backend=process时才会用到

        # Object Found 日志去重
        self._last_logged = {}  # key: class_id -> (cx, cy, ts)
        log_cfg = ctx.config.get("dector", {}).get("log_dedup", {})
        self._log_dedup_enable = bool(log_cfg.get("enable", True))
        self._same_obj_px_th = float(log_cfg.get("same_obj_px_th", 20))
        self._same_obj_time_th = float(log_cfg.get("same_obj_time_th", 0.5))
        self._same_obj_iou_th = float(log_cfg.get("same_obj_iou_th", 0.7))
        
        # 日志报告当前模式
        if not AI_READY:
            log_event(logger, source="DETECT", event="init", result="degraded", reason=f"missing_lib:{MISSING_LIB}", level=logging.WARNING, brief=False)
        else:
            if SOURCE_TYPE == "PC_SCREEN":
                log_event(logger, source="DETECT", event="init", action="source", result="pc_screen", brief=False)
            elif SOURCE_TYPE == "PI_CAM":
                log_event(logger, source="DETECT", event="init", action="source", result="pi_cam", brief=False)

    # 读取类别列表
    def _load_classes(self):
        try : 
            class_file = ctx.config['dector']['class_file']
            
            base_dir = os.path.dirname(os.path.abspath(__file__))
            abs_path = os.path.join(base_dir , '../../' , class_file)

            with open ( abs_path , 'r' , encoding = 'utf-8') as f :
                self.classes = [line.strip() for line in f.readlines()]
            log_event(logger, source="DETECT", event="class_load", result="ok", key={"count": len(self.classes)}, brief=False)
        
        except Exception as e :
            log_event(logger, source="DETECT", event="class_load", result="fail", reason=str(e), level=logging.ERROR, brief=False)
    
    # 初始化硬件
    def _init_hardware(self): 
        if self.mode :   # 如果为模拟模式,无硬件需要初始化,跳过 
            return 
        
        try:
            # 树莓派模式
            if SOURCE_TYPE == "PI_CAM":
                log_event(logger, source="DETECT", event="pi_cam", action="start", result="begin", level=logging.DEBUG, brief=False)
                self.picam2 = Picamera2()
                config = self.picam2.create_configuration(main={"size": (640, 640), "format": "RGB888"})
                self.picam2.configure(config)
                self.picam2.start()

            # 电脑屏幕模式
            elif SOURCE_TYPE == "PC_SCREEN":
                log_event(logger, source="DETECT", event="screen_capture", action="init", result="ok", level=logging.DEBUG, brief=False)

            # 加载yolov5 (共用)
            model_path = ctx.config['dector']['model_path']
            base_dir = os.path.dirname(os.path.abspath(__file__))
            model_abs_path = os.path.join(base_dir, '../../', model_path)
            
            sess_options = onnxruntime.SessionOptions() 
            sess_options.intra_op_num_threads = 4      # 将四核全部使用，加快推理速度
            self.sess = onnxruntime.InferenceSession(model_abs_path, sess_options)
            self.input_name = self.sess.get_inputs()[0].name
            
        except Exception as e:
            log_event(logger, source="DETECT", event="startup", result="fail", reason=str(e), level=logging.ERROR, brief=False)
            self.mode = True # 降级为模拟
    
    # 处理大量图像，采集最新的一张图像
    def _capture_worker(self):
        log_event(logger, source="DETECT", event="capture_worker", action="start", result="ok", key={"source": SOURCE_TYPE}, brief=False)
        
        # 在这里初始化 mss
        local_sct = None
        if SOURCE_TYPE == "PC_SCREEN":
            import mss
            local_sct = mss.mss()
            
        if SOURCE_TYPE == "PC_SCREEN" :     # 只有电脑模式显示窗口
            cv2.namedWindow("Live", cv2.WINDOW_NORMAL)

        while not self.stop_capture:
            try :
                frame = None
                
                # 树莓派
                if SOURCE_TYPE == "PI_CAM" and self.picam2 :
                    frame = self.picam2.capture_array()     # 阻塞，等待下一帧
                    
                # 电脑模式    
                elif SOURCE_TYPE == "PC_SCREEN" and local_sct :
                    monitor = local_sct.monitors[1]
                    sct_img = local_sct.grab(monitor)
                    img_np = np.array(sct_img)
                    frame = cv2.cvtColor(img_np , cv2.COLOR_BGR2RGB)
                    frame = cv2.resize(frame , (640 , 640))
                    
                # 模拟
                elif SOURCE_TYPE  == "MOCK" or self.mode :
                    time.sleep(0.1) 
                    frame = np.zeros((640,640,3) , dtype = np.uint8) 
                
                # 将新的图片写入为下一张处理的图片，减少不同步
                if frame is not None :
                    # 正确的锁语法
                    with self.frame_lock :
                        self.latest_frame = frame 
                        if self.result_frame is not None :
                            res_img = self.result_frame 
                        else :
                            res_img = np.zeros_like(frame)
                        
                    # 降低不同步    
                    if SOURCE_TYPE == "PC_SCREEN":
                        # 实时的原图
                        left_img = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                        
                        # 结果图
                        right_img = res_img
                        
                        # 拼接
                        combined_img = np.hstack([left_img, right_img])
                        
                        # 显示
                        cv2.imshow("Live", combined_img)
                        cv2.waitKey(1)
                
                if SOURCE_TYPE != "PI_CAM" :    # 在非必要时减少cpu占比
                    time.sleep(0.05) 
                        
            except Exception as e :
                log_event(logger, source="DETECT", event="capture", result="fail", reason=str(e), level=logging.ERROR, brief=False)
                time.sleep(1)
    
    # 预处理图片,给yolov5
    def _preprocess(self , img):
        # 输入的图片为（640,640,3）
        img = img / 255.0                     # 像素值从 0-255 变成 0.0-1.0
        img = img.transpose( 2, 0 , 1 )       # 维度顺序从 HWC 变成 CHW
        img = np.expand_dims(img , axis = 0 ) # 增加一个维度 (Batch)
        
        return img.astype(np.float32)
    
    # 计算归一化后中心点距离
    def _calc_center_dist_norm(self, c1, c2, W, H):
        dx = float(c1[0]) - float(c2[0])
        dy = float(c1[1]) - float(c2[1])
        dist = (dx * dx + dy * dy) ** 0.5
        denom = float(min(W, H)) if min(W, H) > 0 else 1.0
        return dist / denom

    # 计算loU
    def _calc_iou_xyxy(self, a, b):
        ax1, ay1, ax2, ay2 = map(float, a)
        bx1, by1, bx2, by2 = map(float, b)

        inter_x1 = max(ax1, bx1)
        inter_y1 = max(ay1, by1)
        inter_x2 = min(ax2, bx2)
        inter_y2 = min(ay2, by2)

        inter_w = max(0.0, inter_x2 - inter_x1)
        inter_h = max(0.0, inter_y2 - inter_y1)
        inter = inter_w * inter_h

        area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
        area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
        union = area_a + area_b - inter
        return inter / union if union > 0 else 0.0

    # 用于排序检测结果，优先面积大、置信度高的框
    def _area_conf_key(self, det):
        return (det.get("area", 0), det.get("conf", 0.0))

    # 检查电瓶车是否违规
    def _check_violation_ebike_strip(self , dets, W, H):
        vcfg = ctx.config.get("dector", {}).get("violation", {})
        if not bool(vcfg.get("enable", False)):
            return None

        ebike_id = int(vcfg.get("ebike_class_id", 0))
        strip_id = int(vcfg.get("strip_class_id", 2))

        ebike_min_area_norm = float(vcfg.get("ebike_min_area_norm", 0.08))
        center_dist_norm_th = float(vcfg.get("center_dist_norm", 0.25))

        cooldown_s = float(vcfg.get("cooldown_s", 1.0))
        now = time.time()
        if (now - self._last_violation_ts) < cooldown_s:
            return None

        ebikes = [d for d in dets if int(d.get("class_id", -1)) == ebike_id]
        strips = [d for d in dets if int(d.get("class_id", -1)) == strip_id]
        if len(ebikes) == 0 or len(strips) == 0:
            return None

        # 先筛“近”的电瓶车（框面积足够大）
        near_ebikes = []
        for e in ebikes:
            area = float(e.get("area", 0))
            area_norm = area / float(W * H + 1.0)
            if area_norm >= ebike_min_area_norm:
                e["_area_norm"] = area_norm
                near_ebikes.append(e)

        if len(near_ebikes) == 0:
            return None

        # 找一对最可能的违规组合（距离越近越好，面积/置信度越高越好）
        best_pair = None
        best_score = -1.0

        for e in near_ebikes:
            for s in strips:
                dist_norm = self._calc_center_dist_norm(e.get("center", [0,0]), s.get("center", [0,0]), W, H)
                if dist_norm <= center_dist_norm_th:
                    conf_e = float(e.get("conf", 0.0))
                    conf_s = float(s.get("conf", 0.0))
                    score = (float(e.get("_area_norm", 0.0)) * 2.0) + (1.0 - dist_norm) + (conf_e + conf_s) * 0.5
                    if score > best_score:
                        best_score = score
                        best_pair = (e, s, dist_norm)

        if best_pair is None:
            return None

        e, s, dist_norm = best_pair

        # 通过判定 -> 更新节流时间戳
        self._last_violation_ts = now

        violation_ev = {
            "type": "violation",
            "rule": "ebike_with_strip_nearby",
            "ts": now,
            "img_size": [int(W), int(H)],

            "dist_norm": float(dist_norm),
            "ebike_area_norm": float(e.get("_area_norm", 0.0)),

            "ebike": {
                "class_id": int(e.get("class_id")),
                "class_name": e.get("class_name"),
                "conf": float(e.get("conf")),
                "bbox_xyxy": e.get("bbox_xyxy"),
                "center": e.get("center"),
                "area": int(e.get("area")),
            },
            "strip": {
                "class_id": int(s.get("class_id")),
                "class_name": s.get("class_name"),
                "conf": float(s.get("conf")),
                "bbox_xyxy": s.get("bbox_xyxy"),
                "center": s.get("center"),
                "area": int(s.get("area")),
            },
        }

        return violation_ev

    # 检查是否发现火灾
    def _check_fire(self , dets) :
        fire_cfg = ctx.config.get("dector",{}).get("violation",{})
        fire_id = int(fire_cfg.get("fire_class_id", 1))
        
        fires = [d for d in dets if int(d.get("class_id",-1)) == fire_id]
        if len(fires) == 0 :
            return None 
        
        fires.sort(key=self._area_conf_key, reverse=True)
        best = fires[0]
        
        return {
            "type": "fire",
            "rule": "fire_detected",
            "ts": float(best.get("ts", time.time())),
            "class_id": int(best.get("class_id", fire_id)),
            "class_name": best.get("class_name", ""),
            "conf": float(best.get("conf", 0.0)),
            "bbox_xyxy": best.get("bbox_xyxy"),
            "center": best.get("center"),
            "area": int(best.get("area", 0)),
            "img_size": best.get("img_size"),
        }

    # 违规存证,保存图片 + 通过JSON保存基本信息
    def _save_violation_to_data(self , violation_ev, img_bgr, draw_bgr=None):
        try:
            vcfg = ctx.config.get("dector", {}).get("violation", {})

            save_enable = bool(vcfg.get("save_enable", True))
            if not save_enable:
                return None

            save_dir_cfg = str(vcfg.get("save_dir", "data"))
            save_draw_img = bool(vcfg.get("save_draw_img", True))

            # 计算项目根目录：src/services/dector.py -> ../../ 即项目根
            base_dir = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.abspath(os.path.join(base_dir, "../../"))

            # data 目录（可配置）
            data_dir = os.path.join(project_root, save_dir_cfg)

            # 通过新建日期子目录，避免 data/ 混乱
            day_str = time.strftime("%Y%m%d", time.localtime())
            out_dir = os.path.join(data_dir, f"violations_{day_str}")
            os.makedirs(out_dir, exist_ok=True)

            ts_epoch = float(violation_ev.get("ts", time.time()))
            ts_str = time.strftime("%y%m%d_%H%M%S", time.localtime(ts_epoch))
            uid = uuid.uuid4().hex[:8]
            prefix = f"violation_{ts_str}_{uid}"

            img_path = os.path.join(out_dir, prefix + ".jpg")
            json_path = os.path.join(out_dir, prefix + ".json")

            # 选择保存画框图还是原图
            save_img = draw_bgr if (save_draw_img and draw_bgr is not None) else img_bgr

            ok = cv2.imwrite(img_path, save_img)
            if not ok:
                log_event(logger, source="DETECT", event="violation_save", result="fail", reason="image_write", key={"img": img_path}, level=logging.WARNING, brief=False)
                return None

            gps = {}
            try:
                if hasattr(ctx, "get_gps_copy"):
                    gps = ctx.get_gps_copy() or {}
                else:
                    gps = getattr(ctx, "gps_state", {}) or {}
            except Exception:
                gps = {}

            gps_ok = bool(gps.get("ok", False))
            gps_lat = gps.get("lat", None)
            gps_lon = gps.get("lon", None)
            gps_ts = gps.get("ts", None)
            gps_src = gps.get("source", None)

            try:
                gps_lat = float(gps_lat) if gps_lat is not None else None
            except Exception:
                gps_lat = None
            try:
                gps_lon = float(gps_lon) if gps_lon is not None else None
            except Exception:
                gps_lon = None
            try:
                gps_ts = float(gps_ts) if gps_ts is not None else None
            except Exception:
                gps_ts = None

            # JSON文件
            meta = {
                "type": violation_ev.get("type", "violation"),
                "rule": violation_ev.get("rule", "unknown"),
                "ts": ts_str,
                "ts_epoch": ts_epoch,
                "time_local": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts_epoch)),
                "img_size": violation_ev.get("img_size", None),

                "dist_norm": violation_ev.get("dist_norm", None),
                "ebike_area_norm": violation_ev.get("ebike_area_norm", None),

                "ebike": violation_ev.get("ebike", {}),
                "strip": violation_ev.get("strip", {}),

                "gps": {
                    "ok": gps_ok,
                    "lat": gps_lat,
                    "lon": gps_lon,
                    "ts_epoch": gps_ts,
                    "source": gps_src,
                },

                "artifacts": {
                    "image": img_path,
                    "json": json_path,
                },
            }

            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)

            log_event(logger, source="DETECT", event="violation_save", result="ok", key={"img": img_path, "json": json_path}, brief=False)

            return meta["artifacts"]

        except Exception as e:
            log_event(logger, source="DETECT", event="violation_save", result="fail", reason=str(e), level=logging.ERROR, brief=False)
            return None

    # 后处理,yolov5结果处理
    def _yolo_postprocess(self, outputs, original_img):
        output = outputs[0][0]
        conf_threshold = ctx.config['dector']['conf_threshold']
        target_classes = ctx.config['dector']['target_classes']
        
        # 收集所有合格的候选框
        boxes = []          # 存坐标
        confidences = []    # 存分数
        class_ids = []      # 存类别ID
        
        for row in output:
            obj_conf = row[4]
            if obj_conf < conf_threshold: continue
            
            class_scores = row[5:]
            class_id = np.argmax(class_scores)
            score = obj_conf * class_scores[class_id]
            
            if score > conf_threshold and class_id in target_classes:
                # 还原坐标
                cx, cy, w, h = row[0:4]
                # 转为左上角坐标 (x, y, w, h)
                x = int(cx - w/2)
                y = int(cy - h/2)
                
                boxes.append([x, y, int(w), int(h)])
                confidences.append(float(score))
                class_ids.append(int(class_id))
        
        # NMS
        indices = cv2.dnn.NMSBoxes(boxes, confidences, conf_threshold, 0.75)
        
        # 只处理幸存下来的框
        draw_img = original_img.copy()
        
        # 优先上报 violation 事件
        H, W = original_img.shape[:2]
        dets = []

        if len(indices) > 0:
            for i in indices.flatten():
                box = boxes[i]
                x, y, w, h = box[0], box[1], box[2], box[3]
                score = confidences[i]
                class_id = class_ids[i]
                
                # 画框
                cv2.rectangle(draw_img, (x, y), (x+w, y+h), (0, 255, 0), 2)
                name = self.classes[class_id] if self.classes else str(class_id)
                label = f"{name} {score:.2f}"
                cv2.putText(draw_img, label, (x, y-10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
                
                H, W = original_img.shape[:2]

                # 避免负数/越界
                x1 = max(0, x)
                y1 = max(0, y)
                x2 = min(W - 1, x + w)
                y2 = min(H - 1, y + h)

                # 画框时也用裁剪后的坐标
                cv2.rectangle(draw_img, (x1, y1), (x2, y2), (0, 255, 0), 2)

                name = self.classes[class_id] if self.classes else str(class_id)
                label = f"{name} {score:.2f}"
                cv2.putText(draw_img, label, (x1, max(0, y1-10)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

                # 不传整张图片
                cx_i = int((x1 + x2) / 2)
                cy_i = int((y1 + y2) / 2)
                area = int((x2 - x1) * (y2 - y1))

                if self._log_dedup_enable:
                    key = int(class_id)
                    now = time.time()

                    last = self._last_logged.get(key)
                    if last is None:
                        should_log = True
                    else:
                        lx, ly, lts, lbox = last
                        dt = now - lts
                        dist = ((cx_i - lx) ** 2 + (cy_i - ly) ** 2) ** 0.5
                        iou = self._calc_iou_xyxy([x1, y1, x2, y2], lbox)

                        # 只要“足够像同一个目标”（IoU高 或 中心点近）并且仍在时间窗内
                        same_obj = ((iou >= self._same_obj_iou_th) or (dist < self._same_obj_px_th)) and (dt < self._same_obj_time_th)
                        should_log = not same_obj

                    if should_log:
                        log_event(logger, source="DETECT", event="object", key={"label": label, "class_id": int(class_id), "conf": round(score,2)}, level=logging.DEBUG)
                        self._last_logged[key] = (cx_i, cy_i, now, [x1, y1, x2, y2])
                else:
                    log_event(logger, source="DETECT", event="object", key={"label": label, "class_id": int(class_id), "conf": round(score,2)}, level=logging.DEBUG)

                dets.append({
                    "type": "detection",
                    "class_id": int(class_id),
                    "class_name": name,
                    "conf": float(score),
                    "bbox_xyxy": [int(x1), int(y1), int(x2), int(y2)],
                    "center": [cx_i, cy_i],
                    "area": area,
                    "img_size": [int(W), int(H)],
                    "ts": time.time(),
                })

        # 违规判定（同帧关系：电瓶车 + 插排 近距离）
        violation_ev = self._check_violation_ebike_strip(dets, W, H)
        if violation_ev is not None:
            log_event(logger, source="DETECT", event="violation", key={"dist_norm": round(violation_ev['dist_norm'],3), "area_norm": round(violation_ev['ebike_area_norm'],3)}, brief=None)

            # 存证（保存画框图优先；保存路径写回事件）
            artifacts = self._save_violation_to_data(violation_ev, original_img, draw_img)
            if artifacts is not None:
                violation_ev["artifacts"] = artifacts

            # 如果满了：丢掉最旧的，保证最新结果能进来（推荐）
            try:
                if ctx.dector_queue.full():
                    ctx.dector_queue.get_nowait()
                ctx.dector_queue.put_nowait(violation_ev)
            except Exception:
                pass

        else:
            # 如果没有违规就上报本帧detection

            if len(dets) > 0:
                dets.sort(key=self._area_conf_key, reverse=True)
                best = dets[0]
                try:
                    if ctx.dector_queue.full():
                        ctx.dector_queue.get_nowait()
                    ctx.dector_queue.put_nowait(best)
                except Exception:
                    pass
        
        # 保存画好的图供显示
        with self.frame_lock:
            self.result_frame = draw_img
        
    # 运行
    def run(self):
        # boot 日志
        log_event(logger, source="DETECT", event="thread", action="start", result="ok", brief=False)

        ctx = self.ctx 
        
        # process 分支
        if self.backend == "process" :
            try : 
                log_event(self.logger, source="DETECT", event="process_start", result="begin", key={"backend": "process"}, level=logging.DEBUG, brief=False)
                pcfg = ctx.config.get("dector", {}).get("process", {}) or {}
                exec_path = str(pcfg.get("exec_path" , ""))
                args = pcfg.get("args" , []) or []

                if not exec_path : 
                    log_event(self.logger, source="DETECT", event="process_start", result="fail", reason="empty_exec_path", level=logging.ERROR, brief=False)
                    return

                self.proc_det = ProcessDetector(exec_path = exec_path , args = args , logger = self.logger)
                self.proc_det.start() 
                log_event(self.logger, source="DETECT", event="process_start", action="spawn", result="ok", key={"exec": exec_path, "args": args}, brief=False)

                while not ctx.system_stop_event.is_set() :
                    msg = self.proc_det.poll(timeout = 0.2) 
                    if not msg :
                        if not self.proc_det.is_alive() : 
                            info = self.proc_det._exit_info() if hasattr(self.proc_det, "_exit_info") else None
                            log_event(self.logger, source="DETECT", event="process_exit", result="fail", reason="proc_exit", key=info, level=logging.ERROR, brief=False) 
                            break 
                        continue
                    
                    dets_raw = msg.get("detections", []) or []
                    if not dets_raw:
                        continue

                    W = msg.get("w", msg.get("img_w", 640))
                    H = msg.get("h", msg.get("img_h", 640))

                    dets = []
                    for d in dets_raw:
                        bbox = d.get("xyxy") or d.get("bbox_xyxy") or d.get("bbox")
                        if not bbox or len(bbox) < 4:
                            continue

                        x1, y1, x2, y2 = map(float, bbox[:4])
                        x1_i, y1_i, x2_i, y2_i = int(x1), int(y1), int(x2), int(y2)
                        area = max(0, x2_i - x1_i) * max(0, y2_i - y1_i)
                        cx = int((x1_i + x2_i) / 2)
                        cy = int((y1_i + y2_i) / 2)

                        dets.append({
                            "type": "detection",
                            "class_id": int(d.get("class_id", -1)),
                            "class_name": d.get("cls", d.get("class_name", "")),
                            "conf": float(d.get("conf", 0.0)),
                            "bbox_xyxy": [x1_i, y1_i, x2_i, y2_i],
                            "center": [cx, cy],
                            "area": int(area),
                            "img_size": [int(W), int(H)],
                            "ts": float(msg.get("ts", time.time())),
                        })

                    if not dets:
                        continue

                    violation_ev = self._check_violation_ebike_strip(dets, W, H)

                    # 违规优先：让 FSM 抢占控制权
                    if violation_ev is not None:
                        saved_image = msg.get("saved_image")
                        if saved_image:
                            violation_ev.setdefault("artifacts", {})["saved_image"] = saved_image

                        try:
                            if hasattr(ctx, "put_latest"):
                                ctx.put_latest(ctx.dector_queue, violation_ev)
                            else:
                                if ctx.dector_queue.full():
                                    ctx.dector_queue.get_nowait()
                                ctx.dector_queue.put_nowait(violation_ev)
                        except Exception as e:
                            log_event(self.logger, source="DETECT", event="process_output", action="queue_put", result="fail", reason="queue_put", key={"err": str(e)}, level=logging.WARNING, brief=False)
                        continue

                    # 否则上报本帧最优检测（面积优先，其次置信度）
                    dets.sort(key=self._area_conf_key, reverse=True)
                    best = dets[0]
                    try:
                        if hasattr(ctx, "put_latest"):
                            ctx.put_latest(ctx.dector_queue, best)
                        else:
                            if ctx.dector_queue.full():
                                ctx.dector_queue.get_nowait()
                            ctx.dector_queue.put_nowait(best)
                    except Exception as e:
                        log_event(self.logger, source="DETECT", event="process_output", action="queue_put", result="fail", reason="queue_put", key={"err": str(e)}, level=logging.WARNING, brief=False)

                info = self.proc_det._exit_info() if hasattr(self.proc_det, "_exit_info") else None
                log_event(self.logger, source="DETECT", event="process_exit", action="finish", result="ok", key=info, brief=False)
                # 确保子进程终止
                if self.proc_det:
                    self.proc_det.stop()
                return   # process 分支结束，不再执行后面的 onnx
            except Exception as e:
                info = self.proc_det._exit_info() if hasattr(self.proc_det, "_exit_info") else None
                log_event(self.logger, source="DETECT", event="process_exit", result="fail", reason=str(e), key=info, level=logging.ERROR, brief=False)
                return
                
        log_event(
            logger,
            source="DETECT",
            event="boot",
            key={
                "AI_READY": AI_READY,
                "SOURCE_TYPE": SOURCE_TYPE,
                "mode": "MOCK" if self.mode else "REAL",
                "conf_threshold": ctx.config['dector'].get('conf_threshold'),
                "target_classes": ctx.config['dector'].get('target_classes'),
            },
            brief=False,
        )        
        log_event(
            logger,
            source="DETECT",
            event="log_dedup",
            key={
                "enable": self._log_dedup_enable,
                "px": self._same_obj_px_th,
                "time": self._same_obj_time_th,
                "iou": self._same_obj_iou_th,
            },
            level=logging.DEBUG,
        )
        
        self._load_classes()
        self._init_hardware()
        
        # 开始采集线程
        capture_thread = threading.Thread(target = self._capture_worker , daemon = True )
        capture_thread.start()
        
        while not ctx.system_stop_event.is_set():
            
            current_img = None 
            with self.frame_lock :
                if self.latest_frame is not None :
                    current_img = self.latest_frame.copy()   # 进行复制
                    
            if current_img is None :
                time.sleep(0.1)
                continue             
            
            # 模拟模式
            if self.mode:
                time.sleep(1)
                if int(time.time()) % 10 == 0:
                    log_event(logger, source="DETECT", event="fake", result="ebike", level=logging.DEBUG, brief=False)
                    fake_ev = {"class_name": "motorcycle", "conf": 0.99, "frame": None}
                    # [修复] 统一队列名
                    if not ctx.dector_queue.full(): ctx.dector_queue.put(fake_ev)
                    time.sleep(1)
                continue

            else:
                # 真实推理
                try:
                    # 预处理
                    # [修复] 调用函数名改为 _preprocess
                    input_tensor = self._preprocess(current_img)
                    
                    # 推理
                    outputs = self.sess.run(None, {self.input_name: input_tensor})
                    
                    # 后处理
                    img_bgr = cv2.cvtColor(current_img, cv2.COLOR_RGB2BGR)
                    self._yolo_postprocess(outputs, img_bgr)
                    
                except Exception as e:
                    log_event(logger, source="DETECT", event="inference", result="fail", reason=str(e), level=logging.ERROR, brief=False)
                    time.sleep(1)

        # 退出清理
        self.stop_capture = True
        if self.picam2:
            try:
                self.picam2.stop()
                log_event(logger, source="DETECT", event="shutdown", action="picam2_stop", result="ok", brief=False)
            except Exception as e:
                log_event(logger, source="DETECT", event="shutdown", action="picam2_stop", result="fail", reason=str(e), level=logging.WARNING, brief=False)
                
        # sct 在子线程里会自动销毁
        if SOURCE_TYPE == "PC_SCREEN" :
            try:
                cv2.destroyAllWindows()
                log_event(logger, source="DETECT", event="shutdown", action="cv2_destroy", result="ok", level=logging.DEBUG, brief=False)
            except Exception as e:
                log_event(logger, source="DETECT", event="shutdown", action="cv2_destroy", result="fail", reason=str(e), level=logging.WARNING, brief=False)
                
        log_event(logger, source="DETECT", event="stop_request", result="ok", brief=False)
