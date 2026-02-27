# 系统配置文件说明（settings_cpp.yaml / settings.yaml）

本文档详细说明配置文件中各项参数的含义、作用及推荐使用方式。

---

# 配置加载优先级

主程序 `src/main.py` 启动时按以下顺序加载：

1. `config/settings_cpp.yaml`
2. `config/settings.yaml`

即：若 `settings_cpp.yaml` 存在且非空，则优先使用它。

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

---

# 3. FSM 状态机配置

```yaml
fsm:
```

控制整体运动策略与目标跟踪。

| 参数                 | 说明       |
| ------------------ | -------- |
| rate_hz            | FSM 运行频率 |
| lost_timeout_s     | 目标丢失判定时间 |
| target_class_names | 允许追踪的类别  |
| stop_cmd           | 停止命令     |
| desired_area_norm  | 目标理想面积比例 |

---

## 巡逻运动参数

| 参数             | 说明      |
| -------------- | ------- |
| patrol_vx      | 巡逻前进速度  |
| patrol_wz      | 巡逻角速度   |
| patrol_wz_freq | 角速度变化频率 |

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
| zip_root             | 打包根目录    |
| zip_output_dir       | zip 输出目录 |
| marker_suffix        | 上传标记     |
| zip_enable           | 是否启用打包   |
| zip_marker_suffix    | 打包标记     |
| zip_include_patterns | 包含文件模式   |
| zip_exclude_dirs     | 排除目录     |

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
