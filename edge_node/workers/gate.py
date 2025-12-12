import cv2
import time
import logging
import signal
from ultralytics import YOLO
import supervision as sv
from datetime import datetime

# Import NodeFormatter to maintain consistent output
from helpers.logger import NodeFormatter 

# --- setup ---
def setup_worker_logger(cam_id):
    logger = logging.getLogger(f"Worker-{cam_id}")
    logger.setLevel(logging.INFO) 
    
    # add gandler to ensure logs appear in console
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(NodeFormatter())   
        logger.addHandler(handler)
    
    return logging.LoggerAdapter(logger, {'location_name': cam_id})

# --- main worker function ---
def run_gate_worker(cam_config, data_queue, node_name, stop_event):
    # NECESSAERY TO PREVENT SHUTDOWN BUGS
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)

    worker_logger = setup_worker_logger(cam_config['id'])
    
    # 1. extract config
    cam_id = cam_config['id']
    lot_id = cam_config['lot_id']
    rtsp_url = cam_config.get('rtsp_url')
    
    # check if this is a video file (ONLY ON DEV)
    is_video_file = str(rtsp_url).endswith('.mp4')
    
    # logic and capacity
    gate_logic = cam_config.get('gate_logic', {})
    line_coords = gate_logic.get('line_coords', []) # [x1, y1, x2, y2]
    
    # we still use this so the video overlay doesn't reset to 0 -> only in dev mode tho
    initial_counts = cam_config.get('initial_state', {})
    initial_in_count = initial_counts.get('in_count', 0)
    initial_out_count = initial_counts.get('out_count', 0)
    
    worker_logger.info(f"Worker initialized. Local history: IN {initial_in_count} / OUT {initial_out_count}")
        
    if len(line_coords) != 4 or not rtsp_url:
        worker_logger.error("Missing critical configuration (RTSP URL or line coordinates). Exiting.")
        return

    # 2. setup CV components
    try:
        model = YOLO("yolov8n.pt")
        tracker = sv.ByteTrack()
        
        # annotators for visualization (dev Mode and snapshots)
        box_annotator = sv.BoxAnnotator()
        label_annotator = sv.LabelAnnotator()
        line_zone_annotator = sv.LineZoneAnnotator(thickness=2, text_thickness=2, text_scale=0.5)

        # create sv.Point objects from the list
        start_point = sv.Point(x=line_coords[0], y=line_coords[1]) 
        end_point = sv.Point(x=line_coords[2], y=line_coords[3])
        
        # initialize LineZone and THEN set internal attributes - need to do this for the counting
        line_zone = sv.LineZone(
            start=start_point, 
            end=end_point,
        )
        line_zone._in_count = initial_in_count
        line_zone._out_count = initial_out_count

        cap = cv2.VideoCapture(rtsp_url)
        
        if not cap.isOpened():
            worker_logger.error(f"Could not open source: {rtsp_url}")
            cap.release()
            return
            
        worker_logger.info(f"Connected to source: {rtsp_url} (Dev Mode: {is_video_file})")

    except Exception as e:
        worker_logger.critical(f"Setup failed: {e}")
        return

    vehicle_classes = [2, 3, 5, 7]
    
    last_snapshot_time = time.time()
    snapshot_interval_sec = 60
    
    # main loop
    while not stop_event.is_set():
        ret, frame = cap.read()
        
        if not ret:
            if is_video_file:
                # loop video file for dev purposes
                worker_logger.info("Video ended, restarting loop...")
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue
            else:
                worker_logger.warning("RTSP frame read failed — retrying...")
                time.sleep(1)
                continue
            
        current_in_count = line_zone.in_count
        current_out_count = line_zone.out_count

        # 3. process frame
        try:
            results = model(frame, verbose=False)[0]
            detections = sv.Detections.from_ultralytics(results)
            
            detections = detections[
                [cls_id in vehicle_classes for cls_id in detections.class_id]
            ]

            detections = tracker.update_with_detections(detections)
            line_zone.trigger(detections)
            
            # --- DEV VIEWER (only if mp4 is given in the config) ---
            if is_video_file:
                debug_frame = frame.copy()
                labels = [f"#{tracker_id}" for tracker_id in detections.tracker_id]
                
                # draw boxes/labels/lines
                debug_frame = box_annotator.annotate(debug_frame, detections)
                debug_frame = label_annotator.annotate(debug_frame, detections, labels)
                line_zone_annotator.annotate(debug_frame, line_counter=line_zone)
                
                # resize to ~640px width
                height, width = debug_frame.shape[:2]
                new_width = 640
                new_height = int(height * (new_width / width))
                debug_frame = cv2.resize(debug_frame, (new_width, new_height))
                
                cv2.imshow(f"Dev: {cam_id}", debug_frame)
                cv2.waitKey(1)
            # ---------------------------------
            
            # 4. detect and publish changes
            if line_zone.in_count != current_in_count or line_zone.out_count != current_out_count:
                # determine delta
                delta = 0
                direction = "NONE"
                # detect the delta as -1 and +1 and send it ot the process manager where it'll be counted
                if line_zone.in_count > current_in_count:
                    direction = "IN"
                    delta = 1
                elif line_zone.out_count > current_out_count:
                    direction = "OUT"
                    delta = -1
                
                worker_logger.info(f"VEHICLE DETECTED: {direction} (Delta: {delta})")

                # send delta payload
                # we're not sending the total, just the change.
                # this allows multiple cameras to simply "vote" to change the number.
                data_queue.put({
                    "id": cam_id,
                    "lot_id": lot_id,
                    "type": "count_delta",
                    "data": {
                        "delta": delta,
                        # we still send these for the "visual" history of this specific camera
                        "local_in": line_zone.in_count,
                        "local_out": line_zone.out_count
                    }
                })
                
                last_snapshot_time = 0

            # 5. handle snapshots
            if time.time() - last_snapshot_time > snapshot_interval_sec or cam_config.get('force_startup_snapshot'):
                
                if cam_config.get('force_startup_snapshot'): cam_config['force_startup_snapshot'] = False
                
                annotated_frame = frame.copy()
                line_zone_annotator.annotate(annotated_frame, line_counter=line_zone)
                # Overlay local counts
                cv2.putText(annotated_frame, f"LOCAL HIST - IN: {line_zone.in_count} | OUT: {line_zone.out_count}", (50, 80), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 255), 2)
                
                ret_code, image_buffer = cv2.imencode(".jpg", annotated_frame)
                if ret_code:
                    data_queue.put({
                        "id": cam_id, 
                        "lot_id": lot_id, 
                        "type": "snapshot", 
                        "data": {"image_bytes": image_buffer.tobytes(), "timestamp": datetime.now().isoformat()}
                    })
                    last_snapshot_time = time.time()
                
        except Exception as e:
            worker_logger.error(f"Processing loop error: {e}")
            time.sleep(5)

    cap.release()
    cv2.destroyAllWindows()
    worker_logger.info("Worker gracefully terminated.")