import time
import sys
import os
import yaml

# 对路径进行配置,保证能正确导入模块
sys.path.append( os.path.dirname(os.path.abspath(__file__)) + '/../')

# 导入全局上下文模块
import src.global_ctx as ctx

# 导入三个线程所需模块
from src.core.fsm import FSM_core
from src.services.dector import DECTOR_ser
from src.drivers.uart import UART_drv

# 导入日志
from src.utils.logger import sys_logger as logger

logger.info("hello user")

def load_config() :
    try :
        base_dir = os.path.dirname(os.path.abspath(__file__))
        yaml_path = os.path.join(base_dir , '../config/settings.yaml')
        with open ( yaml_path , 'r' , encoding='utf-8') as f :
            ctx.config = yaml.safe_load(f)
            logger.info("[ INIT ] Configuration file loaded successfully")
            return True
    except Exception as e :
        logger.error(f"[ INIT ] Configuration file loading failed, error message: {e}")
        return False
    
def main() :
    logger.info("[ INIT ] System started up")
    
    if not load_config() : 
        return
    
    # 创建线程
    uart_thread = UART_drv()
    dector_thread = DECTOR_ser()
    fsm_thread = FSM_core()
    
    # 启动线程
    logger.info("-" * 30)
    uart_thread.start()
    dector_thread.start()   
    fsm_thread.start()
    logger.info("-" * 30)
    logger.info("[ INIT ] System startup completed")
    
    logger.info("[ INIT ] The system is currently running. Press Ctrl+C to stop the system")
    
    try :
        while True :
            if not ( uart_thread.is_alive() ) :
                logger.info("[ INIT ] UART thread exited abnormally, system stop running")
                break
            if not ( dector_thread.is_alive() ) :
                logger.info("[ INIT ] DECTOR thread exited abnormally, system stop running")
                break   
            if not ( fsm_thread.is_alive() ) :
                logger.info("[ INIT ] FSM thread exited abnormally, system stop running")
                break
            
            logger.info("[ INIT ] Runing ")
            
            time.sleep(1)
            
    except KeyboardInterrupt :
        logger.info("+" * 30)
        logger.info("[ INIT ] The system will stop running upon receiving the stop message")
        ctx.system_stop_event.set()
        
        uart_thread.join(timeout = 2)
        dector_thread.join(timeout = 2)
        fsm_thread.join(timeout = 2)
        
        logger.info("[ INIT ] Stop runing")
        
if __name__ == "__main__" :
    main()