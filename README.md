# RoboPatrol_Pi - 校园智能巡逻小车 (上位机系统)

![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)
![Platform](https://img.shields.io/badge/Platform-Raspberry_Pi_5-red.svg)
![Status](https://img.shields.io/badge/Status-In_Development-yellow.svg)

> **挑战杯参赛项目**：基于树莓派 5 + STM32 的校园违规停车自动检测与巡逻系统。

## 项目简介

本项目旨在解决校园内电动车/摩托车违规充电问题。系统采用 **上下位机结构**，利用 **树莓派 5 (8GB)** 的强大算力进行边缘 AI 推理，配合 **STM32** 驱动麦克纳姆轮底盘进行全向移动。

**核心功能：**
* **自主巡逻**：基于状态机 (FSM) 的逻辑控制。
* **视觉识别**：部署 YOLOv5m (ONNX) 模型，实时检测违规车辆。
* **证据留存**：自动抓拍违规画面，记录时间、地点 (GPS) 并打包存证。
* **云端同步**：回巢自动连接 Wi-Fi，将证据包上传至服务器。

---

## 系统架构

### 1. 硬件架构
* **计算核心**：Raspberry Pi 5 (8GB RAM) - 运行 Ubuntu 24.04 Server
* **底盘控制**：STM32F407 + 麦克纳姆轮 (实现全向移动)
* **视觉传感**：CSI 摄像头
* **定位模块**：GPS模块
* **通信链路**：UART

### 2. 软件架构 (多线程模型)
上位机软件采用 **生产者-消费者** 模式，由 4 个核心线程并发协同：
1.  **Main Thread**：负责系统初始化、看门狗监控及异常重启。
2.  **Vision Thread**：负责图像采集与 YOLO 推理，将检测结果写入队列。
3.  **FSM Thread**：核心状态机，根据视觉结果与任务进度决策下一步动作。
4.  **UART Thread**：负责指令封包 (`A0020`) 下发及传感器数据解析。

---

##  快速开始

### 1. 环境准备

确保运行在 **Raspberry Pi 5** 上，且系统已启用 UART 和 Camera 接口。

```bash
# 克隆仓库
git clone https://github[https://github.com/你的用户名/RoboPatrol_Pi.git](https://github.com/你的用户名/RoboPatrol_Pi.git)
cd RoboPatrol_Pi

# 创建虚拟环境
python3 -m venv venv
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

### 2. 配置参数

### 3. 运行系统

---


**License**: MIT

