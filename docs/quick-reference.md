# 快速参考卡片

高频使用的命令、配置与代码片段速查表。

---

# 项目快速启动

## 初次使用

```bash
# 1. 克隆项目
git clone https://github.com/Wang-yifan666/PI_CAR.git
cd PI_CAR

# 2. 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate  # Linux/Pi

# 3. 安装依赖
pip install -r requirements-pc.txt    # PC
pip install -r requirements-pi.txt    # 树莓派，注意！！！：由于不知名原因，numpy的版本必须使用 < 2 的版本

# 4. 启动主程序
python -m src.main
```

## 常用调试命令

```bash
# 测试检测模块
python tool/test_process_detector.py

# 测试 GPS 数据流
python tool/mock_COM11.py

# 测试完整闭环
python tool/mock_COM11_plus.py

# 查看摄像头（Pi）
rpicam-hello --list-cameras

# 查看系统信息
uname -a
python --version
```

---

# 配置参数速查

## UART 配置

```yaml
uart:
  enable: true
  port: /dev/ttyAMA0      # Linux/Pi,建议固定，可能会在 ttyACM0 ， ttyACM1 反复切换
  # port: COM10           # Windows
  baudrate: 115200
  timeout: 1.0
  cmd_timeout: 2.0
  log_rx_line: false
  log_tx_cmd: false
```

## 检测器配置

```yaml
dector:
  enable: true
  backend: process        # 推荐
  # backend: onnx         # 备选
  
  # Process 后端
  process:
    exec_path: detector_cpp/build/Release/detector_ncnn.exe
    args:
      - --param=models/ncnn/best.ncnn.param
      - --bin=models/ncnn/best.ncnn.bin
      - --classes=models/classes.txt
      - --source=camera:0
      - --conf=0.8
  
  violation:
    enable: true
    cooldown_s: 5.0
    ebike_class_id: 0
    fire_class_id: 1
```

## FSM 配置

```yaml
fsm:
  violation_cmd: "S"      # 停止
  stop_cmd: "S"
  fire_enable: true
  fire_cls: 1
  cmd_dedup_s: 0.5
  hold_after_lost_s: 3.0
```

## 巡逻配置

```yaml
patrol:
  enable: true
  loop: true
  arrive_radius_m: 1.5
  forward_sec: 2
  turn_threshold_deg: 5.0
  waypoints:
    - [31.231312, 121.474597]
    - [31.231400, 121.474597]
    - [31.231400, 121.474650]
    - [31.231312, 121.474650]
```

## GPS 配置

```yaml
gps:
  enable: true
  stale_timeout_s: 3.0
  log_every_s: 5.0
```

---

# 串口命令表

| 命令 | 格式 | 示例 | 说明 |
| --- | --- | --- | --- |
| 前进 | F[SSSS] | F0002 | 前进 2 秒 |
| 后退 | B[SSSS] | B0001 | 后退 1 秒 |
| 左转 | L[DDDD] | L0090 | 左转 90° |
| 右转 | R[DDDD] | R0045 | 右转 45° |
| 停止 | S / STOP | S | 立即停止 |
| 查询 | STATUS | STATUS | 查询当前状态 |
| 配置 | CONFIG | CONFIG | 查询配置 |

**返回格式**：
- `OK` - 命令执行成功
- `ERR` - 命令执行失败
- `GPS,<lat>,<lon>` - GPS 数据

---

# 日志字段速查

## 日志级别

| 级别 | 用途 | 进入简报 |
| --- | --- | --- |
| DEBUG | 循环细节、串口收发 |  不进 |
| INFO | 关键事件 | 取决于 brief 参数 |
| WARNING | 可恢复异常 |  自动进 |
| ERROR | 不可恢复错误 |  自动进 |
| CRITICAL | 致命错误 |  自动进 |

## 常见源标签

| source | 含义 |
| --- | --- |
| INIT | 启动/配置/退出 |
| UART | 串口通信 |
| GPS | GPS 数据处理 |
| PATROL | 巡逻导航 |
| FSM | 状态机决策 |
| DETECT | 检测与拍照 |
| ZIP | 打包压缩 |
| UPLOAD | 上传 |
| PROCESS | 子进程管理 |

## 关键字段

```
lat=<float>           # 纬度
lon=<float>           # 经度
dist_m=<float>        # 距离（米）
bearing_deg=<float>   # 方位角（度）
heading_deg=<float>   # 航向（度）
conf=<float>          # 置信度（0-1）
cls=<cls_name>        # 物体类别
hdop=<float>          # GPS 精度因子
port=<str>            # 串口名
cmd=<str>             # 命令
result=ok|fail        # 结果
reason=<reason>       # 原因
cost_ms=<float>       # 耗时（毫秒）
id_run=<str>          # 运行 ID
```

---

# 环境变量

```bash
# 指定配置文件
export PICAR_CONFIG=config/settings.yaml
export PICAR_CONFIG=config/settings_cpp.yaml

# 设置日志级别
export PICAR_LOG_LEVEL=DEBUG
export PICAR_LOG_LEVEL=INFO

# 指定数据目录
export PICAR_DATA_DIR=data/
```

---

# 文件位置速查

| 文件/目录 | 位置 | 用途 |
| --- | --- | --- |
| 主程序 | src/main.py | 启动入口 |
| 配置 | config/settings.yaml | 系统配置 |
| 详细日志 | data/logs_de/ | 调试日志 |
| 简报日志 | data/logs_be/ | 关键事件 |
| 违规证据 | data/violations_*/ | 存证文件 |
| 打包输出 | zips/ | 压缩包 |
| 工具脚本 | tool/ | 仿真与测试 |
| 文档 | docs/ | 所有文档 |
| C++ 检测器 | detector_cpp/ | 检测源码 |
| 模型文件 | models/ | 神经网络模型 |

---

# 快速问题排查

| 症状 | 快速诊断 | 参考文档 |
| --- | --- | --- |
| 无 UART 输出 | 检查 `ls -la /dev/tty*` | troubleshooting.md#uart |
| 无 GPS 数据 | 查看 `grep "stale" data/logs_de/*.log` | troubleshooting.md#gps |
| 检测器崩溃 | 运行 `python tool/test_process_detector.py` | troubleshooting.md#detector |
| 车不动 | 查看 `grep "UART tx" data/logs_de/*.log` | troubleshooting.md#patrol |
| CPU 高占用 | 运行 `top -H` 查看进程 | troubleshooting.md#performance |

---

# Git 常用命令

```bash
# 查看状态
git status

# 添加文件
git add src/
git add --all

# 提交更改
git commit -m "XXX"

# 查看日志
git log --oneline
git log --graph --all --decorate

# 创建分支
git checkout -b feature/new-detection

# 切换分支
git checkout main

# 合并分支
git merge feature/new-detection

# 推送到远程
git push origin feature/new-detection

# 拉取最新
git pull origin main
```

---

# 文档快速导航

| 文档 | 快速链接 |
| --- | --- |
| 项目主页 | [README.md](../README.md) |
| 快速上手 | [docs/start.md](../docs/start.md) |
| 配置说明 | [docs/settings.md](../docs/settings.md) |
| 日志规范 | [docs/logger.md](../docs/logger.md) |
| 工具脚本 | [docs/tool.md](../docs/tool.md) |
| 代码结构 | [docs/code-structure.md](../docs/code-structure.md) |
| 本文档 | [docs/quick-reference.md](../docs/quick-reference.md) |

