import datetime
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, Iterable, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR_DE = os.path.abspath(os.path.join(BASE_DIR, "../../data/logs_de"))
LOG_DIR_BE = os.path.abspath(os.path.join(BASE_DIR, "../../data/logs_be"))

LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 5

FORMAT = "%(asctime)s.%(msecs)03d - %(levelname)s - [%(source)s] - %(message)s"
DATEFMT = "%Y-%m-%d %H:%M:%S"

SOURCE_WHITELIST = {"INIT", "FSM", "PATROL", "GPS", "UART", "DETECT", "UPLOAD", "PROCESS", "ZIP"}
SOURCE_ALIAS = {"DECTOR": "DETECT"}

# 白名单中事件默认 brief=True，其他事件默认 brief=False，均可通过参数覆盖。
BRIEF_WHITELIST = {
    "INIT": {"platform_detect", "startup", "ready", "stop"},
    "DETECT": {"violation_confirm", "snapshot_saved", "process_exit", "violation"},
    "ZIP": {"zip_create"},
    "UPLOAD": {"upload_done", "upload_fail"},
}

# 确保指定的目录存在
def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


class EnsureFieldsFilter(logging.Filter):
    """为所有记录补全 source / brief，防止 formatter 缺字段。"""

    # 过滤日志记录，确保 source 和 brief 字段存在
    def filter(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        if not hasattr(record, "source"):
            record.source = "UNKNOWN"
        if not hasattr(record, "brief"):
            record.brief = False
        return True


class BriefFilter(logging.Filter):
    """仅允许 >=WARNING 或 INFO 且 brief=True。"""

    def filter(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        if record.levelno >= logging.WARNING:
            return True
        brief = getattr(record, "brief", False)
        return bool(brief) and (record.levelno == logging.INFO)

# 规范日志的来源
def _normalize_source(src: Optional[str]) -> str:
    src = (src or "INIT").upper()
    src = SOURCE_ALIAS.get(src, src)
    if src not in SOURCE_WHITELIST:
        return "INIT"
    return src

# 格式化日志输出
def _formatter() -> logging.Formatter:
    return logging.Formatter(FORMAT, DATEFMT)

# 构建文件处理器
def _build_file_handler(path: str, level: int, brief_only: bool) -> logging.Handler:
    handler = RotatingFileHandler(path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8")
    handler.setLevel(level)
    handler.setFormatter(_formatter())
    if brief_only:
        handler.addFilter(BriefFilter())
    return handler

# 输出到控制台
def _build_stream_handler(level: int, brief_only: bool) -> logging.Handler:
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(_formatter())
    if brief_only:
        handler.addFilter(BriefFilter())
    return handler

# 时间戳格式化
def _timestamp() -> str:
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

# 生成日志文件路径
def _log_paths() -> Dict[str, str]:
    ts = _timestamp()
    return {
        "detailed": os.path.join(LOG_DIR_DE, f"log_de_{ts}.log"),
        "brief": os.path.join(LOG_DIR_BE, f"log_be_{ts}.log"),
    }

# 清除指定日志记录器上已绑定的所有处理器
def _reset_handlers(lg: logging.Logger) -> None:
    for h in list(lg.handlers):
        try:
            lg.removeHandler(h)
            h.close()
        except Exception:
            pass

# 配置日志记录器
def configure_logging(*, is_pi: bool = False, enable_cn: Optional[bool] = None, stage: str = "runtime") -> logging.Logger:
    _ensure_dir(LOG_DIR_DE)
    _ensure_dir(LOG_DIR_BE)

    lg = logging.getLogger("RoboPatrol")
    lg.setLevel(logging.DEBUG)
    lg.propagate = False

    _reset_handlers(lg)
    lg.filters.clear()
    lg.addFilter(EnsureFieldsFilter())

    paths = _log_paths()

    lg.addHandler(_build_file_handler(paths["detailed"], logging.DEBUG, brief_only=False))
    lg.addHandler(_build_file_handler(paths["brief"], logging.INFO, brief_only=True))

    stdout_handler = _build_stream_handler(logging.INFO, brief_only=True)
    lg.addHandler(stdout_handler)

    log_event(lg, source="INIT", event="logging_configured", key={"is_pi": is_pi, "enable_cn": enable_cn, "stage": stage}, level=logging.DEBUG, brief=False)
    return lg

# 格式化日志字段值，支持多种类型
def _format_value(val: Any) -> Optional[str]:

    if val is None:
        return None

    if isinstance(val, bool):
        return "true" if val else "false"

    if isinstance(val, (int,)):
        return str(val)

    if isinstance(val, float):
        s = f"{val:.2f}".rstrip("0")
        if s.endswith("."):
            s += "0"  # 至少保留 1 位小数
        return s

    if isinstance(val, (list, tuple, set)):
        parts = []
        for v in val:
            fmt = _format_value(v)
            if fmt is not None:
                parts.append(fmt)
        return ",".join(parts) if parts else None

    if isinstance(val, dict):
        inner = _format_kvs(val, sep=",")
        return inner if inner else None

    text = str(val)
    return text if text != "" else None

# 格式化字典 k=v 形式的字符串
def _format_kvs(items: Dict[str, Any], *, prefix: str = "", sep: str = " ") -> str:
    parts: Iterable[str] = (
        f"{prefix}{k}={v}"
        for k in sorted(items.keys())
        for v in [_format_value(items[k])]
        if v is not None
    )
    return sep.join(parts)

# 构建日志消息主体，包含事件、动作、关键字段、结果、原因、耗时和相关 ID 等信息
def _build_message(source: str, fields: Dict[str, Any]) -> str:
    event = fields.get("event")
    action = fields.get("action")
    key_payload = fields.get("key")
    result = fields.get("result")
    reason = fields.get("reason")
    cost_ms = fields.get("cost_ms")
    ids_payload = fields.get("ids")

    segments = []

    if event:
        seg = str(event)
        if action:
            seg = f"{seg} {action}"
        segments.append(seg)

    key_map: Dict[str, Any] = {}
    if isinstance(key_payload, dict):
        key_map = key_payload
    elif key_payload not in (None, ""):
        key_map = {"detail": key_payload}
    key_str = _format_kvs(key_map)
    if key_str:
        segments.append(key_str)

    res_parts = []
    if result is not None:
        res_val = _format_value(result)
        if res_val is not None:
            res_parts.append(f"result={res_val}")
    if reason is not None:
        reason_val = _format_value(reason)
        if reason_val is not None:
            res_parts.append(f"reason={reason_val}")
    if res_parts:
        segments.append(" ".join(res_parts))

    tail_parts = []
    if cost_ms is not None:
        cost_val = _format_value(cost_ms)
        if cost_val is not None:
            tail_parts.append(f"cost_ms={cost_val}")

    ids_map: Dict[str, Any] = {}
    if isinstance(ids_payload, dict):
        ids_map = ids_payload
    elif ids_payload not in (None, ""):
        ids_map = {"detail": ids_payload}
    ids_str = _format_kvs(ids_map, prefix="id_")
    if ids_str:
        tail_parts.append(ids_str)

    if tail_parts:
        segments.append(" ".join(tail_parts))

    return " | ".join(segments)

# 根据来源和事件判断是否默认 brief=True，优先级：参数 > 白名单 > False。
def _default_brief(src: str, event: Optional[str], brief: Optional[bool], level: int) -> bool:
    if brief is not None:
        return bool(brief)

    ev = (event or "").lower()
    allowed = BRIEF_WHITELIST.get(src, set())
    if ev in allowed:
        return True

    return False

# 实际上的日志记录函数
# 接收参数包括日志记录器、来源、事件、动作、关键字段、结果、原因、耗时、相关 ID、是否简洁输出、日志级别和自定义消息等
def log_event(
    logger: logging.Logger,
    *,
    source: str,
    event: str,
    action: Optional[str] = None,
    key: Optional[Any] = None,
    result: Optional[Any] = None,
    reason: Optional[Any] = None,
    cost_ms: Optional[Any] = None,
    ids: Optional[Any] = None,
    brief: Optional[bool] = None,
    level: int = logging.INFO,
    message: Optional[str] = None,
) -> None:
    src = _normalize_source(source)
    fields: Dict[str, Any] = {
        "event": event,
        "action": action,
        "key": key,
        "result": result,
        "reason": reason,
        "cost_ms": cost_ms,
        "ids": ids,
    }
    msg = message if message is not None else _build_message(src, fields)
    logger.log(level, msg, extra={"source": src, "brief": _default_brief(src, event, brief, level)})

# 默认初始化
sys_logger = configure_logging(is_pi=False, enable_cn=None, stage="import")
        