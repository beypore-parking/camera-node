# workers/offstreet.py
import time
import logging
from datetime import datetime
import signal

worker_logger = logging.getLogger("Orchestrator")

def run_offstreet_worker(config, data_queue, node_name, stop_event):
    try:
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
    except ValueError:
        # Ignore if this is not a main thread in this process
        pass

    camera_id = config['id']
    
    log = logging.LoggerAdapter(worker_logger, {'location_name': f"{node_name}:{camera_id}"})    
    print(f"[{datetime.now()} - {node_name}] Starting Off-Street Worker for {camera_id}")
    
    log.info("Off-Street Worker INITIALIZED. Starting main loop...")
    
    while not stop_event.is_set():
        time.sleep(0.5) 

    log.info("Off-Street Worker received stop signal. Cleaning up resources...")
    log.info("Off-Street Worker SHUTDOWN COMPLETE.")