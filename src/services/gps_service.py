import threading
import time
import logging

import src.global_ctx as ctx
from src.utils.logger import sys_logger as logger, log_event

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

        log_event(
            logger,
            source="GPS",
            event="init",
            key={
                "enable": self.enable,
                "source": self.source,
                "stale_timeout_s": round(self.stale_timeout_s, 2),
                "log_every_s": round(self.log_every_s, 2),
            },
            brief=False,
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
                log_event(logger, source="GPS", event="update", key={"lat": round(float(lat),7), "lon": round(float(lon),7)}, level=logging.DEBUG)
                
        except Exception as e :
            log_event(logger, source="GPS", event="update", result="fail", reason=str(e), level=logging.ERROR, brief=False)
    
    # 把GPS回调绑定到UART        
    def _try_bind_callback(self) -> bool :
        if not self.enable :
            return False 
        
        uart = getattr(ctx , "uart" , None)
        if uart is None :
            return False 
        
        if not hasattr(uart , "set_gps_callback") :
            log_event(logger, source="GPS", event="bind_callback", result="skip", reason="no_method", level=logging.WARNING, brief=False)
            return False
        
        try:
            uart.set_gps_callback(self._on_gps)
            self._callback_bound = True
            log_event(logger, source="GPS", event="bind_callback", result="ok", brief=False)
            return True
        except Exception as e:
            log_event(logger, source="GPS", event="bind_callback", result="fail", reason=str(e), level=logging.ERROR, brief=False)
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

                log_event(logger, source="GPS", event="stale", result="mark_invalid", key={"age": round(age,2), "timeout": round(self.stale_timeout_s,2)}, level=logging.WARNING, brief=False)

        except Exception as e:
            log_event(logger, source="GPS", event="stale", result="fail", reason=str(e), level=logging.ERROR, brief=False)
           
    # 运行
    def run(self) :
        if not self.enable : 
            log_event(logger, source="GPS", event="start", result="skip", reason="disabled", level=logging.WARNING, brief=False)
            return 
        
        log_event(logger, source="GPS", event="start", result="ok", brief=False)
        
        while( not ctx.system_stop_event.is_set()) and ( not self._callback_bound) :
            ok = self._try_bind_callback()
            
            if ok :
                break 
            
            time.sleep(0.3)
            
        if not self._callback_bound : 
            log_event(logger, source="GPS", event="bind_callback", result="pending", reason="not_bound", level=logging.WARNING, brief=False)
            
        while not ctx.system_stop_event.is_set() :
            self._check_stale_and_mark_invalid()
            time.sleep(0.2)
            
        log_event(logger, source="GPS", event="stop_request", result="ok", brief=False)
