import threading 
import time
import src.global_ctx as ctx

from src.utils.logger import sys_logger as logger

class DECTOR_ser( threading.Thread ):
    def __init__( self ):
        super().__init__()
        logger.info( "DECTOR_ser: 初始化完成" )

    def run( self ):
        logger.info( "DECTOR_ser: 线程启动" )
        while not ctx.system_stop_event.is_set() :
            #data = ctx.uart_queue.get()
            #logger.info( f"DECTOR_ser: Processing {data}" )
            time.sleep( 1 )
            
        logger.info( "DECTOR_ser: 线程结束" )