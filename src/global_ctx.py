import threading
import queue
import time

# 全局配置
config = None

# 线程新建
# 串口队列
uart_queue = queue.Queue( maxsize = 1 )
# 业务队列
fsm_queue = queue.Queue( maxsize = 10 )
# 视觉队列
dector_queue = queue.Queue( maxsize = 1 )
# 上传队列
upload_queue = queue.Queue(maxsize=50)
# 上传队列
fsm_queue = queue.Queue(maxsize=10)

# 通知所有线程安全
# 若启动，则退出所有线程
system_stop_event = threading.Event()

uart = None

# 存放新的元素进入队列
def put_latest(q: queue.Queue ,  item) -> bool:
    # 返回 True 表示成功；False 表示失败
    try:
        if q is None:
            return False

        # 如果满就先清掉旧的
        try:
            if q.full():
                q.get_nowait()
        except Exception:
            pass

        q.put_nowait(item)
        return True
    
    except Exception:
        return False

# gps线程锁
_gps_lock = threading.Lock()

gps_state = {
    "ok": False ,      # GPS是否正常工作
    "lat": None ,      # 纬度
    "lon": None ,      # 经度
    "ts": 0.0 ,        # 时间戳
    "source": "unknown" ,    # 
}

# 写入GPS信号
def set_gps(lat: float ,  lon: float ,  ok: bool = True ,  source: str = "uart") -> None:
    with _gps_lock:
        gps_state["ok"] = bool(ok)
        gps_state["lat"] = float(lat) if lat is not None else None
        gps_state["lon"] = float(lon) if lon is not None else None
        gps_state["ts"] = time.time()
        gps_state["source"] = str(source)
 
# 标记 GPS 无效
def set_gps_invalid(source: str = "uart") -> None:
    with _gps_lock:
        gps_state["ok"] = False
        gps_state["ts"] = time.time()
        gps_state["source"] = str(source)
        
# 获取GPS状态 ，避免读写冲突
def get_gps_copy() -> dict:
    with _gps_lock:
        return dict(gps_state)
    
# 任务的线程锁
_mission_lock = threading.Lock()

mission_state = {
    "mode": "IDLE" , 

    # 巡逻相关
    "patrol_start_ts": 0.0 , 
    "patrol_laps_done": 0 ,      # 已完成的巡逻圈数
    "patrol_time_s": 0.0 ,       # 花费时间
    "active_wp_index": 0 ,       # 当前航点序号

    # 基地
    "at_base": False ,           # 是否在基地
    "arrive_base_ts": 0.0 , 

    # 打包/上传
    "zip_triggered": False ,     # 是否打包触发
    "zip_done": False ,          # 是否打包完成
    "last_zip_task_id": "" ,     # 最近打包任务ID
    "last_zip_path": "" ,        # 最近打包文件路径

    # 最近一次输出的串口命令
    "last_uart_cmd": "" ,        # 上次指令是什么
    "last_uart_cmd_ts": 0.0 ,    # 上次指令时间
}

# 更新mission_state的数据
def set_mission(**kwargs) -> None:
    with _mission_lock:
        for k ,  v in kwargs.items():
            mission_state[k] = v

# 获取mission_state状态，避免读写冲突
def get_mission_copy() -> dict:
    with _mission_lock:
        return dict(mission_state)
    
