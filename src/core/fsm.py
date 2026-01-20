import threading 
import time
import src.global_ctx as ctx

from src.utils.logger import sys_logger as logger

class FSM_core( threading.Thread ):
    def __init__ ( self ):
        super().__init__()
        logger.info( "[ FSM ] Initialization completed" )
        
    def run( self ):
        logger.info( "[ FSM ] Thread starting" )
        while not ctx.system_stop_event.is_set() :
            #data = ctx.fsm_queue.get()
            #logger.info( f"FSM_core: Processing {data}" )
            time.sleep( 1 )
            
        logger.info( "[ FSM ] Thread finished" )