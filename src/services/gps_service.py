import threading
import time

import src.global_ctx as ctx
from src.utils.logger import sys_logger as logger

# 只负责吧gps数据传入系统
class GPSService(threading.Thread) : 
    def __init__(self , gps_cfg=None) :
        super().__init__()
        self.daemon = True
        
        # 读取配置
        cfg = {}
        try : 
            cfg = gps_cfg if isinstance(gps_cfg, dict) else (ctx.config or {}).get("gps", {})
        except Exception :
            cfg = {}
            
        self.enable = bool(cfg.get("enable" , True))
        self.source = str(cfg.get("source", "uart"))

        # 超过该秒数没更新视为无效
        self.stale_timeout_s = float(cfg.get("stale_timeout_s", 2.0))
        if self.stale_timeout_s < 0:
            self.stale_timeout_s = 0.0

        # 避免刷屏
        self.log_every_s = float(cfg.get("log_every_s", 2.0))
        if self.log_every_s <= 0:
            self.log_every_s = 2.0

        # 内部状态
        self._last_log_ts = 0.0
        self._callback_bound = False

        logger.info(
            "[ GPS ] init: enable=%s source=%s stale_timeout_s=%.2f log_every_s=%.2f",
            self.enable, self.source, self.stale_timeout_s, self.log_every_s
        )
        
    # UART 回调
    def _on_gps(self , lat : float , lon : float ) : 
        try : 
            if hasattr(ctx, "set_gps"):
                ctx.set_gps( lat = lat , lon = lon , ok = True , source = self.source )
            else:
                # 兜底：没有 set_gps 也保证有状态可读
                ctx.gps_state = {
                    "ok": True,
                    "lat": float(lat),
                    "lon": float(lon),
                    "ts": time.time(),
                    "source": self.source,
                }
                
            now = time.time()
            if ( now - self._last_log_ts ) >= self.log_every_s :
                self._last_log_ts = now 
                logger.info("[ GPS ] update: lat=%.7f lon=%.7f", float(lat), float(lon))
                
        except Exception as e :
            logger.error("[ GPS ] callback error: %s", e)
    
    # 把GPS回调绑定到UART        
    def _try_bind_callback(self) -> bool :
        if not self.enable :
            return False 
        
        uart = getattr(ctx , "uart" , None)
        if uart is None :
            return False 
        
        if not hasattr(uart , "set_gps_callback") :
            logger.warning("[ GPS ] ctx.uart has no set_gps_callback(), cannot bind")
            return False
        
        try:
            uart.set_gps_callback(self._on_gps)
            self._callback_bound = True
            logger.info("[ GPS ] gps_callback bound to UART successfully")
            return True
        except Exception as e:
            logger.error("[ GPS ] bind callback failed: %s", e)
            return False 
    
    # 检查是否过期    
    def _check_stale_and_mark_invalid(self) : 
        if not self.enable :
            return 
        
        # stale_timeout_s=0 表示禁用检查
        if self.stale_timeout_s <= 0:
            return

        try:
            if hasattr(ctx, "get_gps_copy"):
                gs = ctx.get_gps_copy()
            else:
                gs = getattr(ctx, "gps_state", {}) or {}

            ok = bool(gs.get("ok", False))
            ts = float(gs.get("ts", 0.0))

            if not ok:
                return

            now = time.time()
            age = now - ts
            if age > self.stale_timeout_s:
                # 仅标记 GPS 无效，不做巡逻/停车等业务动作
                if hasattr(ctx, "set_gps_invalid"):
                    ctx.set_gps_invalid(source=self.source)
                else:
                    gs["ok"] = False
                    ctx.gps_state = gs

                logger.warning("[ GPS ] stale: last_update=%.2fs ago (> %.2fs), mark invalid", age, self.stale_timeout_s)

        except Exception as e:
            logger.error("[ GPS ] stale check error: %s", e)
           
    # 运行
    def run(self) :
        if not self.enable : 
            logger.warning("[ GPS ] disabled by config, thread will exit")
            return 
        
        logger.info("[ GPS ] thread starting")
        
        while( not ctx.system_stop_event.is_set()) and ( not self._callback_bound) :
            ok = self._try_bind_callback()
            
            if ok :
                break 
            
            time.sleep(0.3)
            
        if not self._callback_bound : 
            logger.warning("[ GPS ] callback not bound , still running")
            
        while not ctx.system_stop_event.is_set() :
            self._check_stale_and_mark_invalid()
            time.sleep(0.2)
            
        logger.info("[ GPS ] thread finished")
