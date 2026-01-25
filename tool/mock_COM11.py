import time
import serial

PORT = "COM11"
BAUD = 115200

# 航点顺序
WAYPOINTS = [
    (31.231300, 121.474500),
    (31.231500, 121.474700),
    (31.231450, 121.474900),
    (31.231250, 121.474850),
]

GPS_PERIOD_S = 1.0      # 每隔多久发一条 GPS
SEGMENT_DURATION_S = 8 # 从一个航点走到下一个航点用多少秒
LOOP = True             # True: 最后一个点回到第一个点继续


def send_line(ser, s: str):
    ser.write((s + "\r\n").encode("ascii", errors="ignore"))
    ser.flush()


def _parse_motion_cmd(cmd: str):
    cmd = (cmd or "").strip()

    if cmd == "STOP" or cmd == "S":
        return "停止"
    if cmd.startswith("S") and len(cmd) >= 2:
        rest = cmd[1:]
        if rest.isdigit():
            return "停止"

    if cmd.startswith("F") and len(cmd) == 5 and cmd[1:].isdigit():
        sec = int(cmd[1:])
        return f"向前{sec}s"
    if cmd.startswith("B") and len(cmd) == 5 and cmd[1:].isdigit():
        sec = int(cmd[1:])
        return f"向后{sec}s"

    if cmd.startswith("HL") and len(cmd) == 5 and cmd[2:].isdigit():
        sec = int(cmd[2:])
        return f"左平移{sec}s"
    if cmd.startswith("HR") and len(cmd) == 5 and cmd[2:].isdigit():
        sec = int(cmd[2:])
        return f"右平移{sec}s"

    if cmd.startswith("L0") and len(cmd) == 5 and cmd[2:].isdigit():
        deg = int(cmd[2:])
        return f"左旋{deg}度"
    if cmd.startswith("R0") and len(cmd) == 5 and cmd[2:].isdigit():
        deg = int(cmd[2:])
        return f"右旋{deg}度"

    if cmd.startswith("D") and len(cmd) == 5:
        direction = cmd[1]
        flag = cmd[2]
        angle = cmd[3:]
        if direction in ("0", "1") and flag == "0" and angle.isdigit():
            ang = int(angle)
            if direction == "0":
                return f"舵机左转{ang}度(相对)"
            else:
                return f"舵机右转{ang}度(相对)"

    if cmd.startswith("A") and len(cmd) == 5 and cmd[1:].isdigit():
        ang = int(cmd[1:])
        return f"舵机转到{ang}度(绝对)"

    return None


def _lerp(a: float, b: float, t: float) -> float:
    # t: 0..1
    return a + (b - a) * t


class WaypointGPS:
    """
    按 WAYPOINTS 顺序在相邻航点之间线性插值移动：
    wp[i] -> wp[i+1]，用 SEGMENT_DURATION_S 秒走完
    """
    def __init__(self, waypoints, segment_duration_s=20.0, loop=True):
        if not waypoints or len(waypoints) < 2:
            raise ValueError("WAYPOINTS 至少需要 2 个点")
        self.wps = list(waypoints)
        self.loop = bool(loop)
        self.seg_s = float(segment_duration_s)

        self.i = 0              # 当前段起点索引
        self.t0 = time.time()   # 当前段开始时间

    def _next_index(self, idx: int) -> int:
        nxt = idx + 1
        if nxt < len(self.wps):
            return nxt
        return 0  # 回到起点（loop）

    def step(self, now: float):
        # 如果不 loop，走到最后一个点就停在终点
        if (not self.loop) and self.i >= len(self.wps) - 1:
            lat, lon = self.wps[-1]
            return lat, lon

        j = self._next_index(self.i)
        (lat0, lon0) = self.wps[self.i]
        (lat1, lon1) = self.wps[j]

        # 当前段进度
        dt = max(0.0, now - self.t0)
        t = dt / self.seg_s if self.seg_s > 0 else 1.0

        if t >= 1.0:
            # 段完成：切到下一段，并“把多余时间”折算到下一段（更平滑）
            prev_i = self.i
            self.i = j

            # 如果刚刚从最后一个点切回到第一个点（回到起点）
            if self.loop and prev_i == len(self.wps) - 1 and self.i == 0:
                print("hello")

            self.t0 = now - (dt - self.seg_s)
            # 递归/循环继续算新段的位置（避免卡在航点不动一秒）
            return self.step(now)


        lat = _lerp(lat0, lat1, t)
        lon = _lerp(lon0, lon1, t)
        return lat, lon


def main():
    ser = serial.Serial(PORT, BAUD, timeout=0.1)
    print(f"[MOCK_STM32] opened {PORT} @ {BAUD}")

    # 可选：模拟上电提示
    send_line(ser, "BOOT,OK")

    gps_sim = WaypointGPS(
        waypoints=WAYPOINTS,
        segment_duration_s=SEGMENT_DURATION_S,
        loop=LOOP
    )

    last_gps = 0.0

    while True:
        raw = ser.readline()
        if raw:
            cmd = raw.decode("ascii", errors="ignore").strip().replace("\r", "")
            if cmd:
                print(f"[MOCK_STM32] RX: {cmd}")

                if cmd == "STATUS":
                    send_line(ser, "STATE,1,0")
                    send_line(ser, "M0,TRPM,100.0,ARPM,98.5,CNT,12345")
                    send_line(ser, "M1,TRPM,100.0,ARPM,98.0,CNT,12000")
                    send_line(ser, "SERVO,ANG,90.0,BUSY,0")
                    send_line(ser, "OK")

                elif cmd == "CONFIG":
                    send_line(ser, "KP,1.0,KI,0.1,KD,0.01")
                    send_line(ser, "OK")

                elif cmd == "STOP":
                    act = _parse_motion_cmd(cmd)
                    if act:
                        print(f"[MOCK_STM32] ACT: {act}")
                    send_line(ser, "OK")

                elif cmd.startswith(("S", "F", "B", "HL", "HR", "L0", "R0", "D", "A")):
                    act = _parse_motion_cmd(cmd)
                    if act:
                        print(f"[MOCK_STM32] ACT: {act}")
                    send_line(ser, "OK")

                else:
                    send_line(ser, "ERR01")

        # 周期性发GPS：按航点顺序走线
        now = time.time()
        if now - last_gps > GPS_PERIOD_S:
            last_gps = now
            lat, lon = gps_sim.step(now)
            send_line(ser, f"GPS,{lat:.6f},{lon:.6f}")


if __name__ == "__main__":
    main()
