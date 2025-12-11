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

    yield 
    pm.perform_cleanup(sync_thread)
    
app = FastAPI(lifespan=lifespan)

@app.get("/")
def home():
    return {"status": "Online", "node": node_config.get("node_name")}

@app.get("/health")
def health_check():
    return {"processes": {k: v.is_alive() for k, v in pm.processes.items()}}

# --- RESTART ENDPOINT (Admin Updates) ---
@app.post("/restart/{cam_id}")
def restart_camera(cam_id: str):
    # 1. find context in local config
    target_lot_id = None
    for lot_id, lot_data in node_config.get('parking_lots', {}).items():
        if cam_id in lot_data.get('cameras', {}):
            target_lot_id = lot_id
            break
            
    if not target_lot_id:
        raise HTTPException(status_code=404, detail="Camera ID not found")

    # 2. kill old worker
    if cam_id in pm.processes:
        if pm.processes[cam_id].is_alive():
            pm.processes[cam_id].terminate()
            pm.processes[cam_id].join()

    # 3. fetch fresh truth from firebase
    node_name = node_config['node_name']
    
    # A. get lot capacities
    try:
        lot_cap_ref = db.reference(f"locations/{node_name}/config/parking_lots/{target_lot_id}/capacities")
        new_caps = lot_cap_ref.get()
        if new_caps:
            node_config['parking_lots'][target_lot_id]['capacities'] = new_caps

        # B. get camera specifics (polygons, lines)
        cam_ref = db.reference(f"locations/{node_name}/config/parking_lots/{target_lot_id}/cameras/{cam_id}")
        new_cam_data = cam_ref.get()
        
        if new_cam_data:
            # merge updates into memory
            node_config['parking_lots'][target_lot_id]['cameras'][cam_id].update(new_cam_data)
        else:
            logger.warning("Firebase config missing for restart. Using cached.")

    except Exception as e:
        logger.error(f"Failed to fetch update from Firebase: {e}")
        # continue with cached data if network fails

    # 4. re-Spawn
    full_cam_config = node_config['parking_lots'][target_lot_id]['cameras'][cam_id]
    
    # re-inject critical context
    full_cam_config['id'] = cam_id
    full_cam_config['lot_id'] = target_lot_id
    full_cam_config['lot_type'] = node_config['parking_lots'][target_lot_id]['type']
    full_cam_config['lot_capacities'] = node_config['parking_lots'][target_lot_id]['capacities']

    if full_cam_config.get('enabled', True):
        # note: start_single_camera automatically injects 'force_startup_snapshot': True
        # and fetches 'initial_state' (counts) if it's a gate.
        pm.start_single_camera(full_cam_config, node_config)
        return {"status": "restarted", "source": "firebase_fresh"}
    else:
        return {"status": "stopped", "reason": "disabled_in_firebase"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)