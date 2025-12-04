import time
import logging
import signal
import cv2
import torch
import numpy as np
from datetime import datetime
from ultralytics import RTDETR, YOLO

# Standard Logger (will be adapted)
worker_logger = logging.getLogger("Orchestrator")

def run_offstreet_worker(config, data_queue, node_name, stop_event):
    """
    Production Worker for Off-Street Parking (Fixed Spots).
    """
    # reset signal handlers to prevent double-cleanup crashes
    try:
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
    except ValueError:
        pass

    # setup context & logging
    camera_id = config['id']
    rtsp_url = config['url']
    # load polygons from the orchestrator config 
    polygons = config.get('polygons', []) 
    
    log = logging.LoggerAdapter(worker_logger, {'location_name': f"{node_name}:{camera_id}"})
    log.info("Off-Street Worker INITIALIZED.")

    # load model (check CUDA)
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    log.info(f"Loading Model on: {device}")
    
    try:
        model = RTDETR("rtdetr-x.pt") 
        model.to(device)
    except Exception as e:
        log.error(f"Failed to load model: {e}")
        return

    # open Stream
    cap = cv2.VideoCapture(rtsp_url)
    
    # logic parameters
    frame_interval = 5  # process every 5th frame to save CPU
    frame_count = 0
    
    # state tracking (to prevent spamming the queue if status hasn't changed)
    previous_state = {} 

    log.info("Starting Processing Loop...")

    while not stop_event.is_set():
        ret, frame = cap.read()
        
        if not ret:
            log.warning("Video feed lost. Retrying in 2s...")
            time.sleep(2)
            cap = cv2.VideoCapture(rtsp_url) # reconnect
            continue

        # skip frames to improve performance
        frame_count += 1
        if frame_count % frame_interval != 0:
            continue

        try:
            # --- inference ---
            # using conf=0.25 as per old script
            results = model.predict(frame, conf=0.25, verbose=False, device=device)
            detections = results[0].boxes.data.cpu().numpy()

            # --- processing data structures ---
            # dictionary: { "Slot_ID": "Occupied", "Slot_ID_2": "Free" }
            current_occupancy = {}
            
            # initialize all slots as free first
            # assuming polygon structure in config is: {'label': 'C1', 'points': [[x,y],...], 'type': 'car'}
            for poly in polygons:
                slot_id = poly.get('label', 'Unknown')
                current_occupancy[slot_id] = "Free"

            # --- point-in-polygon logic ---
            for row in detections:
                x1, y1, x2, y2, conf, cls = map(int, row[:6])
                
                # calculate center point
                cx = (x1 + x2) // 2
                cy = (y1 + y2) // 2
                center_point = (cx, cy)

                # check this dot against ALL polygons
                for poly in polygons:
                    # convert points to numpy array for OpenCV
                    # ensure config points are [[x,y], [x,y]]
                    pts = np.array(poly['points'], np.int32)
                    
                    # check if center_point is inside pts
                    # params: (InputArray contour, Point2f pt, bool measureDist)
                    hit = cv2.pointPolygonTest(pts, center_point, False)
                    
                    if hit >= 0: # 0=on edge, >0=inside
                        slot_id = poly.get('label')
                        current_occupancy[slot_id] = "Occupied"
                        # we don't break cos one big truck might span two spots (edge case)

            # --- send data to orchestrator ---
            # optimization: only push to queue if data CHANGED or every N seconds -> later
            # for now: push every update
            
            payload = {
                "id": camera_id,
                "data": {
                    "slots": current_occupancy,
                    "last_update": datetime.now().isoformat()
                }
            }
            
            data_queue.put(payload)
            
            # sleep slightly to free up CPU
            # time.sleep(0.05) 

        except Exception as e:
            log.error(f"Processing Error: {e}")
            time.sleep(1)

    # cleanup
    cap.release()
    log.info("Off-Street Worker SHUTDOWN COMPLETE.")