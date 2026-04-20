import os

import src.global_ctx as ctx


class DetectorStatus:
    # 重置状态统计窗口内的累计计数。
    def _status_reset_locked(self, now: float) -> None:
        self._status_window_start = now
        self._status_capture_frames = 0
        self._status_infer_frames = 0
        self._status_infer_time = 0.0
        self._status_violations = 0

    # 记录一帧采集计数。
    def _status_inc_capture(self) -> None:
        with self._status_lock:
            self._status_capture_frames += 1

    # 记录一帧推理及其耗时。
    def _status_inc_infer(self, cost_s: float) -> None:
        with self._status_lock:
            self._status_infer_frames += 1
            self._status_infer_time += max(0.0, float(cost_s))

    # 记录一次违章事件计数。
    def _status_inc_violation(self) -> None:
        with self._status_lock:
            self._status_violations += 1

    # 到达时间窗口时汇总并写入状态指标。
    def _status_maybe_log(self, now: float, *, backend: str) -> None:
        with self._status_lock:
            elapsed = now - self._status_window_start
            if elapsed < self._status_window_s:
                return

            cap = self._status_capture_frames
            inf = self._status_infer_frames
            t_sum = self._status_infer_time
            vio = self._status_violations
            window = elapsed if elapsed > 0 else self._status_window_s
            self._status_reset_locked(now)

        gps = {}
        try:
            if hasattr(ctx, "get_gps_copy"):
                gps = ctx.get_gps_copy() or {}
            else:
                gps = getattr(ctx, "gps_state", {}) or {}
        except Exception:
            gps = {}

        temp_c, cpu_pct = self._read_sys_metrics()

        fps_capture = cap / window if window > 0 else 0.0
        fps_infer = inf / window if window > 0 else 0.0
        cost_ms_avg = (t_sum / inf * 1000.0) if inf > 0 else None

        try:
            if hasattr(ctx, "set_status_metrics"):
                ctx.set_status_metrics(
                    "fps",
                    {
                        "window_s": window,
                        "frames_capture": cap,
                        "fps_capture": fps_capture,
                        "frames_infer": inf,
                        "fps_infer": fps_infer,
                        "cost_ms_avg": cost_ms_avg,
                        "violations": vio,
                        "backend": backend,
                        "source": getattr(self, "_source_type", "UNKNOWN"),
                        "cpu_pct": cpu_pct,
                        "temp_c": temp_c,
                    },
                )
        except Exception:
            pass

    # 读取温度与 CPU 负载等系统指标。
    def _read_sys_metrics(self):
        temp_c = None
        cpu_pct = None

        try:
            with open("/sys/class/thermal/thermal_zone0/temp", "r", encoding="utf-8") as f:
                raw = f.read().strip()
                if raw:
                    temp_c = float(raw) / 1000.0
        except Exception:
            temp_c = None

        try:
            import psutil  # type: ignore

            cpu_pct = psutil.cpu_percent(interval=None)
        except Exception:
            try:
                load1, _load5, _load15 = os.getloadavg()
                cpu_pct = load1
            except Exception:
                cpu_pct = None

        return temp_c, cpu_pct
