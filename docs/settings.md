# 系统配置文件说明（settings_cpp.yaml / settings.yaml）

本文档详细说明配置文件中各项参数的含义、作用及推荐使用方式。

---

# 配置加载优先级

主程序 `src/main.py` 启动时按平台加载：

1. Linux / 树莓派：`config/settings.yaml` → `config/settings_cpp.yaml`
2. Windows / PC：`config/settings_cpp.yaml` → `config/settings.yaml`

也可通过环境变量 `PICAR_CONFIG` 强制指定配置文件路径（支持相对项目根目录路径或绝对路径）。

推荐分工：

* `config/settings.yaml`：Linux / 树莓派实车主配置
* `config/settings_cpp.yaml`：Windows / PC 调试配置（视频回放、硬件线程可关闭）

---

# 1. UART 串口配置

```yaml
uart:
```

用于配置上位机与 STM32 下位机之间的串口通信。

## 参数说明

| 参数           | 说明                                        |
| ------------ | ----------------------------------------- |
| enable       | 是否启用串口通信                                  |
| port         | 串口号（Windows 如 COM10，Linux 如 /dev/ttyUSB0） |
| baudrate     | 波特率                                       |
| timeout      | 串口读取超时时间                                  |
| cmd_timeout  | 等待 OK/ERR 返回的最大时间                         |
| max_cmd_len  | 单条指令最大长度                                  |
| loop_sleep_s | 接收线程循环休眠时间                                |
| cpu_sleep_s  | 无数据时的休眠时间                                 |
| log_rx_line  | 是否打印接收到的原始串口数据                            |
| log_tx_cmd   | 是否打印发送命令                                  |
| log_gps      | 是否打印 GPS 数据                               |

---

# 2. 视觉检测配置（dector）

```yaml
dector:
```

用于配置目标检测模块。

## 基础参数

| 参数             | 说明                          |
| -------------- | --------------------------- |
| model_path     | ONNX 模型路径（仅 onnx 后端使用）         |
| class_file     | 类别文件                        |
| conf_threshold | 置信度阈值                       |
| target_classes | 需要检测的类别 ID                  |
| show_window    | 是否显示检测窗口                    |
| backend        | 推理后端（推荐 `process`，由 C++ ncnn 可执行程序完成检测） |

---

## 违规判定（violation）

```yaml
violation:
```

用于定义违规行为判断逻辑。

| 参数                  | 说明         |
| ------------------- | ---------- |
| enable              | 是否启用违规判定   |
| cooldown_s          | 同类违规事件冷却时间 |
| ebike_class_id      | 电动车类别ID    |
| fire_class_id       | 明火类别ID     |
| strip_class_id      | 裸露电线类别ID   |
| ebike_min_area_norm | 最小面积比例     |
| center_dist_norm    | 中心点距离阈值    |

违规判定逻辑：
只要满足面积比例或中心距离条件之一，即认为违规。

火灾检测：
使用 `fire_class_id` 作为火焰类别标识，FSM 会结合 `fire_cls` / `fire_conf_threshold` 监听该类别并触发火警返航。

---

## 日志去重（log_dedup）

防止同一目标重复标记。

| 参数               | 说明              |
| ---------------- | --------------- |
| same_obj_px_th   | 中心点移动小于该值视为同一目标 |
| same_obj_time_th | 时间窗口内认为同一物体     |
| same_obj_iou_th  | IoU 超过阈值认为同一目标  |

---

## 进程后端配置

```yaml
process:
```

用于 process 模式下调用外部检测程序。

| 参数        | 说明      |
| --------- | ------- |
| exec_path | 可执行文件路径 |
| args      | 启动参数    |
| opencv_bin | （可选）OpenCV bin 目录，Windows 用于补充 PATH |

常见配置示例（对应当前仓库）：

```yaml
dector:
	backend: process
	process:
		exec_path: detector_cpp/build/Release/detector_ncnn.exe
		args:
			- --param=models/ncnn/best.ncnn.param
			- --bin=models/ncnn/best.ncnn.bin
			- --classes=models/classes.txt
			- --source=video:data/test.mp4
			- --imgsz=640
			- --threads=4
			- --conf=0.8
			- --nms=0.45
			- --out=out0
			- --debug=0
			- --topk=20
```

树莓派常见配置示例（process + ncnn + CSI）：

```yaml
dector:
  backend: process
  process:
    exec_path: detector_cpp/build/Release/detector_ncnn
    args:
      - --param=models/ncnn/best.ncnn.param
      - --bin=models/ncnn/best.ncnn.bin
      - --classes=models/classes.txt
      - --source=camera:0
      - --imgsz=640
      - --threads=4
      - --conf=0.8
      - --nms=0.45
      - --out=out0
      - --debug=0
      - --topk=20
```

