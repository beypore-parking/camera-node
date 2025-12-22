import cv2
import time
import logging
import signal
from ultralytics import YOLO
import supervision as sv
from datetime import datetime
from vidgear.gears import CamGear
import os

# import NodeFormatter to maintain consistent output
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
    
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"

    # options for the vidgear
    options = {
        "CAP_PROP_BUFFERSIZE": 1,        # keep buffer tiny for low latency
        "THREADED_QUEUE_MODE": True,     # enable the 'buffer buster' thread
    }

    # check if this is a video file (ONLY ON DEV)
    is_video_file = str(rtsp_url).endswith('.mp4')
    
    # logic and capacity
    gate_logic = cam_config.get('gate_logic', {})
    line_coords = gate_logic.get('line_coords', []) # [x1, y1, x2, y2]
    
    # we still use this so the video overlay doesn't reset to 0 -> only in dev mode tho
    # load prebiosu counts
    # look for specific keys in the history -> if missing default to 0
    history = cam_config.get('initial_state', {})
    
    init_car_in = history.get('car_in', 0)
    init_car_out = history.get('car_out', 0)
    init_bike_in = history.get('bike_in', 0)
    init_bike_out = history.get('bike_out', 0)
    
    worker_logger.info(f"Restored History -> CAR: {init_car_in}/{init_car_out} | BIKE: {init_bike_in}/{init_bike_out}")
        
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
        
        # create sv.Point objects from the list
        start_point = sv.Point(x=line_coords[0], y=line_coords[1]) 
        end_point = sv.Point(x=line_coords[2], y=line_coords[3])
        
        # two line zones 
        # car zone
        lz_car = sv.LineZone(start=start_point, end=end_point)
        lz_car._in_count = init_car_in
        lz_car._out_count = init_car_out

        # bike zone
        lz_bike = sv.LineZone(start=start_point, end=end_point)
        lz_bike._in_count = init_bike_in
        lz_bike._out_count = init_bike_out
        
        # visualizer
        lz_annotator = sv.LineZoneAnnotator(thickness=2, text_thickness=2, text_scale=0.5)

        # initialize camgear isntead of cv2
        stream = CamGear(source=rtsp_url, **options).start()
        
        # check if the stream worked
        if stream is None:
            worker_logger.error(f"Could not open source: {rtsp_url}")
            return
        
        # yay stream worked
        worker_logger.info(f"Connected to source via CamGear: {rtsp_url} (Dev Mode: {is_video_file})")

    except Exception as e:
        worker_logger.critical(f"Setup failed: {e}")
        return

    # define classes
    bike_classes = [3] 
    car_classes = [2, 5, 7]
    all_classes = bike_classes + car_classes
    
    last_snapshot_time = time.time()
    snapshot_interval_sec = 60
    
    # main loop
    while not stop_event.is_set():
        # non blocking stream read
        frame = stream.read()
        
        # hadle frame drop or disconnect
        if frame is None:
            if is_video_file:
                # loop video file for dev purposes
                worker_logger.info("Video ended, restarting loop...")
                # re-initalize camgear for non looping streams
                stream.stop()
                stream = CamGear(source=rtsp_url, **options).start()
                continue
            else:
                worker_logger.warning("RTSP frame read failed — retrying...")
                time.sleep(1)
                continue
            
        # snapshot current counts BEFORE processing to detect changes
        prev_car_in = lz_car.in_count
        prev_car_out = lz_car.out_count
        prev_bike_in = lz_bike.in_count
        prev_bike_out = lz_bike.out_count

        # 3. process frame
        try:
            results = model(frame, verbose=False)[0]
            detections = sv.Detections.from_ultralytics(results)
            
            # filter for all valid vehicles first
            detections = detections[
                [cls_id in all_classes for cls_id in detections.class_id]
            ]

            # track everything
            detections = tracker.update_with_detections(detections)

            # sub-filters for the LineZones
            mask_car = [cls_id in car_classes for cls_id in detections.class_id]
            dets_car = detections[mask_car]
            
            mask_bike = [cls_id in bike_classes for cls_id in detections.class_id]
            dets_bike = detections[mask_bike]

            # trigger BOTH zones independently
            lz_car.trigger(dets_car)
            lz_bike.trigger(dets_bike)
            
            # --- DEV VIEWER (only if mp4 is given in the config) ---
            if is_video_file:
                debug_frame = frame.copy()
                labels = [f"#{tracker_id}" for tracker_id in detections.tracker_id]
                
                # draw boxes/labels/lines
                debug_frame = box_annotator.annotate(debug_frame, detections)
                debug_frame = label_annotator.annotate(debug_frame, detections, labels)
                lz_annotator.annotate(debug_frame, line_counter=lz_car) 
                
                # resize to ~640px width
                height, width = debug_frame.shape[:2]
                new_width = 640
                new_height = int(height * (new_width / width))
                debug_frame = cv2.resize(debug_frame, (new_width, new_height))
                
                cv2.imshow(f"Dev: {cam_id}", debug_frame)
                cv2.waitKey(1)

            debug_frame = frame.copy()
            labels = [f"#{tracker_id}" for tracker_id in detections.tracker_id]
            
            # draw boxes/labels/lines
            debug_frame = box_annotator.annotate(debug_frame, detections)
            debug_frame = label_annotator.annotate(debug_frame, detections, labels)
            lz_annotator.annotate(debug_frame, line_counter=lz_car) 
            
            # resize to ~640px width
            height, width = debug_frame.shape[:2]
            new_width = 640
            new_height = int(height * (new_width / width))
            debug_frame = cv2.resize(debug_frame, (new_width, new_height))
            
            cv2.imshow(f"Dev: {cam_id}", debug_frame)
            cv2.waitKey(1)
            # ---------------------------------
            
            # 4. detect and publish changes
            # we check both zones. If BOTH happen in the same frame (rare), we send two messages.

            # check cars
            if lz_car.in_count != prev_car_in or lz_car.out_count != prev_car_out:
                # determine delta
                # detect the delta as -1 and +1 and send it ot the process manager where it'll be counted
                delta = 1 if lz_car.in_count > prev_car_in else -1
                direction = "IN" if delta > 0 else "OUT"
                
                worker_logger.info(f"CAR {direction} (Total: In = {lz_car.in_count}, Out = {lz_car.out_count})")

                # send delta payload
                # we're not sending the total, just the change.
                # this allows multiple cameras to simply "vote" to change the number.
                # but now we send separely for car and bike
                data_queue.put({
                    "id": cam_id, "lot_id": lot_id, "type": "count_delta",
                    "data": {
                        "delta": delta,
                        "vehicle_type": "car",
                        # send the full flattened state so sync func can save it all
                        "full_state": {
                            "car_in": lz_car.in_count, "car_out": lz_car.out_count,
                            "bike_in": lz_bike.in_count, "bike_out": lz_bike.out_count
                        }
                    }
                })
                last_snapshot_time = 0 # force snapshot update on change

            # check bikes
            if lz_bike.in_count != prev_bike_in or lz_bike.out_count != prev_bike_out:
                delta = 1 if lz_bike.in_count > prev_bike_in else -1
                direction = "IN" if delta > 0 else "OUT"
                
                worker_logger.info(f"BIKE {direction} (Total: In = {lz_bike.in_count}, Out = {lz_bike.out_count})")

                data_queue.put({
                    "id": cam_id, "lot_id": lot_id, "type": "count_delta",
                    "data": {
                        "delta": delta,
                        "vehicle_type": "bike",
                        "full_state": {
                            "car_in": lz_car.in_count, "car_out": lz_car.out_count,
                            "bike_in": lz_bike.in_count, "bike_out": lz_bike.out_count
                        }
                    }
                })
                last_snapshot_time = 0

            # 5. handle snapshots
            if time.time() - last_snapshot_time > snapshot_interval_sec or cam_config.get('force_startup_snapshot'):
                
                if cam_config.get('force_startup_snapshot'): cam_config['force_startup_snapshot'] = False
                
                annotated_frame = frame.copy()
                # lz_annotator.annotate(annotated_frame, line_counter=lz_car)
                # line_zone_annotator.annotate(annotated_frame, line_counter=line_zone)
                
                # overlay local counts
                cv2.putText(annotated_frame, f"CARS : IN {lz_car.in_count} | OUT {lz_car.out_count}", (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
                cv2.putText(annotated_frame, f"BIKES: IN {lz_bike.in_count} | OUT {lz_bike.out_count}", (20, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 0, 0), 2)
                
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

    # drop the stream
    stream.stop()
    cv2.destroyAllWindows()
    worker_logger.info("Worker gracefully terminated.")