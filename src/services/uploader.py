import os
import time
import json
import glob
import zipfile
import hashlib
import fnmatch
import logging
import threading
import queue

import requests

from datetime import datetime
from typing import Dict, Optional, List, Iterable, Tuple

import src.global_ctx as ctx
from src.utils.logger import sys_logger as logger, log_event

class zips:
    def __init__(self , root_path : str , task_id : str , meta: Optional[Dict[str, str]] = None) :
        self.root_path = root_path 
        self.task_id = task_id
        self.meta = meta or {}
        
    # 转化成dict    
    def _dict(self) -> dict : 
        
        return {
            "root_path" : self.root_path ,
            "task_id" : self.task_id ,
            "meta" : self.meta ,
        }
     
# 在目录不存在时创建   
def _mkdir(path : str ) -> None :
        os.makedirs(path , exist_ok=True)         

# 回到根目录
def _project_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# 绝对路径
def _resolve_dir(path: str) -> str:
    if os.path.isabs(path):
        return path
    return os.path.join(_project_root(), path)

# 创建时间戳    
def _now_time() -> str :
        date = datetime.now()
        return date.strftime("%Y%m%d_%H%M%S")
    
# 通过哈希算法计算
def _sha256_file(path : str , chunk_size : int = 1024 * 1024 ) -> str :
        h = hashlib.sha256() 
        
        # 修复：二进制读取，避免文本模式导致 hash 不一致/报错
        with open(path , "rb") as f : 
            while True :
                b = f.read(chunk_size)
                if not b : 
                    break 
                
                h.update(b)
                
        return h.hexdigest()
    
# 遍历每一个文件
def _iter_files(root: str,
                include_patterns: Optional[List[str]] = None,
                exclude_dirs: Optional[List[str]] = None) -> Iterable[str]:
    exclude_dirs = exclude_dirs or []
    root = os.path.abspath(root)

    # include_patterns统一成list
    patterns = None
    if include_patterns:
        patterns = [p.strip() for p in include_patterns if str(p).strip()]
        if not patterns:
            patterns = None

    for dir_path, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in exclude_dirs]

        for fn in filenames:
            full = os.path.join(dir_path, fn)

            if patterns is None:
                yield full
            else:
                # 按相对路径和文件名两种方式匹配
                rel = os.path.relpath(full, root).replace("\\", "/")
                if any(fnmatch.fnmatch(fn, p) or fnmatch.fnmatch(rel, p) for p in patterns):
                    yield full
                
# 生成显示文件
def _write_manifest(manifest_path: str, 
                        task: zips,
                        root: str,
                        file_rows: List[Tuple[str, int, str]]) -> None:
    payload = {
        "task" : task._dict() , 
        "root_abs" : os.path.abspath(root) ,
        "generated_at" : datetime.now().isoformat(timespec="seconds") ,
        "file_count" : len(file_rows) ,
        "files" : 
        [
            {
                "rel": rel, "size": size, "sha256": sha256
            }
            for (rel , size , sha256) in file_rows
        ],
    }
    
    with open(manifest_path , "w" , encoding="utf-8") as f :
        json.dump(payload, f, ensure_ascii=False, indent=2)

# 计算单个文件的大小是否超过限制
def _zip_payload_limit_bytes(cfg: Optional[Dict[str, object]] = None ) -> int :
    cfg = cfg or {}
    max_bytes = int( cfg.get("zip_max_bytes", 20 * 1024 * 1024) )  # 默认20MB
    overhead = int( cfg.get("zip_overhead_bytes", 256 * 1024) )   # 默认256KB的安全余量
    
    limit = max_bytes - overhead
    if limit <= 0:
        raise ValueError("zip payload limit must be > 0")     # 安全检查
    return limit
    
# 生成分卷的zip文件名
def _zip_part_name(task_id: str , part_no: int , fmt: str ) -> str :
    fmt = ( fmt or "{task_id}.part{part_no:03d}.zip" ).strip()
    
    return fmt.format( task_id = task_id , part_no = part_no )

