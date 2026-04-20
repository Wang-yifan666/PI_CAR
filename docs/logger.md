# 日志规范

本项目统一使用结构化日志入口 log_event，目标是可检索、可聚合、可追踪。

## 日志输出通道

- 详细日志: data/logs_de/
- 简报日志: data/logs_be/
- 控制台: 与简报过滤规则一致

## 行格式

```text
%(asctime)s.%(msecs)03d - %(levelname)s - [%(source)s] - %(message)s
```

## message 结构

推荐四段式（空段省略）：

```text
<event> <action> | <key kv...> | result=<...> reason=<...> | cost_ms=<...> id_<name>=<...>
```

示例：

```text
process_exit finish | returncode=0 lines=152 | result=ok | cost_ms=45.3 id_run=9fa2
```

## log_event 字段约定

- source: 来源模块
- event: 事件名（必填）
- action: 动作（可选）
- key: 结构化参数字典（可选）
- result: 结果（ok/fail/degraded/skip 等）
- reason: 原因（可选）
- cost_ms: 耗时（可选）
- ids: 追踪 ID（可选）
- brief: 是否允许 INFO 进入简报

## source 白名单（当前代码）

- INIT
- MODE
- UART
- GPS
- PATROL
- FSM
- DETECT
- PROCESS
- ZIP
- UPLOAD
- SHOWCASE

说明：文档中统一使用当前代码里的 source 名称，避免历史拼写混用。

## 级别建议

- DEBUG: 高频循环细节
- INFO: 关键流程节点
- WARNING: 可恢复异常
- ERROR: 功能失败
- CRITICAL: 致命错误

## brief 使用建议

- 关键业务事件可设置 brief=true（便于现场观察）
- 高频循环日志保持 brief=false
- WARNING/ERROR 会自动进入简报

## 常见检索命令

```bash
# 查看最新简报
ls -lt data/logs_be | head

# 过滤 mode 相关
grep -R "\[MODE\]" data/logs_de -n | tail -n 50

# 过滤上传失败
grep -R "\[UPLOAD\].*result=fail" data/logs_de -n
```

## 命名建议

- 字段使用 snake_case
- 含单位字段带后缀：_ms, _s, _m, _deg, _bytes
- 不直接打原始大对象，优先拆成关键字段
