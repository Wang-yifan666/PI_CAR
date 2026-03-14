# 串口仿真与检测调试工具说明

本文档介绍项目中用于**下位机串口仿真**与**检测子进程联调**的三个工具脚本。这些工具主要用于在没有真实硬件环境时进行系统闭环测试与模块验证。

---

# 一、mock_COM11_plus.py

## 串口闭环运动学仿真器

### 1. 功能概述

该工具用于模拟 STM32 下位机行为，实现：

> 上位机发送控制命令 → 下位机执行运动 → 下位机回传更新后的 GPS → 上位机继续决策

属于**闭环仿真工具**。

---

### 2. 工作机制

* 串口监听来自上位机的控制命令：

  * `F0004`：前进 4 秒
  * `L0090`：左转 90°
  * `R0045`：右转 45°
  * `S / STOP`：停止
* 使用简化运动学模型进行计算：

  * 前进速度：`vx`
  * 横移速度：`vy`
  * 角速度：`omega`
* 以固定时间步长 `DT_S` 更新位置与航向
* 周期性发送：

  ```
  GPS,lat,lon
  ```
* 支持 `STATUS` / `CONFIG` 查询

---

### 3. 适用场景

* 验证 Patrol 巡航算法
* 验证 TURN → GO 切换逻辑
* 验证航向计算是否稳定
* 排查“上位机发指令但位置无变化”的问题
* 进行完整闭环测试

---

### 4. 典型调试流程

1. 运行 mock：

   ```
   python mock_COM11_plus.py
   ```
2. 启动主程序
3. 观察：

   * `[RX]` 输出（收到的命令）
   * `[GPS]` 输出（位置变化）
4. 查看上位机日志中的：

   * `PATROL suggest`
   * `UART tx`
   * `GPS state`

---

# 二、mock_COM11.py

## 航点回放型 GPS 仿真器

### 1. 功能概述

该工具用于模拟：

> 下位机周期性上报 GPS，但不受上位机运动命令影响

适用于仅测试 GPS 数据流的情况。

---

### 2. 工作机制

* 预设航点数组 `WAYPOINTS`
* 在相邻航点之间做线性插值
* 每隔 `GPS_PERIOD_S` 发送一条 GPS 数据：

  ```
  GPS,lat,lon
  ```
* 上位机发来的命令仅做 ACK，不改变运动轨迹

---

### 3. 适用场景

* 测试 GPSService 是否正确解析数据
* 测试 Patrol 是否按预设路线运行
* 快速回放固定轨迹
* 验证日志系统是否正常

---

# 三、test_process_detector.py

## 检测子进程调试工具

### 1. 功能概述

该工具用于独立测试：

```
ProcessDetector
```

模块是否能正确启动外部检测进程并接收其输出。

---

### 2. 工作机制

* 启动 `detector_ncnn.exe`（Windows）或 `detector_ncnn`（Linux / Pi）
* 调用：

  ```
  det.poll(timeout=0.5)
  ```
* 持续读取检测输出
* 运行 5 秒后停止

---

### 3. 适用场景

* 检查 detector 可执行程序是否正常启动
* 验证 JSON 输出是否正确
* 排查进程异常退出问题
* 单独联调检测模块（无需运行整套系统）

> 说明：`detector_ncnn.exe` 通常需要 `--param/--bin/...` 参数。
> 若仅做“进程拉起/退出链路”联调，可替换为 `detector_stub` 或在脚本中补全参数。

> Linux / Pi 场景通常使用无后缀可执行文件：`detector_ncnn`。

---

# 四、推荐使用顺序

在开发阶段建议按以下顺序测试：

### ① 测试检测模块

```
python tool/test_process_detector.py
```

### ② 测试 GPS 数据流

```
python tool/mock_COM11.py
```

### ③ 测试完整闭环

```
python tool/mock_COM11_plus.py
```

---

# 五、Pi 实机最小联调顺序

建议在树莓派上按“先单模块，再整机”方式联调：

### 1 检测进程联调

```
python tool/test_process_detector.py
```

期望：持续读到 `[ NCNN ]{...}`。

### 2 串口/GPS 联调

```
python -m src.main
```

期望：UART 可发送命令、GPS 状态持续更新。

> 如需测试停机链路，可在串口侧发送 `M0004`，主程序会记录 `MODE stop_request` 并优雅退出。

### 3 巡逻闭环联调

确认 Patrol/FSM 日志稳定后，再开启完整任务。

---

# 六、systemd 配合调试

当项目配置为服务运行后，建议优先使用以下命令排障：

```bash
systemctl status patrol.service
journalctl -u patrol.service -f
```

若配置变更后未生效：

```bash
sudo systemctl daemon-reload
sudo systemctl restart patrol.service
```