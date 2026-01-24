import time
import serial

PORT = "COM11"
BAUD = 115200


def send_line(ser, s: str):
    ser.write((s + "\r\n").encode("ascii", errors="ignore"))
    ser.flush()


def _parse_motion_cmd(cmd: str):
    """
    解析类似：
    F0002 / B0010 / HL120 / HR005 / L0090 / R0180
    D10015 (示例：D + direction + 0 + angle(2)) 你可以按真实协议再改
    A0090 (示例：绝对角)
    S / S0000 / STOP
    返回一个中文动作描述字符串；不能解析返回 None
    """
    cmd = (cmd or "").strip()

    # STOP 兼容
    if cmd == "STOP":
        return "停止"
    if cmd == "S":
        return "停止"
    if cmd.startswith("S") and len(cmd) >= 2:
        # 兼容 S0000 这种（如果你将来用）
        rest = cmd[1:]
        if rest.isdigit():
            # 通常 S0000 也代表停止
            return "停止"

    # 前进/后退：Fxxxx / Bxxxx
    if cmd.startswith("F") and len(cmd) == 5 and cmd[1:].isdigit():
        sec = int(cmd[1:])
        return f"向前{sec}s"
    if cmd.startswith("B") and len(cmd) == 5 and cmd[1:].isdigit():
        sec = int(cmd[1:])
        return f"向后{sec}s"

    # 平移：HLxxx / HRxxx
    if cmd.startswith("HL") and len(cmd) == 5 and cmd[2:].isdigit():
        sec = int(cmd[2:])
        return f"左平移{sec}s"
    if cmd.startswith("HR") and len(cmd) == 5 and cmd[2:].isdigit():
        sec = int(cmd[2:])
        return f"右平移{sec}s"

    # 旋转：L0xxx / R0xxx
    if cmd.startswith("L0") and len(cmd) == 5 and cmd[2:].isdigit():
        deg = int(cmd[2:])
        return f"左旋{deg}度"
    if cmd.startswith("R0") and len(cmd) == 5 and cmd[2:].isdigit():
        deg = int(cmd[2:])
        return f"右旋{deg}度"

    # 舵机相对：D + direction(0/1) + 0 + angle(2)
    # 例：D00015 / D10015 （你现有 uart.py: cmd = f"D{direction}0{angle:02d}"）
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

    # 舵机绝对：Axxxx（示例：A0090）
    if cmd.startswith("A") and len(cmd) == 5 and cmd[1:].isdigit():
        ang = int(cmd[1:])
        return f"舵机转到{ang}度(绝对)"

    return None


def main():
    ser = serial.Serial(PORT, BAUD, timeout=0.1)
    print(f"[MOCK_STM32] opened {PORT} @ {BAUD}")

    # 可选：模拟上电提示
    send_line(ser, "BOOT,OK")

    last_gps = 0.0

    while True:
        # 读一行命令（以 \n 为界）
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
                    # 兼容 STOP
                    act = _parse_motion_cmd(cmd)
                    if act:
                        print(f"[MOCK_STM32] ACT: {act}")
                    send_line(ser, "OK")

                elif cmd.startswith(("S", "F", "B", "HL", "HR", "L0", "R0", "D", "A")):
                    # 这里会把 F0020 -> “向前20s” 打出来
                    act = _parse_motion_cmd(cmd)
                    if act:
                        print(f"[MOCK_STM32] ACT: {act}")
                    send_line(ser, "OK")

                else:
                    # 未知命令
                    send_line(ser, "ERR01")

        # 可选：周期性发GPS
        now = time.time()
        if now - last_gps > 1.0:
            last_gps = now

            # 模拟缓慢移动
            lat = 31.230416 + (last_gps % 1000) * 0.000001
            lon = 121.473701 + (last_gps % 1000) * 0.000001

            send_line(ser, f"GPS,{lat:.6f},{lon:.6f}")


if __name__ == "__main__":
    main()
    
"""

收到 STOP/Fxxxx/Bxxxx/HLxxx/... 就回 OK

收到 STATUS 就回几行状态 + OK

收到 CONFIG 回一行配置 + OK

"""
