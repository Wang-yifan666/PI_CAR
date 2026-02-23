# 日志规范及含义解读

## 1. 日志输出通道

### 1.1 Detailed（详细日志）

* 目录：`data/logs_de/`
* 级别：`DEBUG+`
* 用途：排查问题、查看循环细节、串口收发、导航决策、检测细节等。

### 1.2 Brief（简略日志）

* 目录：`data/logs_be/`
* 过滤规则：

  * 允许 `level >= WARNING`
  * 或者 `level == INFO 且 brief == True`
* 用途：只保留关键事件（启动、平台检测、违规确认、打包、上传、退出等），避免刷屏。

### 1.3 Console（终端输出）

* 与 Brief 同步（同样过滤规则），用于运行时快速观察关键事件。

---

## 2. 日志行格式（固定）

每一行日志的固定格式为：

```text
%(asctime)s.%(msecs)03d - %(levelname)s - [%(source)s] - %(message)s
```

示例：

```text
2026-02-23 18:37:37.132 - WARNING - [INIT] - platform_detect | stage=import err=ModuleNotFoundError | result=fail reason=import_fail
```

---

## 3. message 的结构化规范（四段式）

`%(message)s` 必须采用**四段式结构**，段与段之间使用 `|` 分隔，段内字段使用空格分隔：

```text
<event> <action> | <key kv...> | result=<...> reason=<...> | cost_ms=<...> id_<name>=<value> ...
```

规则：

* `<action>` 可省略（没有动作就不写）。
* 四段中任何**为空的段落**都应省略。
* 禁止输出原始 `dict/list`（例如 `ids={...}`），应展开为 `k=v`。
* 统一使用 `snake_case` 字段名；单位写在字段名后缀中（如 `_ms/_s/_m/_deg/_mb`）。

示例（简报更常见）：

```text
process_exit | returncode=1 stderr_tail="model not found" | result=fail reason=proc_exit | id_run=8c12a3
```

---

## 4. 打日志的统一入口：log_event

项目中业务日志统一使用：

```python
log_event(
    logger,
    source="INIT",
    event="startup",
    action=None,
    key={...},
    result="ok",
    reason="booting",
    cost_ms=12.3,
    ids={"run": "8c12a3"},
    brief=False,
    level=logging.INFO,
)
```

### 4.1 brief 参数规则

* `brief=False`（默认）：不会进入 Brief/Console（除非 level>=WARNING）
* `brief=True`：允许 INFO 进入 Brief/Console
* 推荐：只有“关键事件”设置 `brief=True`，避免简报被填满。

---

## 5. source（来源）白名单

`[%(source)s]` 必须来自以下白名单之一：

* `INIT`：系统启动/配置/总流程/退出
* `UART`：串口通信
* `GPS`：GPS 数据与状态
* `PATROL`：巡航/导航逻辑
* `FSM`：状态机决策
* `DETECT`：检测与拍照留存
* `ZIP`：打包压缩
* `UPLOAD`：上传
* `PROCESS`：外部子进程管理（启动/退出/日志桥接）

禁止出现：

* 拼写错误（如 `DECTOR`）
* 未定义来源（如 `MISC`）
* 若出现第三方日志未带 source，会被视为为 `UNKNOWN`

---

## 6. level（重要级）使用规范

* `DEBUG`：循环细节、每帧检测、串口 RX/TX 细节、内部变量（只进 Detailed）
* `INFO`：关键事件（通常配合 `brief=True` 才进入简报）
* `WARNING`：可恢复异常/退化（自动进入 Brief）
* `ERROR`：不可恢复错误或关键组件退出（自动进入 Brief）
* `CRITICAL`：致命错误（极少使用）

---

## 7. 字段含义词典（每个“词/字段”的含义）

下面解释日志中常见每个词（字段）代表什么。

### 7.1 固定头部字段（由 formatter 生成）

* `asctime`：日志时间（本地时间）
* `msecs`：毫秒部分
* `levelname`：日志等级（DEBUG/INFO/WARNING/ERROR/CRITICAL）
* `source`：日志来源模块（INIT/UART/...）
* `message`：业务结构化消息（四段式）

### 7.2 message 第一段：event / action

* `event`：事件名（发生了什么），必须是可枚举、短语化的字符串
  例：`startup`, `platform_detect`, `connect`, `violation_confirm`, `zip_create`, `upload_done`, `process_exit`
* `action`：对 event 的补充动作（可选）
  例：`begin`, `ok`, `start`, `finish`, `mark_invalid`

**约定：**

* 不要在 event/action 里写大段自然语言。
* event/action 不应重复表达 result（避免“fail + result=fail”重复）。

### 7.3 message 第二段：key（普通参数）

