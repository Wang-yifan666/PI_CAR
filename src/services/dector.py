import threading 
import time
import src.global_ctx as ctx

class DECTOR_ser( threading.Thread ):
    def __init__( self ):
        super().__init__()
        print( "DECTOR_ser: 初始化完成" )

    def run( self ):
        print( "DECTOR_ser: 线程启动" )
        while not ctx.system_stop_event.is_set() :
            #data = ctx.uart_queue.get()
            #print( f"DECTOR_ser: Processing {data}" )
            time.sleep( 1 )
            
        print( "DECTOR_ser: 线程结束" )