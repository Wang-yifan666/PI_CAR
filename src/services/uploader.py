import os
import time
import json
import glob
import zipfile
import hashlib

from datetime import datetime
from typing import Dict, Optional, List, Iterable, Tuple

import src.global_ctx as ctx
from src.utils.logger import sys_logger as logger

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
        root = os.path.abspath(root)    # 建立绝对路径
        
        # 遍历全部文件
        for dir_path , dirnames , filenames in os.walk(root) : 
            dirnames[:] = [ d for d in dirnames if d not in exclude_dirs ]
            
            for fn in filenames :
                yield os.path.join( dir_path , fn )
                
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
    
    logger.info(f"[ ZIP ] start: task_id={task.task_id} root={root}")
    logger.info(f"[ ZIP ] config: include_patterns={include_patterns} exclude_dirs={exclude_dirs}")
    logger.info(f"[ ZIP ] output_dir: {zip_output_dir}")
    logger.info(f"[ ZIP ] output: {zip_path}") 
    
    files = list(_iter_files(root , include_patterns =include_patterns , exclude_dirs = exclude_dirs))
    logger.info(f"[ ZIP ] files collected: count={len(files)}")
    
    tmp_manifest = os.path.join(zip_output_dir, f".manifest_{task.task_id}_{_now_time()}.json")
    file_rows: List[Tuple[str, int, str]] = []
    
    for p in files:
        rel = os.path.relpath(p, root).replace("\\", "/")
        try:
            size = os.path.getsize(p)
            sha256 = _sha256_file(p)
            file_rows.append((rel, size, sha256))
        except Exception as e:
            logger.exception(f"[ ZIP ] file stat/hash failed: file={p} err={e}")
        
        
    try:
        _write_manifest(tmp_manifest, task, root, file_rows)
        logger.debug(f"[ ZIP ] manifest ready: {tmp_manifest}")

        # 写 zip
        with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            # 先写业务文件
            for p in files:
                arcname = os.path.relpath(p, root).replace("\\", "/")
                try:
                    zf.write(p, arcname=arcname)
                except Exception as e:
                    logger.exception(f"[ ZIP ] zip write failed: file={p} arcname={arcname} err={e}")

            # 再写 manifest 到 zip 根目录
            zf.write(tmp_manifest, arcname="manifest.json")

        # marker
        try:
            with open(marker_path, "w", encoding="utf-8") as f:
                f.write(datetime.now().isoformat(timespec="seconds"))
            logger.info(f"[ ZIP ] marker written: {marker_path}")
        except Exception as e:
            logger.exception(f"[ ZIP ] marker write failed: {e}")

        # 总结日志
        zip_size = os.path.getsize(zip_path) if os.path.exists(zip_path) else -1
        elapsed = time.time() - t0
        logger.info(
            f"[ ZIP ] done: task_id={task.task_id} "
            f"zip_size={zip_size}B files={len(files)} elapsed={elapsed:.2f}s"
        )

        return zip_path

    finally:
        # 清理临时manifest
        try:
            if os.path.exists(tmp_manifest):
                os.remove(tmp_manifest)
                logger.debug(f"[ ZIP ] tmp manifest removed: {tmp_manifest}")
        except Exception:
            pass    
 
# 快速测试函数
def build_zip_for_data(task_id: Optional[str] = None,
                       meta: Optional[Dict[str, str]] = None,
                       **kwargs) -> str:
    if task_id is None:
        task_id = f"DATA_{_now_time()}"
    task = zips(root_path="data", task_id=task_id, meta=meta or {})
    return build_zip(task, **kwargs)
