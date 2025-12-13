import os
import time
import firebase_admin
from datetime import datetime
from firebase_admin import credentials, db, storage
from helpers.logger import logger
import helpers.process_manager as pm

# --- firebase setup ---
def init_firebase(node_config):
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
def sync_config_state(node_config):
    node_name = node_config.get('node_name')
    if not node_name: return

    logger.info("Starting Configuration Sync (Hybrid Mode)...")
    
    cloud_config_ref = db.reference(f"locations/{node_name}/config/parking_lots")
    cloud_data = cloud_config_ref.get() or {} 

    # IF FIRST RUN: upload local config as baseline
    if not cloud_data:
        logger.info("Cloud config empty. Initializing with Local Defaults.")
        cloud_config_ref.set(node_config.get('parking_lots', {}))
        return 

    local_lots = node_config.get('parking_lots', {})
    
    # 1. iterate through local config parking lots 
    for lot_id, local_data in local_lots.items():
        base_path = f"locations/{node_name}/config/parking_lots/{lot_id}"
        
        # A. push local hardware truth 
        db.reference(base_path).update({
            "lat": local_data.get('lat'),
            "long": local_data.get('long'),
            "display_name": local_data.get('display_name'),
            "type": local_data.get('type')
        })

        local_cameras = local_data.get('cameras', {})
        for cam_id, cam_data in local_cameras.items():
            cam_base_path = f"{base_path}/cameras/{cam_id}"
            db.reference(cam_base_path).update({
                "rtsp_url": cam_data.get('rtsp_url'),
                "role": cam_data.get('role', 'gate_counter'),
            })

        # B. pull cloud logic truth 
        cloud_lot = cloud_data.get(lot_id, {})
        
        if 'capacities' in cloud_lot:
            node_config['parking_lots'][lot_id]['capacities'] = cloud_lot['capacities']
        if 'enabled' in cloud_lot:
            node_config['parking_lots'][lot_id]['enabled'] = cloud_lot['enabled']

        cloud_cameras = cloud_lot.get('cameras', {})
        
        for cam_id, local_cam in local_cameras.items():
            if cam_id in cloud_cameras:
                cloud_cam = cloud_cameras[cam_id]
                # pull polygon/enable disable overrides
                if 'polygons' in cloud_cam:
                        node_config['parking_lots'][lot_id]['cameras'][cam_id]['polygons'] = cloud_cam['polygons']
                if 'gate_logic' in cloud_cam:
                    node_config['parking_lots'][lot_id]['cameras'][cam_id]['gate_logic'] = cloud_cam['gate_logic']
                if 'roi_polygon' in cloud_cam:
                    node_config['parking_lots'][lot_id]['cameras'][cam_id]['roi_polygon'] = cloud_cam['roi_polygon']
                if 'enabled' in cloud_cam:
                        node_config['parking_lots'][lot_id]['cameras'][cam_id]['enabled'] = cloud_cam['enabled']

        # C. CLEANUP (delete removed cameras)
        if cloud_cameras:
            cloud_cam_ids = set(cloud_cameras.keys())
            local_cam_ids = set(local_cameras.keys())
            cameras_to_delete = cloud_cam_ids - local_cam_ids
            
            for cam_id_to_remove in cameras_to_delete:
                logger.warning(f"Deleting removed camera from Cloud: {cam_id_to_remove}")
                db.reference(f"{base_path}/cameras/{cam_id_to_remove}").delete()
                db.reference(f"locations/{node_name}/live_status/{lot_id}/snapshots/{cam_id_to_remove}").delete()

    logger.info("Sync Complete. Local memory aligned with Cloud Logic.")

# --- persistant counts --- 
def get_current_counts(node_config, lot_id):
    # This is for global lot counts, not specific camera history
    node_name = node_config.get('node_name')
    try:
        ref = db.reference(f"locations/{node_name}/live_status/{lot_id}/occupancy") 
        data = ref.get()
        if data is None: 
             return {'net_occupancy': 0, 'car_occupancy': 0, 'bike_occupancy': 0}
        return data if isinstance(data, dict) else {'net_occupancy': 0}
    except Exception as e:
        logger.error(f"Error fetching persistent counts for {lot_id}: {e}")
        return {'net_occupancy': 0}

# helper to get camera history
def get_camera_history(node_config, lot_id, cam_id):
    node_name = node_config.get('node_name')
    try:
        # Fetches the specific camera history (car_in, bike_in, etc)
        ref = db.reference(f"locations/{node_name}/live_status/{lot_id}/occupancy/cameras/{cam_id}")
        data = ref.get()
        # Return empty dict if null, Process Manager will handle defaults
        return data if data else {}
    except:
        return {}

