import time
import math
import serial
from dataclasses import dataclass

# 串口参数
PORT = "COM11"
BAUD = 115200
SER_TIMEOUT_S = 0.05

# GPS 输出参数
GPS_PERIOD_S = 0.2        # 多久发一次 GPS 给上位机
DT_S = 0.05               # 积分时间步长（越小越平滑，但 CPU 更高）

# 运动学参数
SPEED_FWD_MPS = 2      # 前后速度（m/s）
SPEED_LAT_MPS = 0.45      # 横移速度（m/s）
TURN_RATE_DPS = 90.0      # 转向角速度（deg/s）

# 初始位置
INIT_LAT = 22.540000
INIT_LON = 113.934500
INIT_HEADING_DEG = 90.0   # 0=北，90=东

EARTH_R_M = 6371393.0     # 地球半径（米）


def send_line(ser, s: str):
    """发送一行串口数据"""
    ser.write((s + "\r\n").encode("ascii", errors="ignore"))
    ser.flush()


def _wrap360(deg: float) -> float:
    """归一化角度到 0~360"""
    deg = deg % 360.0
    if deg < 0:
        deg += 360.0
    return deg


def _meters_to_dlat(dy_m: float) -> float:
    """北向位移（m）-> 纬度变化（deg）"""
    return (dy_m / EARTH_R_M) * (180.0 / math.pi)


def _meters_to_dlon(dx_m: float, lat_deg: float) -> float:
    """东向位移（m）-> 经度变化（deg）"""
    lat_rad = math.radians(lat_deg)
    denom = EARTH_R_M * max(1e-9, math.cos(lat_rad))
    return (dx_m / denom) * (180.0 / math.pi)


@dataclass
class Motion:
    # 机体系速度：+x 前进，+y 向右（m/s）
    vx: float = 0.0
    vy: float = 0.0
    # 角速度：正为右转（deg/s）
    omega: float = 0.0
    # 动作结束时间戳（到点自动停）
    end_ts: float = 0.0


class ClosedLoopSim:
    """
    闭环仿真器：命令驱动的运动学积分
    heading 定义：0=北，90=东（与 patrol bearing 一致）
    """
    def __init__(self, lat: float, lon: float, heading_deg: float):
        self.lat = float(lat)
        self.lon = float(lon)
        self.heading = float(heading_deg)
        self.motion = Motion()

    def set_motion_for(self, vx: float, vy: float, omega: float, duration_s: float):
        now = time.time()
        self.motion = Motion(
            vx=float(vx),
            vy=float(vy),
            omega=float(omega),
            end_ts=now + max(0.0, float(duration_s)),
        )

    def stop(self):
        self.motion = Motion(vx=0.0, vy=0.0, omega=0.0, end_ts=0.0)

    def step(self, dt: float):
        now = time.time()
        m = self.motion
        if now >= m.end_ts:
            self.stop()
            m = self.motion

        # 更新航向
        self.heading = _wrap360(self.heading + m.omega * dt)

        # body -> ENU (East, North)
        # forward 分量：East=sin, North=cos
        hdg = math.radians(self.heading)
        east_f = math.sin(hdg)
        north_f = math.cos(hdg)

        # right 分量：forward +90deg
        east_r = math.sin(hdg + math.pi / 2)
        north_r = math.cos(hdg + math.pi / 2)

        # 世界坐标速度（东/北）
        ve = m.vx * east_f + m.vy * east_r
        vn = m.vx * north_f + m.vy * north_r

        # 积分更新经纬度
        self.lat += _meters_to_dlat(vn * dt)
        self.lon += _meters_to_dlon(ve * dt, self.lat)


def _parse_cmd(cmd: str):
    """
    解析上位机命令
    返回 (kind, value)
    """
    c = (cmd or "").strip()

    if c in ("S", "STOP") or (c.startswith("S") and c[1:].isdigit()):
        return ("stop", None)

    if c == "STATUS":
        return ("status", None)

    if c == "CONFIG":
        return ("config", None)

    if c.startswith("F") and len(c) == 5 and c[1:].isdigit():
        return ("forward_sec", int(c[1:]))

    if c.startswith("B") and len(c) == 5 and c[1:].isdigit():
        return ("back_sec", int(c[1:]))

    if c.startswith("HL") and len(c) == 5 and c[2:].isdigit():
        return ("left_sec", int(c[2:]))

    if c.startswith("HR") and len(c) == 5 and c[2:].isdigit():
        return ("right_sec", int(c[2:]))

    if c.startswith("L0") and len(c) == 5 and c[2:].isdigit():
        return ("turn_left_deg", int(c[2:]))

    if c.startswith("R0") and len(c) == 5 and c[2:].isdigit():
        return ("turn_right_deg", int(c[2:]))

    # 舵机等命令：ACK 但不影响仿真位置
    if c.startswith(("D", "A")):
        return ("other", None)

    return ("unknown", None)