`key` 段是 event 的核心参数，要求：

* 使用 `k=v` 展开
* 字段用 `snake_case`
* 单位写在字段名中

常见 key 字段示例：

* UART：`port=COM10 baudrate=115200 timeout_s=1.0 cmd_timeout_s=2.0 cmd=F0002`
* GPS：`lat=31.123456 lon=121.654321 stale_s=2.5 hdop=0.9`
* PATROL：`wp_idx=1 dist_m=2.4 bearing_deg=87.5 heading_deg=85.0`
* DETECT：`cls=charge conf=0.86 frames=8 path=...`
* ZIP：`files=12 zip_size_bytes=1048576 elapsed_s=1.8 zip_path=...`
* UPLOAD：`http=200 size_mb=37.1 retry=2/5`

### 7.4 message 第三段：result / reason

* `result`：结果状态，建议固定枚举（推荐）

  * `ok`：成功
  * `fail`：失败
  * `degraded`：退化（可运行但能力下降，如 GPS stale）
  * `skip`：跳过（满足条件不执行、去重等）
* `reason`：原因（可枚举短语），用于解释 result 或决策原因
  例：`import_fail`, `stale_timeout`, `threshold_pass`, `proc_exit`, `cmd_dedup`, `main_exit`, `keyboard_interrupt`

### 7.5 message 第四段：cost_ms / ids

* `cost_ms`：耗时（毫秒），用于衡量动作性能
  例：`cost_ms=12.3`
* `id_<name>`：追踪 ID，用于把一系列日志串起来（只用于“标识符”，不要用于普通参数）
  常见：

  * `id_run`：一次程序运行 ID
  * `id_mission`：一次任务/巡逻 ID
  * `id_detect`：一次违规事件 ID
  * `id_upload`：一次上传批次 ID
  * `id_frame`：帧序号（建议仅 detailed）

---

## 8. Brief 白名单建议（哪些事件值得进简报）

建议这些事件使用 `brief=True`（INFO 进入 Brief）：

* INIT：`platform_detect`, `startup`, `stop`
* DETECT：`violation_confirm`, `snapshot_saved`, `process_exit`
* ZIP：`zip_create`（建议只让 finish 进入 brief）
* UPLOAD：`upload_done`, `upload_fail`

其他高频或初始化类事件默认 `brief=False`。

## 9. Glossary：日志英文单词/缩写中文含义

本节用于解释日志中出现的英文单词、缩写与常见字段值，便于快速阅读与沟通。

### 9.1 通用字段与结构词

| 英文/缩写 | 中文含义 | 说明/示例 |
|---|---|---|
| timestamp / time / asctime | 时间戳/时间 | 日志行开头时间 |
| ms / msecs | 毫秒 | `...16:09:29.549` 的 `.549` |
| level / levelname | 日志等级 | DEBUG/INFO/WARNING/ERROR/CRITICAL |
| source | 来源模块 | `[INIT]`、`[UART]` 等 |
| message | 消息正文 | 四段式结构化 message |
| event | 事件名 | 发生了什么（可枚举） |
| action | 动作/阶段 | event 的子动作，如 `start/finish` |
| key | 参数字段 | 普通参数的 `k=v` 列表 |
| result | 结果 | `ok/fail/degraded/skip` |
| reason | 原因 | `import_fail/proc_exit/...` |
| cost_ms | 耗时（毫秒） | 性能指标 |
| id_XXX | 追踪ID | `id_run/id_detect/id_upload` 等 |

### 9.2 日志等级（Level）

| 英文 | 中文含义 | 用途 |
|---|---|---|
| DEBUG | 调试 | 高频细节，仅详细日志 |
| INFO | 信息 | 关键事件（通常需 `brief=true` 才进简报） |
| WARNING / WARN | 警告 | 可恢复异常/退化 |
| ERROR | 错误 | 不可恢复错误/关键失败 |
| CRITICAL | 严重错误 | 致命错误  |

### 9.3 常见结果值（result）

| 英文 | 中文含义 | 说明 |
|---|---|---|
| ok | 成功 | 动作成功完成 |
| fail | 失败 | 动作失败/异常 |
| degraded | 退化 | 能运行但能力下降（例如 GPS 过期） |
| skip | 跳过 | 被去重、条件不满足等 |

### 9.4 常见原因值（reason）

