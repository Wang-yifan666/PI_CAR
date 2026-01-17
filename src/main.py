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

print("hello uesr")

def load_config() :
    try :
        base_dir = os.path.dirname(os.path.abspath(__file__))
        yaml_path = os.path.join(base_dir , '../config/settings.yaml')
        with open ( yaml_path , 'r' , encoding='utf-8') as f :
            ctx.config = yaml.safe_load(f)
            print("配置文件加载成功")
            return True
    except Exception as e :
        print("配置文件加载失败,错误信息：", {e} )
        return False
    
def main() :
    print("+++ 系统开始启动 +++")
    
    if not load_config() : 
        return
    
    # 创建线程
    uart_thread = UART_drv()
    dector_thread = DECTOR_ser()
    fsm_thread = FSM_core()
    
    # 启动线程
    print("-" * 30)
    uart_thread.start()
    dector_thread.start()   
    fsm_thread.start()
    print("-" * 30)
    print("+++ 系统启动完成 +++")
    
    print("系统正在运行中,按 Ctrl+C 停止系统")
    
    try :
        while True :
            if not ( uart_thread.is_alive() ) :
                print("UART线程异常退出,系统停止运行")
                break
            if not ( dector_thread.is_alive() ) :
                print("DECTOR线程异常退出,系统停止运行")
                break   
            if not ( fsm_thread.is_alive() ) :
                print("FSM线程异常退出,系统停止运行")
                break
            
            print("系统运行正常...")
            
            time.sleep(1)
            
    except KeyboardInterrupt :
        print("+" * 30)
        print("收到停止信息,系统即将停止运行")
        ctx.system_stop_event.set()
        
        uart_thread.join(timeout = 2)
        dector_thread.join(timeout = 2)
        fsm_thread.join(timeout = 2)
        
        print("+++ 系统已停止运行 +++")
        
if __name__ == "__main__" :
    main()