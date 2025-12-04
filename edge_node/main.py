import os
import json
import time
import logging
import threading
import multiprocessing
import signal
from datetime import datetime
from fastapi import FastAPI
from contextlib import asynccontextmanager
import firebase_admin
from firebase_admin import credentials, db, storage

# worker functions stub for now (assume these accept `stop_event` as the last argument)
from workers.offstreet import run_offstreet_worker
from workers.onstreet import run_onstreet_worker
from workers.gate import run_gate_worker

# --- configs & logging ---
CONFIG_PATH = "config/config.json"
CREDS_PATH = "config/serviceAccountKey.json"

# custom logger configuration
class NodeFormatter(logging.Formatter):
    def format(self, record):
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        # Use getattr with a default in case the extra dict hasn't been added yet
        location = getattr(record, 'location_name', "ORCHESTRATOR")
        return f"[{timestamp} - {location}] {record.getMessage()}"

logger = logging.getLogger("Orchestrator")
handler = logging.StreamHandler()
handler.setFormatter(NodeFormatter())
logger.addHandler(handler)
logger.setLevel(logging.INFO)

# global State
processes = {}
data_queue = multiprocessing.Queue()
node_config = {}
sync_thread = None # should evenutally hold the threading.Thread instance
stop_event = multiprocessing.Event() # flag to signal processes/threads to stop

# --- cleanup and signal handling ---
def perform_cleanup():
    """Terminates worker processes and signals the sync thread to stop."""
    global sync_thread
    
    # 1. set the global stop flag for all workers and threads
    if not stop_event.is_set():
        stop_event.set()
        logger.info("Stop event signaled. Waiting for processes/threads to clean up...")

    # 2. terminate and join multiprocessing workers
    for pid, p in processes.items():
        if p.is_alive():
            logger.info(f"Terminating worker process {pid} (Type: {p.name})...")
            p.terminate()
            p.join(timeout=5) # timeout for termination
            if p.is_alive():
                logger.error(f"Worker process {pid} failed to terminate gracefully.")

    # 3. join the Sync Thread
    if sync_thread and sync_thread.is_alive():
        logger.info("Joining Firebase sync thread...")
        sync_thread.join(timeout=5)
        if sync_thread.is_alive():
            logger.error("Firebase sync thread failed to join.")

    # 4. clear the queue
    while not data_queue.empty():
        try:
            data_queue.get_nowait()
        except:
            break
            
    logger.info("Graceful shutdown complete.")

def handle_sigterm(signum, frame):
    """Handles SIGINT/SIGTERM for graceful external shutdown."""
    # ensure this runs only once
    if stop_event.is_set():
        return
        
    logger.info(f"Received signal {signum} ({signal.Signals(signum).name}). Initiating external graceful shutdown...")
    perform_cleanup()
    
    # exit the main process after cleanup
    os._exit(0)

# register signal handlers immediately for external shutdowns (Ctrl+C, Docker Stop)
signal.signal(signal.SIGINT, handle_sigterm)
signal.signal(signal.SIGTERM, handle_sigterm)

# --- firebase setp ---
def init_firebase():
    if not firebase_admin._apps:
        try:
            cred = credentials.Certificate(CREDS_PATH)
            firebase_admin.initialize_app(cred, {
                'databaseURL': node_config.get('firebase_db_url'),
                'storageBucket': node_config.get('firebase_storage_bucket') 
            })
            logger.info("Firebase Connected Successfully.")
        except Exception as e:
            logger.error(f"Failed to connect to Firebase: {e}")

# func to start single camera
def start_single_camera(cam_config):
    """Helper to start/restart a specific camera process."""
    p_id = cam_config['id']
    p_type = cam_config['type']
    
    target_func = None
    if p_type == "offstreet": target_func = run_offstreet_worker
    elif p_type == "onstreet": target_func = run_onstreet_worker
    elif p_type == "gate": target_func = run_gate_worker
    
    if target_func:
        p = multiprocessing.Process(
            target=target_func,
            args=(cam_config, data_queue, node_config['node_name'], stop_event),
            name=f"Worker-{p_id}-{p_type.upper()}"
        )
        p.start()
        processes[p_id] = p
        logger.info(f"Worker {p_id} RESTARTED.")

# --- process manager ---
def start_camera_processes():
    """Spawns a new process for each enabled camera in config."""
    cameras = node_config.get('cameras', [])
    
    if not cameras:
        logger.warning("No cameras found in configuration. Waiting for updates...")
        return

    for cam in cameras:
        if not cam.get('enabled', True):
            continue

        p_id = cam['id']
        p_type = cam['type']
        
        # select the correct worker function
        target_func = None
        worker_name = ""
        if p_type == "offstreet":
            target_func = run_offstreet_worker
            worker_name = "OFFSTREET"
        elif p_type == "onstreet":
            target_func = run_onstreet_worker
            worker_name = "ONSTREET"
        elif p_type == "gate":
            target_func = run_gate_worker
            worker_name = "GATE"
        
        if target_func:
            # create process
            p = multiprocessing.Process(
                target=target_func,
                args=(cam, data_queue, node_config['node_name'], stop_event),
                name=f"Worker-{p_id}-{worker_name}"
            )
            p.start()
            processes[p_id] = p
            
            # log process start
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            logger.info(f"Worker {p_id} ({worker_name}) STARTED.")

