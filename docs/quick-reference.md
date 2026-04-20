# 快速参考卡

高频命令、关键配置和常见路径速查。

## 常用命令

```bash
# 环境
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-pc.txt

# 主程序
python -m src.main

# 检测子进程
python tool/test_process_detector.py

# 串口仿真
python -m tool.mock_COM11
python -m tool.mock_COM11_plus

# 打包工具
python -m tool.zip_tool --use-data --task-id TEST_001
```

## Pi 构建 C++ 检测器

```bash
cmake -S detector_cpp -B detector_cpp/build -DCMAKE_BUILD_TYPE=Release
cmake --build detector_cpp/build -j
```

## systemd

```bash
sudo cp deploy/pi_car.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pi_car.service
systemctl status pi_car.service
journalctl -u pi_car.service -f
```

## 配置键速查

### uart

```yaml
uart:
  enable: true
  port: "/dev/serial/by-id/..."
  baudrate: 115200
  timeout: 1.0
  cmd_timeout: 2.0
```

### dector

```yaml
dector:
  enable: true
  backend: process
  model_path: models/onnx/best.onnx
  class_file: models/classes.txt
  conf_threshold: 0.60
  process:
    exec_path: detector_cpp/build/detector_ncnn
    args: []
```

### fsm / gps / patrol

```yaml
fsm:
  default_mode: M0001
  fire_enable: true

gps:
  enable: true
  stale_timeout_s: 2.0

patrol:
  enable: true
  arrive_radius_m: 3.0
  forward_sec: 4
```

## 模式码

- M0001: PATROL
- M0002: SHOWCASE
- M0004: STOP_REQUEST

## 关键目录

- src/: 主程序
- config/: 配置
- tool/: 联调脚本
- detector_cpp/: C++ 检测器
- data/logs_de/: 详细日志
- data/logs_be/: 简报日志
- data/violations_*/: 违规存证
- zips/: 打包输出

## 文档导航

- README.md
- start.md
- settings.md
- logger.md
- tool.md
- troubleshooting.md
- code-structure.md
