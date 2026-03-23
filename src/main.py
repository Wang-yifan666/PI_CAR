import time
import sys
import os
import yaml
import threading
import logging 
import re

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
from src.mode.showcase import ShowcaseMode


# 递归读取并填充缺失的配置项
def _deep_fill_missing(dst: dict, src: dict) -> dict:
    if not isinstance(dst, dict):
        dst = {}
    if not isinstance(src, dict):
        return dst

    for k, v in src.items():
        if k not in dst:
            dst[k] = v
        else:
            if isinstance(dst[k], dict) and isinstance(v, dict):
                _deep_fill_missing(dst[k], v)

    return dst

# 读取配置文件，当前优先级的配置项缺失时，自动从次优先级配置项中补齐。返回是否成功加载配置。
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

        valid_paths = []
        for p in yaml_candidates:
            if os.path.exists(p) and os.path.getsize(p) > 0:
                valid_paths.append(p)

        if len(valid_paths) == 0:
            raise FileNotFoundError("No config file found or all config files are empty")

        # 如果显式指定 PICAR_CONFIG，仍然只读这一份
        if env_cfg:
            with open(valid_paths[0], 'r', encoding='utf-8') as f:
                ctx.config = yaml.safe_load(f) or {}

            log_event(
                logger,
                source="INIT",
                event="config_load",
                result="ok",
                reason=os.path.basename(valid_paths[0]),
                key={
                    "prefer_pi": bool(prefer_pi),
                    "env_override": True,
                    "merged": False,
                },
                brief=False,
            )
            return True

        # 默认模式：先读优先配置，再用后面的配置补缺
        with open(valid_paths[0], 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}

        for p in valid_paths[1:]:
            with open(p, 'r', encoding='utf-8') as f:
                extra_cfg = yaml.safe_load(f) or {}
            cfg = _deep_fill_missing(cfg, extra_cfg)

        ctx.config = cfg

        log_event(
            logger,
            source="INIT",
            event="config_load",
            result="ok",
            reason=os.path.basename(valid_paths[0]),
            key={
                "prefer_pi": bool(prefer_pi),
                "env_override": False,
                "merged_from": [os.path.basename(p) for p in valid_paths],
            },
            brief=False,
        )
        return True
    
    except Exception as e:
        log_event(logger, source="INIT", event="config_load", result="fail",
                  reason=str(e), level=logging.ERROR, brief=False)
        return False

#region 处理选择模式

# 读取模式配置
_MODE_RE = re.compile(r"^M\d{4}$")
_mode_select_lock = threading.Lock()   # 线程锁
_mode_select_event = threading.Event() # 事件对象
_selected_mode_code = ""               # 目前的代码模式
_shutdown_request_event = threading.Event()  # 是否已请求优雅停机

# 重置模式选择状态
def _reset_mode_selection():
    global _selected_mode_code
    with _mode_select_lock:
        _selected_mode_code = ""
    _mode_select_event.clear()

# 获取当前选择的模式
def _get_selected_mode() -> str:
    with _mode_select_lock:
        return _selected_mode_code

# 设置当前选择的模式
def _set_selected_mode(mode_code: str) -> None:
    global _selected_mode_code
    mode_code = str(mode_code or "").strip().upper()
    if not _MODE_RE.fullmatch(mode_code):
        return

    with _mode_select_lock:
        _selected_mode_code = mode_code

    try:
        ctx.set_mission(
            selected_mode=mode_code,
            mode_select_ts=time.time(),
        )
    except Exception:
        pass

    _mode_select_event.set()


def _request_graceful_shutdown(reason: str = "") -> bool:
    """Try to trigger the existing graceful stop path once."""
    if _shutdown_request_event.is_set():
        return False

    _shutdown_request_event.set()
    ctx.system_stop_event.set()

    log_event(
        logger,
        source="MODE",
        event="stop_request",
        result="ok",
        reason=reason or "external",
        key={"via": "M0004"},
        brief=False,
        level=logging.INFO,
    )
    return True

