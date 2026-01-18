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
            logger.info("[ INIT ]配置文件加载成功")
            return True
    except Exception as e :
        logger.error(f"[ INIT ]配置文件加载失败,错误信息：{e}")
        return False
    
def main() :
    logger.info("[ INIT ]系统开始启动")
    
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
    logger.info("[ INIT ]系统启动完成")
    
    logger.info("[ INIT ]系统正在运行中,按 Ctrl+C 停止系统")
    
    try :
        while True :
            if not ( uart_thread.is_alive() ) :
                logger.info("[ INIT ]UART线程异常退出,系统停止运行")
                break
            if not ( dector_thread.is_alive() ) :
                logger.info("[ INIT ]DECTOR线程异常退出,系统停止运行")
                break   
            if not ( fsm_thread.is_alive() ) :
                logger.info("[ INIT ]FSM线程异常退出,系统停止运行")
                break
            
            logger.info("[ INIT ]系统运行正常...")
            
            time.sleep(1)
            
    except KeyboardInterrupt :
        logger.info("+" * 30)
        logger.info("[ INIT ]收到停止信息,系统即将停止运行")
        ctx.system_stop_event.set()
        
        uart_thread.join(timeout = 2)
        dector_thread.join(timeout = 2)
        fsm_thread.join(timeout = 2)
        
        logger.info("[ INIT ]系统已停止运行")
        
if __name__ == "__main__" :
    main()