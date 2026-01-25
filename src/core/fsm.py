import time
import threading
from typing import Any, Dict, Optional, Tuple

import src.global_ctx as ctx
from src.utils.logger import sys_logger as logger
from src.services.uploader import build_zip_for_data

# 返回时间戳
def _now_ts() -> float :
    return time.time()

class FSMService(threading.Thread) : 
    def __init__(self) :
        super().__init__()
        self.daemon = True 
        
        # 读取配置
        cfg = {}
        try:
            cfg = (ctx.config or {}).get("fsm", {})
        except Exception:
            cfg = {}
        
        self.enable: bool = bool(cfg.get("enable", True))

        # 目标消失后保持控制的时间
        self.hold_after_lost_s: float = float(cfg.get("hold_after_lost_s", 2.0))

        # 命令过期判定的时间
        self.patrol_stale_s: float = float(cfg.get("patrol_stale_s", 6.0))

        # 该时间内不重复发同一命令
        self.cmd_dedup_s: float = float(cfg.get("cmd_dedup_s", 1.0))

        self.stop_cmd: str = "S"
        self.violation_cmd: str = str(cfg.get("violation_cmd", "ERROR"))

        # 避免日志刷屏
        self.log_every_s: float = float(cfg.get("log_every_s", 0.5))
        if self.log_every_s <= 0:
            self.log_every_s = 0.5

        self._last_violation_ts: float = 0.0     # 最近一次“检测到目标”的时间戳
        self._last_sent_cmd: str = ""            # 最近一次实际下发的 UART 指令
        self._last_sent_ts: float = 0.0          # 最近一次实际下发的时间戳
        self._last_log_ts: float = 0.0           # 最近一次打印状态日志的时间戳

        # 缓存最新的patrol建议
        self._patrol_cached_cmd: str = ""
        self._patrol_cached_ts: float = 0.0

        logger.info(
            "[ FSM ] init: enable=%s hold_after_lost_s=%.2f patrol_stale_s=%.2f cmd_dedup_s=%.2f stop_cmd=%s violation_cmd=%s",
            self.enable, self.hold_after_lost_s, self.patrol_stale_s, self.cmd_dedup_s, self.stop_cmd, self.violation_cmd
        )    
    
    # 判定是否触发抢夺    
    def _event_has_target(self, ev: dict) -> bool:
        if not ev or not isinstance(ev, dict):
            return False

        ev_type = ev.get("type", "")
        if ev_type == "violation":
            return True

        # 默认：不因为 detection 或 fake 占用控制权
        return False  
    
    # 向uart发消息
    def _emit_uart(self , cmd : str , reason : str ) :
        cmd = str(cmd)
        now = _now_ts()
        
        # 防止重复打印
        if cmd == self._last_sent_cmd and ( now - self._last_sent_ts ) < self.cmd_dedup_s : 
            return 
        
        try:
            # 覆盖写入,只保留最新命令
            if hasattr(ctx, "put_latest"):
                ctx.put_latest(ctx.uart_queue, cmd)
            else:

                try:
                    if ctx.uart_queue.full():
                        ctx.uart_queue.get_nowait()
                except Exception:
                    pass
                ctx.uart_queue.put_nowait(cmd)

            self._last_sent_cmd = cmd
            self._last_sent_ts = now

            try:
                if hasattr(ctx, "set_mission"):
                    ctx.set_mission(last_uart_cmd=cmd, last_uart_cmd_ts=now)
            except Exception:
                pass

        except Exception as e:
            logger.error("[ FSM ] emit to uart failed: %s", e)
            return
        
        # 周期打印日志
        if (now - self._last_log_ts) >= self.log_every_s:
            self._last_log_ts = now
            logger.info("[ FSM ] out=%s reason=%s v_age=%.2fs patrol_age=%.2fs",
                        cmd, reason,
                        (now - self._last_violation_ts) if self._last_violation_ts > 0 else 9999.0,
                        (now - self._patrol_cached_ts) if self._patrol_cached_ts > 0 else 9999.0)

    # 读取导航队列
    def _poll_patrol_cmd(self) :
        try:
            item = ctx.patrol_cmd_queue.get_nowait()
        except Exception:
            item = None

        if not item:
            return

        try:
            self._patrol_cached_cmd = str(item.get("cmd", "")) if isinstance(item, dict) else str(item)
            self._patrol_cached_ts = float(item.get("ts", _now_ts())) if isinstance(item, dict) else _now_ts()
            
            if ( _now_ts() - self._last_log_ts ) >= self.log_every_s:
                logger.info("[ FSM ] got patrol cmd=%s age=%.2fs", self._patrol_cached_cmd, _now_ts() - self._patrol_cached_ts)

        except Exception:
            # 解析失败就忽略，不覆盖
            return
        
    # 读取视觉队列
    def _poll_dector_event(self) -> Optional[Dict[str, Any]]:
        try:
            return ctx.dector_queue.get_nowait()
        except Exception:
            return None
    
    # 裁定输出哪个命令
    def _decide_output(self) -> Tuple[str, str]:
        now = _now_ts()

        # 违规是否仍处于抢占中
        violation_active = (now - self._last_violation_ts) <= self.hold_after_lost_s

        # patrol是否过期
        patrol_fresh = bool(self._patrol_cached_cmd) and ((now - self._patrol_cached_ts) <= self.patrol_stale_s)

        # 优先级：违规 > 巡逻 > stop
        if violation_active:
            return self.violation_cmd, "violation_override"
        if patrol_fresh:
            return self._patrol_cached_cmd, "patrol"
        return self.stop_cmd, "idle_stop"

    # 运行
    def run(self) : 
        if not self.enable :
            logger.warning("[ FSM ] disabled by config, thread will exit")
            return 
        
        logger.info("[ FSM ] start")

        while not ctx.system_stop_event.is_set():
            # 读dector
            ev = self._poll_dector_event()
            if self._event_has_target(ev):
                self._last_violation_ts = _now_ts()

            # 读patrol建议
            self._poll_patrol_cmd()

            # 仲裁输出并下发
            out_cmd, reason = self._decide_output()
            self._emit_uart(out_cmd, reason)
            
            # 每个循环都检查一次是否有打包请求
            if hasattr(ctx, "pack_event") and ctx.pack_event.is_set():
                ctx.pack_event.clear()  # 先clear，避免重复触发
                try:
                    ctx.set_mission(zip_triggered=True)
                except Exception:
                    pass
                self._start_pack_thread_once(meta={"reason": "return_to_base"})

            time.sleep(0.02)      
            
        self._emit_uart(self.stop_cmd , "shutdown_stop") 
        logger.info("[ FSM ] finsihed")     

    # 到基地，起一个临时线程打包数据
    def _start_pack_thread_once(self, meta=None):
        with ctx.pack_lock:
            if ctx.pack_in_progress:
                logger.info("[ PACK ] skip: already packing")
                return
            ctx.pack_in_progress = True

        def _job():
            try:
                zip_path = build_zip_for_data(meta=meta or {})
                logger.info(f"[ PACK ] done -> {zip_path}")
            except Exception as e:
                logger.exception(f"[ PACK ] failed: {e}")
            finally:
                with ctx.pack_lock:
                    ctx.pack_in_progress = False

        threading.Thread(target=_job, daemon=True).start()
        logger.info("[ PACK ] started background pack thread")
