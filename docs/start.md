# 快速上手与模块调用总览

本篇面向初次接触项目的同学，帮助快速理解主流程、各模块职责、调用关系与数据流。建议先通读，再按需展开阅读对应文件。

## 1. 启动主流程（src/main.py）
- 配置加载：优先读取环境变量 `PICAR_CONFIG`；未指定时按平台顺序合并加载（Pi: settings.yaml → settings_cpp.yaml，PC 反之），缺失项自动补齐。
- 日志初始化：configure_logging 生成详细/简报两类日志。
- 平台探测：尝试导入 `picamera2` 判定是否为 Pi。
- 模式选择：支持串口返回 `Mxxxx` 进行模式选择，等待 `_wait_mode_selection`。
- 线程启动顺序（按配置 enable）：UART → GPS → Patrol → DETECTOR → FSM → Uploader → Showcase。
- 退出：监听 `ctx.system_stop_event`，统一收尾并广播停止。

**模式速览（串口 Mxxxx）**
- `M0001`：巡逻模式（启动 GPS、Patrol、DETECT、FSM、Uploader）。
- `M0002`：展演模式（Showcase）。
- `M0004`：优雅停机（立即设置 stop event，跳过业务线程，直接进入收尾）。

## 2. 核心服务与线程职责
- UART 通信（src/drivers/uart.py）
  - 与 STM32 串口收发，解析行文本；回调 GPS 数据；命令发送带去重/超时控制。
- GPS 服务（src/services/gps_service.py）
  - 绑定 UART GPS 回调写入全局 `ctx`；定期检查超时 `stale_timeout_s`，标记无效。
- 视觉检测 DETECTOR（src/services/dector.py）
  - 数据源：Pi 相机 / PC 屏幕截取 / Mock。
  - 后端：`backend=process`（推荐，C++ ncnn 子进程）或 onnx；违规/火警判定基于 `violation` 配置，事件写入 `ctx.dector_queue`，违规可落盘存证。
- 检测子进程管理（src/services/process_detector.py）
  - 启动 `detector_ncnn(.exe)`，桥接 stdout `[ NCNN ]{json}` → 队列，stderr 留尾部日志；支持注入 OpenCV bin 到 PATH。
- 巡逻逻辑（src/core/patrol_logic.py）
  - Waypoints 折线路径，状态 TURN/GO；基于 GPS 计算距离/方位，产生命令建议放入 `ctx.patrol_cmd_queue`（不直接串口）。支持返航与基地触发打包。
- 状态机 FSM（src/core/fsm.py）
  - 仲裁优先级：违规 > 巡逻建议 > 停止；命令去重节流，真正写入 `ctx.uart_queue`。火警触发返航并打包。
- 打包与上传（src/services/uploader.py）
  - `build_zip_for_data()` 打包 data/ 并写 `.zipped` 标记；`UploadService` 读上传队列，成功写 `.uploaded` 标记。
- 展示模式（src/mode/showcase.py）
  - 预设动作序列循环发指令到 `ctx.uart_queue`，用于展演/自检。

## 3. 全局上下文与关键队列
- `ctx.config`：配置字典。
- `ctx.uart_queue`：FSM 写入的串口命令；UART pump 读取发送。
- `ctx.dector_queue`：视觉检测事件（违规/火警等）。
- `ctx.patrol_cmd_queue`：巡逻命令建议，FSM 读取仲裁。
- `ctx.upload_queue`：待上传 zip 任务。
- `ctx.pack_event`：返航/到基地触发打包信号。
- `ctx.system_stop_event`：全局停止信号。

## 4. 数据与日志流向
- 日志：详细日志 data/logs_de/；简报日志 data/logs_be/；格式/规范详见 docs/logger.md。
- 违规留存：data/violations_YYYYMMDD/violation_*.json/.jpg。
- 打包输出：zips/（或配置 zip_output_dir），标记 `.zipped`/`.uploaded` 控制重复处理。

## 5. 配置要点（config/settings.yaml / settings_cpp.yaml）
- 环境变量 `PICAR_CONFIG` 可指定配置文件；Pi 实机建议 settings.yaml，PC 调试建议 settings_cpp.yaml。
- 关键段落：
  - `uart`: 端口/波特率/日志开关。
  - `dector`: backend=process 时配置 exec_path/args；阈值与违规规则；显示窗口开关。
  - `fsm`: 超时与仲裁命令（违规/停止），火警类别与阈值。
  - `patrol`: waypoints、到点半径、转向阈值、循环开关。
  - `uploader`: endpoint、超时、打包/标记规则。
  - `gps`: 数据源、超时、节流。

## 6. 开发与联调常用工具（tool/）
- mock_COM11_plus.py：串口闭环运动学仿真（命令→运动→GPS 回传）。
- mock_COM11.py：GPS 回放仿真（不受命令影响）。
- test_process_detector.py：检测子进程独立拉起/读取测试。

## 7. 一句话调用链回顾
main → 配置/日志 → 线程启动 → DETECTOR 事件入 dector_queue → PATROL 产生命令建议入 patrol_cmd_queue → FSM 仲裁写 uart_queue → UART 下发 → GPS 回传 → 到基地触发打包 → Uploader 可选上传；Showcase 可独立循环演示。
