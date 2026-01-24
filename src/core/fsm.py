import threading 
import time
import math
import src.global_ctx as ctx

from src.utils.logger import sys_logger as logger

class FSM_core( threading.Thread ):
    def __init__ ( self ):
        super().__init__()
        
        self.daemon = True
        
        # 读取 FSM 配置
        cfg = {}
        try:
            if ctx.config is not None :
                cfg = ctx.config.get("fsm" , {})
        except Exception:
            cfg = {}
        
        # FSM 主循环频率
        self.rate_hz = float(cfg.get("rate_hz" , 5))
        if self.rate_hz <= 0 :
            self.rate_hz = 5
        self.dt = 1.0 / self.rate_hz
        
        # 读取目标类别列表：
        self.target_class_names = cfg.get("target_class_names" , [])
        if self.target_class_names is None :
            self.target_class_names = []
        
        # 输出命令格式：F + 4位秒数
        self.forward_action = str(cfg.get("forward_action" , "F"))
        self.forward_sec = int(cfg.get("forward_sec" , 2))
        if self.forward_sec < 0 :
            self.forward_sec = 0
        if self.forward_sec > 9999 :
            self.forward_sec = 9999
        
        # 停止命令
        self.stop_cmd = str(cfg.get("stop_cmd" , "S0000"))
        
        # 目标丢失后的保持时间（秒）
        self.hold_after_lost_s = float(cfg.get("hold_after_lost_s" , 0.8))
        if self.hold_after_lost_s < 0 :
            self.hold_after_lost_s = 0.0
        
        # 只保留一个“日志频率控制”
        self.log_every_n = int(cfg.get("log_every_n" , 1))
        if self.log_every_n <= 0 :
            self.log_every_n = 1
        
        # FSM 内部运行状态
        self.loop_count = 0
        
        # 最近一次看到目标的时间
        self.last_seen_ts = 0.0
        
        # 当前状态（演示阶段：FORWARD / STOP）
        self.state = "STOP"
        
        # 最近一次下发的命令（避免重复 put 一样的命令）
        self.last_cmd = None
        
        logger.info( "[ FSM ] Initialization completed" )
        logger.info( "[ FSM ] Config summary" )
        logger.info( f"[ FSM ] rate_hz={self.rate_hz} dt={self.dt:.3f}" )
        logger.info( f"[ FSM ] target_class_names={self.target_class_names}" )
        logger.info( f"[ FSM ] forward_action={self.forward_action} forward_sec={self.forward_sec}" )
        logger.info( f"[ FSM ] stop_cmd={self.stop_cmd} hold_after_lost_s={self.hold_after_lost_s}" )
        logger.info( f"[ FSM ] log_every_n={self.log_every_n}" )

    # 安全地往 UART 队列投递命令（不重复）
    def _emit_cmd(self, cmd: str):
        try:
            if cmd is None:
                return
            cmd = str(cmd)

            # 去重：相同命令不重复投递
            if self.last_cmd == cmd:
                return

            # 你的项目里通常会有 ctx.uart_queue
            if not hasattr(ctx, "uart_queue") or ctx.uart_queue is None:
                # 没有队列就只能打日志提示
                logger.warning("[ FSM ] ctx.uart_queue not found, cmd dropped: %s", cmd)
                self.last_cmd = cmd
                return

            # 队列满了丢最旧，保证最新能进来
            try:
                if ctx.uart_queue.full():
                    ctx.uart_queue.get_nowait()
            except Exception:
                pass

            ctx.uart_queue.put_nowait(cmd)
            self.last_cmd = cmd

            if (self.loop_count % self.log_every_n) == 0:
                logger.info("[ FSM ] emit cmd -> %s", cmd)

        except Exception as e:
            logger.error("[ FSM ] emit cmd failed: %s", e)

    # 判断一个 detection/violation 是否命中目标
    def _is_target_event(self, ev) -> bool:
        if ev is None:
            return False

        # violation 一律当作目标命中（你也可以按规则过滤）
        if isinstance(ev, dict) and ev.get("type") == "violation":
            return True

        # detection：按 class_id 或 class_name 判断
        if isinstance(ev, dict):
            cid = ev.get("class_id", None)
            cname = ev.get("class_name", None)

            # 兼容你的配置 target_class_names 可能填的是 [0,1,2]
            if cid is not None and cid in self.target_class_names:
                return True

            # 如果你的 target_class_names 填的是名字，也支持
            if cname is not None and cname in self.target_class_names:
                return True

        return False
        
    def run( self ):
        logger.info( "[ FSM ] Thread starting" )
        while not ctx.system_stop_event.is_set() :
            tick_start = time.time()
            self.loop_count += 1

            # 读取 dector 的最新事件（非阻塞，尽量“取最后一个”）
            latest_ev = None
            try:
                if hasattr(ctx, "dector_queue") and ctx.dector_queue is not None:
                    while True:
                        latest_ev = ctx.dector_queue.get_nowait()
            except Exception:
                pass

            now = time.time()

            # 更新 last_seen_ts
            if self._is_target_event(latest_ev):
                self.last_seen_ts = now

            # 状态机：看见目标 -> FORWARD；丢失超过 hold_after_lost_s -> STOP
            if (now - self.last_seen_ts) <= self.hold_after_lost_s:
                if self.state != "FORWARD":
                    self.state = "FORWARD"
                cmd = f"{self.forward_action}{self.forward_sec:04d}"
                self._emit_cmd(cmd)
            else:
                if self.state != "STOP":
                    self.state = "STOP"
                self._emit_cmd(self.stop_cmd)

            # 按 rate_hz 控频
            cost = time.time() - tick_start
            sleep_s = self.dt - cost
            if sleep_s > 0:
                time.sleep(sleep_s)
            else:
                # 忙不过来就让出一下
                time.sleep(0.001)
            
        logger.info( "[ FSM ] Thread finished" )
