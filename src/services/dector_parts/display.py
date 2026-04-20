# 该文件系 ai 协助下的，为了通过 ssh 和 xming 在电脑上看到摄像头的画面

import ctypes
import logging
import os
import socket
from typing import Any, Callable, Optional

# 选择可用的系统字体目录供 Qt 使用。
def pick_system_font_dir() -> str:
    for font_dir in (
        "/usr/share/fonts/truetype/dejavu",
        "/usr/share/fonts/dejavu",
        "/usr/share/fonts/truetype/freefont",
    ):
        if os.path.isdir(font_dir):
            return font_dir
    return ""

# 设置 Qt/X11 兼容环境变量并补充字体目录。
def ensure_qt_compat_env() -> None:
    os.environ.setdefault("QT_XCB_NO_XI2", "1")
    os.environ.setdefault("QT_XCB_NO_XRANDR", "1")

    cur_font_dir = str(os.environ.get("QT_QPA_FONTDIR", "")).strip()
    if cur_font_dir and os.path.isdir(cur_font_dir):
        return

    fallback_font_dir = pick_system_font_dir()
    if fallback_font_dir:
        os.environ["QT_QPA_FONTDIR"] = fallback_font_dir

# 探测 DISPLAY 是否可达，优先走 X11 握手校验。
def probe_display_reachable(
    display_value: str,
    *,
    logger_obj: Any = None,
    log_event_func: Optional[Callable[..., None]] = None,
):
    display_value = str(display_value or "").strip()
    if not display_value:
        return False, "display_not_set", {"display_value": ""}

    # Prefer a real X11 handshake first; socket checks alone can produce false positives.
    try:
        libx11 = ctypes.cdll.LoadLibrary("libX11.so.6")
        libx11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        libx11.XOpenDisplay.restype = ctypes.c_void_p
        libx11.XCloseDisplay.argtypes = [ctypes.c_void_p]

        dpy_ptr = libx11.XOpenDisplay(display_value.encode("utf-8"))
        if dpy_ptr:
            try:
                libx11.XCloseDisplay(ctypes.c_void_p(dpy_ptr))
            except Exception:
                pass
            return True, "ok", {"display_value": display_value, "display_check": "x11_open"}

        return False, "display_x11_open_fail", {"display_value": display_value, "display_check": "x11_open"}
    except Exception as e:
        if logger_obj is not None and log_event_func is not None:
            try:
                log_event_func(
                    logger_obj,
                    source="DETECT",
                    event="display",
                    action="probe",
                    result="degraded",
                    reason="display_x11_probe_unavailable",
                    key={"display_value": display_value, "err": str(e)[:160]},
                    level=logging.WARNING,
                    brief=False,
                )
            except Exception:
                pass

    if display_value.startswith(":"):
        disp_raw = display_value[1:].split(".", 1)[0]
        if not disp_raw.isdigit():
            return False, "display_parse_fail", {"display_value": display_value}

        disp_no = int(disp_raw)
        sock_path = f"/tmp/.X11-unix/X{disp_no}"
        if os.path.exists(sock_path):
            return True, "ok", {"display_value": display_value, "display_socket": sock_path}
        return False, "display_unix_socket_missing", {"display_value": display_value, "display_socket": sock_path}

    host = ""
    disp_part = ""

    if display_value.startswith("["):
        rb = display_value.find("]")
        if rb <= 0 or (rb + 1 >= len(display_value)) or (display_value[rb + 1] != ":"):
            return False, "display_parse_fail", {"display_value": display_value}
        host = display_value[1:rb]
        disp_part = display_value[rb + 2:]
    else:
        if ":" not in display_value:
            return False, "display_parse_fail", {"display_value": display_value}
        host, disp_part = display_value.rsplit(":", 1)

    if "/" in host:
        host = host.split("/", 1)[0]

    disp_raw = str(disp_part).split(".", 1)[0]
    if not disp_raw.isdigit():
        return False, "display_parse_fail", {"display_value": display_value, "display_host": host}

    disp_no = int(disp_raw)
    port = 6000 + disp_no
    host = host or "127.0.0.1"

    try:
        with socket.create_connection((host, port), timeout=0.3):
            pass
        return True, "ok", {"display_value": display_value, "display_host": host, "display_port": port}
    except Exception as e:
        return False, "display_tcp_unreachable", {
            "display_value": display_value,
            "display_host": host,
            "display_port": port,
            "err": str(e)[:120],
        }

# 按去重策略关闭显示开关并记录降级日志。
def disable_display_flag(
    *,
    flag_name: str,
    flag_enabled: bool,
    display_warned: set[str],
    logger_obj: Any,
    log_event_func: Callable[..., None],
    reason: str,
    err: Exception | None = None,
) -> bool:
    if not flag_enabled:
        return False

    err_msg = str(err) if err is not None else ""
    dedup_key = f"{flag_name}:{reason}:{err_msg}"
    if dedup_key in display_warned:
        return False

    display_warned.add(dedup_key)
    log_event_func(
        logger_obj,
        source="DETECT",
        event="display",
        action="disable",
        result="degraded",
        reason=reason,
        key={"display_flag": flag_name, "err": err_msg[:200]},
        level=logging.WARNING,
        brief=False,
    )
    return False

# 安全创建 OpenCV 窗口，失败时自动降级显示能力。
def safe_named_window(
    *,
    cv2_module: Any,
    window_name: str,
    window_mode: str,
    window_flag: int,
    flag_name: str,
    flag_enabled: bool,
    display_warned: set[str],
    logger_obj: Any,
    log_event_func: Callable[..., None],
) -> bool:
    if not flag_enabled:
        return False

    try:
        cv2_module.namedWindow(window_name, window_flag)
        if window_mode == "autosize":
            try:
                cv2_module.setWindowProperty(window_name, cv2_module.WND_PROP_FULLSCREEN, cv2_module.WINDOW_NORMAL)
            except Exception:
                pass
        return True
    except Exception as e:
        return disable_display_flag(
            flag_name=flag_name,
            flag_enabled=flag_enabled,
            display_warned=display_warned,
            logger_obj=logger_obj,
            log_event_func=log_event_func,
            reason="named_window_failed",
            err=e,
        )
