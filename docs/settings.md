# 配置说明（settings.yaml / settings_cpp.yaml）

本文档说明当前配置键与推荐用法，内容以仓库现状为准。

## 加载规则

- Linux / Pi: config/settings.yaml -> config/settings_cpp.yaml
- Windows / PC: config/settings_cpp.yaml -> config/settings.yaml
- 环境变量覆盖: PICAR_CONFIG

```bash
export PICAR_CONFIG=config/settings.yaml
```

## uart

```yaml
uart:
  enable: true
  port: "/dev/serial/by-id/..."
  baudrate: 115200
  timeout: 1.0
  cmd_timeout: 2.0
  max_cmd_len: 64
  log_rx_line: true
  log_tx_cmd: true
  log_gps: true
```

说明：port 在 Linux 与 Windows 不同，务必改为本机实际串口。

## dector

```yaml
dector:
  enable: true
  backend: process          # 推荐
  model_path: models/onnx/best.onnx
  class_file: models/classes.txt
  conf_threshold: 0.60
  target_classes: [0, 1, 2]
```

### dector.violation

```yaml
violation:
  enable: true
  cooldown_s: 60.0
  ebike_class_id: 0
  fire_class_id: 1
  strip_class_id: 2
  ebike_min_area_norm: 0.1
  center_dist_norm: 0.4
```

### dector.process

Linux/Pi 示例：

```yaml
process:
  exec_path: detector_cpp/build/detector_ncnn
  args: []
```

Windows 示例：

```yaml
process:
  exec_path: detector_cpp/build/Release/detector_ncnn.exe
  args:
    - --param=models/ncnn/best.ncnn.param
    - --bin=models/ncnn/best.ncnn.bin
    - --classes=models/classes.txt
    - --source=video:data/test.mp4
```

## fsm

```yaml
fsm:
  enable: true
  default_mode: M0001
  rate_hz: 5
  lost_timeout_s: 0.8
  stop_cmd: S
  fire_enable: true
  fire_conf_threshold: 0.40
  return_to_base: true
```

模式码：

- M0001 巡逻
- M0002 展演
- M0004 停机

## gps

```yaml
gps:
  enable: true
  source: uart
  stale_timeout_s: 2.0
  log_every_s: 2.0
  precision_threshold_m: 5.0
  discard_limit: 3
```

## patrol

```yaml
patrol:
  enable: true
  loop: true
  arrive_radius_m: 3.0
  forward_sec: 4
  turn_threshold_deg: 10
  waypoints:
    - [22.540000, 113.934500]
    - [22.540000, 113.934700]
```

## uploader

```yaml
uploader:
  enable: true
  endpoint: "https://ultraman.tech/"
  zip_root: data
  zip_output_dir: zips
  marker_suffix: uploaded
  zip_enable: true
  zip_marker_suffix: .zipped
```

说明：若未接入真实上传服务，建议临时设置 enable: false，避免频繁重试。

## status_log

```yaml
status_log:
  enable: true
  mode: fast
  interval_s: null
```

## showcase

```yaml
showcase:
  repeat: true
  stop_cmd: S
  actions:
    - cmd: F0004
      duration: 2.1
```

## 调参建议

- 误报多：提高 dector.conf_threshold
- 导航抖动：增大 patrol.turn_threshold_deg 或 arrive_radius_m
- GPS 经常失效：适当放宽 gps.precision_threshold_m
