import serial
import time
import threading
from typing import Optional, List, Tuple, Callable, Any
from dataclasses import dataclass
from enum import Enum

#导入日志
from src.utils.logger import sys_logger as logger

# 读取配置文件
def _get_uart_cfg():
    try:
        import src.global_ctx as ctx
        return (ctx.config or {}).get("uart", {}) if hasattr(ctx, "config") else {}
    except Exception:
        return {}
    
class STM32CommandType(Enum):
    """命令类型枚举"""
    STOP = "S"
    FORWARD = "F"
    BACKWARD = "B"
    LEFT_SHIFT = "HL"
    RIGHT_SHIFT = "HR"
    LEFT_ROTATE = "L0"
    RIGHT_ROTATE = "R0"
    STATUS = "STATUS"
    CONFIG = "CONFIG"
    SERVO_RELATIVE = "D"
    SERVO_ABSOLUTE = "A"  # 注意：默认未启用

# 响应数据类
class STM32Response:
    def __init__(self, success: bool, error_code: int = 0, 
                data_lines: List[str] = None, raw_response: str = ""):
        self.success = success
        self.error_code = error_code
        self.data_lines = data_lines or []
        self.raw_response = raw_response
        
    def __str__(self):
        if self.success:
            return f"Response: OK"
        else:
            return f"Response: ERR{self.error_code:02d}"
    
    @classmethod
    def from_raw(cls, raw_data: str) -> 'STM32Response':
        """从原始数据解析响应"""
        lines = raw_data.strip().split('\n')
        
        # 检查是否错误响应
        for line in reversed(lines):
            line = line.strip()
            if line.startswith("ERR"):
                try:
                    error_code = int(line[3:5])
                    return cls(False, error_code, lines, raw_data)
                except ValueError:
                    pass
        
        # 检查是否成功
        for line in reversed(lines):
            if line.strip() == "OK":
                return cls(True, 0, lines[:-1], raw_data)
        
        # 如果都没有，可能是部分响应或GPS数据
        return cls(False, 0, lines, raw_data)


@dataclass
class MotorStatus:
    """电机状态数据类"""
    motor_id: int
    target_rpm: float
    actual_rpm: float
    encoder_count: int


@dataclass
class RobotStatus:
    """机器人状态数据类"""
    active: bool
    timed: bool
    motors: List[MotorStatus]
    servo_angle: Optional[float] = None
    servo_busy: Optional[bool] = None
    
