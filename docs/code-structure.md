# 代码结构导读

本文档帮助新开发者快速了解项目的代码组织结构、核心模块职责与数据流向。

---

# 项目目录树

```
PI_CAR/
├── config/                  # 配置文件目录
│   ├── settings.yaml        # 实车（Pi）配置
│   └── settings_cpp.yaml    # PC 调试配置
│
├── src/                     # 主程序源码
│   ├── main.py              # 启动入口
│   ├── global_ctx.py        # 全局上下文（线程间通信）
│   │
│   ├── core/                # 核心逻辑层
│   │   ├── fsm.py           # 状态机（决策/仲裁）
│   │   └── patrol_logic.py  # 巡逻导航逻辑
│   │
│   ├── drivers/             # 硬件驱动层
│   │   ├── uart.py          # UART 通信驱动
│   │   └── camera.py        # 摄像头驱动
│   │
│   ├── services/            # 业务服务层
│   │   ├── dector.py        # 检测服务（ONNX/Process）
│   │   ├── gps_service.py   # GPS 处理
│   │   ├── process_detector.py  # 外部进程检测器桥接
│   │   └── uploader.py      # 上传与打包
│   │
│   ├── mode/                # 运行模式
│   │   └── showcase.py      # 展演模式
│   │
│   └── utils/               # 工具函数
│       └── logger.py        # 日志系统
│
├── detector_cpp/            # C++ 检测器
│   ├── CMakeLists.txt       # CMake 构建配置
│   ├── detector_ncnn.cpp    # NCNN 推理实现
│   ├── stub.cpp             # 集成测试桩
│   └── third_party/         # 第三方库（NCNN）
│
├── tool/                    # 工具脚本
│   ├── mock_COM11.py        # GPS 回放仿真器
│   ├── mock_COM11_plus.py   # 闭环运动学仿真器
│   ├── test_process_detector.py  # 检测器独立测试
│   └── zip_tool.py          # 打包工具
│
├── data/                    # 运行数据目录
│   ├── logs_de/             # 详细日志
│   ├── logs_be/             # 简报日志
│   └── violations_*/        # 违规证据
│
├── models/                  # 模型文件
│   ├── ncnn/                # NCNN 模型
│   ├── onnx/                # ONNX 模型
│   └── classes.txt          # 类别标签
│
├── docs/                    # 文档目录
│   ├── start.md             # 快速上手
│   ├── settings.md          # 配置说明
│   ├── logger.md            # 日志规范
│   ├── tool.md              # 工具说明
│   ├── troubleshooting.md   # 故障排查
│   ├── code-structure.md    # 本文
│
├── requirements-pc.txt      # PC 依赖
├── requirements-pi.txt      # Pi 依赖
├── README.md                # 项目简介
└── LICENSE

```

---

# 核心模块详解

## 1. 启动与配置（src/main.py 与 src/global_ctx.py）

### 启动流程

```python
# src/main.py 核心逻辑
def main():
    # 1. 配置加载
    config = load_config(env_config_path or platform_config_paths)
    configure_logging(config)  # 初始化日志
    
    # 2. 全局上下文初始化
    ctx = GlobalContext(config)
    
    # 3. 平台检测
    is_pi = detect_platform()
    
    # 4. 线程启动
    threads = []
    if config['uart']['enable']:
        threads.append(UARTService(ctx))
    if config['gps']['enable']:
        threads.append(GPSService(ctx))
    if config['dector']['enable']:
        threads.append(DetectorService(ctx))
    if config['patrol']['enable']:
        threads.append(PatrolService(ctx))
    if config['fsm']['enable']:
        threads.append(FSMService(ctx))
    if config['uploader']['enable']:
        threads.append(UploaderService(ctx))
    
    # 5. 启动所有线程
    for thread in threads:
        thread.start()
    
    # 6. 等待停止信号
    ctx.system_stop_event.wait()
    
    # 7. 优雅关闭
    for thread in threads:
        thread.join(timeout=5)
```

### 全局上下文（ctx）

`GlobalContext` 是线程间通信的中枢，包含：

