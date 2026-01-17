import logging
import os
import datetime
import sys

# 确定日志保存路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, '../../data/logs')\

# 创建日志目录（如果不存在）
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)
    
def _setup_logger():
    # 添加时间戳
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    log_name = f'log_{timestamp}.log'
    log_path = os.path.join(LOG_DIR, log_name)
    
    # 配置logger
    logger = logging.getLogger('RoboPatrol')
    # 设置记录级别 >= INFO
    logger.setLevel(logging.INFO)
    
    if not logger.handlers:
        # 设置格式
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        
        # 文件输出
        file_handler = logging.FileHandler(log_path , encoding='utf-8')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        
        # 控制台输出
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        
    return logger

# 方便导入
sys_logger = _setup_logger()
        