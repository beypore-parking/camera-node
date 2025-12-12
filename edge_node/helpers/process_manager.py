import multiprocessing
import time
from workers.offstreet import run_offstreet_worker
from workers.onstreet import run_onstreet_worker
from workers.gate import run_gate_worker
from helpers.logger import logger
from helpers.firebase_sync import get_camera_history

# --- global state ---
processes = {}
# user manager queue to prevent deadlocks that arise with the normal queue -> the bug where the total occupancy doesnt get udpated when the workder restarts
_manager = multiprocessing.Manager()
data_queue = _manager.Queue()
stop_event = multiprocessing.Event()

# helper to stop a specific worker 
def stop_worker(cam_id):
    if cam_id in processes:
        p = processes[cam_id]
        if p.is_alive():
            logger.info(f"Stopping worker: {cam_id}...")
            p.terminate()
            # still a short jointime
            p.join(timeout=2)
            if p.is_alive():
                p.kill() # force kill if stuck
        del processes[cam_id]

# --- process manager ---
def start_single_camera(cam_config, node_config):
    cam_id = cam_config['id']
    lot_type = cam_config.get('lot_type', 'offstreet') 
    lot_id = cam_config.get('lot_id')
    
    # 1. INJECT SNAPSHOT TRIGGER
    # the worker logic must check this flag and send a snapshot immediately on startup
    cam_config['force_startup_snapshot'] = True

    # 2. INJECT PERSISTENT STATE (gor gates)
    if lot_type == 'gate':
        history = get_camera_history(node_config, lot_id, cam_id)
        cam_config['initial_state'] = history
        logger.info(f"Injecting history for {cam_id}: {history}")

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
        # STOP THE OLD PROCESS IF IT EXISTS VERY IMPORTATNY 
        stop_worker(cam_id)

        p = multiprocessing.Process(
            target=target_func,
            args=(cam_config, data_queue, node_config['node_name'], stop_event),
            name=f"Worker-{cam_id}-{worker_label}"
        )
        p.start()
        processes[cam_id] = p
        logger.info(f"Worker {cam_id} ({worker_label}) STARTED.")

def start_camera_processes(node_config):
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

            start_single_camera(cam_data, node_config)

# --- cleanup ---
def perform_cleanup(sync_thread=None):
    if not stop_event.is_set():
        stop_event.set()
        logger.info("Stopping...")
    for pid, p in processes.items():
        if p.is_alive():
            p.terminate()
            p.join(timeout=3)
    if sync_thread and sync_thread.is_alive():
        sync_thread.join(timeout=3)