```python
class GlobalContext:
    config: dict              # 配置字典
    
    # 队列（生产者-消费者模式）
    uart_queue: Queue        # UART 命令队列
    dector_queue: Queue      # 检测事件队列
    patrol_cmd_queue: Queue  # 巡逻建议队列
    upload_queue: Queue      # 上传任务队列
    
    # 状态变量
    gps_state: dict          # GPS 位置与状态
    uart_ready: Event        # UART 就绪信号
    system_stop_event: Event # 全局停止信号
    pack_event: Event        # 打包触发信号
```

**作用**：避免全局变量污染，集中管理状态与队列，便于测试与扩展。

---

## 2. 硬件驱动层（src/drivers/）

### 2.1 UART 驱动（uart.py）

**职责**：与 STM32 下位机通信

```python
class UARTService:
    def run(self):
        while not ctx.system_stop_event.is_set():
            # 1. 接收循环
            # 监听串口 RX，识别 GPS 数据和应答
            line = uart_read()  # 阻塞读
            if line.startswith("GPS,"):
                # GPS 回调
                parse_gps(line)
            elif line in ["OK", "ERR"]:
                # 命令应答
                handle_ack()
            
            # 2. 发送循环
            # 从 uart_queue 读取命令并发送
            try:
                cmd = ctx.uart_queue.get(timeout=0.1)
                uart_write(cmd)
                ctx.uart_queue.task_done()
            except Empty:
                pass
```

**关键参数**（config/settings.yaml）:
```yaml
uart:
  port: COM10                 # 串口号
  baudrate: 115200            # 波特率
  cmd_timeout: 2.0            # 命令应答超时
```

---

### 2.2 摄像头驱动（camera.py）

**职责**：图像采集（多模式支持）

```python
class CameraService:
    def __init__(self, source_type):
        # 3 种模式自动选择
        if source_type == "picamera2":
            self.camera = Picamera2()  # 树莓派 CSI
        elif source_type == "desktop":
            self.camera = DesktopScreenCapture()  # PC 屏幕截取
        else:
            self.camera = MockCamera()  # 模拟模式
    
    def capture(self):
        frame = self.camera.read()
        return frame
```

---

## 3. 检测服务层（src/services/）

### 3.1 检测主服务（dector.py）

**职责**：检测框架与违规判定

```python
class DetectorService:
    def run(self):
        # 1. 选择后端
        if config['dector']['backend'] == 'process':
            detector = ProcessDetector(config)  # C++ 子进程
        else:
            detector = ONNXDetector(config)     # Python ONNX
        
        while not ctx.system_stop_event.is_set():
            # 2. 帧处理
            frame = camera.capture()
            
            # 3. 推理
            results = detector.infer(frame)
            
            # 4. 违规判定
            violation = check_violation(results)
            if violation:
                # 存证
                save_snapshot(frame, violation)
                # 投递到队列
                ctx.dector_queue.put(violation)
```

**违规判定逻辑**：

```python
def check_violation(results):
    for detection in results:
        cls_id, conf, box = detection
        
        # 1. 类别匹配
        if cls_id == config['violation']['ebike_class_id']:
            # 2. 面积或中心距离判定
            area_norm = calculate_area_norm(box)
            center_dist = calculate_center_dist(box)
            
            if area_norm > threshold or center_dist < threshold:
                return {
                    'type': 'ebike',
                    'conf': conf,
                    'box': box,
                    'timestamp': time.time()
                }
        
        # 火灾检测
        if cls_id == config['violation']['fire_class_id']:
            if conf > config['fsm']['fire_conf_threshold']:
                return {'type': 'fire', 'conf': conf, ...}
    
    return None
```

---

### 3.2 外部进程检测器（process_detector.py）

**职责**：启动和管理 C++ 检测子进程

```python
class ProcessDetector:
    def __init__(self, config):
        exec_path = config['dector']['process']['exec_path']
        args = config['dector']['process']['args']
        
        # 启动进程
        self.proc = subprocess.Popen(
            [exec_path] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
    
    def poll(self, timeout):
        # 读取 stdout
        try:
            line = self.proc.stdout.readline(timeout=timeout)
            
            # 期望格式：[ NCNN ]{...json...}
            if line.startswith("[ NCNN ]"):
                json_str = line[8:]  # 去掉前缀
                return json.loads(json_str)
        except:
            return None
```