def main():
    ser = serial.Serial(PORT, BAUD, timeout=SER_TIMEOUT_S)
    print(f"[闭环模拟] 串口打开 {PORT} @ {BAUD}")

    # 上电
    send_line(ser, "BOOT,OK")

    sim = ClosedLoopSim(INIT_LAT, INIT_LON, INIT_HEADING_DEG)

    last_gps = 0.0
    last_step = time.time()

    while True:
        # 读取命令
        raw = ser.readline()
        if raw:
            cmd = raw.decode("ascii", errors="ignore").strip().replace("\r", "")
            if cmd:
                kind, val = _parse_cmd(cmd)
                
                print(f"[RX] {cmd}")

                if kind == "status":
                    send_line(ser, "STATE,1,0")
                    send_line(ser, f"POSE,HDG,{sim.heading:.1f}")
                    send_line(ser, "OK")

                elif kind == "config":
                    send_line(ser, f"CFG,SPEED_FWD,{SPEED_FWD_MPS:.2f}")
                    send_line(ser, f"CFG,SPEED_LAT,{SPEED_LAT_MPS:.2f}")
                    send_line(ser, f"CFG,TURN_RATE,{TURN_RATE_DPS:.1f}")
                    send_line(ser, "OK")

                elif kind == "stop":
                    sim.stop()
                    send_line(ser, "OK")

                elif kind == "forward_sec":
                    sim.set_motion_for(vx=+SPEED_FWD_MPS, vy=0.0, omega=0.0, duration_s=float(val))
                    send_line(ser, "OK")

                elif kind == "back_sec":
                    sim.set_motion_for(vx=-SPEED_FWD_MPS, vy=0.0, omega=0.0, duration_s=float(val))
                    send_line(ser, "OK")

                elif kind == "left_sec":
                    sim.set_motion_for(vx=0.0, vy=-SPEED_LAT_MPS, omega=0.0, duration_s=float(val))
                    send_line(ser, "OK")

                elif kind == "right_sec":
                    sim.set_motion_for(vx=0.0, vy=+SPEED_LAT_MPS, omega=0.0, duration_s=float(val))
                    send_line(ser, "OK")

                elif kind == "turn_left_deg":
                    deg = max(0.0, min(359.0, float(val)))
                    dur = deg / max(1e-6, TURN_RATE_DPS)
                    sim.set_motion_for(vx=0.0, vy=0.0, omega=-TURN_RATE_DPS, duration_s=dur)
                    send_line(ser, "OK")

                elif kind == "turn_right_deg":
                    deg = max(0.0, min(359.0, float(val)))
                    dur = deg / max(1e-6, TURN_RATE_DPS)
                    sim.set_motion_for(vx=0.0, vy=0.0, omega=+TURN_RATE_DPS, duration_s=dur)
                    send_line(ser, "OK")

                elif kind == "other":
                    # 舵机类命令不影响位置
                    send_line(ser, "OK")

                else:
                    send_line(ser, "ERR01")

        # 积分更新位置
        now = time.time()
        dt = now - last_step
        if dt >= DT_S:
            steps = int(dt / DT_S)
            steps = max(1, min(50, steps))
            for _ in range(steps):
                sim.step(DT_S)
            last_step = now
            
        # print(
        #     f"[STATE] "
        #     f"lat={sim.lat:.6f}, "
        #     f"lon={sim.lon:.6f}, "
        #     f"hdg={sim.heading:6.1f}, "
        #     f"vx={sim.motion.vx:+.2f}, "
        #     f"vy={sim.motion.vy:+.2f}, "
        #     f"omega={sim.motion.omega:+.1f}"
        # )

        # 周期性发送 GPS 给上位机
        if now - last_gps >= GPS_PERIOD_S:
            last_gps = now
            send_line(ser, f"GPS,{sim.lat:.6f},{sim.lon:.6f}")
            
            print(
                f"[GPS] lat={sim.lat:.6f}, "
                f"lon={sim.lon:.6f}, "
                f"hdg={sim.heading:6.1f}, "
                f"vx={sim.motion.vx:+.2f}, "
                f"vy={sim.motion.vy:+.2f}, "
                f"omega={sim.motion.omega:+.1f}"
            )


if __name__ == "__main__":
    main()