# STM32串口通信类.用于树莓派与STM32之间的通信  
class STM32Communicator:
    def __init__(self, port: str = None, baudrate: int = None, timeout: float = None):
        """
        初始化串口通信
        Args:
            port: 串口设备路径，如 '/dev/ttyUSB0' 或 '/dev/ttyAMA0'
            baudrate: 波特率，默认115200
            timeout: 读取超时时间（秒）
        """
        # 从 yaml 读取默认值
        cfg = _get_uart_cfg() or {}
        self.port = port if port is not None else str(cfg.get("port", "/dev/ttyUSB0"))
        self.baudrate = int(baudrate if baudrate is not None else cfg.get("baudrate", 115200))
        self.timeout = float(timeout if timeout is not None else cfg.get("timeout", 1.0))

        # 可配置项
        self.cmd_timeout = float(cfg.get("cmd_timeout", 2.0))
        self.max_cmd_len = int(cfg.get("max_cmd_len", 64))
        self.loop_sleep_s = float(cfg.get("loop_sleep_s", 0.01))
        self.cpu_sleep_s = float(cfg.get("cpu_sleep_s", 0.01))

        self.log_rx_line = bool(cfg.get("log_rx_line", False))
        self.log_tx_cmd = bool(cfg.get("log_tx_cmd", True))
        self.log_gps = bool(cfg.get("log_gps", True))

        self.ser = None
        self.running = False
        self.response_callback = None
        self.gps_callback = None
        self.receive_thread = None
        self.command_lock = threading.Lock()

        # 串口只允许一个线程读取：_receive_loop 负责读；send_command 只等待事件
        self._resp_event = threading.Event()
        self._resp_lines = []
        self._waiting_resp = False
        self._resp_result = None

        logger.info(f"[ UART ] init: port={self.port} baudrate={self.baudrate} timeout={self.timeout} cmd_timeout={self.cmd_timeout}")
        
    # 连接串口设备   
    def connect(self) -> bool:
        
        try:
            self.ser = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self.timeout
            )
            
            if self.ser.is_open:
                logger.info(f"[ UART ] Successfully connected to the serial port")
                self._start_receive_thread()
                return True
            else:
                logger.error(f"[ UART ] unable to open the serial port")
                return False
                
        except serial.SerialException as e:
            logger.error(f"[ UART ] unable to connect the serial port: {e}")
            return False
        
    # 断开连接
    def disconnect(self):
        self.running = False
        if self.receive_thread and self.receive_thread.is_alive():
            self.receive_thread.join(timeout=2.0)
        
        if self.ser and self.ser.is_open:
            try:
                self.ser.close()
            except Exception:
                pass
            logger.info(f"[ UART ] serial port of connecting closed")
            
    # 设置命令响应回调函数
    def set_response_callback(self, callback: Callable[[STM32Response], None]):
        self.response_callback = callback
        
    # 设置GPS数据回调函数
    def set_gps_callback(self, callback: Callable[[float, float], None]):
        self.gps_callback = callback
        
    # 启动接收线程 
    def _start_receive_thread(self):    
        self.running = True
        self.receive_thread = threading.Thread(target=self._receive_loop, daemon=True)
        self.receive_thread.start()
        
    # 接收线程主循环
    def _receive_loop(self):
        buffer = ""

        while self.running and self.ser and self.ser.is_open:
            try:
                # 读取所有可用数据
                if self.ser.in_waiting > 0:
                    raw_data = self.ser.read(self.ser.in_waiting).decode('ascii', errors='ignore')
                    buffer += raw_data
                    
                    # 按行分割处理
                    while '\n' in buffer:
                        line_end = buffer.find('\n')
                        line = buffer[:line_end].strip()
                        buffer = buffer[line_end + 1:]
                        
                        if line:
                            self._process_received_line(line)
                else:
                    time.sleep(self.cpu_sleep_s)
                            
            except serial.SerialException as e:
                logger.error(f"[ UART ] serial reading failed: {e}")
                time.sleep(0.1)
            except Exception as e:
                logger.error(f"[ UART ] receive loop error: {e}")
                time.sleep(0.1)
            
            time.sleep(self.loop_sleep_s)  # 短暂休眠避免CPU占用过高
            
    # 处理接收到的单行数据
    def _process_received_line(self, line: str):
        
        # 移除可能的回车符
        line = line.replace('\r', '')

        if self.log_rx_line:
            logger.info("[ UART ] RX <- %s", line)
        
        # 检查是否是GPS数据
        if line.startswith("GPS,"):
            self._process_gps_data(line)
            return
        
        # 检查是否是启动提示
        if line.startswith("BOOT,OK"):
            logger.info(f"[ UART ] STM32 have opend")
            if self.response_callback:
                self.response_callback(STM32Response(True, 0, [line], line))
            return
        
        # 如果正在等待命令响应：由接收线程统一收集，并在 OK/ERR 结束时唤醒 send_command
        if self._waiting_resp:
            # 中间行（例如 STATUS/CONFIG 的多行数据）
            if line != "OK" and (not line.startswith("ERR")):
                self._resp_lines.append(line)
                return

            # 终止行：OK / ERRxx
            if line == "OK":
                raw = '\n'.join(self._resp_lines + ["OK"])
                self._resp_result = STM32Response(True, 0, self._resp_lines, raw)
                self._resp_event.set()
                if self.response_callback:
                    self.response_callback(self._resp_result)
                return

            if line.startswith("ERR"):
                try:
                    error_code = int(line[3:5])
                except ValueError:
                    error_code = 0
                raw = '\n'.join(self._resp_lines + [line])
                self._resp_result = STM32Response(False, error_code, self._resp_lines + [line], raw)
                self._resp_event.set()
                if self.response_callback:
                    self.response_callback(self._resp_result)
                return
        
        # 普通命令响应，通过回调处理
        if self.response_callback:
            if line.startswith("ERR"):
                try:
                    error_code = int(line[3:5])
                    response = STM32Response(False, error_code, [line], line)
                    self.response_callback(response)
                except ValueError:
                    pass
            elif line == "OK":
                response = STM32Response(True, 0, [], line)
                self.response_callback(response)
            else:
                response = STM32Response(True, 0, [line], line)
                self.response_callback(response)
                
    # 处理GPS数据
    def _process_gps_data(self, line: str):
        
        try:
            parts = line.split(',')
            if len(parts) >= 3:
                lat_str = parts[1]
                lon_str = parts[2]
                
                if lat_str != "NA" and lon_str != "NA":
                    lat = float(lat_str)
                    lon = float(lon_str)
                    
                    if self.gps_callback:
                        self.gps_callback(lat, lon)
                    else:
                        if self.log_gps:
                            logger.info(f"[ UART ] GPS is {lat:.7f},{lon:.7f}")
                else:
                    logger.warning(f"[ UART ] Without GPS position")
        except Exception as e:
            logger.error(f"[ UART ] GPS parsing error: {e}")
    
    # 发送命令
    def send_command(self, command: str, wait_for_response: bool = True, 
                    timeout: float = None) -> Optional[STM32Response]:
        """
        发送命令到STM32
        
        Args:
            command: 命令字符串（不含换行符）
            wait_for_response: 是否等待响应
            timeout: 等待响应的超时时间（秒）
            
        Returns:
            STM32Response对象或None
        """
        if not self.ser or not self.ser.is_open:
            logger.error(f"[ UART ] serial port not connected")
            return None

        if timeout is None:
            timeout = self.cmd_timeout
        
        with self.command_lock:
            # 确保命令以\r\n结尾
            if not command.endswith('\r\n'):
                command += '\r\n'
            
            # 检查命令长度
            if len(command) > self.max_cmd_len:
                logger.error(f"[ UART ] warning: len{len(command)} exceeded the limit {self.max_cmd_len}")
                return None
            
            try:
                # 发送前先准备等待结构，避免响应太快被错过
                if wait_for_response:
                    self._resp_event.clear()
                    self._resp_lines = []
                    self._resp_result = None
                    self._waiting_resp = True

                # 发送命令
                if self.log_tx_cmd:
                    logger.info("[ UART ] TX -> %s", command.strip())
                self.ser.write(command.encode('ascii'))
                self.ser.flush()
                
                if not wait_for_response:
                    return None
                
                # 不在这里读串口，只等待接收线程 set 事件
                ok = self._resp_event.wait(timeout=timeout)
                self._waiting_resp = False

                if not ok:
                    logger.error(f"[ UART ] waiting overtime {timeout}秒")
                    return None

                return self._resp_result
                
            except serial.SerialException as e:
                logger.error(f"[ UART ] error in sending command: {e}")
                self._waiting_resp = False
                return None
    
    # 等待响应
    def _wait_for_response(self, timeout: float) -> Optional[STM32Response]:
        """
        等待命令响应（简化版，使用事件等待）
        实际应用中可以使用更复杂的响应匹配机制
        """
        # 串口只允许接收线程读取
        start_time = time.time()
        collected_lines = []
        
        while time.time() - start_time < timeout:
            time.sleep(0.01)
        
        logger.error(f"[ UART ] waiting overtime {timeout}秒")
        return None
    
    # ========== 具体命令方法 ==========
    
    def stop(self) -> Optional[STM32Response]:
        return self.send_command("S")
    
    def forward(self, seconds: int) -> Optional[STM32Response]:
        """
        前进指定秒数
        
        Args:
            seconds: 秒数 (0-9999)
        """
        if not 0 <= seconds <= 9999:
            logger.error(f"[ UART ] fault : FORWARD time exceed range(0-9999)")
            return None
        
        cmd = f"F{seconds:04d}"
        return self.send_command(cmd)
    
    def backward(self, seconds: int) -> Optional[STM32Response]:
        """
        后退指定秒数
        
        Args:
            seconds: 秒数 (0-9999)
        """
        if not 0 <= seconds <= 9999:
            logger.error(f"[ UART ] fault : BACKWARD time exceed range(0-9999)")
            return None
        
        cmd = f"B{seconds:04d}"
        return self.send_command(cmd)
    
    def left_shift(self, seconds: int) -> Optional[STM32Response]:
        """
        左平移指定秒数
        
        Args:
            seconds: 秒数 (0-999)
        """
        if not 0 <= seconds <= 999:
            logger.error(f"[ UART ] fault : LEFT_SHIFT time exceed range(0-9999)")
            return None
        
        cmd = f"HL{seconds:03d}"
        return self.send_command(cmd)
    
    def right_shift(self, seconds: int) -> Optional[STM32Response]:
        """
        右平移指定秒数
        
        Args:
            seconds: 秒数 (0-999)
        """
        if not 0 <= seconds <= 999:
            logger.error(f"[ UART ] fault : RIGHT_SHIFT time exceed range(0-999)")
            return None
        
        cmd = f"HR{seconds:03d}"
        return self.send_command(cmd)
    
    def left_rotate(self, degrees: int) -> Optional[STM32Response]:
        """
        左旋转指定角度
        
        Args:
            degrees: 角度 (0-999)
        """
        if not 0 <= degrees <= 999:
            logger.error(f"[ UART ] fault : LEFT_ROTATE time exceed range(0-999)")
            return None
        
        cmd = f"L0{degrees:03d}"
        return self.send_command(cmd)
    
    def right_rotate(self, degrees: int) -> Optional[STM32Response]:
        """
        右旋转指定角度
        
        Args:
            degrees: 角度 (0-999)
        """
        if not 0 <= degrees <= 999:
            logger.error(f"[ UART ] fault : RIGHT_ROTATE time exceed range(0-999)")
            return None
        
        cmd = f"R0{degrees:03d}"
        return self.send_command(cmd)
    
    def get_status(self) -> Optional[RobotStatus]:
        """
        查询机器人状态
        
        Returns:
            RobotStatus对象或None
        """
        response = self.send_command("STATUS")
        
        if not response or not response.success:
            logger.error(f"[ UART ] Faild to obtian the status")
            return None
        
        return self._parse_status_response(response.data_lines)
    
    def _parse_status_response(self, lines: List[str]) -> RobotStatus:
        """解析状态响应"""
        motors = []
        active = False
        timed = False
        servo_angle = None
        servo_busy = None
        
        for line in lines:
            if line.startswith("STATE,"):
                parts = line.split(',')
                if len(parts) >= 3:
                    active = parts[1] == "1"
                    timed = parts[2] == "1"
            
            elif line.startswith("M"):
                # 解析电机状态: M0,TRPM,100.0,ARPM,98.5,CNT,12345
                parts = line.split(',')
                try:
                    motor_id = int(parts[0][1:])  # 去掉M
                    target_rpm = float(parts[2])
                    actual_rpm = float(parts[4])
                    encoder_count = int(parts[6])
                    
                    motor = MotorStatus(motor_id, target_rpm, actual_rpm, encoder_count)
                    motors.append(motor)
                except (ValueError, IndexError):
                    continue
            
            elif line.startswith("SERVO,"):
                # SERVO,ANG,90.0,BUSY,0
                parts = line.split(',')
                try:
                    for i in range(len(parts)):
                        if parts[i] == "ANG":
                            servo_angle = float(parts[i+1])
                        elif parts[i] == "BUSY":
                            servo_busy = parts[i+1] == "1"
                except (ValueError, IndexError):
                    continue
        
        return RobotStatus(active, timed, motors, servo_angle, servo_busy)
    
    def get_config(self) -> Optional[dict]:
        """
        查询配置
        
        Returns:
            配置字典或None
        """
        response = self.send_command("CONFIG")
        
        if not response or not response.success or not response.data_lines:
            print("获取配置失败")
            return None
        
        config_line = response.data_lines[0]
        parts = config_line.split(',')
        
        config = {}
        for i in range(0, len(parts), 2):
            if i + 1 < len(parts):
                try:
                    value = float(parts[i+1])
                    config[parts[i]] = value
                except ValueError:
                    config[parts[i]] = parts[i+1]
        
        return config
    
    def servo_relative(self, direction: str, angle: int) -> Optional[STM32Response]:
        """
        舵机相对角度控制
        
        Args:
            direction: 方向 '0'=左, '1'=右
            angle: 角度 (0-99)
        """
        if direction not in ['0', '1']:
            print("错误：舵机方向必须是 '0'(左) 或 '1'(右)")
            logger.error(f"[ UART ] Error: The direction of the servo must be '0' (left) or '1' (right).") 
        
        if not 0 <= angle <= 99:
            logger.error(f"[ UART ] fault: The relative angle of the steering gear exceeds the limit range.")
            return None
        
        cmd = f"D{direction}0{angle:02d}"
        return self.send_command(cmd)
    
    def wait_servo_idle(self, timeout: float = 10.0) -> bool:
        """
        等待舵机空闲
        
        Args:
            timeout: 超时时间（秒）
            
        Returns:
            True: 舵机空闲, False: 超时或失败
        """
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            status = self.get_status()
            if status and status.servo_busy is not None and not status.servo_busy:
                return True
            time.sleep(0.1)
        
        return False
