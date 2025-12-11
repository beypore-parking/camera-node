import cv2
from ultralytics import YOLO
import supervision as sv

# Replace with your RTSP URL
RTSP_URL = "rtsp://127.0.0.1:8554/stream"
MODEL_NAME = "yolov8n.pt"

# Initial capacity
TOTAL_SPOTS = 100

# Example coordinates
START_POINT = sv.Point(x=74, y=959)
END_POINT = sv.Point(x=1872, y=81)

def main():

    model = YOLO(MODEL_NAME)
    tracker = sv.ByteTrack()

    line_zone = sv.LineZone(start=START_POINT, end=END_POINT)

    box_annotator = sv.BoxAnnotator()
    label_annotator = sv.LabelAnnotator()
    line_zone_annotator = sv.LineZoneAnnotator(
        thickness=2, text_thickness=2, text_scale=0.5
    )

    # ---- RTSP CAPTURE ----
    cap = cv2.VideoCapture(RTSP_URL)

    if not cap.isOpened():
        print("Error: Could not open RTSP stream.")
        return

    print("Connected to RTSP stream — Press 'q' to quit.")
    # ----------------------

    while True:
        ret, frame = cap.read()
        if not ret:
            print("RTSP frame read failed — retrying...")
            continue

        # Run YOLO
        results = model(frame, verbose=False)[0]
        detections = sv.Detections.from_ultralytics(results)

        # Filter classes
        detections = detections[
            (detections.class_id == 2) |
            (detections.class_id == 3) |
            (detections.class_id == 5) |
            (detections.class_id == 7)
        ]

        # Tracking
        detections = tracker.update_with_detections(detections)

        # Line counter logic
        line_zone.trigger(detections)
        available_spots = TOTAL_SPOTS - line_zone.in_count + line_zone.out_count

        # Annotate
        labels = [f"#{tracker_id}" for tracker_id in detections.tracker_id]

        annotated_frame = box_annotator.annotate(frame.copy(), detections)
        annotated_frame = label_annotator.annotate(annotated_frame, detections, labels)
        line_zone_annotator.annotate(annotated_frame, line_counter=line_zone)

        cv2.putText(
            annotated_frame,
            f"SPOTS AVAILABLE: {available_spots}",
            (50, 80),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.5,
            (0, 255, 0),
            3
        )

        cv2.imshow("RTSP Vehicle Counter", annotated_frame)

        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()