# PI_CAR

校园智能巡逻小车上位机系统（Raspberry Pi + STM32）。

![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)
![Platform](https://img.shields.io/badge/Platform-Raspberry_Pi_5-red.svg)
![Status](https://img.shields.io/badge/Status-In_Development-yellow.svg)

## 项目简介

PI_CAR 面向校园场景中的电动车/摩托车违规行为巡检，系统目标是形成：

- 自主巡逻
- 视觉检测
- 证据留存
- 打包上传

的完整闭环。

系统采用上下位机架构：

- 上位机（Raspberry Pi）负责巡逻决策、视觉检测、数据留存与上传。
- 下位机（STM32）负责底盘运动控制、GPS 数据上报、状态回传。

## 核心功能

- 折线航点巡逻（waypoints）
- 外部 C++ NCNN 检测链路（主链路）
- ONNX 推理链路（兼容保留）
- 违规事件抓拍与结构化记录
- 火警触发返航与打包
- ZIP 分卷打包与可选上传
- 串口模式选择（M0001 / M0002 / M0004）

## 目录结构

```text
PI_CAR/
├── config/               # 配置文件
├── src/                  # 主程序源码
├── detector_cpp/         # C++ 检测器
├── tool/                 # 联调与仿真工具
├── docs/                 # 项目文档
├── deploy/               # systemd 服务文件
├── data/                 # 运行数据与日志
├── zips/                 # 打包输出
└── models/               # 模型与类别文件
```

## 快速开始

### 1. 克隆仓库

```bash
git clone https://github.com/Wang-yifan666/PI_CAR.git
cd PI_CAR
```

### 2. 创建虚拟环境并安装依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip

# PC 调试环境
pip install -r requirements-pc.txt

# Pi 实机环境
# pip install -r requirements-pi.txt
```

### 3. 启动主程序

```bash
python -m src.main
```

## 配置加载规则

默认按平台加载并合并配置：

- Linux / Pi：`config/settings.yaml` -> `config/settings_cpp.yaml`
- Windows / PC：`config/settings_cpp.yaml` -> `config/settings.yaml`

可通过环境变量指定单一配置：

```bash
export PICAR_CONFIG=config/settings.yaml
```

## 模式选择（串口）

- `M0001`：巡逻模式（PATROL）
- `M0002`：展演模式（SHOWCASE）
- `M0004`：优雅停机（STOP_REQUEST）

如果未收到下位机模式码，程序会按 `fsm.default_mode` 回退（默认 `M0001`）。

## NCNN 检测链路

默认推荐链路：

```text
DETECT (backend=process)
  -> ProcessDetector
  -> detector_cpp/build/detector_ncnn  (Linux/Pi)
  -> detector_cpp/build/Release/detector_ncnn.exe  (Windows)
```

子进程通过 stdout 输出：

```text
[ NCNN ]{...json...}
```

Python 侧解析后投递到 `ctx.dector_queue`，再由 FSM 仲裁下发运动命令。

## C++ 检测器构建

### Linux / Pi

```bash
cmake -S detector_cpp -B detector_cpp/build -DCMAKE_BUILD_TYPE=Release
cmake --build detector_cpp/build -j
```

### Windows

可使用 VS Code CMake Tools 构建 `detector_ncnn` 目标。

## systemd 部署（Pi）

仓库提供服务文件：`deploy/pi_car.service`。

```bash
sudo cp deploy/pi_car.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pi_car.service
systemctl status pi_car.service
journalctl -u pi_car.service -f
```

注意：请按实际用户名和路径调整 `User`、`WorkingDirectory`、`ExecStart`。

## 常用联调命令

```bash
# 检测子进程单测
python tool/test_process_detector.py

# GPS 回放仿真
python -m tool.mock_COM11

# 闭环运动学仿真
python -m tool.mock_COM11_plus

# 手动打包 data/
python -m tool.zip_tool --use-data --task-id TEST_001
```

## 文档导航

- [快速上手](docs/start.md)
- [配置说明](docs/settings.md)
- [日志规范](docs/logger.md)
- [工具说明](docs/tool.md)
- [代码结构](docs/code-structure.md)
- [速查卡](docs/quick-reference.md)

## 合作者

- https://github.com/Wang-yifan666
- https://github.com/zhurui-f

## License

MIT License
