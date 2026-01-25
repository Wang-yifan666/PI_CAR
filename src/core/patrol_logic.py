import math
import time
import threading

import src.global_ctx as ctx
from src.utils.logger import sys_logger as logger

# 计算球面距离
def _haversine_m(lat1 : float , lon1 : float , lat2 : float , lon2 : float ) -> float :
    R = 6371393.0
    
    # 将纬度转换成弧度
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    
    dphi = math.radians(lat2 - lat1)  # 纬度差
    dlmb = math.radians(lon2 - lon1)  # 经度差
    
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    
    return 2 * R * math.asin(math.sqrt(a))

# 计算航向角
def _bearing_deg(lat1 : float , lon1 : float , lat2 : float , lon2 : float ) -> float :
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    
    dlmb = math.radians(lon2 - lon1)
    
    # 计算x，y分量
    y = math.sin(dlmb) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dlmb)
    
    # 计算角度
    brng = math.degrees(math.atan2(y, x))
    
    return (brng + 360.0) % 360.0

# 归一化角度
def _wrap180(deg : float) -> float :
    return (deg + 180.0) % 360.0 - 180.0

class PatrolService(threading.Thread) : 
    def __init__(self , patrol_cfg : dict = None ) :
        super().__init__()
        self.daemon = True
        
        cfg = {}
        try : 
            cfg = patrol_cfg if isinstance(patrol_cfg, dict) else (ctx.config or {}).get("patrol", {})
        except Exception :
            cfg = {}
            
        self.enable = bool(cfg.get("enable", False))
        self.loop = bool(cfg.get("loop", True))

        # 到点判定半径
        self.arrive_radius_m = float(cfg.get("arrive_radius_m", 3.0))

        # 直行时间
        self.forward_sec = int(cfg.get("forward_sec", 2))

        # 偏航大于这个值就先转向再走
        self.turn_threshold_deg = float(cfg.get("turn_threshold_deg", 8.0))

        # 旋转角速度估计，用于TURN后sleep一下让下位机完成动作
        self.turn_rate_dps = float(cfg.get("turn_rate_dps", 90.0))
        if self.turn_rate_dps <= 1e-6:
            self.turn_rate_dps = 90.0

        # 只有当位移超过该阈值才用GPS位移方向更新“当前航向”
        # 用于抑制GPS抖动导致的航向乱跳
        self.heading_update_min_move_m = float(cfg.get("heading_update_min_move_m", 1.0))
        if self.heading_update_min_move_m < 0:
            self.heading_update_min_move_m = 1.0

        # 拐点列表：[[lat, lon], ...]
        self.waypoints = cfg.get("waypoints", []) or []
        self.idx = 0

        self._last_at_base = False
        self._has_departed_base = False
        
        # 状态：TURN/GO
        self.state = "TURN"

        # 用于根据GPS位移估计航向
        self._last_pos = None      # (lat, lon, ts)
        self._heading_deg = None   # 当前航向估计

        # 日志节流
        self.log_every_s = float(cfg.get("log_every_s", 1.0))
        if self.log_every_s <= 0:
            self.log_every_s = 1.0
        self._last_log_ts = 0.0

        logger.info(
            "[ PATROL ] init: enable=%s loop=%s arrive_radius_m=%.2f forward_sec=%d turn_threshold_deg=%.2f",
            self.enable, self.loop, self.arrive_radius_m, self.forward_sec, self.turn_threshold_deg
        )        
    
    # 获取最新的GPS    
    def _get_gps(self) -> dict : 
        if hasattr(ctx , "get_gps_copy") : 
            try : 
                return ctx.get_gps_copy() or {}
            except Exception : 
                return {}
            
        return getattr( ctx , "gps_state" , {}) or {}
    
    # 防止与fsm争抢指令，不直接向下行机发送信息
    def _emit_gps(self, cmd: str):
        try:
            item = {
                "cmd": str(cmd),
                "ts": time.time(),
                "reason": "patrol",
                "meta": {"wp_idx": self.idx, "state": self.state},
            }
            ctx.put_latest(ctx.patrol_cmd_queue, item)
            logger.info(f"[ PATROL ] suggest cmd -> {cmd} qsize={ctx.patrol_cmd_queue.qsize()}")
        except Exception as e:
            logger.error(f"[ PATROL ] suggest cmd failed: {e} patrol_q={getattr(ctx,'patrol_cmd_queue',None)}")
            
    # 估计航向，只有超过阈值才更新
    def _update_heading_from_motion(self, lat: float, lon: float, ts: float) :
        if self._last_pos is None : 
            self._last_pos = (lat, lon, ts)
            
            return 
        
        lat0 , lon0 , ts0 = self._last_pos
        dist = _haversine_m(lat0 , lon0 , lat , lon)
        
        if dist >= self.heading_update_min_move_m : 
            self._heading_deg = _bearing_deg(lat0 , lon0 , lat , lon)
            self._last_pos = (lat, lon, ts)
            
    # 切换至下一个节点
    def _next_waypoint(self) : 
        self.idx += 1 
        if self.idx >= len(self.waypoints) :
            
            if self.loop :
                self.idx = 0 
            else :
                self.idx = len(self.waypoints)
                
    # 运行
    def run(self) :
        if not self.enable:
            logger.warning("[ PATROL ] disabled by config, thread will exit")
            return

        if (not isinstance(self.waypoints, list)) or len(self.waypoints) < 2:
            logger.error("[ PATROL ] need at least 2 waypoints")
            return

        logger.info(f"[ PATROL ] start: n_waypoints={len(self.waypoints)} loop={self.loop}")
        
        self.idx = 0 
        self.state = "TURN"
        
        while not ctx.system_stop_event.is_set() :
            # loop=false 且跑完所有点则结束
            if (not self.loop) and (self.idx >= len(self.waypoints)) :
                logger.info("[ PATROL ] finished (loop=false)")
                break        
            
            gs = self._get_gps()
            ok = bool(gs.get("ok" , False))
            lat = gs.get("lat", None)
            lon = gs.get("lon", None)
            ts = float(gs.get("ts", time.time()))
            
            if ( not ok ) or ( lat is None ) or ( lon is None ) : 
                time.sleep(0.2)
                continue 
            
            lat = float(lat)
            lon = float(lon)
            
            # 更新航向
            self._update_heading_from_motion(lat, lon, ts)               
 
            # 当前目标拐点
            tgt = self.waypoints[self.idx]
            tgt_lat, tgt_lon = float(tgt[0]), float(tgt[1])     
            
            # 到目标点距离和目标航向
            dist = _haversine_m(lat, lon, tgt_lat, tgt_lon)
            brng_tgt = _bearing_deg(lat, lon, tgt_lat, tgt_lon)     
            
            # 基地回归触发：wp[0]作为基地
            base_lat, base_lon = float(self.waypoints[0][0]), float(self.waypoints[0][1])
            dist_base = _haversine_m(lat, lon, base_lat, base_lon)
            at_base = (dist_base <= self.arrive_radius_m)

            # 一旦离开基地半径，允许后续“回到基地”触发打包
            if not at_base:
                self._has_departed_base = True

            # 边沿触发：从不在基地到进入基地
            if self._has_departed_base and at_base and (not self._last_at_base):
                try:
                    ctx.pack_event.set()
                    logger.info(f"[ PATROL ] back to base(wp[0]) dist_base={dist_base:.2f}m -> request pack")
                except Exception as e:
                    logger.error(f"[ PATROL ] request pack failed: {e}")

            self._last_at_base = at_base                       
            
            # 周期性打印状态
            now = time.time()
            if (now - self._last_log_ts) >= self.log_every_s:
                self._last_log_ts = now
                logger.info(
                    "[ PATROL ] wp[%d] dist=%.2fm brng_tgt=%.1f heading=%s state=%s",
                    self.idx, dist, brng_tgt,
                    ("None" if self._heading_deg is None else f"{self._heading_deg:.1f}"),
                    self.state
                )                 
                
            # 到点,切换下一点，并进入TURN
            if dist <= self.arrive_radius_m:
                logger.info(f"[ PATROL ] reached wp[{self.idx}] dist={dist:.2f}m")
                self._next_waypoint()
                self.state = "TURN"                        
                time.sleep(0.2)
                continue     
            
            # 刚启动或还没移动够，再走一步让航向可估计
            if self._heading_deg is None:
                self._emit_gps(f"F{self.forward_sec:04d}")
                time.sleep(max(0.2, float(self.forward_sec)))
                continue     
            
            # 计算需要转的角度差（右转为正，左转为负）
            delta = _wrap180(brng_tgt - self._heading_deg)   
                        
            # 旋转状态
            if self.state == "TURN" :
                if abs(delta) <= self.turn_threshold_deg :
                    self.state = "GO"
                    
                else : 
                    deg = int(round(abs(delta))) 
                    deg = max(0, min(359, deg))
                                           
                    if deg > 0:
                        if delta > 0:
                            self._emit_gps(f"R0{deg:03d}")
                        else:
                            self._emit_gps(f"L0{deg:03d}")

                        # 给下位机一点时间完成旋转
                        sleep_t = max(0.5, deg / self.turn_rate_dps)
                        time.sleep(2 * sleep_t)

                    self.state = "GO"  
            
            # 前进状态        
            elif self.state == "GO":
                self._emit_gps(f"F{self.forward_sec:04d}")
                time.sleep(max(0.2, float(self.forward_sec)))

                # 角度过大，下次循环会回到TURN
                if abs(delta) > self.turn_threshold_deg:
                    self.state = "TURN"  

            else:
                self.state = "TURN"

            time.sleep(0.05)

        logger.info("[ PATROL ] thread finished")              
                                             