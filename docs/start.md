# 快速上手

本文档给出从 0 到可运行的最短路径。

## 1. 环境准备

- Python 3.11+
- Linux / Pi 建议使用系统 Python3 + venv
- Windows 调试建议开启虚拟串口对（如 COM10 <-> COM11）

## 2. 获取代码与安装依赖

```bash
git clone https://github.com/Wang-yifan666/PI_CAR.git
cd PI_CAR
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements-pc.txt
```

Pi 实机请改为：

```bash
pip install -r requirements-pi.txt
```

## 3. 配置文件选择

默认平台加载顺序：

- Linux/Pi：config/settings.yaml -> config/settings_cpp.yaml
- Windows/PC：config/settings_cpp.yaml -> config/settings.yaml

也可以手动指定：

```bash
export PICAR_CONFIG=config/settings.yaml
```

## 4. 启动主程序

```bash
python -m src.main
```

## 5. 模式选择说明

程序通过串口模式码决定运行模式：

- M0001：巡逻模式（PATROL）
- M0002：展演模式（SHOWCASE）
- M0004：优雅停机（STOP_REQUEST）

若超时未收到模式码，将按 fsm.default_mode 回退（默认 M0001）。

## 6. 常用联调流程

```bash
# 1) 测检测子进程
python tool/test_process_detector.py

# 2) 测 GPS 回放
python -m tool.mock_COM11

# 3) 测闭环运动学
python -m tool.mock_COM11_plus

# 4) 启动主程序
python -m src.main
```

## 7. Pi 最小闭环（process + ncnn + CSI）

```bash
# 检查相机
rpicam-hello --list-cameras

# 构建 C++ 检测器
cmake -S detector_cpp -B detector_cpp/build -DCMAKE_BUILD_TYPE=Release
cmake --build detector_cpp/build -j

# 验证检测链路 + 启动主程序
python tool/test_process_detector.py
python -m src.main
```

## 8. 更多文档

- 配置：settings.md
- 日志：logger.md
- 工具：tool.md
- 排障：troubleshooting.md
- 结构：code-structure.md
- 速查：quick-reference.md