> 说明：Windows 常见可执行文件为 `.exe`，Linux / Pi 常见为无后缀可执行文件。

---

# 3. FSM 状态机配置

```yaml
fsm:
```

控制整体运动策略与目标跟踪。

| 参数                 | 说明       |
| ------------------ | -------- |
| hold_after_lost_s  | 违规事件消失后保持抢占的时间 |
| patrol_stale_s     | 巡逻建议过期时间 |
| cmd_dedup_s        | 相同命令去重时间窗口 |
| violation_cmd      | 违规时下发命令 |
| log_every_s        | FSM 状态日志节流 |
| stop_cmd           | 停止命令     |
| fire_enable        | 是否启用火灾检测返航 |
| fire_cls           | 火灾类别ID（默认跟随 violation.fire_class_id） |
| fire_conf_threshold | 火灾触发置信度（默认跟随 dector.conf_threshold） |

> 当前实现以“违规抢占 > 巡逻建议 > 停止”进行仲裁。

火灾检测行为：
* 视觉检测到类别 `fire_cls`（或事件类型为 `fire`）且置信度不低于 `fire_conf_threshold` 时，FSM 仅触发一次火警事件。
* 触发后会调用 `set_mission(mode="FIRE_RETURN", return_to_base=True, fire_found_ts=...)`，强制返航并避免重复触发。

---

## 巡逻运动参数

| 参数             | 说明      |
| -------------- | ------- |
| turn_rate_dps  | 转向角速度估计（用于 TURN 结束等待） |
| heading_update_min_move_m | 只有位移超过阈值才更新航向，抑制 GPS 抖动 |
| log_every_s    | Patrol 日志节流 |

> `patrol_vx / patrol_wz / patrol_wz_freq` 为历史参数，当前 patrol 逻辑不直接读取。

---

# 4. 上传模块（uploader）

```yaml
uploader:
```

用于违规数据上传。

| 参数                   | 说明       |
| -------------------- | -------- |
| enable               | 是否启用上传   |
| endpoint             | 服务器地址    |
| timeout_s            | 上传超时时间（秒） |
| retry_s              | 失败重试间隔（秒） |
| zip_root             | 打包根目录    |
| zip_output_dir       | zip 输出目录 |
| marker_suffix        | 上传标记（写入 zip_path.marker_suffix） |
| zip_enable           | 是否启用打包   |
| zip_marker_suffix    | 打包标记     |
| zip_include_patterns | 包含文件模式   |
| zip_exclude_dirs     | 排除目录     |

建议：若 `endpoint` 尚未配置真实服务地址，可先将 `enable` 设为 `false`，避免持续重试刷日志。

标记说明：

* `zip_marker_suffix`（默认 `.zipped`）表示“已完成打包”
* `marker_suffix`（默认 `uploaded`）表示“已完成上传”

---

# 5. GPS 配置

```yaml
gps:
```

| 参数              | 说明                    |
| --------------- | --------------------- |
| enable          | 是否启用 GPS              |
| mode            | replay 表示使用模拟         |
| source          | 数据来源（uart 表示来自 STM32） |
| stale_timeout_s | GPS 超时判定              |
| log_every_s     | GPS 日志打印间隔            |

---

# 6. 巡逻模块配置

```yaml
patrol:
```

| 参数                 | 说明     |
| ------------------ | ------ |
| enable             | 是否启用巡逻 |
| loop               | 是否循环   |
| arrive_radius_m    | 到点判定半径 |
| forward_sec        | 每次前进时间 |
| turn_threshold_deg | 偏航阈值   |
| waypoints          | 巡逻路径点  |

示例路径为矩形路径。

---

# 7. 数据打包配置

```yaml
data_pack:
```

| 参数         | 说明       |
| ---------- | -------- |
| enable     | 是否启用数据打包 |
| base_index | 基地索引     |
| data_dir   | 数据目录     |
| out_dir    | 输出目录     |
| prefix     | 打包文件前缀   |

---

# 参数调优建议

### 导航不稳定

* 调大 `turn_threshold_deg`
* 增加 `arrive_radius_m`

### 航向抖动

* 增加 `heading_update_min_move_m`
* 降低 GPS 输出频率

### 检测误报多

* 提高 `conf_threshold`
* 调整 `center_dist_norm`

---

# 总结

`settings_cpp.yaml / settings.yaml` 是系统行为的核心配置入口，涵盖：

* 串口通信
* 视觉检测
* 状态机控制
* 巡逻策略
* GPS 处理
* 数据上传
* 数据打包

修改配置无需改动代码即可调整系统行为。