# --- firebase data sync loop ---
def firebase_sync_loop(node_config, data_queue, stop_event):
    node_name = node_config.get('node_name')
    while not stop_event.is_set():
        try:
            if not data_queue.empty():
                payload = data_queue.get()
                cam_id = payload.get('id')
                lot_id = payload.get('lot_id')
                p_type = payload.get('type')
                data = payload.get('data')

                base_ref = f"locations/{node_name}"

                # --- delta logic handler ---
                if p_type == 'count_delta':
                    delta = data.get('delta')
                    v_type = data.get('vehicle_type') # 'car' or 'bike'
                    full_state = data.get('full_state', {})

                    # 1. UPDATE LOCAL CAMERA HISTORY (Persistence Layer)
                    # We save the full state (car_in, bike_in, etc) to the camera node
                    # This ensures that when the worker restarts, it gets these exact numbers back.
                    cam_ref = db.reference(f"{base_ref}/live_status/{lot_id}/occupancy/cameras/{cam_id}")
                    cam_ref.update(full_state)

                    # 2. UPDATE GLOBAL PARKING LOT OCCUPANCY (Logic Layer)
                    occ_ref = db.reference(f"{base_ref}/live_status/{lot_id}/occupancy")
                    
                    # Check Config for "Combined" vs "Separate" mode
                    # If config is missing, default to True (Combined) for safety
                    lot_caps = node_config['parking_lots'][lot_id].get('capacities', {})
                    is_combined = lot_caps.get('combined', True) 

                    def transaction_logic(current_data):
                        if not current_data:
                            current_data = {
                                "net_occupancy": 0, 
                                "car_occupancy": 0, 
                                "bike_occupancy": 0
                            }
                        
                        # --- MODE 1: COMBINED (Legacy/Simple) ---
                        if is_combined:
                            # Both cars and bikes affect the MAIN 'net_occupancy'
                            current_net = current_data.get('net_occupancy', 0)                        
                            new_net = current_net + delta
                            if new_net < 0: new_net = 0
                            current_data['net_occupancy'] = new_net
                        
                        # --- MODE 2: SEPARATE (Car vs Bike) ---
                        else:
                            if v_type == 'bike':
                                curr = current_data.get('bike_occupancy', 0)
                                new_val = curr + delta
                                if new_val < 0: new_val = 0
                                current_data['bike_occupancy'] = new_val
                            else:
                                # Cars (and trucks/buses)
                                curr = current_data.get('car_occupancy', 0)
                                new_val = curr + delta
                                if new_val < 0: new_val = 0
                                current_data['car_occupancy'] = new_val

                        return current_data

                    try:
                        occ_ref.transaction(transaction_logic)
                        mode_str = "COMBINED" if is_combined else "SEPARATE"
                        logger.info(f"Occupancy Updated [{mode_str}]. Type: {v_type}, Delta: {delta:+d}")
                    except firebase_admin.db.TransactionError as e:
                        logger.error(f"Transaction failed: {e}")

                # snapshot handler
                elif p_type == 'snapshot':
                    image_bytes = data.get('image_bytes')
                    ts = data.get('timestamp')
                    bucket = storage.bucket()

                    blob = bucket.blob(f"snapshots/{node_name}/{lot_id}/{cam_id}.jpg")
                    blob.upload_from_string(image_bytes, content_type='image/jpeg')
                    blob.make_public() 
                    
                    db.reference(f"{base_ref}/live_status/{lot_id}/snapshots/{cam_id}").update({ 
                        "url": blob.public_url, 
                        "timestamp": ts 
                    })
                    logger.info(f"Snapshot uploaded for {cam_id}")

            time.sleep(0.05)
        except Exception as e:
            logger.error(f"Sync Error: {e}")
            time.sleep(1)

# --- hearbeat ---
def heartbeat_loop(node_config, stop_event):
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

def config_monitor_loop(node_config, stop_event):
    node_name = node_config.get('node_name')
    if not node_name: return

    logger.info("Starting Remote Config Monitor...")

    while not stop_event.is_set():
        try:
            cloud_ref = db.reference(f"locations/{node_name}/config/parking_lots")
            cloud_data = cloud_ref.get()

            if cloud_data:
                for lot_id, lot_data in cloud_data.items():
                    cloud_cameras = lot_data.get('cameras', {})
                    
                    for cam_id, cam_data in cloud_cameras.items():
                        
                        if cam_data.get('restart_required') is True:
                            logger.warning(f"Config change detected for {cam_id}. Applying update...")

                            # A. UPDATE LOCAL MEMORY
                            if lot_id in node_config['parking_lots']:
                                if cam_id in node_config['parking_lots'][lot_id]['cameras']:
                                    
                                    target_cam = node_config['parking_lots'][lot_id]['cameras'][cam_id]
                                    
                                    if 'polygons' in cam_data: target_cam['polygons'] = cam_data['polygons']
                                    if 'gate_logic' in cam_data: target_cam['gate_logic'] = cam_data['gate_logic']
                                    if 'roi_polygon' in cam_data: target_cam['roi_polygon'] = cam_data['roi_polygon']
                                    if 'enabled' in cam_data: target_cam['enabled'] = cam_data['enabled']
                                    if 'rtsp_url' in cam_data: target_cam['rtsp_url'] = cam_data['rtsp_url']

                            # B. RESTART THE WORKER
                            full_cam_config = node_config['parking_lots'][lot_id]['cameras'][cam_id]
                            full_cam_config['id'] = cam_id
                            full_cam_config['lot_id'] = lot_id
                            full_cam_config['lot_type'] = node_config['parking_lots'][lot_id]['type']
                            full_cam_config['lot_capacities'] = node_config['parking_lots'][lot_id].get('capacities', {})

                            if full_cam_config.get('enabled', True):
                                pm.start_single_camera(full_cam_config, node_config)
                            else:
                                pm.stop_worker(cam_id)
                                logger.info(f"Camera {cam_id} disabled by remote config.")

                            # C. RESET THE FLAG
                            db.reference(f"locations/{node_name}/config/parking_lots/{lot_id}/cameras/{cam_id}").update({
                                "restart_required": False
                            })
                            logger.info(f"Update applied for {cam_id}. Flag cleared.")

        except Exception as e:
            logger.error(f"Config Monitor Error: {e}")
        
        time.sleep(10)