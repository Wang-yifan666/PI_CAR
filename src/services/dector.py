import threading 
import time
import os
import numpy as np 
import logging
import src.global_ctx as ctx 

from src.utils.logger import sys_logger as logger, log_event
from src.services.dector_parts import DetectorRules, DetectorStatus
from src.services.process_detector import ProcessDetector
from src.services.dector_parts.display import (
    ensure_qt_compat_env,
    probe_display_reachable,
    disable_display_flag,
    safe_named_window)

# 在导入 cv2 前先注入 Qt 兼容环境
ensure_qt_compat_env()

# 导入核心库
try:
    import cv2
    import onnxruntime
    AI_READY = True
except ImportError as e:
    AI_READY = False
    MISSING_LIB = str(e)

# OpenCV 导入后会重写 QT_QPA_FONTDIR，需再次校正。
ensure_qt_compat_env()

# 尝试导入树莓派摄像头
PICAMERA2_IMPORT_ERR = None
try:
    from picamera2 import Picamera2
    SOURCE_TYPE = "PI_CAM"
except Exception as e:
    PICAMERA2_IMPORT_ERR = e
    # 如果没有树莓派摄像头，尝试导入 mss 用于截屏
    try:
        import mss
        SOURCE_TYPE = "PC_SCREEN"
    except Exception:
        SOURCE_TYPE = "MOCK" # 进入模拟     
    

