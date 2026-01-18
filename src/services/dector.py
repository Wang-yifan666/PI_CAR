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
        
        # 日志报告当前模式
        if not AI_READY:
            logger.warning(f"[ DECTOR ]: 核心库缺失 ({MISSING_LIB})，进入模拟模式")
        else:
            if SOURCE_TYPE == "PC_SCREEN":
                logger.info("[ DECTOR ]: 未检测到树莓派摄像头，切换为电脑录屏")
            elif SOURCE_TYPE == "PI_CAM":
                logger.info("[ DECTOR ]: 检测到树莓派摄像头，切换PI】")

    # 读取类别列表
    def _load_classes(self):
        try : 
            class_file = ctx.config['dector']['class_file']
            
            base_dir = os.path.dirname(os.path.abspath(__file__))
            abs_path = os.path.join(base_dir , '../../' , class_file)

            with open ( abs_path , 'r' , encoding = 'utf-8') as f :
                self.classes = [line.strip() for line in f.readlines()]
            logger.info(f"[ DECTOR ] 类别文件加载成功 ,加载了{len(self.classes)} 个类别标签")
        
        except Exception as e :
            logger.error(f"[ DECTOR ] 类别文件加载失败 {e} ")
    
    # 初始化硬件
    def _init_hardware(self):
        if self.mode :   # 如果为模拟模式,无硬件需要初始化,跳过 
            return 
        
        try:
            # 树莓派模式
            if SOURCE_TYPE == "PI_CAM":
                logger.info("[ DECTOR ]正在启动 Picamera2...")
                self.picam2 = Picamera2()
                config = self.picam2.create_configuration(main={"size": (640, 640), "format": "RGB888"})
                self.picam2.configure(config)
                self.picam2.start()

            # 电脑屏幕模式
            elif SOURCE_TYPE == "PC_SCREEN":
                logger.info("[ DECTOR ]正在启动屏幕捕获 (mss)...")
                self.sct = mss.mss()

            # 加载yolov5 (共用)
            model_path = ctx.config['dector']['model_path']
            base_dir = os.path.dirname(os.path.abspath(__file__))
            model_abs_path = os.path.join(base_dir, '../../', model_path)
            
            logger.info(f"[ DECTOR ]正在加载模型: {model_path}")
            self.sess = onnxruntime.InferenceSession(model_abs_path)
            self.input_name = self.sess.get_inputs()[0].name
            
        except Exception as e:
            logger.error(f"[ DECTOR ]启动失败: {e}")
            self.mode = True # 降级为模拟
    
    # 预处理图片,给yolov5
    def _preprocess(self , img):
        # 输入的图片为（640,640,3）
        img = img / 255.0                     # 像素值从 0-255 变成 0.0-1.0
        img = img.transpose( 2, 0 , 1 )       # 维度顺序从 HWC 变成 CHW
        img = np.expand_dims(img , axis = 0 ) # 增加一个维度 (Batch)
        
        return img.astype(np.float32)
    
    # 后处理,yolov5结果处理
    def _postprocess(self , outputs , img):
        output = outputs[0][0] 
        
        conf_threshold = ctx.config['dector']['conf_threshold']
        
        for row in output :
            obj_conf = row[4]  # 物体的置信度
            if obj_conf < conf_threshold :
                continue
            
            class_scores = row[5:]
            class_id = np.argmax(class_scores)
            score = obj_conf * class_scores[class_id]
        
            if score > conf_threshold :
                
                # 获取名字
                name = self.classes[class_id] if self.classes else str(class_id)
                logger.info(f"[ DECTOR ] 发现目标: {name} (置信度: {score:.2f})")
                
                # 打包证据
                evidence = {
                    "type": "dectorion",
                    "class_name": name,
                    "conf": float(score),
                    "frame": img # 把原图带上
                }
                
                # 放入队列,通知 FSM
                if not ctx.dector_queue.full():
                    ctx.dector_queue.put(evidence)
                    
                # 为了防止日志刷屏,稍微停一下
                time.sleep(0.5) 
        
    # 运行
    def run(self):
        logger.info("[ DECTOR ]: 线程启动")
        self._load_classes()
        self._init_hardware()
        
        while not ctx.system_stop_event.is_set():
            # 模拟模式
            if self.mode:
                time.sleep(1)
                if int(time.time()) % 10 == 0:
                    logger.info("[ fake ]发现摩托车...")
                    fake_ev = {"class_name": "motorcycle", "conf": 0.99, "frame": None}
                    if not ctx.dector_queue.full(): ctx.dector_queue.put(fake_ev)
                    time.sleep(1)
                continue

            #真实推理模式
            try:
                img_rgb = None
                
                # 获取图像
                if SOURCE_TYPE == "PI_CAM":
                    # 树莓派直接吐出 RGB
                    img_rgb = self.picam2.capture_array()
                
                elif SOURCE_TYPE == "PC_SCREEN":
                    # 截取全屏并强制缩放到 640x640
                    monitor = self.sct.monitors[1] # 获取主屏幕
                    sct_img = self.sct.grab(monitor)
                    # mss 抓到的是 BGRA，转成 RGB
                    img_np = np.array(sct_img)
                    img_rgb = cv2.cvtColor(img_np, cv2.COLOR_BGRA2RGB)
                    # 强制缩放
                    img_rgb = cv2.resize(img_rgb, (640, 640))

                # 推理流程 (共用)
                if img_rgb is not None:
                    # 预处理
                    input_tensor = self._preprocess(img_rgb)
                    # 推理
                    outputs = self.sess.run(None, {self.input_name: input_tensor})
                    # 后处理 (转回 BGR 方便 OpenCV 存图)
                    img_bgr = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)
                    self._postprocess(outputs, img_bgr)
                
                # 如果是电脑模式，在屏幕上显示一个小窗口，让你看到它在看哪里
                if SOURCE_TYPE == "PC_SCREEN":
                    cv2.imshow("RoboPatrol View", img_bgr)
                    cv2.waitKey(1)

                time.sleep(0.05) # 约 20 FPS

            except Exception as e:
                logger.error(f"[ DECTOR ][ OTHER ]推理循环出错: {e}")
                time.sleep(1)

        # 退出清理
        if self.picam2: self.picam2.stop()
        if self.sct: self.sct.close()
        cv2.destroyAllWindows()
        logger.info("[ DECTOR ]线程结束")
