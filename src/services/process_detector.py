import subprocess
import threading
import json
import queue
import logging
import time
import os
from typing import Optional , Any , Dict , List

import src.global_ctx as ctx

from src.utils.logger import sys_logger as logger, log_event


class ProcessDetector :
    def __init__ ( self , exec_path : str , args : Optional[list[str]] = None , logger = None ) :
        self.exec_path = exec_path 
        self.args = args or []
        self.logger = logger 
        self.proc : Optional[subprocess.Popen] = None
        self.q: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=200)
        self.start_ts: float = 0.0
        self.stderr_tail: List[str] = []

    def start( self ) -> None :
        
        cmd = [ self.exec_path ] + self.args 
        
        self._log_event(event="process_launch", result="start", key={"exec": self.exec_path, "args": self.args})
        
        env = os.environ.copy()            # 复制当前环境变量，使opencv的DLL能够被找到

        # 从配置中注入 OpenCV 的 bin 目录
        opencv_bin = None
        try:
            opencv_bin = (ctx.config or {}).get("dector", {}).get("process", {}).get("opencv_bin")
        except Exception:
            opencv_bin = None

        if opencv_bin and os.path.isdir(opencv_bin):
            env["PATH"] = opencv_bin + os.pathsep + env.get("PATH", "")

        self.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env
        )
        self.start_ts = time.time()
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        
        self._log_event(event="process_start", result="spawn", key={"exec": self.exec_path, "args": self.args}, ids={"pid": self.proc.pid if self.proc else None})
        
    def _log_event(self, *, event: str, action: Optional[str] = None, result: Optional[str] = None, reason: Optional[str] = None, key: Optional[Dict[str, Any]] = None, ids: Optional[Dict[str, Any]] = None, level: int = logging.INFO) -> None:
        payload = key or {}
        # 避免输出整行原始文本，最多保留 200 字符
        if payload.get("line"):
            payload["line"] = str(payload["line"])[:200]

        if self.logger:
            try:
                log_event(self.logger, source="PROCESS", event=event, action=action, result=result, reason=reason, key=payload or None, ids=ids, level=level, brief=False)
                return
            except Exception:
                pass

        parts = [p for p in [event, action, result, reason, payload] if p]
        print(" ".join(map(str, parts)), flush=True)

    def _log_stream(self, stream: str, line: str, level: int = logging.INFO) -> None:
        self._log_event(event="process_output", action=stream, result="note", key={"line": line}, level=level)
        
    def _read_stdout(self) -> None:
        if not self.proc or not self.proc.stdout:
            return
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue

            if not line.startswith("[ NCNN ]"):
                self._log_stream("stdout", line)
                continue

            payload = line[len("[ NCNN ]"):]
            try:
                msg = json.loads(payload)
                if self.q.full():
                    try:
                        _ = self.q.get_nowait()
                    except Exception:
                        pass
                self.q.put_nowait(msg)
            except Exception as e:
                self._log_event(event="process_output", action="json_parse_fail", result="fail", reason=str(e), key={"payload": payload[:200]}, level=logging.WARNING)
    
    def _read_stderr( self ) -> None :
        if not self.proc or not self.proc.stderr:
            return
        for line in self.proc.stderr:
            clean = line.rstrip()
            self.stderr_tail.append(clean)
            if len(self.stderr_tail) > 5:
                self.stderr_tail = self.stderr_tail[-5:]
            self._log_stream("stderr", clean, level=logging.WARNING)
    
    def poll( self , timeout: float = 0.0 ) -> Optional[Dict[str, Any]] :
        try :
            return self.q.get(timeout = timeout)
        except queue.Empty :
            return None
        
    def is_alive( self ) -> bool : 
        state = self.proc is not None and self.proc.poll() is None
        return state
    
    def stop( self ) -> None :
        if self.proc and self.is_alive() :
            self.proc.terminate()
            self._log_event(event="process_exit", result="terminate", key=self._exit_info(), ids={"pid": self.proc.pid if self.proc else None})

    def _exit_info(self) -> Dict[str, Any]:
        rc = None
        uptime = None
        try:
            rc = self.proc.poll() if self.proc else None
        except Exception:
            rc = None
        try:
            uptime = max(0.0, time.time() - self.start_ts) if self.start_ts else None
        except Exception:
            uptime = None
        tail = " | ".join(self.stderr_tail[-3:]) if self.stderr_tail else None
        return {"returncode": rc, "uptime_s": round(uptime,2) if uptime is not None else None, "stderr_tail": tail}