class DECTOR_ser(DetectorStatus, DetectorRules, threading.Thread):
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
        self._source_type = SOURCE_TYPE
        self._cv2 = globals().get("cv2")
        
        self.classes = []   # 防止加载失败时报错
        
        self.mode = not AI_READY
        
        self.latest_frame = None            # 只保留最新的图片，防止出现大幅度不同步
        self.result_frame = None            # 存放结果
        self.frame_lock = threading.Lock()  # 线程锁
        self.stop_capture = False
        
        dcfg = ctx.config.get("dector", {})
        self._show_window_cfg = bool(dcfg.get("show_window", False))
        self._pi_video_open_cfg = bool(dcfg.get("pi_video_open", False))
        self._window_mode = str(dcfg.get("window_mode", "autosize")).strip().lower()
        if self._window_mode not in ("autosize", "normal"):
            self._window_mode = "autosize"
        self._window_flag = cv2.WINDOW_AUTOSIZE if self._window_mode == "autosize" else cv2.WINDOW_NORMAL
        self._display_value = str(os.environ.get("DISPLAY", "")).strip()
        self._has_display = bool(self._display_value)

        self._display_probe_ok = False
        self._display_probe_reason = "display_not_set"
        self._display_probe_key = {"display_value": self._display_value}
        if self._has_display:
            self._display_probe_ok, self._display_probe_reason, self._display_probe_key = probe_display_reachable(
                self._display_value,
                logger_obj=logger,
                log_event_func=log_event,
            )

        self.show_window = self._display_probe_ok and self._show_window_cfg
        # 专用于 Pi 摄像头链路：通过 SSH X11 在远端电脑显示处理后画面
        self.pi_video_open = self._display_probe_ok and self._pi_video_open_cfg

        log_event(
            logger,
            source="DETECT",
            event="display",
            action="config",
            result="ok",
            key={
                "display_env": self._has_display,
                "display_value": self._display_value,
                "display_probe_ok": self._display_probe_ok,
                "display_probe_reason": self._display_probe_reason,
                "qt_xcb_no_xi2": str(os.environ.get("QT_XCB_NO_XI2", "")),
                "qt_xcb_no_xrandr": str(os.environ.get("QT_XCB_NO_XRANDR", "")),
                "qt_qpa_fontdir": str(os.environ.get("QT_QPA_FONTDIR", "")),
                "window_mode": self._window_mode,
                "show_window_cfg": self._show_window_cfg,
                "show_window_active": self.show_window,
                "pi_video_open_cfg": self._pi_video_open_cfg,
                "pi_video_open_active": self.pi_video_open,
            },
            level=logging.INFO,
            brief=False,
        )

        if self._show_window_cfg and not self.show_window:
            key = dict(self._display_probe_key)
            key["display_flag"] = "show_window"
            log_event(logger, source="DETECT", event="display", action="init", result="skip", reason=self._display_probe_reason, key=key, level=logging.WARNING, brief=False)
        if self._pi_video_open_cfg and not self.pi_video_open:
            key = dict(self._display_probe_key)
            key["display_flag"] = "pi_video_open"
            log_event(logger, source="DETECT", event="display", action="init", result="skip", reason=self._display_probe_reason, key=key, level=logging.WARNING, brief=False)
        
        # 用于避免同一个违规情况在每一帧都触发保存
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

        # 检测日志配置（支持“每次检测都记录”）
        obj_log_cfg = ctx.config.get("dector", {}).get("object_log", {})
        self._object_log_enable = bool(obj_log_cfg.get("enable", True))
        self._object_log_every_detection = bool(obj_log_cfg.get("every_detection", False))
        self._object_log_brief = bool(obj_log_cfg.get("brief", False))
        self._object_log_level = self._parse_log_level(obj_log_cfg.get("level", "DEBUG"), default=logging.DEBUG)

        # 15s 状态窗口统计：采集帧率、推理帧率、平均耗时、违规次数
        now = time.time()
        self._status_lock = threading.Lock()
        self._status_window_s = 15.0
        self._status_window_start = now
        self._status_capture_frames = 0
        self._status_infer_frames = 0
        self._status_infer_time = 0.0
        self._status_violations = 0

        # HighGUI 在 headless OpenCV 上不可用；运行时失败后自动降级关闭窗口功能
        self._display_warned = set()
        
        # 日志报告当前模式
        if not AI_READY:
            log_event(logger, source="DETECT", event="init", result="degraded", reason=f"missing_lib:{MISSING_LIB}", level=logging.WARNING, brief=False)
        else:
            if PICAMERA2_IMPORT_ERR is not None:
                log_event(
                    logger,
                    source="DETECT",
                    event="init",
                    action="source",
                    result="degraded",
                    reason="picamera2_import_fail",
                    key={
                        "fallback_source": SOURCE_TYPE,
                        "err": str(PICAMERA2_IMPORT_ERR)[:200],
                    },
                    level=logging.WARNING,
                    brief=False,
                )
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
                # config = self.picam2.create_configuration(main={"size": (640, 640), "format": "RGB888"})
                config = self.picam2.create_preview_configuration(
                        main={"size": (640, 640), "format": "RGB888"}
                )                      # create_configuration 是旧版 API，create_preview_configuration 是新版 API ， 
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
            
        if SOURCE_TYPE == "PC_SCREEN" and self.show_window:
            self.show_window = safe_named_window(
                cv2_module=cv2,
                window_name="Live",
                window_mode=self._window_mode,
                window_flag=self._window_flag,
                flag_name="show_window",
                flag_enabled=self.show_window,
                display_warned=self._display_warned,
                logger_obj=logger,
                log_event_func=log_event,
            )
        elif SOURCE_TYPE == "PI_CAM" and self.pi_video_open:
            self.pi_video_open = safe_named_window(
                cv2_module=cv2,
                window_name="PiLive",
                window_mode=self._window_mode,
                window_flag=self._window_flag,
                flag_name="pi_video_open",
                flag_enabled=self.pi_video_open,
                display_warned=self._display_warned,
                logger_obj=logger,
                log_event_func=log_event,
            )

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
                    if img_np.ndim == 3 and img_np.shape[2] == 4:
                        frame = cv2.cvtColor(img_np, cv2.COLOR_BGRA2RGB)
                    else:
                        frame = cv2.cvtColor(img_np, cv2.COLOR_BGR2RGB)
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
                            res_img = self.result_frame.copy()
                        else :
                            res_img = None

                    # 采集帧计数（用于周期性状态日志）
                    self._status_inc_capture()
                        
                    # 降低不同步    
                    if SOURCE_TYPE == "PC_SCREEN" and self.show_window:
                        # 实时的原图
                        left_img = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                        
                        # 结果图
                        right_img = res_img if res_img is not None else np.zeros_like(left_img)
                        
                        # 拼接
                        combined_img = np.hstack([left_img, right_img])
                        
                        # 显示
                        try:
                            cv2.imshow("Live", combined_img)
                            cv2.waitKey(1)
                        except Exception as e:
                            self.show_window = disable_display_flag(
                                flag_name="show_window",
                                flag_enabled=self.show_window,
                                display_warned=self._display_warned,
                                logger_obj=logger,
                                log_event_func=log_event,
                                reason="imshow_failed",
                                err=e,
                            )
                    elif SOURCE_TYPE == "PI_CAM" and self.pi_video_open:
                        # Pi 摄像头只显示“处理后画面”；首帧推理前回退到原图
                        show_img = res_img
                        if show_img is None:
                            show_img = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                        try:
                            cv2.imshow("PiLive", show_img)
                            cv2.waitKey(1)
                        except Exception as e:
                            self.pi_video_open = disable_display_flag(
                                flag_name="pi_video_open",
                                flag_enabled=self.pi_video_open,
                                display_warned=self._display_warned,
                                logger_obj=logger,
                                log_event_func=log_event,
                                reason="imshow_failed",
                                err=e,
                            )
                
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

    # 解析日志级别配置
    def _parse_log_level(self, level_value, default=logging.DEBUG):
        if isinstance(level_value, int):
            return level_value

        level_name = str(level_value or "").strip().upper()
        level_map = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
            "CRITICAL": logging.CRITICAL,
        }
        return level_map.get(level_name, default)

    # 检测物体
    def _log_object_if_needed(self, det: dict):
        if not self._object_log_enable:
            return

        class_id = int(det.get("class_id", -1))
        class_name = str(det.get("class_name", ""))
        conf = float(det.get("conf", 0.0))
        bbox = det.get("bbox_xyxy") or [0, 0, 0, 0]
        center = det.get("center") or [0, 0]
        now = float(det.get("ts", time.time()))

        if len(bbox) < 4 or len(center) < 2:
            return

        x1, y1, x2, y2 = map(int, bbox[:4])
        cx_i, cy_i = int(center[0]), int(center[1])

        should_log = True

        if self._log_dedup_enable and not self._object_log_every_detection:
            key = class_id
            last = self._last_logged.get(key)

            if last is not None:
                lx, ly, lts, lbox = last
                dt = now - lts
                dist = ((cx_i - lx) ** 2 + (cy_i - ly) ** 2) ** 0.5
                iou = self._calc_iou_xyxy([x1, y1, x2, y2], lbox)

                same_obj = (
                    ((iou >= self._same_obj_iou_th) or (dist < self._same_obj_px_th))
                    and (dt < self._same_obj_time_th)
                )
                should_log = not same_obj

            if should_log:
                self._last_logged[key] = (cx_i, cy_i, now, [x1, y1, x2, y2])

        if should_log:
            log_event(
                self.logger,
                source="DETECT",
                event="object",
                key={
                    "label": class_name,
                    "class_id": class_id,
                    "conf": round(conf, 2),
                    "center": [cx_i, cy_i],
                    "bbox": [x1, y1, x2, y2],
                },
                level=self._object_log_level,
                brief=self._object_log_brief,
            )

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
            if row.shape[0] < 6:
                continue

            cx, cy, w, h = map(float, row[0:4])
            obj_conf = float(row[4])

            if (not np.isfinite(cx) or not np.isfinite(cy) or
                not np.isfinite(w) or not np.isfinite(h) or
                not np.isfinite(obj_conf) or w <= 0.0 or h <= 0.0):
                continue

            if obj_conf < conf_threshold:
                continue
            
            class_scores = np.asarray(row[5:], dtype=np.float32)
            if class_scores.size == 0:
                continue

            class_scores = np.nan_to_num(class_scores, nan=0.0, posinf=0.0, neginf=0.0)
            class_id = int(np.argmax(class_scores))
            cls_conf = float(class_scores[class_id])
            score = obj_conf * cls_conf
            
            if (not np.isfinite(score)) or score <= conf_threshold or class_id not in target_classes:
                continue

            # 转为左上角坐标 (x, y, w, h)
            x = int(cx - w / 2)
            y = int(cy - h / 2)

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
                det = {
                    "type": "detection",
                    "class_id": int(class_id),
                    "class_name": name,
                    "conf": float(score),
                    "bbox_xyxy": [int(x1), int(y1), int(x2), int(y2)],
                    "center": [cx_i, cy_i],
                    "area": area,
                    "img_size": [int(W), int(H)],
                    "ts": time.time(),
                }
                self._log_object_if_needed(det)
                dets.append(det)

        # 火警优先：一旦发现 fire，优先上报给 FSM 触发返航逻辑
        fire_ev = self._check_fire(dets)
        if fire_ev is not None:
            try:
                if hasattr(ctx, "put_latest"):
                    ctx.put_latest(ctx.dector_queue, fire_ev)
                else:
                    if ctx.dector_queue.full():
                        ctx.dector_queue.get_nowait()
                    ctx.dector_queue.put_nowait(fire_ev)
            except Exception:
                pass

            with self.frame_lock:
                self.result_frame = draw_img
            return

        # 违规判定（同帧关系：电瓶车 + 插排 近距离）
        violation_ev = self._check_violation_ebike_strip(dets, W, H)
        if violation_ev is not None:
            self._status_inc_violation()
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
                if self.pi_video_open:
                    log_event(
                        self.logger,
                        source="DETECT",
                        event="display",
                        action="init",
                        result="skip",
                        reason="process_backend_no_live_frame",
                        key={"pi_video_open": True},
                        level=logging.WARNING,
                        brief=False,
                    )
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

                conf_threshold = float(ctx.config.get("dector", {}).get("conf_threshold", 0.25))

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

                    W = int(msg.get("w", msg.get("img_w", 640)) or 640)
                    H = int(msg.get("h", msg.get("img_h", 640)) or 640)
                    if W <= 0 or H <= 0:
                        continue

                    dets = []
                    for d in dets_raw:
                        bbox = d.get("xyxy") or d.get("bbox_xyxy") or d.get("bbox")
                        if not bbox or len(bbox) < 4:
                            continue

                        try:
                            x1, y1, x2, y2 = map(float, bbox[:4])
                            conf = float(d.get("conf", 0.0))
                        except (TypeError, ValueError):
                            continue

                        if (not np.isfinite(x1) or not np.isfinite(y1) or
                            not np.isfinite(x2) or not np.isfinite(y2) or
                            not np.isfinite(conf) or conf < conf_threshold):
                            continue

                        if x1 > x2:
                            x1, x2 = x2, x1
                        if y1 > y2:
                            y1, y2 = y2, y1

                        x1_i = max(0, min(W - 1, int(x1)))
                        y1_i = max(0, min(H - 1, int(y1)))
                        x2_i = max(0, min(W - 1, int(x2)))
                        y2_i = max(0, min(H - 1, int(y2)))

                        if x2_i <= x1_i or y2_i <= y1_i:
                            continue

                        try:
                            class_id = int(d.get("class_id", -1))
                        except (TypeError, ValueError):
                            class_id = -1

                        area = max(0, x2_i - x1_i) * max(0, y2_i - y1_i)
                        cx = int((x1_i + x2_i) / 2)
                        cy = int((y1_i + y2_i) / 2)

                        dets.append({
                            "type": "detection",
                            "class_id": class_id,
                            "class_name": d.get("cls", d.get("class_name", "")),
                            "conf": conf,
                            "bbox_xyxy": [x1_i, y1_i, x2_i, y2_i],
                            "center": [cx, cy],
                            "area": int(area),
                            "img_size": [int(W), int(H)],
                            "ts": float(msg.get("ts", time.time())),
                        })

                    for det in dets:
                        self._log_object_if_needed(det)

                    if not dets:
                        continue

                    fire_ev = self._check_fire(dets)
                    if fire_ev is not None:
                        try:
                            if hasattr(ctx, "put_latest"):
                                ctx.put_latest(ctx.dector_queue, fire_ev)
                            else:
                                if ctx.dector_queue.full():
                                    ctx.dector_queue.get_nowait()
                                ctx.dector_queue.put_nowait(fire_ev)
                        except Exception as e:
                            log_event(self.logger, source="DETECT", event="process_output", action="queue_put", result="fail", reason="queue_put", key={"err": str(e)}, level=logging.WARNING, brief=False)
                            self._status_inc_infer(0.0)
                            self._status_maybe_log(time.time(), backend=self.backend)
                            continue

                        self._status_inc_infer(0.0)
                        self._status_maybe_log(time.time(), backend=self.backend)
                        continue

                    violation_ev = self._check_violation_ebike_strip(dets, W, H)

                    # 违规优先：让 FSM 抢占控制权
                    if violation_ev is not None:
                        self._status_inc_violation()
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
                            self._status_inc_infer(0.0)
                            self._status_maybe_log(time.time(), backend=self.backend)
                            continue
                    else:
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

                    self._status_inc_infer(0.0)
                    self._status_maybe_log(time.time(), backend=self.backend)

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
                "display": bool(self._has_display),
                "show_window": bool(self.show_window),
                "pi_video_open": bool(self.pi_video_open),
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
        log_event(
            logger,
            source="DETECT",
            event="object_log",
            key={
                "enable": self._object_log_enable,
                "every_detection": self._object_log_every_detection,
                "brief": self._object_log_brief,
                "level": logging.getLevelName(self._object_log_level),
            },
            level=logging.DEBUG,
            brief=False,
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
                    t0 = time.time()
                    input_tensor = self._preprocess(current_img)
                    
                    # 推理
                    outputs = self.sess.run(None, {self.input_name: input_tensor})
                    
                    # 后处理,不能过度处理
                    img_bgr = current_img # cv2.cvtColor(current_img, cv2.COLOR_RGB2BGR)
                    self._yolo_postprocess(outputs, img_bgr)

                    # 统计推理耗时并尝试输出周期状态
                    cost_s = time.time() - t0
                    self._status_inc_infer(cost_s)
                    self._status_maybe_log(time.time(), backend=self.backend)
                    
                except Exception as e:
                    log_event(logger, source="DETECT", event="inference", result="fail", reason=str(e), level=logging.ERROR, brief=False)
                    time.sleep(1)

        # 退出清理
        self.stop_capture = True
        if self.picam2:
            try:
                self.picam2.stop()
                self.picam2.close()
                log_event(logger, source="DETECT", event="shutdown", action="picam2_stop", result="ok", brief=False)
            except Exception as e:
                log_event(logger, source="DETECT", event="shutdown", action="picam2_stop", result="fail", reason=str(e), level=logging.WARNING, brief=False)
                
        # sct 在子线程里会自动销毁
        if (SOURCE_TYPE == "PC_SCREEN" and self.show_window) or (SOURCE_TYPE == "PI_CAM" and self.pi_video_open):
            try:
                cv2.destroyAllWindows()
                log_event(logger, source="DETECT", event="shutdown", action="cv2_destroy", result="ok", level=logging.DEBUG, brief=False)
            except Exception as e:
                self.show_window = disable_display_flag(
                    flag_name="show_window",
                    flag_enabled=self.show_window,
                    display_warned=self._display_warned,
                    logger_obj=logger,
                    log_event_func=log_event,
                    reason="destroy_window_failed",
                    err=e,
                )
                self.pi_video_open = disable_display_flag(
                    flag_name="pi_video_open",
                    flag_enabled=self.pi_video_open,
                    display_warned=self._display_warned,
                    logger_obj=logger,
                    log_event_func=log_event,
                    reason="destroy_window_failed",
                    err=e,
                )
                
        log_event(logger, source="DETECT", event="stop_request", result="ok", brief=False)