| 英文/缩写 | 中文含义 | 场景 |
|---|---|---|
| import_ok | 导入成功 | 平台检测、模块加载 |
| import_fail | 导入失败 | `picamera2` 不存在等 |
| create_ok | 创建成功 | `Picamera2()` 创建成功 |
| create_fail | 创建失败 | 相机驱动/权限/硬件问题 |
| keyboard_interrupt | 键盘中断 | Ctrl+C 或外部中断事件 |
| main_exit | 主程序退出 | 正常收尾退出 |
| fatal_error | 致命错误 | 需要立即停机的错误 |
| proc_exit | 子进程退出 | detector 子进程退出/崩溃 |
| timeout | 超时 | 串口/等待/请求超时 |
| stale / stale_timeout | 数据过期/过期超时 | GPS/巡航数据过期 |
| threshold_pass | 阈值通过 | 违规确认条件满足 |
| cmd_dedup | 指令去重 | 短时间内重复指令不重复发送 |
| http_error | HTTP错误 | 上传失败 |
| http_4xx / http_5xx | 客户端/服务端错误 | 上传/接口调用 |

### 9.5 source 模块（来源）解释

| source | 中文含义 | 说明 |
|---|---|---|
| INIT | 初始化/主流程 | 启动、平台检测、最终停机 |
| UART | 串口通信 | 与 STM32 通信 |
| GPS | 定位模块 | 读取/解析 GPS 数据 |
| PATROL | 巡航/导航 | 航点逻辑、运动控制决策 |
| FSM | 状态机 | 系统状态与输出控制 |
| DETECT | 检测模块 | YOLO/违规判定/拍照 |
| ZIP | 打包模块 | 文件压缩打包 |
| UPLOAD | 上传模块 | 上传 zip 到服务器 |
| PROCESS | 子进程管理 | detector_stub.exe 的启动/退出/输出桥接 |

### 9.6 检测/视觉常见词

| 英文/缩写 | 中文含义 | 说明 |
|---|---|---|
| detect / detector | 检测/检测器 | 目标检测模块 |
| inference | 推理 | 模型前向推理 |
| model | 模型 | YOLOv5/NCNN 模型等 |
| conf | 置信度 | confidence，0~1 |
| cls | 类别 | class |
| label | 标签 | 检测到的类别名 |
| bbox | 边界框 | bounding box |
| frame | 帧 | 摄像头/视频一帧 |
| snapshot | 快照/拍照 | 留存图片 |
| violation | 违规 | 违规充电事件 |
| threshold | 阈值 | 判定阈值 |
| area_norm | 归一化面积 | 面积归一化指标 |
| dist_norm | 归一化距离 | 距离归一化指标 |

### 9.7 导航/控制常见词

| 英文/缩写 | 中文含义 | 说明 |
|---|---|---|
| waypoint / wp | 航点 | 巡航目标点 |
| wp_idx | 航点序号 | 第几个航点 |
| dist_m | 距离（米） | distance meters |
| bearing_deg / brng_tgt | 目标方位角（度） | bearing |
| heading_deg / heading | 当前航向（度） | heading |
| speed_mps | 速度（米/秒） | speed |
| arrive_radius_m | 到达半径（米） | 进入该半径算到达 |

### 9.8 串口/进程/系统常见词

| 英文/缩写 | 中文含义 | 说明 |
|---|---|---|
| port | 端口 | `COM10` 等 |
| baudrate | 波特率 | 115200 |
| rx | 接收 | receive |
| tx | 发送 | transmit |
| cmd | 指令 | 发给下位机的命令 |
| process | 进程 | 外部 detector 进程 |
| returncode / rc | 返回码 | 子进程退出码 |
| stderr / stdout | 标准错误/标准输出 | 子进程输出 |
| uptime_s | 运行时长（秒） | 进程运行时间 |
| restart | 重启 | 进程重启 |
| thread | 线程 | 多线程服务 |

### 9.9 打包/上传常见词

| 英文/缩写 | 中文含义 | 说明 |
|---|---|---|
| zip_create | 创建压缩包 | zip 打包动作 |
| files | 文件数量 | 打包的文件数量 |
| zip_size_bytes | 压缩包大小（字节） | 大小字段 |
| upload | 上传 | 上传到服务器 |
| upload_done | 上传完成 | 成功结束 |
| upload_fail | 上传失败 | 失败结束 |
| retry | 重试 | `retry=2/5` |

### 9.10 单位后缀约定

| 后缀 | 中文含义 | 示例 |
|---|---|---|
| _ms | 毫秒 | `cost_ms=12.3` |
| _s | 秒 | `elapsed_s=1.8` |
| _m | 米 | `dist_m=2.4` |
| _deg | 度 | `heading_deg=85.0` |
| _mb | 兆字节 | `size_mb=37.1` |
| _bytes | 字节 | `zip_size_bytes=445675` |
| _mps | 米/秒 | `speed_mps=0.6` |

> 若你在日志里看到未收录的新单词/缩写，请把对应日志行贴出来，我们将该词补充到本表中。

---
