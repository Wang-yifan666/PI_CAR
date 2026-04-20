# 工具脚本说明

本文档对应 tool/ 目录下脚本的当前用途与运行方式。

## 工具总览

- tool/mock_COM11.py: GPS 回放仿真（命令不改变轨迹）
- tool/mock_COM11_plus.py: 闭环运动学仿真（命令会改变轨迹）
- tool/test_process_detector.py: 独立测试 ProcessDetector
- tool/zip_tool.py: 手动触发打包

## 运行前准备

```bash
source .venv/bin/activate
pip install -r requirements-pc.txt
```

建议在项目根目录 PI_CAR 下执行。

## 1) mock_COM11_plus.py

适用场景：联调 Patrol/FSM/UART 的闭环行为。

```bash
python -m tool.mock_COM11_plus
```

支持常见指令：

- Fxxxx / Bxxxx
- HLxxx / HRxxx
- Lxxxx / Rxxxx
- S / STOP
- STATUS / CONFIG

## 2) mock_COM11.py

适用场景：仅验证 GPS 数据链路与日志输出。

```bash
python -m tool.mock_COM11
```

## 3) test_process_detector.py

适用场景：确认 C++ 检测器是否可被 Python 子进程拉起并读取输出。

```bash
python tool/test_process_detector.py
```

当前脚本默认路径：

- /home/wangyifan/PI_CAR/detector_cpp/build/detector_ncnn

如果你的仓库位置不同，请改脚本中的 exec_path。

## 4) zip_tool.py

常用命令：

```bash
# 打包默认 data/
python -m tool.zip_tool --use-data --task-id TEST_001

# 打包指定目录
python -m tool.zip_tool --root data/violations_20260126 --task-id TEST_002

# 指定输出目录
python -m tool.zip_tool --use-data --task-id TEST_003 --out zips
```

## 推荐联调顺序

1. python tool/test_process_detector.py
2. python -m tool.mock_COM11
3. python -m tool.mock_COM11_plus
4. python -m src.main

## Pi 服务化相关

```bash
sudo cp deploy/pi_car.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart pi_car.service
systemctl status pi_car.service
journalctl -u pi_car.service -f
```
