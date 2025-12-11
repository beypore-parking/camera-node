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
        print(f" CRITICAL CONFIG ERROR: The following fields are missing or default:")
        print(f" -> {', '.join(missing_fields)}")
        print(f" Please update '{config_path}' and restart.")
        print("x"*60 + "\n")
        return None

    return config