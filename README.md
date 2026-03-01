# RoboPatrol_Pi

### 校园智能巡逻小车上位机系统

![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)
![Platform](https://img.shields.io/badge/Platform-Raspberry_Pi_5-red.svg)
![Status](https://img.shields.io/badge/Status-In_Development-yellow.svg)

---

# 项目简介

**RoboPatrol_Pi** 是一套运行在 Raspberry Pi 上的智能巡逻小车上位机系统。

本项目针对校园内电动车/摩托车违规充电问题，构建了一个：

> 自主巡逻 + 视觉识别 + 证据存储 + 云端同步 的完整闭环系统

系统采用 **上下位机架构**：

* 上位机（Raspberry Pi）负责：

  * 多线程调度
  * 巡逻决策
  * 视觉识别
  * 数据存储与上传

* 下位机（STM32）负责：

  * 电机驱动
  * 麦克纳姆轮运动控制
  * GPS 模块解析
  * 状态回传

---

# 核心功能

*  折线航点巡逻（Waypoints）
*  全向麦克纳姆轮控制
*  NCNN 外部进程检测（主链路）
*  ONNX 内置推理（兼容保留）
*  自动抓拍违规证据
*  GPS 定位记录
*  回巢自动打包上传
*  支持 PC 仿真闭环测试

---

# 系统架构

## 一、硬件架构

| 模块             | 说明      |
| -------------- | ------- |
| Raspberry Pi 5 | 上位机计算核心 |
| STM32F407      | 底盘驱动控制  |
| 麦克纳姆轮          | 实现全向移动  |
| CSI 摄像头        | 图像采集    |
| GPS 模块         | 下位机解析定位 |
| UART           | 上下位机通信  |

---

## 二、软件架构（多线程模型）

系统采用 **生产者-消费者并发模型**，线程之间通过 `ctx` 共享状态与队列。

### 线程说明

### 1 Main Thread

* 加载配置
* 初始化模块
* 启动各线程
* 退出（Ctrl+C）

---

### 2 Vision Thread（DECTOR）

负责图像采集与目标检测。

支持三种运行模式：

| 模式   | 条件           | 说明         |
| ---- | ------------ | ---------- |
| 实车模式 | 存在 Picamera2 | 使用 CSI 摄像头 |
| 桌面模式 | 无摄像头         | 使用 mss 抓屏  |
| 模拟模式 | 无关键依赖        | 生成虚拟检测数据   |

---

### 3 FSM Thread（核心决策层）

* 接收视觉检测结果
* 接收巡逻建议
* 输出统一运动指令
* 避免多源指令冲突

> 建议所有运动指令统一由 FSM 下发。

---

### 4 UART Thread

* 发送控制命令到 STM32
* 解析回传数据（GPS / 状态）
* 维护串口心跳

---

### 5 GPS Thread

* 监听 UART 解析结果
* 更新全局 GPS 状态
* 进行 stale 超时检测

---

### 6 Patrol Thread

* 根据 Waypoints 计算巡逻路径
* 输出 TURN / GO 建议命令
* 与 FSM 协同完成导航

---

# GPS 数据链路

## 下位机上报格式

```
GPS,<lat>,<lon>
```

示例：

```
GPS,31.231312,121.474597
```

## 上位机处理流程

UART 线程识别 `GPS,` → 解析经纬度 → 更新 `GPSService` → 写入 `ctx`

---

# 巡逻策略说明

采用通用折线巡逻模型：

```
P0 → P1 → P2 → ... → PN
```

## 状态流程

### 1️⃣ TURN 阶段

* 计算目标方向
* 一次性发送旋转指令（L0xxx / R0xxx）

### 2️⃣ GO 阶段

* 小步长前进（Fxxxx）
* 周期性检查偏航

### 3️⃣ 到点判定

* 球面距离 < arrive_radius_m

---

# 快速开始

## 1️⃣ 克隆项目

```bash
git clone https://github.com/Wang-yifan666/PI_CAR.git
cd PI_CAR
```

## 2️⃣ 创建虚拟环境

```bash
python3 -m venv venv
source venv/bin/activate
```

---

## 3️⃣ 安装依赖

### PC 调试环境

```bash
pip install -r requirements-pc.txt
```

### 树莓派部署环境

```bash
pip install -r requirements-pi.txt
```

---

# 配置说明（settings_cpp.yaml / settings.yaml）

程序启动时会按平台读取：

1. Linux / 树莓派：`config/settings.yaml` → `config/settings_cpp.yaml`
2. Windows / PC：`config/settings_cpp.yaml` → `config/settings.yaml`

也可通过环境变量强制指定：

```bash
PICAR_CONFIG=config/settings.yaml
```

两份配置建议分工：

* `config/settings.yaml`：Linux / Pi 实车运行主配置
* `config/settings_cpp.yaml`：Windows / PC 调试与回放配置

系统配置主要模块包括：

* uart
* gps
* dector
* fsm
* patrol
* uploader

建议详细参数说明见：

```
docs/settings.md
```

> 建议：实车部署时使用 `settings.yaml` 作为主配置，`settings_cpp.yaml` 保留给 PC 调试。

---

# 树莓派部署最小闭环（process + ncnn + CSI）

建议在 Pi 上按以下顺序执行：

1 前置检查（无桌面环境同样适用）

```bash
cat /etc/os-release
getconf LONG_BIT
libcamera-hello --list-cameras
```

2 安装依赖并创建虚拟环境

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip cmake build-essential pkg-config libopencv-dev libcamera-dev python3-picamera2
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements-pi.txt
```

3 构建 C++ 检测器（在 Pi 本地）

```bash
cmake -S detector_cpp -B detector_cpp/build -DCMAKE_BUILD_TYPE=Release
cmake --build detector_cpp/build -j
```

4 先测检测子进程，再启动主程序

```bash
python tool/test_process_detector.py
python -m src.main
```

> 期望结果：`tool/test_process_detector.py` 能持续读取 `[ NCNN ]{...}`，主程序可进入巡逻/检测主循环。

---

# NCNN 检测链路说明

当前默认检测链路为：

```text
DECTOR_ser (backend=process)
  -> ProcessDetector
  -> detector_cpp/build/Release/detector_ncnn(.exe)
```

`detector_ncnn(.exe)` 通过标准输出持续输出：

```text
[ NCNN ]{...json...}
```

Python 侧会解析 JSON 并投递到 `ctx.dector_queue`，供 FSM 决策使用。

> Linux / Pi 环境请使用 `detector_cpp/build/Release/detector_ncnn`。
> Windows 环境使用 `detector_cpp/build/Release/detector_ncnn.exe`。

---

# C++ 检测器构建（Windows）

项目包含 C++ 检测器源码：

```text
detector_cpp/detector_ncnn.cpp
```

可在 VS Code 中使用 CMake Tools 直接构建 `detector_ncnn` 目标，输出示例：

```text
detector_cpp/build/Release/detector_ncnn.exe
```

模型与类别文件默认位于：

```text
models/ncnn/
models/classes.txt
```

---

# systemd 服务化（树莓派）

建议将主程序配置为系统服务，以支持断电重启后自动拉起：

```bash
sudo cp deploy/patrol.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now patrol.service
systemctl status patrol.service
journalctl -u patrol.service -f
```

> 注意：`deploy/patrol.service` 需按你的 Pi 路径与运行用户配置 `WorkingDirectory` / `ExecStart`。

---

# PC 端闭环仿真测试

当没有真实 STM32 时，可使用虚拟串口对进行测试：

### 推荐工具

* Windows：com0com

示例：

```
COM10 <-> COM11
```

* 上位机连接 COM10
* 运行 mock 工具连接 COM11

---

# Tool 工具说明

目录：

```
tool/
```

包含：

### mock_COM11.py

* 简单 GPS 回放仿真

### mock_COM11_plus.py

* 闭环运动学仿真（命令驱动）

### test_process_detector.py

* 独立测试检测子进程（ProcessDetector）

---

# 开发调试流程建议

1 先测试 detector

```
python tool/test_process_detector.py
```

2 测试 GPS 数据链路

```
python tool/mock_COM11.py
```

3 测试完整闭环巡逻

```
python tool/mock_COM11_plus.py
```

---

# 合作者

* [https://github.com/Wang-yifan666](https://github.com/Wang-yifan666)
* [https://github.com/zhurui-f](https://github.com/zhurui-f)

---

# License

MIT License
