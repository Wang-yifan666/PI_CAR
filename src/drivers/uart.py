import serial
import time
import threading
from typing import Optional, List, Tuple, Callable, Any
from dataclasses import dataclass
from enum import Enum

#导入日志
from src.utils.logger import sys_logger as logger


class STM32CommandType(Enum):
    """命令类型枚举"""
    STOP = "STOP"
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


class STM32Response:
    """响应数据类"""
    
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


class STM32Communicator:
    """
    STM32串口通信类
    用于树莓派与STM32之间的通信
    """
    
    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 1.0):
        """
        初始化串口通信
        
        Args:
            port: 串口设备路径，如 '/dev/ttyUSB0' 或 '/dev/ttyAMA0'
            baudrate: 波特率，默认115200
            timeout: 读取超时时间（秒）
        """
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.ser = None
        self.running = False
        self.response_callback = None
        self.gps_callback = None
        self.receive_thread = None
        self.command_lock = threading.Lock()
        
    def connect(self) -> bool:
        """连接串口设备"""
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
            logger.error(f"[ UART ] unable to connect the serial port")
            return False
    
    def disconnect(self):
        """断开连接"""
        self.running = False
        if self.receive_thread and self.receive_thread.is_alive():
            self.receive_thread.join(timeout=2.0)
        
        if self.ser and self.ser.is_open:
            self.ser.close()
            logger.error(f"[ UART ] serial port of connecting closed")
    
    def set_response_callback(self, callback: Callable[[STM32Response], None]):
        """设置命令响应回调函数"""
        self.response_callback = callback
    
    def set_gps_callback(self, callback: Callable[[float, float], None]):
        """设置GPS数据回调函数"""
        self.gps_callback = callback
    
    def _start_receive_thread(self):
        """启动接收线程"""
        self.running = True
        self.receive_thread = threading.Thread(target=self._receive_loop, daemon=True)
        self.receive_thread.start()
    
    def _receive_loop(self):
        """接收线程主循环"""
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
                            
            except serial.SerialException as e:
                logger.error(f"[ UART ] category file date_reading failed {e}")
                time.sleep(0.1)
            except Exception as e:
                logger.error(f"[ UART ] category file receive_resolve failed {e}")
                time.sleep(0.1)
            
            time.sleep(0.01)  # 短暂休眠避免CPU占用过高
    
    def _process_received_line(self, line: str):
        """处理接收到的单行数据"""
        # 移除可能的回车符
        line = line.replace('\r', '')
        
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
        
        # 普通命令响应，通过回调处理
        if self.response_callback:
            # 检查是否是错误响应
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
                # 可能是状态查询的中间行
                response = STM32Response(True, 0, [line], line)
                self.response_callback(response)
    
    def _process_gps_data(self, line: str):
        """处理GPS数据"""
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
                        logger.info(f"[ UART ] GPS is {lat:.7f},{lon:.7f}")
                else:
                    logger.error(f"[ UART ] Without GPS position")
        except Exception as e:
            logger.error(f"[ UART ] GPS parsing error")
    
    def send_command(self, command: str, wait_for_response: bool = True, 
                    timeout: float = 2.0) -> Optional[STM32Response]:
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
        
        with self.command_lock:
            # 确保命令以\r\n结尾
            if not command.endswith('\r\n'):
                command += '\r\n'
            
            # 检查命令长度
            if len(command) > 64:
                logger.error(f"[ UART ] warning: len{len(command)} exceeded the limit 64")
                return None
            
            try:
                # 发送命令
                self.ser.write(command.encode('ascii'))
                self.ser.flush()
                
                if not wait_for_response:
                    return None
                
                # 等待响应
                return self._wait_for_response(timeout)
                
            except serial.SerialException as e:
                logger.error(f"[ UART ] error in sending command")
                return None
    
    def _wait_for_response(self, timeout: float) -> Optional[STM32Response]:
        """
        等待命令响应（简化版，使用事件等待）
        实际应用中可以使用更复杂的响应匹配机制
        """
        start_time = time.time()
        collected_lines = []
        
        while time.time() - start_time < timeout:
            if self.ser.in_waiting > 0:
                try:
                    # 读取一行
                    line = self.ser.readline().decode('ascii', errors='ignore').strip()
                    
                    if line:
                        # 处理GPS数据
                        if line.startswith("GPS,"):
                            self._process_gps_data(line)
                            continue
                        
                        collected_lines.append(line)
                        
                        # 检查是否收到OK
                        if line == "OK":
                            return STM32Response(True, 0, collected_lines[:-1], 
                                                '\n'.join(collected_lines))
                        
                        # 检查是否收到ERR
                        if line.startswith("ERR"):
                            try:
                                error_code = int(line[3:5])
                                return STM32Response(False, error_code, 
                                                    collected_lines, 
                                                    '\n'.join(collected_lines))
                            except ValueError:
                                pass
                except Exception as e:
                    logger.error(f"[ UART ] reading error")
            
            time.sleep(0.01)
        
        logger.error(f"[ UART ] waiting overtime {timeout}秒")
        return None
    
    # ========== 具体命令方法 ==========
    
    def stop(self) -> Optional[STM32Response]:
        """发送停止命令"""
        return self.send_command("STOP")
    
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


