import os
import json
import logging

logger = logging.getLogger("ConfigManager")

# skeleton config file
DEFAULT_CONFIG_TEMPLATE = {
    "node_name": "ENTER_NODE_NAME_HERE", 
    "firebase_db_url": "ENTER_FIREBASE_DB_URL_HERE",
    "firebase_storage_bucket": "ENTER_STORAGE_BUCKET_HERE",
    "service_account_path": "config/serviceAccountKey.json",
    "parking_lots": {} 
}

def validate_gate_logic(lot_id, cam_id, cam_data, lot_capacities):
    """
    Specific checks for Gate logic. Returns a list of warning strings.
    """
    warnings = []
    
    # check line coordinates
    gate_logic = cam_data.get('gate_logic', {})
    coords = gate_logic.get('line_coords', [])
    if not coords or len(coords) != 4:
         warnings.append(f"[{lot_id}][{cam_id}] Missing or invalid 'line_coords'. Camera will not count.")

    # check capacity mode
    if not lot_capacities:
        warnings.append(f"[{lot_id}] No 'capacities' block defined.")
    else:
        is_combined = lot_capacities.get('combined')
        
        if is_combined is None:
             warnings.append(f"[{lot_id}] 'capacities.combined' (true/false) is missing.")
        
        elif is_combined is True:
            # check for total
            if lot_capacities.get('total') is None:
                warnings.append(f"[{lot_id}] Combined mode is TRUE, but 'total' capacity is missing.")
        
        elif is_combined is False:
            # check for car/bike
            if lot_capacities.get('car') is None:
                warnings.append(f"[{lot_id}] Combined mode is FALSE, but 'car' capacity is missing.")
            if lot_capacities.get('bike') is None:
                warnings.append(f"[{lot_id}] Combined mode is FALSE, but 'bike' capacity is missing.")

    return warnings

def load_and_validate_config(config_path):
    # checks if config file exists.
    # if NOT, creates a dummy file and raises an error to stop the app.
    # if YES, validate critical fields (DB URL, Node Name).
    
    # create default if missing
    if not os.path.exists(config_path):
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, 'w') as f:
            json.dump(DEFAULT_CONFIG_TEMPLATE, f, indent=4)
        
        print("\n" + "!"*60)
        print(f" CONFIG FILE MISSING: Created template at '{config_path}'")
        print(" PLEASE EDIT THE FILE WITH YOUR NODE NAME AND FIREBASE DETAILS.")
        print("!"*60 + "\n")
        return None  # signal to main.py to exit

    # load existing
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"Config file is invalid JSON: {e}")
        return None

    # validate critical fields
    missing_fields = []
    
    # check node name
    if not config.get("node_name") or "ENTER_NODE_NAME" in config["node_name"]:
        missing_fields.append("node_name")

    # check database URL
    if not config.get("firebase_db_url") or "ENTER_FIREBASE" in config["firebase_db_url"]:
        missing_fields.append("firebase_db_url")
        
    # check storage bucket
    if not config.get("firebase_storage_bucket") or "ENTER_STORAGE" in config["firebase_storage_bucket"]:
        missing_fields.append("firebase_storage_bucket")

    if missing_fields:
        print("\n" + "x"*60)
        print(f" CRITICAL CONFIG ERROR: Missing fields -> {', '.join(missing_fields)}")
        print("x"*60 + "\n")
        return None

    # allow the app to start but scream at them in the logs if they dont have soem fields
    parking_lots = config.get('parking_lots', {})
    
    for lot_id, lot_data in parking_lots.items():
        if not lot_data.get('enabled', False):
            continue

        lot_type = lot_data.get('type')
        capacities = lot_data.get('capacities', {})
        cameras = lot_data.get('cameras', {})

        if lot_type == 'gate':
            for cam_id, cam_data in cameras.items():
                if cam_data.get('enabled', False):
                    gate_warnings = validate_gate_logic(lot_id, cam_id, cam_data, capacities)
                    for w in gate_warnings:
                        logger.warning(w)

    return config
