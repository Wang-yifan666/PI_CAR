import threading 
import time
import os
import sys
import numpy as np 
import src.global_ctx as ctx 

from src.utils.logger import sys_logger as logger

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
        
        self.daemon = True
        self.picam2 = None     # 树莓派的摄像头
        self.sct = None        # 电脑进行截屏
        self.sess = None
        self.input_name = None
        
        self.mode = not AI_READY
        
        self.latest_frame = None            # 只保留最新的图片，防止出现大幅度不同步
        self.result_frame = None            # 存放结果
        self.frame_lock = threading.Lock()  # 线程锁，防止又读又写
        self.stop_capture = False
        
        # 日志报告当前模式
        if not AI_READY:
            logger.warning(f"[ DECTOR ]: Core library missing ({MISSING_LIB}),Enter simulation mode")
        else:
            if SOURCE_TYPE == "PC_SCREEN":
                logger.info("[ DECTOR ]: No Raspberry Pi camera, switching to computer screen recording")
            elif SOURCE_TYPE == "PI_CAM":
                logger.info("[ DECTOR ]: Raspberry Pi camera equiped, switching to PI")

    # 读取类别列表
    def _load_classes(self):
        try : 
            class_file = ctx.config['dector']['class_file']
            
            base_dir = os.path.dirname(os.path.abspath(__file__))
            abs_path = os.path.join(base_dir , '../../' , class_file)

            with open ( abs_path , 'r' , encoding = 'utf-8') as f :
                self.classes = [line.strip() for line in f.readlines()]
            logger.info(f"[ DECTOR ] Category file loaded successfully ,load{len(self.classes)} category labels")
        
        except Exception as e :
            logger.error(f"[ DECTOR ] Category file loading failed {e} ")
    
    # 初始化硬件
    def _init_hardware(self):
        if self.mode :   # 如果为模拟模式,无硬件需要初始化,跳过 
            return 
        
        try:
            # 树莓派模式
            if SOURCE_TYPE == "PI_CAM":
                logger.info("[ DECTOR ] Starting Picamera2...")
                self.picam2 = Picamera2()
                config = self.picam2.create_configuration(main={"size": (640, 640), "format": "RGB888"})
                self.picam2.configure(config)
                self.picam2.start()

            # 电脑屏幕模式
            elif SOURCE_TYPE == "PC_SCREEN":
                logger.info("[ DECTOR ] Screen capture will start in worker thread.")

            # 加载yolov5 (共用)
            model_path = ctx.config['dector']['model_path']
            base_dir = os.path.dirname(os.path.abspath(__file__))
            model_abs_path = os.path.join(base_dir, '../../', model_path)
            
            sess_options = onnxruntime.SessionOptions() 
            sess_options.intra_op_num_threads = 4      # 将四核全部使用，加快推理速度
            self.sess = onnxruntime.InferenceSession(model_abs_path, sess_options)
            self.input_name = self.sess.get_inputs()[0].name
            
        except Exception as e:
            logger.error(f"[ DECTOR ] Startup failure: {e}")
            self.mode = True # 降级为模拟
    
    # 处理大量图像，采集最新的一张图像
    def _capture_worker(self):
        logger.info("[ DECTOR ] capture worker starting ... ")
        
        # 在这里初始化 mss
        local_sct = None
        if SOURCE_TYPE == "PC_SCREEN":
            import mss
            local_sct = mss.mss()
            
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
                elif SOURCE_TYPE  == "MOCK" or self.mock_mode :
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
                            np.zeros_like(frame)
                        
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
                logger.error(f"[ DECTOR ] : Capture error : {e}")
                time.sleep(1)
    
    # 预处理图片,给yolov5
    def _preprocess(self , img):
        # 输入的图片为（640,640,3）
        img = img / 255.0                     # 像素值从 0-255 变成 0.0-1.0
        img = img.transpose( 2, 0 , 1 )       # 维度顺序从 HWC 变成 CHW
        img = np.expand_dims(img , axis = 0 ) # 增加一个维度 (Batch)
        
        return img.astype(np.float32)
    
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
                
                logger.info(f"[ DECTOR ] Object Found: {name} (Conf: {score:.2f})")
                
                evidence = {
                    "type": "detection",
                    "class_name": name,
                    "conf": float(score),
                    "frame": original_img
                }
                
                if not ctx.dector_queue.full():
                    ctx.dector_queue.put(evidence)

        # 保存画好的图供显示
        with self.frame_lock:
            self.result_frame = draw_img
        
    # 运行
    def run(self):
        logger.info("[ DECTOR ]: Thread starting")
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
                    logger.info("[ fake ] ebike founded")
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
                    
                    # # 如果是电脑模式，显示实时画面
                    # if SOURCE_TYPE == "PC_SCREEN" and current_img is not None:
                    #     show_img = cv2.cvtColor(current_img, cv2.COLOR_RGB2BGR)
                    #     cv2.imshow("RoboPatrol Live", show_img)
                    #     cv2.waitKey(1)

                    
                except Exception as e:
                    logger.error(f"[ DECTOR ]: Inference Error: {e}")
                    time.sleep(1)

        # 退出清理
        self.stop_capture = True
        if self.picam2: self.picam2.stop()
        # sct 在子线程里会自动销毁
        try:
            cv2.destroyAllWindows()
        except:
            pass
        logger.info("[ DECTOR ] Thread finished")
