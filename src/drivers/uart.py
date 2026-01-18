import threading
import time
import src.global_ctx as ctx

from src.utils.logger import sys_logger as logger

class UART_drv ( threading.Thread ):
    def __init__( self ):
        super().__init__()
        logger.info( "[ UART]初始化完成" )

    def run( self ):
        logger.info( "[ UART]线程启动" )
        while not ctx.system_stop_event.is_set() :
            #data = "data_from_uart"
            #logger.info( f"UART_drv: Received {data}" )
            #ctx.uart_queue.put( data )
            time.sleep( 1 )
            
        logger.info( "[ UART]线程结束" )
