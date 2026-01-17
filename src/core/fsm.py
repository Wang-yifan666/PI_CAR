import threading 
import time

import src.global_ctx as ctx

class FSM_core( threading.Thread ):
    def __init__ ( self ):
        super().__init__()
        print( "FSM_core: 初始化完成" )
        
    def run( self ):
        print( "FSM_core: 线程启动" )
        while not ctx.system_stop_event.is_set() :
            #data = ctx.fsm_queue.get()
            #print( f"FSM_core: Processing {data}" )
            time.sleep( 1 )
            
        print( "FSM_core: 线程结束" )