# 串口仿真与检测调试工具说明

本文档说明 `tool/` 目录下工具脚本的真实用途、运行方法与注意事项，面向 PC 仿真联调与 Pi 实机排障。

---

# 1. 工具总览

| 脚本 | 作用 | 典型场景 |
| --- | --- | --- |
| `tool/mock_COM11_plus.py` | 闭环运动学串口仿真（命令会改变位置） | 验证 Patrol/FSM/UART 闭环 |
| `tool/mock_COM11.py` | 航点回放串口仿真（命令不改变轨迹） | 只测试 GPS 数据链路 |
| `tool/test_process_detector.py` | 独立拉起 `ProcessDetector` | 排查检测子进程能否启动与读数 |
| `tool/zip_tool.py` | 调用 uploader 打包逻辑 | 手动打包 `data/` 或指定目录 |

---

# 2. 运行前准备

## 2.1 在仓库根目录执行

以下命令默认在项目根目录执行（即 `PI_CAR/`）。

## 2.2 虚拟环境与依赖

```bash
# Windows
.venv\Scripts\Activate.ps1

# Linux/Pi
source .venv/bin/activate

pip install -r requirements-pc.txt
```

## 2.3 串口对要求（PC）

如果没有真实 STM32，建议准备虚拟串口对，例如：

```text
COM10 <-> COM11
```

- 上位机配置使用 `COM10`
- mock 工具默认监听 `COM11`

> 默认端口写在脚本常量中（`PORT="COM11"`），可按需修改。

---

# 3. mock_COM11_plus.py（闭环运动学仿真）

## 3.1 功能

模拟“命令驱动运动 + GPS 回传”的下位机行为：

> 上位机发运动命令 → 仿真器积分更新位姿 → 周期回传 `GPS,lat,lon`

该脚本会根据接收命令改变位置和航向，适合闭环验证。

## 3.2 运行

```bash
python -m tool.mock_COM11_plus
```

## 3.3 支持命令（当前代码）

- `F0004`：前进 4 秒
- `B0002`：后退 2 秒
- `HL003`：左平移 3 秒
- `HR003`：右平移 3 秒
- `L0090`：左转 90°
- `R0045`：右转 45°
- `S` / `STOP`：停止
- `STATUS` / `CONFIG`：查询状态或配置
- `Dxxxx`、`Axxxx`：ACK 但不影响位置

## 3.4 关键参数（脚本内常量）

- `PORT`、`BAUD`
- `GPS_PERIOD_S`：GPS 回传周期
- `DT_S`：积分步长
- `SPEED_FWD_MPS`、`SPEED_LAT_MPS`、`TURN_RATE_DPS`

## 3.5 观测点

- 工具侧输出：`[RX]`、`[GPS]`
- 上位机日志重点看：`PATROL suggest`、`UART tx`、`GPS state`

---

# 4. mock_COM11.py（航点回放仿真）

## 4.1 功能

按 `WAYPOINTS` 做线性插值并周期发送 GPS：

> 命令会得到 ACK，但不会改变轨迹

适合只验证 GPS 流、日志与状态机基本链路。

## 4.2 运行

```bash
python -m tool.mock_COM11
```

## 4.3 可配置项（脚本内常量）

- `PORT`、`BAUD`
- `WAYPOINTS`
- `GPS_PERIOD_S`
- `SEGMENT_DURATION_S`
- `LOOP`

---

# 5. test_process_detector.py（检测子进程独立测试）

## 5.1 功能

独立验证 `src/services/process_detector.py`：

- 是否能启动检测可执行文件
- 是否能通过 `poll()` 读到消息

## 5.2 运行

```bash
python tool/test_process_detector.py
```

## 5.3 重要注意

当前脚本里 `exec_path` 是硬编码 Windows 路径：

```python
exec_path = "D:\\PI_CAR\\detector_cpp\\build\\Release\\detector_ncnn.exe"
```

在以下情况需要先修改该变量：

- 仓库路径不是 `D:\PI_CAR`
- 在 Linux/Pi 上运行（应改成无后缀可执行文件路径）

此外脚本默认 `args=[]`，若检测器需要模型参数，请补上参数后再测。

---

# 6. zip_tool.py（打包工具）

## 6.1 功能

命令行调用 uploader 的打包逻辑：

- `build_zip_for_data()`：打包默认 `data/`
- `build_zip()`：打包指定根目录

## 6.2 常用命令

### 打包默认 data 目录

```bash
python -m tool.zip_tool --use-data --task-id TEST_001
```

### 打包指定目录

```bash
python -m tool.zip_tool --root data/TASK_001 --task-id TASK_001
```

### 指定输出目录

```bash
python -m tool.zip_tool --root data/TASK_001 --task-id TASK_001 --out zips
```

### 带 include / exclude / meta

```bash
python -m tool.zip_tool \
  --use-data \
  --task-id TEST_001 \
  --include *.jpg *.json \
  --exclude logs_de logs_be __pycache__ \
  --meta source=manual mode=debug
```

## 6.3 退出码与输出

- 成功：退出码 `0`，打印 `ZIP_OK`
- 失败：退出码 `1`，打印 `ZIP_FAIL: ...`

---

# 7. 推荐联调顺序

## 7.1 PC 无实车（推荐）

1. 检测链路：`python tool/test_process_detector.py`
2. GPS 回放：`python -m tool.mock_COM11`
3. 闭环仿真：`python -m tool.mock_COM11_plus`
4. 启动主程序：`python -m src.main`

## 7.2 Pi 实机

1. 检测进程联调：`python tool/test_process_detector.py`
2. 主程序联调：`python -m src.main`
3. systemd 运行后使用：

```bash
systemctl status patrol.service
journalctl -u patrol.service -f
```

配置改动后重载：

```bash
sudo systemctl daemon-reload
sudo systemctl restart patrol.service
```

---

# 8. 常见问题

## Q1: mock 启动后收不到命令

- 检查串口对是否正确（如 `COM10 <-> COM11`）
- 检查主程序 `uart.port` 是否与 mock 另一端对应
- 检查波特率双方是否一致（默认 `115200`）

## Q2: `test_process_detector.py` 无输出

- 先确认 `exec_path` 存在
- 检查可执行文件是否需要模型参数
- 直接在终端手动运行检测器观察 `stdout/stderr`

## Q3: `zip_tool.py` 报 root 不存在

- 指定 `--root` 时必须确保目录真实存在
- 或使用 `--use-data` 直接打包默认数据目录

---

# 9. 相关文档

- 快速上手：`docs/start.md`
- 配置说明：`docs/settings.md`
- 日志规范：`docs/logger.md`
- 故障排查：`docs/troubleshooting.md`
- 