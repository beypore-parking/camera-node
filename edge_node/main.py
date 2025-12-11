import os
import json
import time
import logging
import threading
import multiprocessing
import signal
from datetime import datetime
from fastapi import FastAPI, HTTPException
from contextlib import asynccontextmanager
import firebase_admin
from firebase_admin import credentials, db, storage

# config manager
from helpers.config_manager import load_and_validate_config

# worker functions
from workers.offstreet import run_offstreet_worker
from workers.onstreet import run_onstreet_worker
from workers.gate import run_gate_worker

CONFIG_PATH = "config/config.json"

# --- logging ---
class NodeFormatter(logging.Formatter):
    def format(self, record):
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        location = getattr(record, 'location_name', "ORCHESTRATOR")
        return f"[{timestamp} - {location}] {record.getMessage()}"

logger = logging.getLogger("Orchestrator")
handler = logging.StreamHandler()
handler.setFormatter(NodeFormatter())
logger.addHandler(handler)
logger.setLevel(logging.INFO)

# --- global state ---
processes = {}
data_queue = multiprocessing.Queue()
node_config = {} 
sync_thread = None 
stop_event = multiprocessing.Event() 

# --- firebase setup ---
def init_firebase():
    # we assume node_config is loaded by now
    creds_path = node_config.get('service_account_path', 'config/serviceAccountKey.json')
    
    if not os.path.exists(creds_path):
        logger.critical(f"Service Account Key missing at: {creds_path}")
        os._exit(1)

    try:
        cred = credentials.Certificate(creds_path)
        firebase_admin.initialize_app(cred, {
            'databaseURL': node_config['firebase_db_url'],
            'storageBucket': node_config['firebase_storage_bucket']
        })
        logger.info("Firebase Connected.")
    except Exception as e:
        logger.critical(f"FIREBASE CONNECTION FAILED: {e}")
        os._exit(1)

# --- hybird sync logic ---
def sync_config_state():
    # 1. pushes local truth (lat/long/identity) to firebase.
    # 2. pulls cloud truth (polygons/rules) to local memory.

    node_name = node_config.get('node_name')
    if not node_name: return

    logger.info("Starting Configuration Sync (Hybrid Mode)...")
    
    # reference to the cloud config
    cloud_config_ref = db.reference(f"locations/{node_name}/config/parking_lots")
    cloud_data = cloud_config_ref.get()

    # IF FIRST RUN: upload local config as baseline
    if not cloud_data:
        logger.info("Cloud config empty. Initializing with Local Defaults.")
        cloud_config_ref.set(node_config.get('parking_lots', {}))
        return 

    # IF SUBSEQUENT RUN: merge
    local_lots = node_config.get('parking_lots', {})
    
    for lot_id, local_data in local_lots.items():
        base_path = f"locations/{node_name}/config/parking_lots/{lot_id}"
        
        # A. ENFORCE LOCAL TRUTH -> push to firebase
        # physical location and hardware identity never change from the cloud side so we just keep it
        db.reference(base_path).update({
            "lat": local_data.get('lat'),
            "long": local_data.get('long'),
            "display_name": local_data.get('display_name'),
            "type": local_data.get('type')
        })

        # B. ENFORCE CLOUD TRUTH -> pull to memory
        cloud_lot = cloud_data.get(lot_id)
        if cloud_lot:
            # overwrite capacities
            if 'capacities' in cloud_lot:
                node_config['parking_lots'][lot_id]['capacities'] = cloud_lot['capacities']
            
            # overwrite enabled status
            if 'enabled' in cloud_lot:
                node_config['parking_lots'][lot_id]['enabled'] = cloud_lot['enabled']

            # overwrite Camera logic (polygons/lines)
            cloud_cameras = cloud_lot.get('cameras', {})
            local_cameras = local_data.get('cameras', {})
            
            for cam_id, local_cam in local_cameras.items():
                if cam_id in cloud_cameras:
                    cloud_cam = cloud_cameras[cam_id]
                    
                    # merge logic fields
                    if 'polygons' in cloud_cam:
                         node_config['parking_lots'][lot_id]['cameras'][cam_id]['polygons'] = cloud_cam['polygons']
                    if 'gate_logic' in cloud_cam:
                        node_config['parking_lots'][lot_id]['cameras'][cam_id]['gate_logic'] = cloud_cam['gate_logic']
                    if 'roi_polygon' in cloud_cam:
                        node_config['parking_lots'][lot_id]['cameras'][cam_id]['roi_polygon'] = cloud_cam['roi_polygon']
                    if 'enabled' in cloud_cam:
                         node_config['parking_lots'][lot_id]['cameras'][cam_id]['enabled'] = cloud_cam['enabled']

    logger.info("Sync Complete. Local memory aligned with Cloud Logic.")

# --- persistant counts ---
def get_current_counts(lot_id):
    # fetches current occupancy numbers from firebase. 
    # needed for Gate Workers to not reset to 0 on restart.
        
    node_name = node_config.get('node_name')
    try:
        ref = db.reference(f"locations/{node_name}/live_status/{lot_id}/occupancy")
        data = ref.get()
        return data if data else {}
    except:
        return {}

