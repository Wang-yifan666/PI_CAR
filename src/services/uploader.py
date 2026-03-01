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

# 打包变成zip文件
def build_zip(task: zips,
              zip_output_dir : Optional[str] = None,
              include_patterns : Optional[List[str]] = None,
              exclude_dirs : Optional[List[str]] = None,
              marker_suffix : str = ".zipped") -> str:
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

    t0 = time.time()
    
    root = os.path.abspath(task.root_path)

    # 将输出目录解析成“项目根目录下的绝对路径”
    zip_output_dir = _resolve_dir(zip_output_dir)

    _mkdir( zip_output_dir )
    
    zip_name = f"{task.task_id}.zip"
    zip_path = os.path.join(zip_output_dir , zip_name)
    marker_path = zip_path + marker_suffix
    
    log_event(logger, source="ZIP", event="zip_create", action="start", result="begin", key={"root": root}, ids={"task": task.task_id}, brief=False)
    log_event(logger, source="ZIP", event="config", key={"include_patterns": include_patterns, "exclude_dirs": exclude_dirs}, level=logging.DEBUG)
    log_event(logger, source="ZIP", event="output", key={"output_dir": zip_output_dir, "zip_path": zip_path}, level=logging.DEBUG)
    
    files = list(_iter_files(root , include_patterns =include_patterns , exclude_dirs = exclude_dirs))
    log_event(logger, source="ZIP", event="files_collected", key={"count": len(files)}, level=logging.DEBUG)
    
    tmp_manifest = os.path.join(zip_output_dir, f".manifest_{task.task_id}_{_now_time()}.json")
    file_rows: List[Tuple[str, int, str]] = []
    
    for p in files:
        rel = os.path.relpath(p, root).replace("\\", "/")
        try:
            size = os.path.getsize(p)
            sha256 = _sha256_file(p)
            file_rows.append((rel, size, sha256))
        except Exception as e:
            log_event(logger, source="ZIP", event="file_hash", result="fail", reason=str(e), key={"file": p}, level=logging.ERROR)
        
        
    try:
        _write_manifest(tmp_manifest, task, root, file_rows)
        log_event(logger, source="ZIP", event="manifest", result="ok", key={"tmp": tmp_manifest}, level=logging.DEBUG)

        # 写 zip
        with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            # 先写业务文件
            for p in files:
                arcname = os.path.relpath(p, root).replace("\\", "/")
                try:
                    zf.write(p, arcname=arcname)
                except Exception as e:
                    log_event(logger, source="ZIP", event="zip_write", result="fail", reason=str(e), key={"file": p, "arcname": arcname}, level=logging.ERROR)

            # 再写 manifest 到 zip 根目录
            zf.write(tmp_manifest, arcname="manifest.json")

        # marker
        try:
            with open(marker_path, "w", encoding="utf-8") as f:
                f.write(datetime.now().isoformat(timespec="seconds"))
            log_event(logger, source="ZIP", event="marker", result="ok", key={"marker": marker_path}, level=logging.DEBUG)
        except Exception as e:
            log_event(logger, source="ZIP", event="marker", result="fail", reason=str(e), level=logging.ERROR)

        # 总结日志
        zip_size = os.path.getsize(zip_path) if os.path.exists(zip_path) else -1
        elapsed = time.time() - t0
        log_event(
            logger,
            source="ZIP",
            event="zip_create",
            action="finish",
            result="ok",
            key={"zip_size_bytes": zip_size, "files": len(files), "elapsed_s": round(elapsed,2)},
            ids={"task": task.task_id},
            brief=True,
        )

        return zip_path

    finally:
        # 清理临时manifest
        try:
            if os.path.exists(tmp_manifest):
                os.remove(tmp_manifest)
                log_event(logger, source="ZIP", event="manifest", action="cleanup", key={"tmp": tmp_manifest}, level=logging.DEBUG)
        except Exception:
            pass    
 
# 快速函数
def build_zip_for_data(task_id: Optional[str] = None,
                       meta: Optional[Dict[str, str]] = None,
                       **kwargs) -> str:
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
