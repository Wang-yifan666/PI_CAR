import time
import sys
import os
import yaml
import threading
import logging 

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
from src.services.uploader import UploadService

from src.utils.logger import sys_logger as logger, configure_logging, log_event

# 读取配置文件：
# - Linux/Pi 默认优先 settings.yaml（实车配置）
# - Windows 默认优先 settings_cpp.yaml（PC 调试配置）
# - 可用环境变量 PICAR_CONFIG 强制指定
def load_config(prefer_pi: bool = False) :  
    try:
        base_dir = os.path.dirname( os.path.abspath(__file__) )
        project_dir = os.path.abspath(os.path.join(base_dir, ".."))

        env_cfg = str(os.environ.get("PICAR_CONFIG", "")).strip()
        yaml_candidates = []

        if env_cfg:
            if os.path.isabs(env_cfg):
                yaml_candidates = [env_cfg]
            else:
                yaml_candidates = [os.path.join(project_dir, env_cfg)]
        else:
            if prefer_pi:
                yaml_candidates = [
                    os.path.join(base_dir, '../config/settings.yaml'),
                    os.path.join(base_dir, '../config/settings_cpp.yaml'),
                ]
            else:
                yaml_candidates = [
                    os.path.join(base_dir, '../config/settings_cpp.yaml'),
                    os.path.join(base_dir, '../config/settings.yaml'),
                ]


        yaml_path = None
        for p in yaml_candidates:
            if os.path.exists(p) and os.path.getsize(p) > 0 : 
                yaml_path = p
                break

        if yaml_path is None :
            raise FileNotFoundError("No config file found or all config files are empty")

        with open(yaml_path, 'r', encoding='utf-8') as f :
            ctx.config = yaml.safe_load(f) or {}

        log_event(logger, source="INIT", event="config_load", result="ok",
                  reason=os.path.basename(yaml_path),
                  key={
                      "prefer_pi": bool(prefer_pi),
                      "env_override": bool(env_cfg),
                  },
                  brief=False)
        return True
    except Exception as e:
        log_event(logger, source="INIT", event="config_load", result="fail",
                  reason=str(e), level=logging.ERROR, brief=False)
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

            # 同步到全局状态，方便日志/调试
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

def _detect_platform_is_pi() -> tuple[bool, str, str, str]:
    """检测平台是否为树莓派，返回 (is_pi, result, reason, err)."""

    # 两级检测：先尝试创建，再尝试 import
    try:
        from picamera2 import Picamera2  # type: ignore
        try:
            _ = Picamera2()
            return True, "ok", "create_ok", ""
        except Exception as e:
            return False, "fail", "create_fail", str(e)
    except Exception as e:
        return False, "fail", "import_fail", str(e)

