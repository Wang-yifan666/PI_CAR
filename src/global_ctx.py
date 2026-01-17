import threading
import queue

# 全局配置
config = None
model = None 

# 线程新建
# 串口线程
uart_queue = queue.Queue( maxsize = 1 )
# 业务线程
fsm_queue = queue.Queue( maxsize = 10 )

# 通知所有线程安全
# 若启动，则退出所有线程
system_stop_event = threading.Event()

