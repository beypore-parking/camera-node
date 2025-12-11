import os
import time
import firebase_admin
from datetime import datetime
from firebase_admin import credentials, db, storage
from helpers.logger import logger

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
def get_current_counts(node_config, lot_id):
    # fetches current occupancy numbers from firebase. 
    # needed for Gate Workers to not reset to 0 on restart.
        
    node_name = node_config.get('node_name')
    try:
        ref = db.reference(f"locations/{node_name}/live_status/{lot_id}/occupancy")
        data = ref.get()
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