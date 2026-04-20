# 代码结构导读

本文档描述当前仓库的实际模块分层、线程职责和数据流。

## 目录分层

```text
src/
├── main.py                 # 启动与线程编排
├── global_ctx.py           # 全局上下文（队列+状态）
├── core/
│   ├── fsm.py              # 状态机仲裁
│   └── patrol_logic.py     # 巡逻建议生成
├── drivers/
│   ├── uart.py             # 串口通信
│   └── camera.py           # 图像采集
├── services/
│   ├── dector.py           # 检测主服务（onnx/process）
│   ├── process_detector.py # C++ 子进程桥接
│   ├── gps_service.py      # GPS 服务
│   ├── uploader.py         # 打包/上传
│   └── dector_parts/       # 检测子模块
├── mode/
│   └── showcase.py         # 展演模式
└── utils/
    └── logger.py           # 结构化日志
```

## 线程与服务

主线程在 src/main.py 中按配置启动服务线程，典型链路：

1. UART
2. GPS
3. Patrol
4. DETECT
5. FSM
6. Uploader
7. Showcase（仅展演模式）

## 关键上下文字段（ctx）

- ctx.uart_queue: 最终下发给 STM32 的命令
- ctx.dector_queue: 检测事件队列
- ctx.patrol_cmd_queue: 巡逻建议队列
- ctx.upload_queue: 上传任务队列
- ctx.pack_event: 触发打包事件
- ctx.system_stop_event: 全局停机信号

## 主流程（M0001 巡逻模式）

1. Detector 识别并投递事件到 ctx.dector_queue
2. Patrol 生成导航建议到 ctx.patrol_cmd_queue
3. FSM 按优先级仲裁（违规 > 巡逻 > 停止）
4. FSM 将最终命令写入 ctx.uart_queue
5. UART 线程实际发送命令到下位机
6. GPS 通过 UART 回传更新位置状态

## 模式分支

- M0001: PATROL（巡逻主流程）
- M0002: SHOWCASE（动作序列演示）
- M0004: STOP_REQUEST（请求停机并收尾）

## 数据落盘

- data/logs_de/: 详细日志
- data/logs_be/: 简报日志
- data/violations_YYYYMMDD/: 违规 JSON 与图片
- zips/: 打包输出（含 .zipped/.uploaded 标记）

## C++ 检测器

- 源码: detector_cpp/detector_ncnn.cpp
- 产物(Linux): detector_cpp/build/detector_ncnn
- 产物(Windows): detector_cpp/build/Release/detector_ncnn.exe

ProcessDetector 读取 stdout 的 [ NCNN ] JSON 行并转换为 Python 事件。
