import threading
import time
from typing import Any, Dict, List

import src.global_ctx as ctx
from src.utils.logger import sys_logger as logger, log_event

# 展示模式线程：按预设的动作列表，循环发送命令到 uart_queue,负责展示用
class ShowcaseMode(threading.Thread):
    def __init__(self, cfg: Dict[str, Any] | None = None):
        super().__init__(daemon=True, name="showcase_mode")
        self.cfg = cfg or {}
        
        self.start_delay_s = float(self.cfg.get("start_delay_s", 0.5))       # 启动后等待多久开始执行动作
        self.repeat = bool(self.cfg.get("repeat", False))                    # 是否循环执行动作列表
        self.loop_interval_s = float(self.cfg.get("loop_interval_s", 0.2))   # 每轮动作列表之间的间隔
        self.stop_cmd = str(self.cfg.get("stop_cmd", "STOP")).strip()        # 停止命令

        self.actions: List[Dict[str, Any]] = self.cfg.get("actions", [
            {"cmd": "F", "duration": 2.0, "note": "forward"},
            {"cmd": "L0", "duration": 0.8, "note": "left_turn"},
            {"cmd": "F", "duration": 1.5, "note": "forward"},
            {"cmd": "R0", "duration": 0.8, "note": "right_turn"},
            {"cmd": "F", "duration": 1.5, "note": "forward"},
            {"cmd": "STOP", "duration": 0.5, "note": "stop"},
        ])
        
    # 发送命令到 uart_queue
    def _send_msg(self, cmd: str):
        cmd = str(cmd or "").strip()
        if not cmd:
            return False

        ok = ctx.put_latest(ctx.uart_queue, cmd)
        if ok:
            try:
                ctx.set_mission(last_uart_cmd=cmd, last_uart_cmd_ts=time.time())
            except Exception:
                pass
        return ok
    
    # 当等待时，检查是否需要提前停止
    def _sleep_with_stop(self, seconds: float) -> bool:
        end_ts = time.time() + max(0.0, float(seconds))
        while time.time() < end_ts:
            if ctx.system_stop_event.is_set():
                return False
            time.sleep(min(0.05, end_ts - time.time()))
        return True
    
    # 执行一轮动作
    def _run_one_round(self) -> None:
        total = len(self.actions)

        for idx, item in enumerate(self.actions, start=1):
            if ctx.system_stop_event.is_set():
                return

            cmd = str(item.get("cmd", "")).strip()
            duration = float(item.get("duration", 0.5))
            note = str(item.get("note", "")).strip()

            if not cmd:
                continue

            ctx.set_mission(
                mode="SHOWCASE",
                active_wp_index=idx,
            )

            log_event(
                logger,
                source="SHOWCASE",
                event="action",
                action="send",
                result="begin",
                key={
                    "index": idx,
                    "total": total,
                    "cmd": cmd,
                    "duration": duration,
                    "note": note,
                },
                brief=False,
            )

            send_ok = self._send_msg(cmd)

            log_event(
                logger,
                source="SHOWCASE",
                event="action",
                action="send",
                result="ok" if send_ok else "fail",
                key={
                    "index": idx,
                    "total": total,
                    "cmd": cmd,
                    "duration": duration,
                    "note": note,
                },
                brief=False,
            )

            if not self._sleep_with_stop(duration):
                return
            
    # 主线程
    def run(self) -> None:
        log_event(
            logger,
            source="SHOWCASE",
            event="thread",
            action="start",
            result="ok",
            brief=False,
        )
        log_event(
            logger,
            source="SHOWCASE",
            event="config",
            action="load",
            result="ok",
            key={
                "start_delay_s": self.start_delay_s,
                "repeat": self.repeat,
                "loop_interval_s": self.loop_interval_s,
                "stop_cmd": self.stop_cmd,
                "actions_count": len(self.actions),
            }
        ) 

        ctx.set_mission(
            mode="SHOWCASE",
            patrol_start_ts=time.time(),
            patrol_laps_done=0,
            patrol_time_s=0.0,
            active_wp_index=0,
            at_base=False,
        )

        # 启动后等待一段时间
        if self.start_delay_s > 0:
            if not self._sleep_with_stop(self.start_delay_s):
                return

        round_count = 0

        # 循环执行动作列表，直到被要求停止
        try:
            while not ctx.system_stop_event.is_set():
                round_count += 1
                self._run_one_round()

                now = time.time()
                try:
                    mission = ctx.get_mission_copy()
                    start_ts = float(mission.get("patrol_start_ts", now) or now)
                    elapsed = max(0.0, now - start_ts)
                except Exception:
                    elapsed = 0.0

                ctx.set_mission(
                    mode="SHOWCASE",
                    patrol_laps_done=round_count,
                    patrol_time_s=elapsed,
                    active_wp_index=0,
                )

                if not self.repeat:
                    break

                if self.loop_interval_s > 0:
                    if not self._sleep_with_stop(self.loop_interval_s):
                        break

        # 结束后，发送停止命令，并记录日志
        finally:
            try:
                self._send_msg(self.stop_cmd)
            except Exception:
                pass

            ctx.set_mission(
                mode="SHOWCASE_DONE",
                active_wp_index=0,
            )

            log_event(
                logger,
                source="SHOWCASE",
                event="thread",
                action="stop",
                result="ok",
                key={"rounds": round_count},
                brief=False,
            )