# 从串口响应中提取模式代码
def _extract_mode_code_from_resp(resp) -> str:
    if resp is None:
        return ""

    candidates = []

    try:
        for x in (getattr(resp, "lines", []) or []):
            s = str(x).strip().upper()
            if s:
                candidates.append(s)
    except Exception:
        pass

    raw = str(getattr(resp, "raw", "") or "").replace("\r", "")
    if raw:
        for x in raw.split("\n"):
            s = x.strip().upper()
            if s:
                candidates.append(s)

    for line in candidates:
        if _MODE_RE.fullmatch(line):
            return line

    return ""

# 处理串口接收到的模式
def _uart_mode_callback(resp):
    mode_code = _extract_mode_code_from_resp(resp)
    if not mode_code:
        return

    old_mode = _get_selected_mode()
    if old_mode == mode_code:
        return

    _set_selected_mode(mode_code)

    log_event(
        logger,
        source="MODE",
        event="select",
        result="ok",
        key={"mode": mode_code},
        brief=False,
    )

    if mode_code == "M0004":
        _request_graceful_shutdown(reason="mode_m0004_rx")

# 等待模式选择并返回选择的模式
def _wait_mode_selection(timeout_s=20.0) -> str:
    start_ts = time.time()

    try:
        ctx.set_mission(mode="WAIT_MODE_SELECT")
    except Exception:
        pass

    showcase_cfg = (ctx.config or {}).get("showcase", {})
    if bool(showcase_cfg.get("pi_test_only", False)):
        fsm_cfg = (ctx.config or {}).get("fsm", {})
        default_mode = str(fsm_cfg.get("default_mode", "M0001")).strip().upper()

        if not _MODE_RE.fullmatch(default_mode):
            default_mode = "M0001"

        _set_selected_mode(default_mode)

        log_event(
            logger,
            source="MODE",
            event="wait",
            result="skip",
            reason="pi_test_only",
            key={"mode": default_mode},
            brief=False,
        )

        return default_mode

    while not ctx.system_stop_event.is_set():
        mode_code = _get_selected_mode()
        if _MODE_RE.fullmatch(mode_code):
            return mode_code

        if _shutdown_request_event.is_set():
            return mode_code or "M0004"

        if timeout_s is not None and timeout_s > 0:
            if (time.time() - start_ts) >= timeout_s:
                return ""

        _mode_select_event.wait(timeout=0.1)

    if _shutdown_request_event.is_set():
        return _get_selected_mode() or "M0004"

    return ""

#endregion

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

