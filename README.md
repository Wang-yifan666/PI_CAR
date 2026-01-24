# RoboPatrol_Pi - 校园智能巡逻小车 (上位机系统)

![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)
![Platform](https://img.shields.io/badge/Platform-Raspberry_Pi_5-red.svg)
![Status](https://img.shields.io/badge/Status-In_Development-yellow.svg)

## 项目简介

本项目旨在解决校园内电动车/摩托车违规充电问题。系统采用 **上下位机结构**，利用 **树莓派 5 (8GB)** 进行边缘 AI 推理，配合 **STM32** 驱动麦克纳姆轮底盘进行全向移动。

**核心功能：**

* **自主巡逻**：基于状态机 (FSM) 的逻辑控制，支持手写航点（Waypoints）折线巡逻。
* **视觉识别**：部署 YOLOv5m (ONNX) 模型，实时检测违规车辆。
* **证据留存**：自动抓拍违规画面，记录时间、地点 (GPS) 并打包存证。
* **云端同步**：回巢自动连接 Wi-Fi，将证据包上传至服务器。

---

## 系统架构

### 1. 硬件架构

* **计算核心**：Raspberry Pi 5 (8GB RAM) 
* **底盘控制**：STM32F407 + 麦克纳姆轮 (实现全向移动)
* **视觉传感**：CSI 摄像头
* **定位模块**：GPS 模块（由下位机解析，上位机接收经纬度结果）
* **通信链路**：UART

### 2. 软件架构 (多线程模型)

上位机软件采用 **生产者-消费者** 思路，多线程并发协同（以 `main.py` 启动为准）：

1. **Main Thread（主线程）**
   负责系统初始化、加载配置、启动各线程、看门狗监控与优雅退出（Ctrl+C）。

2. **Vision Thread（DECTOR 线程）**
   负责图像采集与 YOLO 推理，将检测结果写入共享状态/队列。内置 **硬件抽象层 (HAL)**，支持三种运行模式自动切换：

   * **实车模式**：检测到 `Picamera2` 库时，调用树莓派 CSI 摄像头进行实时推理。
   * **桌面模式**：在 PC 端运行（缺少相机库）时，自动调用 `mss` 抓取电脑屏幕画面，便于算法验证。
   * **模拟模式**：当缺少核心依赖（如 `cv2`）时，自动生成虚拟数据以维持系统逻辑闭环。

3. **FSM Thread（核心状态机线程）**
   根据视觉结果、巡逻进度与安全状态进行决策，并输出运动指令（建议：由 FSM 统一下发，避免多源指令冲突）。

4. **UART Thread（串口泵/发送线程）**
   从 `ctx.uart_queue` 取出命令，调用 `STM32Communicator.send_command()` 下发给下位机；同时接收并解析下位机回传数据（例如 GPS、状态信息）。

5. **GPS Thread（GPSService 线程）**
   负责将 UART 解析出的 GPS 数据写入全局上下文 `ctx`，并进行 **stale 超时检测**（仅标记有效/无效，不直接做业务动作）。

6. **Patrol Thread（巡逻线程 / PatrolService）**
   负责巡逻策略输出（例如折线巡逻的旋转与直行指令），为 FSM 提供导航意图/建议命令。

> 注：某些线程（如视觉采集）内部可能还会启动 worker 线程用于抓帧/推理，这是模块内部实现细节。

---

## GPS 数据链路

### 1) 解析位置

GPS 的 NMEA/原始信号在 **下位机 STM32** 侧完成解析，上位机只接收解析后的经纬度结果。

### 2) 上报协议（已验证）

下位机通过 UART 上报：

```
GPS,<lat>,<lon>\n
```

示例：

```
GPS,31.231312,121.474597
```

### 3) 上位机处理流程

UART 驱动识别 `GPS,` 前缀 -> 解析 `lat/lon` -> 触发 `gps_callback` -> `GPSService` 写入 `ctx`。

---

## 巡逻策略（手写 Waypoints 折线巡逻）

本项目巡逻不限定圆形轨迹，采用通用的 **折线巡逻**：

* 将巡逻路线离散为一串 **航点（Waypoints）**：`P0, P1, ...`
* 每段路线为直线：`Pi -> P(i+1)`
* 到达航点后，切换下一段

**动作序列：**

1. **转向阶段（TURN）**：计算目标航点方向，使用下位机旋转指令一次转到位（`L0xxx / R0xxx`，单位为度）。
2. **直行阶段（GO）**：按小步长前进（例如 `F0002`），周期性检查距离是否到点、是否需要纠偏。
3. **到点判定**：距离小于 `arrive_radius_m` 认为到达，进入下一航点。

> 由于上位机目前只使用 GPS，经纬度到米的距离换算采用球面距离（或局部近似）用于“到点判定”。

---

## 快速开始

### 1. 环境准备

确保运行在 **Raspberry Pi 5** 上，且系统已启用 UART 和 Camera 接口。

```bash
# 克隆仓库
git clone https://github.com/你的用户名/RoboPatrol_Pi.git
cd RoboPatrol_Pi

# 创建虚拟环境
python3 -m venv venv
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

### 2. 配置参数（settings.yaml）

**UART 示例：**

```yaml
uart:
  enable: true
  required: false
  port: "COM10"        # 树莓派上一般为 /dev/ttyAMA0 或 /dev/ttyS0
  baudrate: 115200
  timeout: 1.0
```

**GPS 示例：**

```yaml
gps:
  enable: true
  source: "uart"
  stale_timeout_s: 2.0
  log_every_s: 2.0
```

**巡逻（手写航点）示例：**

```yaml
patrol:
  enable: true
  loop: true
  arrive_radius_m: 3.0
  forward_sec: 2
  turn_threshold_deg: 8
  waypoints:
    - [11.111111, 122.222222]
    - [11.111211, 122.222322]
    - [11.111311, 122.222422]
```

### 3. 运行系统

```bash
python3 src/main.py
```

---

## PC 端仿真测试（推荐）

在无法移动实车/没有真实 STM32 时，可使用 **com0com** 创建虚拟串口对（如 `COM10 <-> COM11`）：

* 上位机程序连接 `COM10`
* 使用 MOCK 脚本连接 `COM11`，周期性发送：

  ```
  GPS,31.231312,121.474597
  ```

即可验证 UART/GPS/巡逻逻辑闭环。

### tools 工具脚本（串口仿真/调试）
项目新增 `tools/` 文件夹，用于存放开发阶段的辅助小工具脚本，例如：

- **PC 端通过虚拟串口（com0com）连接 COM10 的小程序**
- 用于模拟下位机（STM32）行为：接收上位机指令并回 `OK/STATUS/CONFIG`，以及周期性发送 `GPS,<lat>,<lon>` 上报
- 方便在无法移动实车或缺少真实硬件时进行全链路联调

> 使用方法：创建虚拟串口对（如 `COM10 <-> COM11`），上位机连接 `COM10`，然后运行 `tools/` 中的串口模拟程序连接 `COM11`。

---

## 合作者
- https://github.com/Wang-yifan666
- https://github.com/zhurui-f

**License**: MIT