# --- process manager ---
def start_single_camera(cam_config):
    cam_id = cam_config['id']
    lot_type = cam_config.get('lot_type', 'offstreet') 
    lot_id = cam_config.get('lot_id')
    
    # 1. INJECT SNAPSHOT TRIGGER
    # the worker logic must check this flag and send a snapshot immediately on startup
    cam_config['force_startup_snapshot'] = True

    # 2. INJECT PERSISTENT STATE (For Gates)
    if lot_type == 'gate':
        current_counts = get_current_counts(lot_id)
        cam_config['initial_state'] = current_counts
        logger.info(f"Injecting initial state for {cam_id}: {current_counts}")

    target_func = None
    worker_label = ""

    if lot_type == "offstreet": 
        target_func = run_offstreet_worker
        worker_label = "OFFSTREET"
    elif lot_type == "onstreet": 
        target_func = run_onstreet_worker
        worker_label = "ONSTREET"
    elif lot_type == "gate": 
        target_func = run_gate_worker
        worker_label = "GATE"
    
    if target_func:
        p = multiprocessing.Process(
            target=target_func,
            args=(cam_config, data_queue, node_config['node_name'], stop_event),
            name=f"Worker-{cam_id}-{worker_label}"
        )
        p.start()
        processes[cam_id] = p
        logger.info(f"Worker {cam_id} ({worker_label}) STARTED.")

def start_camera_processes():
    # uses the node_config which is now populated with firebase data
    lots = node_config.get('parking_lots', {})
    
    for lot_id, lot_data in lots.items():
        if not lot_data.get('enabled', True):
            continue

        lot_type = lot_data.get('type')
        lot_caps = lot_data.get('capacities', {})
        cameras = lot_data.get('cameras', {})

        for cam_id, cam_data in cameras.items():
            if not cam_data.get('enabled', True):
                continue
            
            # re-inject context
            cam_data['id'] = cam_id
            cam_data['lot_id'] = lot_id
            cam_data['lot_type'] = lot_type
            cam_data['lot_capacities'] = lot_caps

            start_single_camera(cam_data)

# --- firebase data sync loop ---
def firebase_sync_loop():
    node_name = node_config.get('node_name')
    while not stop_event.is_set():
        try:
            if not data_queue.empty():
                payload = data_queue.get()
                cam_id = payload.get('id')
                lot_id = payload.get('lot_id')
                p_type = payload.get('type', 'status')
                data = payload.get('data')

                if not node_name: continue
                base_ref = f"locations/{node_name}"

                # snapshot Handler
                if p_type == 'snapshot':
                    image_bytes = data.get('image_bytes')
                    ts = data.get('timestamp')
                    bucket = storage.bucket()
                    # path: snapshots/Node/Lot/Cam.jpg
                    blob = bucket.blob(f"snapshots/{node_name}/{lot_id}/{cam_id}.jpg")
                    blob.upload_from_string(image_bytes, content_type='image/jpeg')
                    blob.make_public() 
                    
                    # update the live status with the new image URL
                    db.reference(f"{base_ref}/live_status/{lot_id}/snapshots/{cam_id}").update({ 
                        "url": blob.public_url, 
                        "timestamp": ts 
                    })
                    logger.info(f"Snapshot uploaded for {cam_id}")

                elif p_type == 'gate_event':
                    db.reference(f"{base_ref}/gate_events/{lot_id}").push(data)

                elif p_type == 'slot_update':
                     db.reference(f"{base_ref}/live_status/{lot_id}/slots").update(data)
                     db.reference(f"{base_ref}/live_status/{lot_id}").update({"last_updated": datetime.now().isoformat()})

                elif p_type == 'occupancy_update':
                    db.reference(f"{base_ref}/live_status/{lot_id}/occupancy").update(data)
                    db.reference(f"{base_ref}/live_status/{lot_id}").update({"last_updated": datetime.now().isoformat()})

            time.sleep(0.05)
        except Exception as e:
            logger.error(f"Sync Error: {e}")
            time.sleep(1)

# --- hearbeat ---
def heartbeat_loop():
    node_name = node_config.get('node_name')
    if not node_name: return
    while not stop_event.is_set():
        try:
            if firebase_admin._apps:
                db.reference(f"locations/{node_name}/system_health").update({
                    "status": "online",
                    "last_seen": datetime.now().isoformat(),
                })
        except Exception: pass 
        time.sleep(60)

# --- cleaup ---
def perform_cleanup():
    global sync_thread
    if not stop_event.is_set():
        stop_event.set()
        logger.info("Stopping...")
    for pid, p in processes.items():
        if p.is_alive():
            p.terminate()
            p.join(timeout=3)
    if sync_thread and sync_thread.is_alive():
        sync_thread.join(timeout=3)

def handle_sigterm(signum, frame):
    if stop_event.is_set(): return 
    perform_cleanup()
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
    
    logger_extra = {'location_name': node_config['node_name']}
    logger = logging.LoggerAdapter(logging.getLogger("Orchestrator"), logger_extra)

    # 2. connect to firebase
    init_firebase()
    
    # 3. HYBRID SYNC: push Lat/Long -> pull polygons/state
    sync_config_state()
    
    # 4. start workers
    start_camera_processes()
    
    sync_thread = threading.Thread(target=firebase_sync_loop, daemon=True)
    sync_thread.start()
    hb_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    hb_thread.start()

    yield 
    perform_cleanup()
    
app = FastAPI(lifespan=lifespan)

@app.get("/")
def home():
    return {"status": "Online", "node": node_config.get("node_name")}

@app.get("/health")
def health_check():
    return {"processes": {k: v.is_alive() for k, v in processes.items()}}

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
    if cam_id in processes:
        if processes[cam_id].is_alive():
            processes[cam_id].terminate()
            processes[cam_id].join()

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
        start_single_camera(full_cam_config)
        return {"status": "restarted", "source": "firebase_fresh"}
    else:
        return {"status": "stopped", "reason": "disabled_in_firebase"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)