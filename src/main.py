import time
import sys
import os
import yaml
import threading

# 对路径进行配置,保证能正确导入模块
sys.path.append( os.path.dirname(os.path.abspath(__file__)) + '/../')

# 导入全局上下文模块
import src.global_ctx as ctx

# 导入三个线程所需模块
from src.core.fsm import FSM_core
from src.services.dector import DECTOR_ser
from src.drivers.uart import STM32Communicator

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

# 方便从线程中拿命令
def uart_pump(uart):
    while not ctx.system_stop_event.is_set():
        try:
            cmd = ctx.uart_queue.get(timeout=0.2)
        except Exception:
            continue

        if cmd is None:
            continue

        uart.send_command(str(cmd), wait_for_response=True)

def main() :
    logger.info("[ INIT ] System started up")

    if not load_config() :
        return

    # 初始化 UART
    uart_cfg = ctx.config.get("uart", {})
    uart_enable = bool(uart_cfg.get("enable", True))
    uart_required = bool(uart_cfg.get("required", False))

    uart = None
    if uart_enable:
        uart = STM32Communicator(
            port=str(uart_cfg.get("port", "COM10")),
            baudrate=int(uart_cfg.get("baudrate", 115200)),
            timeout=float(uart_cfg.get("timeout", 1.0)),
        )

        # 放到全局上下文，方便 FSM 等模块使用
        ctx.uart = uart

        logger.info(f"[ UART ] init: port={uart.port} baudrate={uart.baudrate} timeout={uart.timeout}")

        ok = uart.connect()
        if not ok:
            logger.warning("[ UART ] connect failed")
            if uart_required:
                logger.error("[ INIT ] UART is required, system stop running")
                return
            else:
                logger.warning("[ INIT ] UART not ready, continue without lower machine")
        if ok:
            uart_thread = threading.Thread(target=uart_pump, args=(uart,), daemon=True)
            uart_thread.start()
            logger.info("[ UART ] pump thread started")

    else:
        ctx.uart = None
        logger.warning("[ UART ] disabled by config")

    # 创建线程
    dector_thread = DECTOR_ser()
    fsm_thread = FSM_core()

    # 启动线程
    logger.info("-" * 30)
    dector_thread.start()
    fsm_thread.start()
    logger.info("-" * 30)
    logger.info("[ INIT ] System startup completed")

    logger.info("[ INIT ] The system is currently running. Press Ctrl+C to stop the system")

    try :
        while True :
            # UART 不再用 is_alive 检查（它不是线程）
            if uart_enable and ctx.uart is not None:
                uart_ok = (ctx.uart.ser is not None) and getattr(ctx.uart.ser, "is_open", False)
                if uart_required and (not uart_ok):
                    logger.info("[ INIT ] UART disconnected and required, system stop running")
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

        # UART 不是 join，而是 disconnect
        try:
            if ctx.uart is not None:
                ctx.uart.disconnect()
        except Exception:
            pass

        dector_thread.join(timeout = 2)
        fsm_thread.join(timeout = 2)

        logger.info("[ INIT ] Stop runing")

if __name__ == "__main__" :
    main()
