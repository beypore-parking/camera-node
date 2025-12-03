import time
from datetime import datetime

def run_gate_worker(config, queue, node_name):
    camera_id = config['id']
    print(f"[{datetime.now()} - {node_name}] Starting Gate Worker for {camera_id}")
    
    while True:
        # placeholder for robolow line crossing
        time.sleep(5)