# 根据文件列表和大小限制，规划分卷方案
def _plan_zip_parts(
    file_rows: List[Tuple[str, int, str]],
    limit_bytes: int,
    large_file_policy: str = "error",
) -> List[List[Tuple[str, int, str]]]:
    parts: List[List[Tuple[str, int, str]]] = []
    current: List[Tuple[str, int, str]] = []
    current_size = 0

    for row in file_rows:
        rel, size, sha256 = row

        if size > limit_bytes:
            if large_file_policy == "skip":
                log_event(
                    logger,
                    source="ZIP",
                    event="zip_part_skip",
                    result="skip",
                    reason="single_file_oversize",
                    key={"file": rel, "size": size, "limit": limit_bytes},
                    level=logging.WARNING,
                )
                continue
            raise RuntimeError(f"single file oversize: {rel} size={size} limit={limit_bytes}")

        if current and (current_size + size > limit_bytes):
            parts.append(current)
            current = []
            current_size = 0

        current.append(row)
        current_size += size

    if current:
        parts.append(current)

    return parts

# 打包为若干个 zip 文件，并生成对应的 marker 文件和 manifest 文件
def build_zip(
    task: zips,
    zip_output_dir: Optional[str] = None,
    include_patterns: Optional[List[str]] = None,
    exclude_dirs: Optional[List[str]] = None,
    marker_suffix: str = ".zipped",
) -> List[str]:
    cfg = {}
    try:
        cfg = (ctx.config or {}).get("uploader", {})
    except Exception:
        cfg = {}

    zip_enable = bool(cfg.get("zip_enable", True))
    if not zip_enable:
        raise RuntimeError("zip_enable is false by config")

    # 输出目录
    conf_out = str(cfg.get("zip_output_dir", "zips")).strip() or "zips"
    if not zip_output_dir:
        zip_output_dir = conf_out

    # marker 后缀
    if marker_suffix == ".zipped":
        marker_suffix = str(cfg.get("zip_marker_suffix", ".zipped"))

    # include/exclude
    if include_patterns is None:
        include_patterns = cfg.get("zip_include_patterns") or None
    if exclude_dirs is None:
        exclude_dirs = cfg.get("zip_exclude_dirs") or []

    zip_split_enable = bool(cfg.get("zip_split_enable", True))
    zip_part_name_fmt = str(
        cfg.get("zip_part_name_fmt", "{task_id}.part{part_no:03d}.zip")
    ).strip() or "{task_id}.part{part_no:03d}.zip"
    zip_large_file_policy = str(cfg.get("zip_large_file_policy", "error")).strip().lower() or "error"

    t0 = time.time()
    root = os.path.abspath(task.root_path)

    # 将输出目录解析成“项目根目录下的绝对路径”
    zip_output_dir = _resolve_dir(zip_output_dir)
    _mkdir(zip_output_dir)

    log_event(
        logger,
        source="ZIP",
        event="zip_create",
        action="start",
        result="begin",
        key={"root": root},
        ids={"task": task.task_id},
        brief=False,
    )
    log_event(
        logger,
        source="ZIP",
        event="config",
        key={
            "include_patterns": include_patterns,
            "exclude_dirs": exclude_dirs,
            "zip_split_enable": zip_split_enable,
            "zip_part_name_fmt": zip_part_name_fmt,
            "zip_large_file_policy": zip_large_file_policy,
        },
        level=logging.DEBUG,
    )
    log_event(
        logger,
        source="ZIP",
        event="output",
        key={"output_dir": zip_output_dir},
        level=logging.DEBUG,
    )

    files = list(_iter_files(root, include_patterns=include_patterns, exclude_dirs=exclude_dirs))
    log_event(
        logger,
        source="ZIP",
        event="files_collected",
        key={"count": len(files)},
        level=logging.DEBUG,
    )

    file_rows: List[Tuple[str, int, str]] = []
    for p in files:
        rel = os.path.relpath(p, root).replace("\\", "/")
        try:
            size = os.path.getsize(p)
            sha256 = _sha256_file(p)
            file_rows.append((rel, size, sha256))
        except Exception as e:
            log_event(
                logger,
                source="ZIP",
                event="file_hash",
                result="fail",
                reason=str(e),
                key={"file": p},
                level=logging.ERROR,
            )

    limit_bytes = _zip_payload_limit_bytes(cfg)

    if zip_split_enable:
        parts = _plan_zip_parts(
            file_rows=file_rows,
            limit_bytes=limit_bytes,
            large_file_policy=zip_large_file_policy,
        )
    else:
        parts = [file_rows]

    log_event(
        logger,
        source="ZIP",
        event="zip_plan",
        result="ok",
        key={
            "parts": len(parts),
            "limit_bytes": limit_bytes,
            "split_enable": zip_split_enable,
        },
        ids={"task": task.task_id},
        brief=False,
    )

    zip_paths: List[str] = []
    part_count = len(parts)

    for idx, part_rows in enumerate(parts, start=1):
        zip_name = _zip_part_name(task.task_id, idx, zip_part_name_fmt)
        zip_path = os.path.join(zip_output_dir, zip_name)
        marker_path = zip_path + marker_suffix
        tmp_manifest = os.path.join(
            zip_output_dir,
            f".manifest_{task.task_id}_part{idx:03d}_{_now_time()}.json"
        )

        log_event(
            logger,
            source="ZIP",
            event="zip_part_open",
            result="begin",
            key={
                "part_no": idx,
                "part_count": part_count,
                "zip_path": zip_path,
            },
            ids={"task": task.task_id},
            level=logging.DEBUG,
        )

        try:
            part_meta = dict(task.meta or {})
            part_meta.update({
                "part_no": idx,
                "part_count": part_count,
            })
            part_task = zips(
                root_path=task.root_path,
                task_id=task.task_id,
                meta=part_meta,
            )

            _write_manifest(tmp_manifest, part_task, root, part_rows)
            log_event(
                logger,
                source="ZIP",
                event="manifest",
                result="ok",
                key={"tmp": tmp_manifest, "part_no": idx},
                level=logging.DEBUG,
            )

            with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                for rel, _, _ in part_rows:
                    p = os.path.join(root, rel.replace("/", os.sep))
                    arcname = rel
                    try:
                        zf.write(p, arcname=arcname)
                    except Exception as e:
                        log_event(
                            logger,
                            source="ZIP",
                            event="zip_write",
                            result="fail",
                            reason=str(e),
                            key={"file": p, "arcname": arcname, "part_no": idx},
                            level=logging.ERROR,
                        )

                # 每个分片都写一个 manifest.json
                zf.write(tmp_manifest, arcname="manifest.json")

            try:
                with open(marker_path, "w", encoding="utf-8") as f:
                    f.write(datetime.now().isoformat(timespec="seconds"))
                log_event(
                    logger,
                    source="ZIP",
                    event="marker",
                    result="ok",
                    key={"marker": marker_path, "part_no": idx},
                    level=logging.DEBUG,
                )
            except Exception as e:
                log_event(
                    logger,
                    source="ZIP",
                    event="marker",
                    result="fail",
                    reason=str(e),
                    key={"marker": marker_path, "part_no": idx},
                    level=logging.ERROR,
                )

            zip_size = os.path.getsize(zip_path) if os.path.exists(zip_path) else -1
            zip_paths.append(zip_path)

            log_event(
                logger,
                source="ZIP",
                event="zip_part_close",
                result="ok",
                key={
                    "part_no": idx,
                    "part_count": part_count,
                    "zip_path": zip_path,
                    "zip_size_bytes": zip_size,
                    "files": len(part_rows),
                },
                ids={"task": task.task_id},
                brief=True,
            )

        finally:
            try:
                if os.path.exists(tmp_manifest):
                    os.remove(tmp_manifest)
                    log_event(
                        logger,
                        source="ZIP",
                        event="manifest",
                        action="cleanup",
                        key={"tmp": tmp_manifest, "part_no": idx},
                        level=logging.DEBUG,
                    )
            except Exception:
                pass

    elapsed = time.time() - t0
    log_event(
        logger,
        source="ZIP",
        event="zip_create",
        action="finish",
        result="ok",
        key={
            "parts": len(zip_paths),
            "files": len(file_rows),
            "elapsed_s": round(elapsed, 2),
        },
        ids={"task": task.task_id},
        brief=True,
    )
    return zip_paths