def main() :
    is_pi, pf_result, pf_reason, pf_err = _detect_platform_is_pi()
    configure_logging(is_pi=is_pi, enable_cn=None, stage="main")

    log_event(
        logger,
        source="INIT",
        event="platform_detect",
        result=pf_result,
        reason=pf_reason,
        key={"err": pf_err} if pf_err else None,
        level=logging.WARNING if pf_result == "fail" else logging.INFO,
        brief=None,
    )

    log_event(logger, source="INIT", event="startup", result="begin", brief=None)

    prefer_pi_config = bool(is_pi or sys.platform.startswith("linux"))

    if not load_config(prefer_pi=prefer_pi_config) :
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

        log_event(logger, source="UART", event="init", action="connect", key={"port": uart.port, "baudrate": uart.baudrate, "timeout": uart.timeout}, brief=False)

        ok = uart.connect()
        if not ok:
            log_event(logger, source="UART", event="connect", result="fail", brief=False, level=logging.WARNING)
            if uart_required:
                log_event(logger, source="INIT", event="startup", result="stop", reason="uart_required", brief=None, level=logging.ERROR)
                return
            else:
                log_event(logger, source="INIT", event="startup", result="degraded", reason="uart_not_ready", brief=None, level=logging.WARNING)
        if ok:
            uart_thread = threading.Thread(target=uart_pump, args=(uart,), daemon=True)
            uart_thread.start()
            log_event(logger, source="UART", event="pump_start", result="ok", brief=False)

    else:
        ctx.uart = None
        log_event(logger, source="UART", event="disabled", result="skip", brief=False, level=logging.WARNING)

    # 创建并启动 gps_service
    gps_cfg = ctx.config.get("gps", {})
    gps_enable = bool(gps_cfg.get("enable", True))

    gps_thread = None
    if gps_enable:
        try:
            gps_thread = GPSService(gps_cfg)  # 你如果构造函数不是这样，按你的改
            gps_thread.start()
            log_event(logger, source="GPS", event="thread_spawn", action="start", result="ok", key={"thread": "GPSService"}, level=logging.DEBUG, brief=False)
        except Exception as e:
            log_event(logger, source="GPS", event="thread_spawn", action="start", result="fail", reason=str(e), level=logging.ERROR, brief=False)
            gps_thread = None
    else:
        log_event(logger, source="GPS", event="disabled", result="skip", level=logging.WARNING, brief=False)

    # 创建巡逻线程
    patrol_cfg = ctx.config.get("patrol", {})
    patrol_enable = bool(patrol_cfg.get("enable", True))

    patrol_thread = None
    if patrol_enable:
        try:
            patrol_thread = PatrolService(patrol_cfg)
            patrol_thread.start()
            log_event(logger, source="PATROL", event="thread_spawn", action="start", result="ok", key={"thread": "PatrolService"}, level=logging.DEBUG, brief=False)
        except Exception as e:
            log_event(logger, source="PATROL", event="thread_spawn", action="start", result="fail", reason=str(e), level=logging.ERROR, brief=False)
            patrol_thread = None
    else:
        log_event(logger, source="PATROL", event="disabled", result="skip", level=logging.WARNING, brief=False)

    # 创建监视和大脑线程
    dector_thread = DECTOR_ser()
    fsm_thread = FSMService()

    # 上传线程（可选）
    upload_cfg = ctx.config.get("uploader", {}) if hasattr(ctx, "config") else {}
    upload_enable = bool(upload_cfg.get("enable", False))
    upload_thread = None
    if upload_enable:
        try:
            upload_thread = UploadService(ctx.config)
            upload_thread.start()
            log_event(logger, source="UPLOAD", event="thread_spawn", action="start", result="ok", key={"endpoint": upload_cfg.get("endpoint", "")}, level=logging.DEBUG, brief=False)
        except Exception as e:
            log_event(logger, source="UPLOAD", event="thread_spawn", action="start", result="fail", reason=str(e), level=logging.ERROR, brief=False)

    # 启动线程
    log_event(logger, source="INIT", event="threads", action="start_bar", result="ok", brief=False)
    dector_thread.start()
    fsm_thread.start()
    log_event(logger, source="INIT", event="startup", result="ok", brief=None)

    log_event(logger, source="INIT", event="running", result="ok", brief=False)

    try :
        while True :
            if uart_enable and ctx.uart is not None:
                uart_ok = (ctx.uart.ser is not None) and getattr(ctx.uart.ser, "is_open", False)
                if uart_required and (not uart_ok):
                    log_event(logger, source="INIT", event="health", result="stop", reason="uart_required_disconnect", level=logging.WARNING, brief=None)
                    break

            if not ( dector_thread.is_alive() ) :
                log_event(logger, source="INIT", event="health", result="stop", reason="detect_thread_dead", level=logging.WARNING, brief=None)
                break
            if not ( fsm_thread.is_alive() ) :
                log_event(logger, source="INIT", event="health", result="stop", reason="fsm_thread_dead", level=logging.WARNING, brief=None)
                break
            if gps_enable and (gps_thread is not None) and (not gps_thread.is_alive()):
                log_event(logger, source="INIT", event="health", result="stop", reason="gps_thread_dead", level=logging.WARNING, brief=None)
                break

            time.sleep(1)

    except KeyboardInterrupt :
        log_event(logger,source="INIT",event="stop_request",result="ok",reason="keyboard_interrupt",key={"where": "main_try_except"},brief=False,level=logging.INFO, ) 
        
    finally:
        ctx.system_stop_event.set()

        # 先通知uart_pump退出
        try:
            if hasattr(ctx, "put_latest"):
                ctx.put_latest(ctx.uart_queue, None)
            else:
                try:
                    if ctx.uart_queue.full() :
                        ctx.uart_queue.get_nowait()
                except Exception :
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
        except Exception :
            pass

        try: 
            dector_thread.join(timeout = 2)
        except Exception : 
            pass 
        try: 
            fsm_thread.join(timeout = 2)
        except Exception : 
            pass
        if gps_thread is not None :
            try : 
                gps_thread.join(timeout = 2)
            except Exception : 
                pass
        if patrol_thread is not None :
            try :  
                patrol_thread.join(timeout = 2)
            except Exception : 
                pass

        log_event(logger, source="INIT", event="stop", result="ok", reason="main_exit", brief=True)

if __name__ == "__main__" :
    main()