# ========== 使用示例 ==========

# def example_usage():
#     """使用示例"""
    
#     # 响应回调函数
#     def on_response(response: STM32Response):
#         if response.success:
#             print("命令执行成功")
#             if response.data_lines:
#                 print("返回数据:")
#                 for line in response.data_lines:
#                     print(f"  {line}")
#         else:
#             print(f"命令执行失败: ERR{response.error_code:02d}")
    
#     # GPS回调函数
#     def on_gps(lat: float, lon: float):
#         print(f"收到GPS: ({lat:.7f}, {lon:.7f})")
    
#     # 创建通信对象
#     # 注意：根据实际情况修改串口设备路径
#     communicator = STM32Communicator(
#         port='/dev/ttyUSB0',  # 或 '/dev/ttyAMA0'（树莓派原生串口）
#         baudrate=115200,
#         timeout=1.0
#     )
    
#     # 设置回调
#     communicator.set_response_callback(on_response)
#     communicator.set_gps_callback(on_gps)
    
#     # 连接
#     if not communicator.connect():
#         print("连接失败，退出")
#         return
    
#     try:
#         # 等待启动完成（可选）
#         time.sleep(1)
        
#         # 示例1: 查询配置
#         print("\n=== 查询配置 ===")
#         config = communicator.get_config()
#         if config:
#             print("当前配置:")
#             for key, value in config.items():
#                 print(f"  {key}: {value}")
        
#         # 示例2: 前进5秒
#         print("\n=== 前进5秒 ===")
#         communicator.forward(5)
#         time.sleep(1)  # 等待命令执行
        
#         # 示例3: 查询状态
#         print("\n=== 查询状态 ===")
#         status = communicator.get_status()
#         if status:
#             print(f"机器人状态: active={status.active}, timed={status.timed}")
#             for motor in status.motors:
#                 print(f"  电机{motor.motor_id}: 目标转速={motor.target_rpm}, "
#                       f"实际转速={motor.actual_rpm}, 编码器={motor.encoder_count}")
        
#         # 示例4: 舵机控制
#         print("\n=== 舵机控制 ===")
#         # 左转15度
#         response = communicator.servo_relative('0', 15)
#         if response and response.error_code == 6:  # ERR06: 舵机忙
#             print("舵机忙，等待...")
#             if communicator.wait_servo_idle():
#                 print("舵机空闲，重新发送命令")
#                 communicator.servo_relative('0', 15)
        
#         # 示例5: 停止
#         print("\n=== 停止 ===")
#         communicator.stop()
        
#         # 保持运行，接收GPS数据
#         print("\n=== 等待GPS数据（10秒）===")
#         time.sleep(10)
        
#     except KeyboardInterrupt:
#         print("\n用户中断")
#     finally:
#         # 断开连接
#         communicator.disconnect()


# def interactive_mode():
#     """交互式命令行模式"""
    
#     communicator = STM32Communicator(
#         port='/dev/ttyUSB0',  # 修改为你的串口设备
#         baudrate=115200
#     )
    
#     if not communicator.connect():
#         return
    
#     print("STM32串口通信交互模式")
#     print("输入命令 (输入 'quit' 退出):")
    
#     while True:
#         try:
#             cmd = input(">>> ").strip()
            
#             if cmd.lower() == 'quit':
#                 break
            
#             if cmd:
#                 response = communicator.send_command(cmd)
#                 if response:
#                     if response.success:
#                         print("OK")
#                         if response.data_lines:
#                             for line in response.data_lines:
#                                 print(line)
#                     else:
#                         print(f"ERR{response.error_code:02d}")
            
#         except KeyboardInterrupt:
#             print("\n退出")
#             break
    
#     communicator.disconnect()

