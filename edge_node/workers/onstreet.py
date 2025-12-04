import time
import logging
from datetime import datetime
import signal

worker_logger = logging.getLogger("Orchestrator")

def run_onstreet_worker(config, data_queue, node_name, stop_event):
    try:
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
    except ValueError:
        # Ignore if this is not a main thread in this process
        pass

    camera_id = config['id']
    
    log = logging.LoggerAdapter(worker_logger, {'location_name': f"{node_name}:{camera_id}"})
    print(f"[{datetime.now()} - {node_name}] Starting On-Street Worker for {camera_id}")
    
    log.info("On-Street Worker INITIALIZED. Starting main loop...")
    
    while not stop_event.is_set():
        time.sleep(1.0) 

    log.info("On-Street Worker received stop signal. Cleaning up resources...")
    log.info("On-Street Worker SHUTDOWN COMPLETE.")