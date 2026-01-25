import time
import sys
import os
import yaml
import threading

# 对路径进行配置,保证能正确导入模块
sys.path.append( os.path.dirname(os.path.abspath(__file__)) + '/../')

# 导入全局上下文模块
import src.global_ctx as ctx

# 导入线程所需模块
from src.services.dector import DECTOR_ser
from src.drivers.uart import STM32Communicator
from src.services.gps_service import GPSService
from src.core.patrol_logic import PatrolService
from src.core.fsm import FSMService

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
    last_cmd = None
    last_ts = 0.0

    # 连续类重复命令最小间隔（按需要调）
    MIN_INTERVAL_CONTINUOUS = 0.5   # 0.2~1.0 都行
    # 离散类重复命令最小间隔（一般设成很大或直接“相同就不发”）
    MIN_INTERVAL_DISCRETE = 9999.0  # 等价于严格去重

    while not ctx.system_stop_event.is_set():
        try:
            cmd = ctx.uart_queue.get(timeout=0.2)
        except Exception:
            continue

        if cmd is None:
            break

        cmd = str(cmd).strip()
        if not cmd:
            continue

        # 串口已断开就不发
        try:
            ser = getattr(uart, "ser", None)
            if (ser is None) or (not getattr(ser, "is_open", False)):
                continue
        except Exception:
            continue

        kind = _cmd_kind(cmd)
        now = time.time()

        # 动作去重
        if cmd == last_cmd:
            if kind == "stop":
                pass
            elif kind == "discrete":
                # 旋转/舵机：相同命令直接丢弃，避免“转两次”
                continue
            elif kind == "continuous":
                # 前进/后退/平移：避免刷屏
                if (now - last_ts) < MIN_INTERVAL_CONTINUOUS:
                    continue
            else:
                # 其他命令：避免刷屏
                if (now - last_ts) < 0.5:
                    continue

        # 真正发送
        try:
            uart.send_command(cmd, wait_for_response=False)
            last_cmd = cmd
            last_ts = now

            # 可选：同步到全局状态，方便日志/调试
            try:
                ctx.set_mission(last_uart_cmd=cmd, last_uart_cmd_ts=now)
            except Exception:
                pass

        except Exception:
            continue

# 防止重复答应相同指令,先对指令分类
def _cmd_kind(cmd: str) -> str:
    c = (cmd or "").strip()

    if c in ("S", "STOP") or (c.startswith("S") and len(c) >= 2):
        return "stop"

    # 重复一次就会叠加执行的必须去重
    if c.startswith(("R0", "L0", "D", "A")):
        return "discrete"

def main() :
    logger.info("[ INIT ] System started up")

    if not load_config() :
        return

    # 初始化 UART
    uart_cfg = ctx.config.get("uart", {})
    uart_enable = bool(uart_cfg.get("enable", True))
    uart_required = bool(uart_cfg.get("required", False))

    uart = None
    uart_thread = None
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

    # 创建并启动 gps_service
    gps_cfg = ctx.config.get("gps", {})
    gps_enable = bool(gps_cfg.get("enable", True))

    gps_thread = None
    if gps_enable:
        try:
            gps_thread = GPSService(gps_cfg)  # 你如果构造函数不是这样，按你的改
            gps_thread.start()
            logger.info("[ GPS ] service started")
        except Exception as e:
            logger.error(f"[ GPS ] failed to start: {e}")
            gps_thread = None
    else:
        logger.warning("[ GPS ] disabled by config")

    # 创建巡逻线程
    patrol_cfg = ctx.config.get("patrol", {})
    patrol_enable = bool(patrol_cfg.get("enable", True))

    patrol_thread = None
    if patrol_enable:
        try:
            patrol_thread = PatrolService(patrol_cfg)
            patrol_thread.start()
            logger.info("[ PATROL ] service started")
        except Exception as e:
            logger.error(f"[ PATROL ] failed to start: {e}")
            patrol_thread = None
    else:
        logger.warning("[ PATROL ] disabled by config")

    # 创建监视和大脑线程
    dector_thread = DECTOR_ser()
    fsm_thread = FSMService()

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
            if gps_enable and (gps_thread is not None) and (not gps_thread.is_alive()):
                logger.info("[ INIT ] GPS service exited abnormally, system stop running")
                break

            logger.info("[ INIT ] Running ")

            time.sleep(1)

    except KeyboardInterrupt :
        logger.info("+" * 30)
        logger.info("[ INIT ] The system will stop running upon receiving the stop message")

    finally:
        ctx.system_stop_event.set()

        # 先通知 uart_pump 退出
        try:
            if hasattr(ctx, "put_latest"):
                ctx.put_latest(ctx.uart_queue, None)
            else:
                try:
                    if ctx.uart_queue.full():
                        ctx.uart_queue.get_nowait()
                except Exception:
                    pass
                ctx.uart_queue.put_nowait(None)
        except Exception:
            pass

        # 等uart_pump线程退出
        try:
            if uart_thread is not None and uart_thread.is_alive():
                uart_thread.join(timeout=1.0)
        except Exception:
            pass

        # 最后断开串口
        try:
            if ctx.uart is not None:
                ctx.uart.disconnect()
        except Exception:
            pass

        try: dector_thread.join(timeout=2)
        except Exception: pass
        try: fsm_thread.join(timeout=2)
        except Exception: pass
        if gps_thread is not None:
            try: gps_thread.join(timeout=2)
            except Exception: pass
        if patrol_thread is not None:
            try: patrol_thread.join(timeout=2)
            except Exception: pass

        logger.info("[ INIT ] Stop running")

if __name__ == "__main__" :
    main()
