import os
import threading
import signal
import logging
from datetime import datetime
from fastapi import FastAPI, HTTPException
from contextlib import asynccontextmanager
from firebase_admin import db 

# helpers
from helpers.logger import logger, NodeFormatter
import helpers.process_manager as pm
import helpers.firebase_sync as fb
from helpers.config_manager import load_and_validate_config

CONFIG_PATH = "config/config.json"

# --- global state ---
node_config = {} 
sync_thread = None 

def handle_sigterm(signum, frame):
    if pm.stop_event.is_set(): return 
    pm.perform_cleanup(sync_thread)
    os._exit(0)

signal.signal(signal.SIGINT, handle_sigterm)
signal.signal(signal.SIGTERM, handle_sigterm)

# --- fastapi lifecycle ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    global node_config, logger, sync_thread
    
    # 1. load local config and validate
    config = load_and_validate_config(CONFIG_PATH)
    if config is None: os._exit(1)
    node_config = config
    
    # update logger adapter
    logger_extra = {'location_name': node_config['node_name']}
    logger = logging.LoggerAdapter(logging.getLogger("Orchestrator"), logger_extra)

    # 2. connect to firebase
    fb.init_firebase(node_config)
    
    # 3. HYBRID SYNC: push Lat/Long -> pull polygons/state
    fb.sync_config_state(node_config)
    
    # 4. start workers
    pm.start_camera_processes(node_config)
    
    sync_thread = threading.Thread(
        target=fb.firebase_sync_loop, 
        args=(node_config, pm.data_queue, pm.stop_event),
        daemon=True
    )
    sync_thread.start()
    
    hb_thread = threading.Thread(
        target=fb.heartbeat_loop,
        args=(node_config, pm.stop_event),
        daemon=True
    )
    hb_thread.start()

    config_thread = threading.Thread(
        target=fb.config_monitor_loop,
        args=(node_config, pm.stop_event),
        daemon=True
    )
    config_thread.start()

    yield
    pm.perform_cleanup(sync_thread)
    
app = FastAPI(lifespan=lifespan)

@app.get("/")
def home():
    return {"status": "Online", "node": node_config.get("node_name")}

@app.get("/health")
def health_check():
    return {"processes": {k: v.is_alive() for k, v in pm.processes.items()}}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)