**进程输出规范**：
```text
[ NCNN ]{
  "detections": [
    {"cls_id": 0, "conf": 0.95, "x1": 100, "y1": 50, "x2": 200, "y2": 150},
    ...
  ]
}
```

---

### 3.3 GPS 服务（gps_service.py）

**职责**：GPS 数据处理与超时检测

```python
class GPSService:
    def run(self):
        # 注册回调到 UART
        uart_service.on_gps = self.on_gps_data
        
        while not ctx.system_stop_event.is_set():
            # 定期检查 GPS 超时
            elapsed_s = time.time() - self.last_gps_timestamp
            
            if elapsed_s > config['gps']['stale_timeout_s']:
                ctx.gps_state['valid'] = False
                log_event(source='GPS', event='stale', ...)
            else:
                ctx.gps_state['valid'] = True
            
            time.sleep(0.5)
    
    def on_gps_data(self, lat, lon):
        # 被 UART 线程调用
        ctx.gps_state['lat'] = lat
        ctx.gps_state['lon'] = lon
        ctx.gps_state['timestamp'] = time.time()
```

---

### 3.4 上传服务（uploader.py）

**职责**：违规数据打包与上传

```python
class UploaderService:
    def run(self):
        while not ctx.system_stop_event.is_set():
            # 1. 等待打包触发
            ctx.pack_event.wait(timeout=1.0)
            
            # 2. 打包
            zip_path = build_zip_for_data()
            
            # 3. 投递到上传队列
            ctx.upload_queue.put(zip_path)
            
            # 4. 上传
            while not ctx.upload_queue.empty():
                zip_path = ctx.upload_queue.get()
                
                try:
                    upload_to_server(zip_path)
                    # 写入上传标记
                    mark_file(zip_path, 'uploaded')
                except Exception as e:
                    # 失败重试
                    log_event(source='UPLOAD', event='retry', ...)
```

---

## 4. 核心逻辑层（src/core/）

### 4.1 巡逻导航（patrol_logic.py）

**职责**：根据航点计算运动建议

```python
class PatrolLogic:
    def __init__(self, waypoints, config):
        self.waypoints = waypoints  # 航点列表
        self.current_idx = 0
        self.state = "TURN"  # TURN 或 GO
    
    def update(self, gps_state):
        if not gps_state['valid']:
            return None  # GPS 无效，无法导航
        
        current_wp = self.waypoints[self.current_idx]
        lat, lon = gps_state['lat'], gps_state['lon']
        
        # 计算距离和方向
        dist_m = haversine(lat, lon, current_wp[0], current_wp[1])
        bearing_deg = calculate_bearing(lat, lon, current_wp[0], current_wp[1])
        heading_deg = gps_state['heading']
        
        # 到点判定
        if dist_m < config['arrive_radius_m']:
            self.current_idx = (self.current_idx + 1) % len(self.waypoints)
            return None
        
        # TURN 阶段：先调整方向
        if self.state == "TURN":
            angle_diff = abs(bearing_deg - heading_deg)
            
            if angle_diff < config['turn_threshold_deg']:
                self.state = "GO"
            else:
                # 发送旋转命令
                cmd = "L{:04d}".format(int(angle_diff)) if bearing_deg > heading_deg \
                      else "R{:04d}".format(int(angle_diff))
                return cmd
        
        # GO 阶段：前进
        if self.state == "GO":
            # 小步长前进
            return f"F{config['forward_sec']:04d}"
```

**航点配置示例**：
```yaml
patrol:
  waypoints:
    - [31.231312, 121.474597]  # 点 A
    - [31.231400, 121.474597]  # 点 B
    - [31.231400, 121.474650]  # 点 C
    - [31.231312, 121.474650]  # 点 D
  arrive_radius_m: 1.5
  turn_threshold_deg: 5.0
  forward_sec: 2
```

---

### 4.2 有限状态机（fsm.py）

**职责**：仲裁多源命令，输出最终运动指令