# 快速函数
def build_zip_for_data(
    task_id: Optional[str] = None,
    meta: Optional[Dict[str, str]] = None,
    **kwargs
) -> List[str]:
    if task_id is None:
        task_id = f"DATA_{_now_time()}"
    task = zips(root_path="data", task_id=task_id, meta=meta or {})
    return build_zip(task, **kwargs)

# 将 zip 任务入队等待上传
def enqueue_upload(zip_path: str, meta: Optional[Dict[str, str]] = None) -> bool:
    payload = {
        "zip_path": zip_path,
        "meta": meta or {},
        "ts": time.time(),
    }

    try:
        if hasattr(ctx, "put_latest"):
            ok = ctx.put_latest(ctx.upload_queue, payload)
            if not ok:
                return False
        else:
            if ctx.upload_queue.full():
                ctx.upload_queue.get_nowait()
            ctx.upload_queue.put_nowait(payload)

        log_event(logger, source="UPLOAD", event="enqueue", result="ok", key={"zip": zip_path}, level=logging.DEBUG)
        return True
    except Exception as e:
        log_event(logger, source="UPLOAD", event="enqueue", result="fail", reason=str(e), key={"zip": zip_path}, level=logging.ERROR)
        return False


class UploadService(threading.Thread):
    def __init__(self, cfg: Optional[Dict[str, object]] = None):
        super().__init__()
        self.daemon = True
        self.cfg = cfg or {}
        self.ucfg = (self.cfg or {}).get("uploader", {}) or {}

        self.enable = bool(self.ucfg.get("enable", False))
        self.endpoint = str(self.ucfg.get("endpoint", "")).strip()
        self.timeout_s = float(self.ucfg.get("timeout_s", self.ucfg.get("timeout", 30)))
        self.retry_s = float(self.ucfg.get("retry_s", 5))
        self.marker_suffix = str(self.ucfg.get("marker_suffix", "uploaded"))

    def _marker_path(self, zip_path: str) -> str:
        suffix = self.marker_suffix or "uploaded"
        if suffix.startswith("."):
            return f"{zip_path}{suffix}"
        return f"{zip_path}.{suffix}"

    def _write_marker(self, zip_path: str) -> None:
        marker = self._marker_path(zip_path)
        try:
            with open(marker, "w", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()))
            log_event(logger, source="UPLOAD", event="marker", result="ok", key={"marker": marker}, level=logging.DEBUG)
        except Exception as e:
            log_event(logger, source="UPLOAD", event="marker", result="fail", reason=str(e), key={"marker": marker}, level=logging.ERROR)

    def _requeue(self, item: dict) -> None:
        try:
            if hasattr(ctx, "put_latest"):
                ctx.put_latest(ctx.upload_queue, item)
            else:
                if ctx.upload_queue.full():
                    ctx.upload_queue.get_nowait()
                ctx.upload_queue.put_nowait(item)
        except Exception:
            pass

    def run(self) -> None:
        if not self.enable:
            log_event(logger, source="UPLOAD", event="start", result="skip", reason="disabled", level=logging.WARNING, brief=False)
            return
        if not self.endpoint:
            log_event(logger, source="UPLOAD", event="start", result="skip", reason="empty_endpoint", level=logging.ERROR, brief=False)
            return

        log_event(logger, source="UPLOAD", event="start", result="ok", key={"endpoint": self.endpoint}, brief=False)

        while not ctx.system_stop_event.is_set():
            try:
                item = ctx.upload_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if not item:
                continue

            zip_path = item.get("zip_path") if isinstance(item, dict) else None
            meta = item.get("meta") if isinstance(item, dict) else {}
            if not zip_path or (not os.path.exists(zip_path)):
                log_event(logger, source="UPLOAD", event="upload", result="skip", reason="missing_zip", key={"zip": zip_path}, level=logging.WARNING)
                continue

            marker = self._marker_path(zip_path)
            if os.path.exists(marker):
                log_event(logger, source="UPLOAD", event="upload", result="skip", reason="marker_exists", key={"marker": marker}, level=logging.DEBUG)
                continue

            try:
                with open(zip_path, "rb") as f:
                    files = {"file": (os.path.basename(zip_path), f, "application/zip")}
                    resp = requests.post(self.endpoint, files=files, data=meta or {}, timeout=self.timeout_s)

                if resp.ok:
                    self._write_marker(zip_path)
                    log_event(logger, source="UPLOAD", event="upload", result="ok", key={"zip": zip_path, "status": resp.status_code}, brief=True)
                else:
                    log_event(logger, source="UPLOAD", event="upload", result="fail", reason=f"status_{resp.status_code}", key={"zip": zip_path}, level=logging.WARNING)
                    self._requeue(item)
                    time.sleep(self.retry_s)

            except Exception as e:
                log_event(logger, source="UPLOAD", event="upload", result="fail", reason=str(e), key={"zip": zip_path}, level=logging.ERROR)
                self._requeue(item)
                time.sleep(self.retry_s)