# 判断是否为pi，
def _detect_platform_is_pi() -> tuple[bool, str, str, str]:

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
    
    # 开始选择模式
    _reset_mode_selection()
    # 先设为None
    gps_thread = None
    patrol_thread = None
    dector_thread = None
    fsm_thread = None
    upload_thread = None
    showcase_thread = None

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
        
        # 设置模式选择回调
        uart.set_response_callback(_uart_mode_callback)
        
        # 放到全局上下文，方便 FSM 等模块使用
        ctx.uart = uart

        log_event(logger, 
                  source="UART", 
                  event="init", 
                  action="connect", 
                  key={"port": uart.port, "baudrate": uart.baudrate, "timeout": uart.timeout}, 
                  brief=False)

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
        
    # 开始模式选择
    selected_mode = ""

    if uart_enable and (uart is not None) and (ctx.uart is not None):
        log_event(
            logger,
            source="MODE",
            event="wait",
            result="begin",
            brief=False,
        )

        selected_mode = _wait_mode_selection(timeout_s=20.0)

        if not selected_mode:
            log_event(
                logger,
                source="MODE",
                event="wait",
                result="fail",
                reason="timeout_no_mode",
                level=logging.ERROR,
                brief=False,
            )
            return
        
    # 给 PC 调试留的，默认按 fsm.default_mode    
    else:
        fsm_cfg = (ctx.config or {}).get("fsm", {})
        selected_mode = str(fsm_cfg.get("default_mode", "M0001")).strip().upper()

        # 非法值兜底
        if not _MODE_RE.fullmatch(selected_mode):
            selected_mode = "M0001"

        _set_selected_mode(selected_mode)

        log_event(
            logger,
            source="MODE",
            event="fallback",
            result="ok",
            key={"mode": selected_mode, "source": "fsm.default_mode"},
            brief=False,
        )

    # 模式分流
    
    #region M0001
    if selected_mode == "M0001":
        ctx.set_mission(mode="PATROL", selected_mode=selected_mode)

        # 创建并启动 gps_service
        gps_cfg = ctx.config.get("gps", {})
        gps_enable = bool(gps_cfg.get("enable", True))
        gps_thread = None

        if gps_enable:
            try:
                gps_thread = GPSService(gps_cfg)
                gps_thread.start()
                log_event(
                    logger,
                    source="GPS",
                    event="thread_spawn",
                    action="start",
                    result="ok",
                    key={"thread": "GPSService"},
                    level=logging.DEBUG,
                    brief=False,
                )
            except Exception as e:
                log_event(
                    logger,
                    source="GPS",
                    event="thread_spawn",
                    action="start",
                    result="fail",
                    reason=str(e),
                    level=logging.ERROR,
                    brief=False,
                )
                gps_thread = None
        else:
            log_event(
                logger,
                source="GPS",
                event="disabled",
                result="skip",
                level=logging.WARNING,
                brief=False,
            )

        # 创建巡逻线程
        patrol_cfg = ctx.config.get("patrol", {})
        patrol_enable = bool(patrol_cfg.get("enable", True))
        patrol_thread = None

        if patrol_enable:
            try:
                patrol_thread = PatrolService(patrol_cfg)
                patrol_thread.start()
                log_event(
                    logger,
                    source="PATROL",
                    event="thread_spawn",
                    action="start",
                    result="ok",
                    key={"thread": "PatrolService"},
                    level=logging.DEBUG,
                    brief=False,
                )
            except Exception as e:
                log_event(
                    logger,
                    source="PATROL",
                    event="thread_spawn",
                    action="start",
                    result="fail",
                    reason=str(e),
                    level=logging.ERROR,
                    brief=False,
                )
                patrol_thread = None
        else:
            log_event(
                logger,
                source="PATROL",
                event="disabled",
                result="skip",
                level=logging.WARNING,
                brief=False,
            )

        # 创建监视和大脑线程
        dector_thread = DECTOR_ser()
        fsm_thread = FSMService()

        # 上传线程
        upload_cfg = ctx.config.get("uploader", {}) if hasattr(ctx, "config") else {}
        upload_enable = bool(upload_cfg.get("enable", False))
        upload_thread = None

        if upload_enable:
            try:
                upload_thread = UploadService(ctx.config)
                upload_thread.start()
                log_event(
                    logger,
                    source="UPLOAD",
                    event="thread_spawn",
                    action="start",
                    result="ok",
                    key={"endpoint": upload_cfg.get("endpoint", "")},
                    level=logging.DEBUG,
                    brief=False,
                )
            except Exception as e:
                log_event(
                    logger,
                    source="UPLOAD",
                    event="thread_spawn",
                    action="start",
                    result="fail",
                    reason=str(e),
                    level=logging.ERROR,
                    brief=False,
                )

        log_event(logger, source="INIT", event="threads", action="start_bar", result="ok", brief=False)
        dector_thread.start()
        fsm_thread.start()
        
    #endregion

    #region M0002
    elif selected_mode == "M0002":
        ctx.set_mission(mode="SHOWCASE", selected_mode=selected_mode)

        showcase_cfg = ctx.config.get("showcase", {})

        try:
            showcase_thread = ShowcaseMode(showcase_cfg)
            showcase_thread.start()
            log_event(
                logger,
                source="SHOWCASE",
                event="thread_spawn",
                action="start",
                result="ok",
                key={"thread": "ShowcaseMode"},
                level=logging.DEBUG,
                brief=False,
            )
        except Exception as e:
            log_event(
                logger,
                source="SHOWCASE",
                event="thread_spawn",
                action="start",
                result="fail",
                reason=str(e),
                level=logging.ERROR,
                brief=False,
            )
            return

    #endregion

    #region M0004
    elif selected_mode == "M0004":
        ctx.set_mission(mode="STOP_REQUEST", selected_mode=selected_mode)
        _request_graceful_shutdown(reason="mode_m0004_dispatch")

        log_event(
            logger,
            source="MODE",
            event="dispatch",
            result="stop",
            reason="mode_m0004",
            key={"mode": selected_mode},
            level=logging.INFO,
            brief=False,
        )

    #endregion
    
    else:
        ctx.set_mission(mode=f"PRESET_{selected_mode}", selected_mode=selected_mode)

        log_event(
            logger,
            source="MODE",
            event="dispatch",
            result="skip",
            reason="unsupported_mode",
            key={"mode": selected_mode},
            level=logging.WARNING,
            brief=False,
        )

    log_event(logger, source="INIT", event="startup", result="ok", brief=None)
    log_event(logger, source="INIT", event="running", result="ok", brief=False)

    try :
        stop_logged = False
        while True:
            if ctx.system_stop_event.is_set():
                if not stop_logged:
                    log_event(
                        logger,
                        source="INIT",
                        event="health",
                        result="stop",
                        reason="system_stop_event",
                        level=logging.INFO,
                        brief=None,
                    )
                    stop_logged = True
                break

            if uart_enable and ctx.uart is not None:
                uart_ok = (ctx.uart.ser is not None) and getattr(ctx.uart.ser, "is_open", False)
                if uart_required and (not uart_ok):
                    log_event(
                        logger,
                        source="INIT",
                        event="health",
                        result="stop",
                        reason="uart_required_disconnect",
                        level=logging.WARNING,
                        brief=None,
                    )
                    break
            
            # 如果是 M0001 模式，任何一个死掉都退出
            if selected_mode == "M0001":
                if (dector_thread is not None) and (not dector_thread.is_alive()):
                    log_event(
                        logger,
                        source="INIT",
                        event="health",
                        result="stop",
                        reason="detect_thread_dead",
                        level=logging.WARNING,
                        brief=None,
                    )
                    break

                if (fsm_thread is not None) and (not fsm_thread.is_alive()):
                    log_event(
                        logger,
                        source="INIT",
                        event="health",
                        result="stop",
                        reason="fsm_thread_dead",
                        level=logging.WARNING,
                        brief=None,
                    )
                    break

                gps_cfg = ctx.config.get("gps", {})
                gps_enable = bool(gps_cfg.get("enable", True))
                if gps_enable and (gps_thread is not None) and (not gps_thread.is_alive()):
                    log_event(
                        logger,
                        source="INIT",
                        event="health",
                        result="stop",
                        reason="gps_thread_dead",
                        level=logging.WARNING,
                        brief=None,
                    )
                    break
            
            # 如果是 M0002 模式，只有展示线程死了才退出
            elif selected_mode == "M0002":
                if (showcase_thread is not None) and (not showcase_thread.is_alive()):
                    log_event(
                        logger,
                        source="INIT",
                        event="health",
                        result="stop",
                        reason="showcase_thread_exit",
                        level=logging.INFO,
                        brief=None,
                    )
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
        
        # 等待其他线程退出
        if dector_thread is not None:
            try:
                dector_thread.join(timeout=2)
            except Exception:
                pass

        if fsm_thread is not None:
            try:
                fsm_thread.join(timeout=2)
            except Exception:
                pass

        if gps_thread is not None:
            try:
                gps_thread.join(timeout=2)
            except Exception:
                pass

        if patrol_thread is not None:
            try:
                patrol_thread.join(timeout=2)
            except Exception:
                pass

        if upload_thread is not None:
            try:
                upload_thread.join(timeout=2)
            except Exception:
                pass

        if showcase_thread is not None:
            try:
                showcase_thread.join(timeout=2)
            except Exception:
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