```python
class FSM:
    def run(self):
        while not ctx.system_stop_event.is_set():
            # 1. 收集所有命令源
            violation_cmd = None
            patrol_cmd = None
            
            # 从违规队列读取
            try:
                violation_evt = ctx.dector_queue.get_nowait()
                violation_cmd = config['fsm']['violation_cmd']  # 通常是 STOP
                
                # 火警处理
                if violation_evt['type'] == 'fire':
                    self.mission_mode = 'FIRE_RETURN'
                    self.mission.return_to_base = True
            except Empty:
                pass
            
            # 从巡逻队列读取
            try:
                patrol_cmd = ctx.patrol_cmd_queue.get_nowait()
            except Empty:
                pass
            
            # 2. 优先级仲裁
            # 优先级：违规 > 巡逻 > 停止
            final_cmd = None
            if violation_cmd:
                final_cmd = violation_cmd
            elif patrol_cmd:
                final_cmd = patrol_cmd
            else:
                final_cmd = config['fsm']['stop_cmd']
            
            # 3. 去重
            if final_cmd != self.last_cmd or \
               time.time() - self.last_cmd_time > config['fsm']['cmd_dedup_s']:
                # 投递到 UART 队列
                ctx.uart_queue.put(final_cmd)
                self.last_cmd = final_cmd
                self.last_cmd_time = time.time()
            
            time.sleep(0.1)
```

**优先级规则**：
```
违规检测（最高）> 巡逻建议 > 停止命令（最低）
```

---

## 5. 展演模式（src/mode/showcase.py）

**职责**：执行预设动作序列

```python
class ShowcaseMode:
    def run(self):
        actions = [
            ("F0002", "前进 2 秒"),
            ("L0090", "左转 90°"),
            ("F0002", "前进 2 秒"),
            ("R0090", "右转 90°"),
        ]
        
        while not ctx.system_stop_event.is_set():
            for cmd, desc in actions:
                ctx.uart_queue.put(cmd)
                time.sleep(3)  # 等待完成
```

---

## 6. 日志系统（src/utils/logger.py）

**职责**：统一日志输出与去重

```python
def log_event(logger, source, event, action=None, key=None, 
              result=None, reason=None, cost_ms=None, ids=None, 
              brief=False, level=logging.INFO):
    """
    统一日志入口，格式：
    <event> <action> | <key kv...> | result=<...> reason=<...> | cost_ms=<...> id_<name>=<value>...
    """
    msg = f"{event}"
    if action:
        msg += f" {action}"
    
    if key:
        msg += " | " + " ".join(f"{k}={v}" for k, v in key.items())
    
    if result or reason:
        msg += " | "
        if result:
            msg += f"result={result}"
        if reason:
            msg += f" reason={reason}" if result else f"reason={reason}"
    
    if cost_ms or ids:
        msg += " | "
        if cost_ms:
            msg += f"cost_ms={cost_ms}"
        if ids:
            msg += " " + " ".join(f"id_{k}={v}" for k, v in ids.items())
    
    logger.log(level, msg, extra={'source': source, 'brief': brief})
```

---

# 线程生命周期

```
                    ┌──────────────┐
                    │   Main Exit  │
                    │ (Ctrl+C/cmd) │
                    └──────┬───────┘
                           │
                    ┌──────▼──────┐
                    │ set_stop_   │
                    │ event()     │
                    └──────┬──────┘
                           │
        ┌──────────────────┼──────────────────┐
        │                  │                  │
    ┌───▼────┐        ┌───▼────┐        ┌───▼────┐
    │ UART   │        │ PATROL │        │ FSM    │
    │ exit() │        │ exit() │        │ exit() │
    └────────┘        └────────┘        └────────┘
        │                  │                  │
        └──────────────────┼──────────────────┘
                           │
                      ┌────▼────┐
                      │ Graceful│
                      │ Cleanup │
                      └─────────┘
```

---

# 关键配置与效果

| 配置项 | 位置 | 说明 |
| --- | --- | --- |
| `uart.port` | settings.yaml | 串口号（影响下位机通信） |
| `dector.backend` | settings.yaml | 检测后端（process/onnx） |
| `fsm.violation_cmd` | settings.yaml | 违规时的响应命令 |
| `patrol.waypoints` | settings.yaml | 巡逻路径 |
| `patrol.arrive_radius_m` | settings.yaml | 到点判定半径 |
| `gps.stale_timeout_s` | settings.yaml | GPS 超时时间 |

---
