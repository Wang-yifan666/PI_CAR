import json
import logging
import os
import time
import uuid

import src.global_ctx as ctx
from src.utils.logger import log_event, sys_logger as logger


class DetectorRules:
    # 计算两个中心点在图像尺度上的归一化距离。
    def _calc_center_dist_norm(self, c1, c2, W, H):
        dx = float(c1[0]) - float(c2[0])
        dy = float(c1[1]) - float(c2[1])
        dist = (dx * dx + dy * dy) ** 0.5
        denom = float(min(W, H)) if min(W, H) > 0 else 1.0
        return dist / denom

    # 计算两个 xyxy 框的 IoU。
    def _calc_iou_xyxy(self, a, b):
        ax1, ay1, ax2, ay2 = map(float, a)
        bx1, by1, bx2, by2 = map(float, b)

        inter_x1 = max(ax1, bx1)
        inter_y1 = max(ay1, by1)
        inter_x2 = min(ax2, bx2)
        inter_y2 = min(ay2, by2)

        inter_w = max(0.0, inter_x2 - inter_x1)
        inter_h = max(0.0, inter_y2 - inter_y1)
        inter = inter_w * inter_h

        area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
        area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
        union = area_a + area_b - inter
        return inter / union if union > 0 else 0.0

    # 生成按面积和置信度排序的键。
    def _area_conf_key(self, det):
        return (det.get("area", 0), det.get("conf", 0.0))

    # 按电动车与隔离带邻近规则产出违章事件。
    def _check_violation_ebike_strip(self, dets, W, H):
        vcfg = ctx.config.get("dector", {}).get("violation", {})
        if not bool(vcfg.get("enable", False)):
            return None

        ebike_id = int(vcfg.get("ebike_class_id", 0))
        strip_id = int(vcfg.get("strip_class_id", 2))

        ebike_min_area_norm = float(vcfg.get("ebike_min_area_norm", 0.08))
        center_dist_norm_th = float(vcfg.get("center_dist_norm", 0.25))

        cooldown_s = float(vcfg.get("cooldown_s", 1.0))
        now = time.time()
        if (now - self._last_violation_ts) < cooldown_s:
            return None

        ebikes = [d for d in dets if int(d.get("class_id", -1)) == ebike_id]
        strips = [d for d in dets if int(d.get("class_id", -1)) == strip_id]
        if len(ebikes) == 0 or len(strips) == 0:
            return None

        near_ebikes = []
        for e in ebikes:
            area = float(e.get("area", 0))
            area_norm = area / float(W * H + 1.0)
            if area_norm >= ebike_min_area_norm:
                e["_area_norm"] = area_norm
                near_ebikes.append(e)

        if len(near_ebikes) == 0:
            return None

        best_pair = None
        best_score = -1.0

        for e in near_ebikes:
            for s in strips:
                dist_norm = self._calc_center_dist_norm(e.get("center", [0, 0]), s.get("center", [0, 0]), W, H)
                if dist_norm <= center_dist_norm_th:
                    conf_e = float(e.get("conf", 0.0))
                    conf_s = float(s.get("conf", 0.0))
                    score = (float(e.get("_area_norm", 0.0)) * 2.0) + (1.0 - dist_norm) + (conf_e + conf_s) * 0.5
                    if score > best_score:
                        best_score = score
                        best_pair = (e, s, dist_norm)

        if best_pair is None:
            return None

        e, s, dist_norm = best_pair
        self._last_violation_ts = now

        violation_ev = {
            "type": "violation",
            "rule": "ebike_with_strip_nearby",
            "ts": now,
            "img_size": [int(W), int(H)],
            "dist_norm": float(dist_norm),
            "ebike_area_norm": float(e.get("_area_norm", 0.0)),
            "ebike": {
                "class_id": int(e.get("class_id")),
                "class_name": e.get("class_name"),
                "conf": float(e.get("conf")),
                "bbox_xyxy": e.get("bbox_xyxy"),
                "center": e.get("center"),
                "area": int(e.get("area")),
            },
            "strip": {
                "class_id": int(s.get("class_id")),
                "class_name": s.get("class_name"),
                "conf": float(s.get("conf")),
                "bbox_xyxy": s.get("bbox_xyxy"),
                "center": s.get("center"),
                "area": int(s.get("area")),
            },
        }

        return violation_ev

    # 从当前检测中挑选最优火情目标。
    def _check_fire(self, dets):
        fire_cfg = ctx.config.get("dector", {}).get("violation", {})
        fire_id = int(fire_cfg.get("fire_class_id", 1))

        fires = [d for d in dets if int(d.get("class_id", -1)) == fire_id]
        if len(fires) == 0:
            return None

        fires.sort(key=self._area_conf_key, reverse=True)
        best = fires[0]

        return {
            "type": "fire",
            "rule": "fire_detected",
            "ts": float(best.get("ts", time.time())),
            "class_id": int(best.get("class_id", fire_id)),
            "class_name": best.get("class_name", ""),
            "conf": float(best.get("conf", 0.0)),
            "bbox_xyxy": best.get("bbox_xyxy"),
            "center": best.get("center"),
            "area": int(best.get("area", 0)),
            "img_size": best.get("img_size"),
        }

    # 将违章图片与元数据落盘到 data 目录。
    def _save_violation_to_data(self, violation_ev, img_bgr, draw_bgr=None):
        try:
            vcfg = ctx.config.get("dector", {}).get("violation", {})

            save_enable = bool(vcfg.get("save_enable", True))
            if not save_enable:
                return None

            save_dir_cfg = str(vcfg.get("save_dir", "data"))
            save_draw_img = bool(vcfg.get("save_draw_img", True))

            base_dir = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.abspath(os.path.join(base_dir, "../../../"))

            data_dir = os.path.join(project_root, save_dir_cfg)

            day_str = time.strftime("%Y%m%d", time.localtime())
            out_dir = os.path.join(data_dir, f"violations_{day_str}")
            os.makedirs(out_dir, exist_ok=True)

            ts_epoch = float(violation_ev.get("ts", time.time()))
            ts_str = time.strftime("%y%m%d_%H%M%S", time.localtime(ts_epoch))
            uid = uuid.uuid4().hex[:8]
            prefix = f"violation_{ts_str}_{uid}"

            img_path = os.path.join(out_dir, prefix + ".jpg")
            json_path = os.path.join(out_dir, prefix + ".json")

            save_img = draw_bgr if (save_draw_img and draw_bgr is not None) else img_bgr

            cv2_module = getattr(self, "_cv2", None)
            if cv2_module is None:
                log_event(
                    logger,
                    source="DETECT",
                    event="violation_save",
                    result="fail",
                    reason="cv2_unavailable",
                    level=logging.WARNING,
                    brief=False,
                )
                return None

            ok = cv2_module.imwrite(img_path, save_img)
            if not ok:
                log_event(
                    logger,
                    source="DETECT",
                    event="violation_save",
                    result="fail",
                    reason="image_write",
                    key={"img": img_path},
                    level=logging.WARNING,
                    brief=False,
                )
                return None

            gps = {}
            try:
                if hasattr(ctx, "get_gps_copy"):
                    gps = ctx.get_gps_copy() or {}
                else:
                    gps = getattr(ctx, "gps_state", {}) or {}
            except Exception:
                gps = {}

            gps_ok = bool(gps.get("ok", False))
            gps_lat = gps.get("lat", None)
            gps_lon = gps.get("lon", None)
            gps_ts = gps.get("ts", None)
            gps_src = gps.get("source", None)

            try:
                gps_lat = float(gps_lat) if gps_lat is not None else None
            except Exception:
                gps_lat = None
            try:
                gps_lon = float(gps_lon) if gps_lon is not None else None
            except Exception:
                gps_lon = None
            try:
                gps_ts = float(gps_ts) if gps_ts is not None else None
            except Exception:
                gps_ts = None

            meta = {
                "type": violation_ev.get("type", "violation"),
                "rule": violation_ev.get("rule", "unknown"),
                "ts": ts_str,
                "ts_epoch": ts_epoch,
                "time_local": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts_epoch)),
                "img_size": violation_ev.get("img_size", None),
                "dist_norm": violation_ev.get("dist_norm", None),
                "ebike_area_norm": violation_ev.get("ebike_area_norm", None),
                "ebike": violation_ev.get("ebike", {}),
                "strip": violation_ev.get("strip", {}),
                "gps": {
                    "ok": gps_ok,
                    "lat": gps_lat,
                    "lon": gps_lon,
                    "ts_epoch": gps_ts,
                    "source": gps_src,
                },
                "artifacts": {
                    "image": img_path,
                    "json": json_path,
                },
            }

            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)

            log_event(
                logger,
                source="DETECT",
                event="violation_save",
                result="ok",
                key={"img": img_path, "json": json_path},
                brief=False,
            )

            return meta["artifacts"]

        except Exception as e:
            log_event(
                logger,
                source="DETECT",
                event="violation_save",
                result="fail",
                reason=str(e),
                level=logging.ERROR,
                brief=False,
            )
            return None