# --- data sync loop ---
def firebase_sync_loop():
    """Reads queue, uploads snapshots to Storage, and status to DB."""
    node_name = node_config.get('node_name')
    
    # check the stop_event regularly to exit gracefully
    while not stop_event.is_set():
        try:
            if not data_queue.empty():
                # get data from workers
                payload = data_queue.get()
                cam_id = payload.get('id')
                p_type = payload.get('type', 'status') # Default to status
                data = payload.get('data')

                # push to Firebase
                if not node_name: continue

                # --- CASE A: SNAPSHOT (Image Upload) ---
                if p_type == 'snapshot':
                    image_bytes = data.get('image_bytes')
                    timestamp = data.get('timestamp')
                    
                    # 1. Upload to Storage
                    bucket = storage.bucket()
                    blob_path = f"snapshots/{node_name}/{cam_id}.jpg"
                    blob = bucket.blob(blob_path)
                    blob.upload_from_string(image_bytes, content_type='image/jpeg')
                    blob.make_public() 
                    
                    # 2. Update DB with URL
                    ref = db.reference(f"locations/{node_name}/live_status/{cam_id}/snapshot")
                    ref.update({
                        "url": blob.public_url,
                        "timestamp": timestamp
                    })
                    logger.info(f"Snapshot uploaded for {cam_id}")

                # --- CASE B: GATE EVENT (History Log) ---
                elif p_type == 'gate_event':
                    # Push to history log (generates unique ID)
                    ref = db.reference(f"locations/{node_name}/gate_events/{cam_id}")
                    ref.push(data)

                # --- CASE C: LIVE STATUS (Occupancy/Counts) ---
                else:
                    # Update live status (Overwrites previous state)
                    ref = db.reference(f"locations/{node_name}/live_status/{cam_id}")
                    ref.update(data)

            time.sleep(0.05)
        except Exception as e:
            logger.error(f"Sync Error: {e}")
            time.sleep(1)

# --- heartbeat loop ---
def heartbeat_loop():
    """Updates system_health in Firebase every 60 seconds."""
    node_name = node_config.get('node_name')
    if not node_name: 
        return

    logger.info("Heartbeat thread started.")
    while not stop_event.is_set():
        try:
            if firebase_admin._apps:
                ref = db.reference(f"locations/{node_name}/system_health")
                ref.update({
                    "status": "online",
                    "last_seen": datetime.now().isoformat(),
                })
        except Exception as e:
            logger.error(f"Heartbeat failed: {e}")
        
        time.sleep(60)

# --- fastapi lifecycle ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # FIX: Moved global declarations to the top to resolve SyntaxError
    global node_config
    global logger
    global sync_thread
    
    # 1. load config
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r') as f:
                node_config = json.load(f)
        except json.JSONDecodeError as e:
             logger.error(f"Error loading config file: {e}")
             # Optionally exit if config is critical
             os._exit(1)


    # 2. interactive setup (if name is missing)
    if not node_config.get("node_name"):
        print("\n" + "="*40)
        print(" NEW NODE DETECTED ")
        print("="*40)
        name = input("Please enter a name for this location (e.g., 'MainGate_Server'): ").strip()
        node_config["node_name"] = name
        
        # Save back to file
        with open(CONFIG_PATH, 'w') as f:
            json.dump(node_config, f, indent=4)
    
    # set logger context
    # Re-initialize logger adapter after node_name is set
    logger_extra = {'location_name': node_config['node_name']}
    logger = logging.LoggerAdapter(logging.getLogger("Orchestrator"), logger_extra)

    # 3. initialize systems
    init_firebase()
    start_camera_processes()
    
    # 4. start sync thread and heartbeat thread (background)
    sync_thread = threading.Thread(target=firebase_sync_loop, daemon=True)
    sync_thread.start()
    logger.info("Firebase sync thread started.")

    hb_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    hb_thread.start()
    logger.info("Heartbeat sync thread started.")

    yield # server is running...
    
    # 5. cleanup on shutdown (uvicorn graceful shutdown path)
    logger.info("FastAPI lifecycle ending. Initiating graceful shutdown via lifespan...")
    perform_cleanup()
    
app = FastAPI(lifespan=lifespan)

# --- api routes ---
@app.get("/")
def home():
    # Use logger here for the adapter to work consistently, but access the base logger if adapter fails
    current_logger = logger if isinstance(logger, logging.LoggerAdapter) else logging.getLogger("Orchestrator")
    current_logger.info("Root path accessed.")
    
    return {"status": "Online", "node": node_config.get("node_name"), "sync_thread_alive": sync_thread.is_alive() if sync_thread else False}

@app.get("/health")
def health_check():
    status = {pid: p.is_alive() for pid, p in processes.items()}
    return {"node": node_config.get("node_name"), "processes": status, "sync_thread_alive": sync_thread.is_alive() if sync_thread else False}

@app.post("/restart/{cam_id}")
def restart_camera(cam_id: str):
    # 1. Kill existing
    if cam_id in processes:
        p = processes[cam_id]
        if p.is_alive():
            p.terminate()
            p.join()
    
    # 2. Find config for this camera
    # (In the future, we fetch this from Firebase 'config' path)
    target_cam = next((c for c in node_config.get('cameras', []) if c['id'] == cam_id), None)
    
    if target_cam:
        # 3. Spawn new
        start_single_camera(target_cam)
        return {"status": "Restarted", "id": cam_id}
    
    return {"error": "Camera Config not found"}

if __name__ == "__main__":
    import uvicorn
    # uvicorn runs FastAPI, and the lifespan context manager handles the cleanup.
    # signal handlers are primarily for non-uvicorn ways of stopping the script.
    uvicorn.run(app, host="0.0.0.0", port=8000) 