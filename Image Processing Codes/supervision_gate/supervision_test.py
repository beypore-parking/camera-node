import cv2
from ultralytics import YOLO
import supervision as sv

# Replace this later on with the stsp stream
VIDEO_PATH = "vid2.mp4" 
MODEL_NAME = "yolov8n.pt"

# Initial Capacity 
TOTAL_SPOTS = 200 

# COORDINATES: Drawing from RIGHT (1280) to LEFT (0)
# This sets the vector so that moving UP (Bottom -> Top) triggers the "IN" counter.
# We can change this direction logic from the admin panel as well, for now keeping this as the default direction vector

# Take these coordinates from the find_coordinates helper script for the particular stream
# These will be fetched from the Realtime database after being set by the admin panel
START_POINT = sv.Point(x=57, y=677) 
END_POINT = sv.Point(x=1895, y=671)

def main():

    model = YOLO(MODEL_NAME)
    tracker = sv.ByteTrack()

    # start = Right, end = Left means "IN" is moving Up
    line_zone = sv.LineZone(start=START_POINT, end=END_POINT)

    # Annotators for reference 
    box_annotator = sv.BoxAnnotator()
    label_annotator = sv.LabelAnnotator()
    line_zone_annotator = sv.LineZoneAnnotator(
        thickness=2, text_thickness=2, text_scale=0.5
    )

    frame_generator = sv.get_video_frames_generator(VIDEO_PATH)
    print("Starting - Press 'q' to quit.")

    for frame in frame_generator:
        
        # Detecting Vehicles 
        results = model(frame, verbose=False)[0]
        detections = sv.Detections.from_ultralytics(results)
        
        # Filter ( Car = 2, Motorcycle = 3, Bus = 5, Truck = 7 )
        detections = detections[
            (detections.class_id == 2) | 
            (detections.class_id == 3) | 
            (detections.class_id == 5) | 
            (detections.class_id == 7)
        ]

        # Track using ByteTrack
        detections = tracker.update_with_detections(detections)

        # Trigger Logic
        line_zone.trigger(detections)

        # Start with Total
        # Subtract cars that went in
        # Add cars that went out
        available_spots = TOTAL_SPOTS - line_zone.in_count + line_zone.out_count

        # Annotate to check if vehicle tarcking works properly or not 
        labels = [f"#{tracker_id}" for tracker_id in detections.tracker_id]
        
        annotated_frame = box_annotator.annotate(frame.copy(), detections)
        annotated_frame = label_annotator.annotate(annotated_frame, detections, labels)
        line_zone_annotator.annotate(annotated_frame, line_counter=line_zone)

        # Displaying available spots in top left to see if it works
        cv2.putText(
            annotated_frame, 
            f"SPOTS AVAILABLE: {available_spots}", 
            (50, 80), 
            cv2.FONT_HERSHEY_SIMPLEX, 
            1.5, 
            (0, 255, 0), 
            3
        )
        
        cv2.imshow("Parking System", annotated_frame) 

        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()