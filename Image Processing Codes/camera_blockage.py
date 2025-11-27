# -*- coding: utf-8 -*-
"""
Created on Sat Aug 31 11:57:11 2024

@author: DELL
"""

# -*- coding: utf-8 -*-
"""
Created on Thu Aug 22 15:36:36 2024

@author: DELL
"""


import cv2
import torch
import numpy as np
import json
import os
import firebase_admin
from firebase_admin import db, storage,firestore
from ultralytics import YOLO
import time
import datetime
from ultralytics import RTDETR
from collections import deque
# Check if CUDA is available
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
print(f"Using device: {device}")

# Load YOLO model
model = RTDETR("rtdetr-x.pt")
model.to(device)  # Move model to GPU

# Initialize the list to store the coordinates of the polygons
polygons = []
current_polygon = []  # Initialize the current polygon being drawn
selected_polygon_index = None  # Index of the selected polygon for dragging
dragging = False  # Flag to indicate if a polygon is being dragged
drag_offset = (0, 0)  # Offset between the click point and the polygon's origin

# File to save polygons
polygons_file = 'path to polygon json file'




# Load polygons from file if it exists
if os.path.exists(polygons_file):
    with open(polygons_file, 'r') as file:
        polygons = json.load(file)

# Function to shift a polygon by a certain offset
def shift_polygon(polygon, offset_x, offset_y):
    return [(x + offset_x, y + offset_y) for x, y in polygon]


# Function to undo the last added vertex of the current polygon
def undo_last_vertex():
    if current_polygon:
        current_polygon.pop()

# Define mouse event handler function
def handle_mouse(event, x, y, flags, param):
    global polygons, current_polygon, selected_polygon_index, dragging, drag_offset

    if event == cv2.EVENT_LBUTTONDOWN:
        # Check if we are clicking inside an existing polygon
        for i, polygon in enumerate(polygons):
            if cv2.pointPolygonTest(np.array(polygon, np.int32), (x, y), False) >= 0:
                selected_polygon_index = i
                drag_offset = (x - polygon[0][0], y - polygon[0][1])
                dragging = True
                return

        # If not clicking inside a polygon, start drawing a new one
        current_polygon.append((x, y))

    elif event == cv2.EVENT_MOUSEMOVE:
        if dragging and selected_polygon_index is not None:
            # Calculate new position of the polygon based on the drag offset
            new_polygon = shift_polygon(polygons[selected_polygon_index], x - polygons[selected_polygon_index][0][0] - drag_offset[0], y - polygons[selected_polygon_index][0][1] - drag_offset[1])
            polygons[selected_polygon_index] = new_polygon

        elif flags & cv2.EVENT_FLAG_LBUTTON:
            # Update the display while drawing a new polygon
            frame_copy = param.copy()
            for i in range(len(current_polygon) - 1):
                cv2.line(frame_copy, current_polygon[i], current_polygon[i+1], (0, 255, 0), 2)
            if current_polygon:
                cv2.line(frame_copy, current_polygon[-1], (x, y), (0, 255, 0), 2)
            cv2.imshow('Draw Polygons', frame_copy)

    elif event == cv2.EVENT_LBUTTONUP:
        if dragging:
            dragging = False
            selected_polygon_index = None
        elif len(current_polygon) > 2 and cv2.norm(current_polygon[0], (x, y)) < 10:
            # Close the polygon if the last point is close to the first point
            polygons.append(current_polygon.copy())
            current_polygon = []
            # Save polygons to file
            with open(polygons_file, 'w') as file:
                json.dump(polygons, file)

    elif event == cv2.EVENT_LBUTTONDBLCLK:
        if len(current_polygon) > 2:
            # Close the polygon
            cv2.line(param, current_polygon[-1], current_polygon[0], (0, 255, 0), 2)
            polygons.append(current_polygon.copy())
            current_polygon = []
            # Save polygons to file
            with open(polygons_file, 'w') as file:
                json.dump(polygons, file)

# Function to allocate slots for cars and bikes
def allocate_slots(total_slots):
    big_car_slots = total_slots // 6
    car_slots = total_slots // 5
    bike_slots = total_slots

    slots = ['bike'] * bike_slots

    # Allocate consecutive slots for big cars, trying to keep them near the start
    big_car_slot_indices = []
    for i in range(big_car_slots):
        slots[i*6:(i*6)+6] = ['car'] * 6
        big_car_slot_indices.append(tuple(range(i*6, (i*6)+6)))

    # Allocate consecutive slots for cars, trying to keep them near the start
    car_slot_indices = []
    for i in range(car_slots):
        slots[i*5:(i*5)+5] = ['car'] * 5
        car_slot_indices.append(tuple(range(i*5, (i*5)+5)))

    return slots, big_car_slot_indices, car_slot_indices

# Function to count available big car slots based on consecutive empty slots (7 consecutive slots)
def count_available_car_slots(slots, car_counts):
    available_car_slots = 0

    i = 0
    while i < 13:
        # Check if the current slot and the next 6 slots are unoccupied
        if (slots[i] == 'car' and all(car_counts[i + j] == 0 for j in range(6))):
            available_car_slots += 1
            # Skip to the slot after the last one checked (to avoid counting overlapping groups)
            i += 6
        else:
            i += 1
    i = 18
    while i < len(slots) - 5:
        # Check if the current slot and the next 6 slots are unoccupied
        if (slots[i] == 'car' and all(car_counts[i + j] == 0 for j in range(6))):
            available_car_slots += 1
            # Skip to the slot after the last one checked (to avoid counting overlapping groups)
            i += 6
        else:
            i += 1           

    return available_car_slots

# Function to count available car slots based on consecutive empty slots (5 consecutive slots)
def count_available_car_slots1(slots, car_counts):
    available_car_slots = 0

    i = 0
    while i < 14 :
        # Check if the current slot and the next 4 slots are unoccupied
        if (slots[i] == 'car' and all(car_counts[i + j] == 0 for j in range(5))):
            available_car_slots += 1
            # Skip to the slot after the last one checked (to avoid counting overlapping groups)
            i += 5
        else:
            i += 1
    i = 18
    while i < len(slots) - 4:
        # Check if the current slot and the next 4 slots are unoccupied
        if (slots[i] == 'car' and all(car_counts[i + j] == 0 for j in range(5))):
            available_car_slots += 1
            # Skip to the slot after the last one checked (to avoid counting overlapping groups)
            i += 5
        else:
            i += 1
    return available_car_slots




# Function to upload a frame to Firebase Storage
def upload_frame_to_firebase(frame, filename):
    _, img_encoded = cv2.imencode('.jpg', frame)
    img_bytes = img_encoded.tobytes()

    # Get a reference to the Firebase Storage bucket
    bucket = storage.bucket()
    blob = bucket.blob(filename)
    blob.upload_from_string(img_bytes, content_type='image/jpeg')
    blob.make_public()
    image_url = blob.generate_signed_url(expiration=datetime.timedelta(days= 365))
    print(f"Uploaded {filename} to Firebase Storage. URL: {image_url}")
        
    return image_url

# Open the video
#cap = cv2.VideoCapture('rtsp://admin:parking_12@192.168.0.7')
#cap = cv2.VideoCapture(r"C:\Users\DELL\Desktop\smartparking\videos\output_video_pranav.avi")



# Load class list for YOLO
with open("C:/Users/DELL/Desktop/smartparking/coco.txt", "r") as my_file:
    class_list = my_file.read().split("\n")

# Initialize Firebase app
cred_obj = firebase_admin.credentials.Certificate('path to firebase admin json file')
default_app = firebase_admin.initialize_app(cred_obj, {
    'databaseURL': 'path to database url',
    'storageBucket': 'path to storage bucket'
})



# =============================================================================
# def send_parking_obstacle_notif(slot):
#     cap = cv2.VideoCapture(0)
#     ret, frame = cap.read()
# 
#     if ret:
#         # Convert the frame to JPEG format
#         _, buffer = cv2.imencode('.jpg', frame)
#         jpg_as_text = buffer.tobytes()
# 
#     # Create a unique filename
#         filename = f'frames/frame_{int(time.time())}.jpg'
# 
#     # Upload the frame to Firebase Storage
#         bucket = storage.bucket()
#         blob = bucket.blob(filename)
#         blob.upload_from_string(jpg_as_text, content_type='image/jpeg')
# 
#     # Get the download URL
#         download_url = blob.generate_signed_url(timedelta(seconds=300), method='GET')
#         print(f'Download URL: {download_url}')
#         doc_ref.update({'parkingObstacle':f'{slot}:{download_url}'})
# =============================================================================

# Function to update slots in Firebase

def update_slots(available_car_spaces, available_big_car_spaces, available_bike_spaces, image_url):
    ref = db.reference('onstreet_spots/CSE Department')
    ref.update({'car/0': available_car_spaces, 'bigCarSpots/0': available_big_car_spaces, 'bike/0': available_bike_spaces,'image/0': image_url})

def upload_frame_to_firebase(frame, filename):
    _, img_encoded = cv2.imencode('.jpg', frame)
    img_bytes = img_encoded.tobytes()

    # Get a reference to the Firebase Storage bucket
    bucket = storage.bucket()
    blob = bucket.blob(filename)
    blob.upload_from_string(img_bytes, content_type='image/jpeg')
    blob.make_public()
    image_url = blob.generate_signed_url(expiration=datetime.timedelta(days=365))
    print(f"Uploaded {filename} to Firebase Storage. URL: {image_url}")

    return image_url

def store_image_url_in_firestore(slot_id, image_url, document_id='dummy'):
    db = firestore.client()
    # Reference to the Firestore collection and document
    ref = db.collection('admins').document(document_id)
    
    # Update the 'parkingObstacles' field with the new slot:image_url entry
    ref.update({
        f'parkingObstacle.{slot_id}': image_url
    })
    print(f"Stored image URL for slot '{slot_id}' in Firestore: {image_url}")

def camera_notif(cam_id,image_url,document_id='dummy'):
    db = firestore.client()
    # Reference to the Firestore collection and document
    ref = db.collection('admins').document(document_id)
    
    # Update the 'parkingObstacles' field with the new slot:image_url entry
    ref.update({
        f'camera.{cam_id}': image_url
    })
    print(f"Stored image URL for slot '{cam_id}' in Firestore: {image_url}")
def initialize_video_capture():
    #cap = cv2.VideoCapture(r"C:\Users\DELL\Desktop\smartparking\videos\output_video_pranav1.avi") 
    cap = cv2.VideoCapture(r'rtsp://admin:parking_12@192.168.0.8')#
    return cap

def reconnect_video_capture(max_retries=5, retry_interval=5):
    retries = 0
    while retries < max_retries:
        cap = initialize_video_capture()
        if cap.isOpened():
            return cap
        print(f"Attempting to reconnect ({retries + 1}/{max_retries})...")
        time.sleep(retry_interval)
        retries += 1
    print("Error: Unable to reconnect to video feed.")
    return None
# Initialize video capture
cap = initialize_video_capture()

if not cap.isOpened():
    cap = reconnect_video_capture()
    if cap is None:
        print("Exiting due to failure to connect to video feed.")
        exit()

# Get video frame dimensions
width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
window_width = 800
window_height = int(window_width * (height / width))
cv2.namedWindow('Draw Polygons', cv2.WINDOW_NORMAL)
cv2.resizeWindow('Draw Polygons', window_width, window_height)

# Main loop variables
initial_frame_uploaded = False
last_available_car_spaces = None
last_available_bike_spaces = None
last_upload_time = time.time()
unoccupied_duration = {i: 0 for i in range(len(polygons))}
unoccupied_threshold = 1
frame_rate = cap.get(cv2.CAP_PROP_FPS)
frame_time = 1 / frame_rate
fps = cap.get(cv2.CAP_PROP_FPS)
frame_interval = int(fps / 3)
detection_counts = deque(maxlen=15)
start_time = time.time()
current_frame = None
frame_count = 0
# Get the video dimensions
width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

# Resize the window to fit the video/image
window_width = 800
window_height = int(window_width * (height / width))
cv2.namedWindow('Draw Polygons', cv2.WINDOW_NORMAL)
cv2.resizeWindow('Draw Polygons', window_width, window_height)

# Main loop
initial_frame_uploaded = False
last_available_car_spaces = None
last_available_bike_spaces = None
last_upload_time = time.time()


last_detection_below_threshold_time = None
# Dictionary to track the unoccupied duration for each polygon
unoccupied_duration = {i: 0 for i in range(len(polygons))}

# Time threshold for saving the image inside the polygon
unoccupied_threshold = 30  # seconds

# Frame rate (frames per second)
frame_rate = cap.get(cv2.CAP_PROP_FPS)  # Set this to your actual frame rate
frame_time = 1 / frame_rate  # Time duration of each frame in seconds



# Get the frame rate of the video
fps = cap.get(cv2.CAP_PROP_FPS)
frame_interval = int(fps / 3)  # Calculate frame interval for 3 FPS

# Variables to keep track of detections
detection_counts = deque(maxlen=30)  # 5 seconds at 3 FPS = 15 frames


# Variables to keep track of the current frame and time
start_time = time.time()
current_frame = None
frame_count = 0


while True:
    ret, frame = cap.read()
    
    if not ret:
        print("Video feed lost. Attempting to reconnect...")
        cap.release()
        cap = reconnect_video_capture()
        if cap is None:
            break
        continue
    
    # Check if frame is valid
    if frame is None:
        print("Received an empty frame.")
        continue

    # Set the mouse callback function for drawing and dragging polygons
    cv2.setMouseCallback('Draw Polygons', handle_mouse, frame)

    # YOLO object detection with confidence threshold
    confidence_threshold = 0.3  # Set your desired confidence threshold
    results = model.predict(frame, conf=confidence_threshold, device=device)
    detections = results[0].boxes.data.cpu().numpy()  # Move detections to CPU and convert to numpy

    # Initialize list to count detected cars in each polygon
    car_counts = [0] * len(polygons)

    # Draw all the polygons on the frame
    for polygon in polygons:
        pts = np.array([polygon], np.int32)
        pts = pts.reshape((-1, 1, 2))
        cv2.polylines(frame, [pts], True, (0, 255, 0), 2)

    # Draw the current polygon being drawn
    for i in range(1, len(current_polygon)):
        cv2.line(frame, current_polygon[i-1], current_polygon[i], (0, 255, 0), 2)
    # Process each detection
    for row in detections:
        x1, y1, x2, y2, conf, cls = map(int, row[:6])
        class_name = class_list[cls]
    
        if 'car' in class_name or 'truck' in class_name:
        
            # Calculate center
            cx = (x1 + x2) // 2
            cy = (y1 + y2) // 2
        
            # Calculate midpoints
            midpoint_top = (cx, y1)
            midpoint_bottom = (cx, y2)
            midpoint_left = (x1, cy)
            midpoint_right = (x2, cy)
            midpoint = (cx, cy)
        
            # Calculate additional midpoints
            midpoint_top_center = ((cx + midpoint_top[0]) // 2, (cy + midpoint_top[1]) // 2)
            midpoint_bottom_center = ((cx + midpoint_bottom[0]) // 2, (cy + midpoint_bottom[1]) // 2)
            midpoint_left_center = ((cx + midpoint_left[0]) // 2, (cy + midpoint_left[1]) // 2)
            midpoint_right_center = ((cx + midpoint_right[0]) // 2, (cy + midpoint_right[1]) // 2)
        
            # Calculate additional midpoints
            midpoint_top_center1 = ((cx + midpoint_top[0]) // 4, (cy + midpoint_top[1]) // 4)
            midpoint_bottom_center1 = ((cx + midpoint_bottom[0]) // 4, (cy + midpoint_bottom[1]) // 4)
            midpoint_left_center1 = ((cx + midpoint_left[0]) // 4, (cy + midpoint_left[1]) // 4)
            midpoint_right_center1 = ((cx + midpoint_right[0]) // 4, (cy + midpoint_right[1]) // 4)
        
            # Additional new midpoints
            midpoint_top_center2 = ((cx + midpoint_top_center[0]) // 2, (cy + midpoint_top_center[1]) // 2)
            midpoint_bottom_center2 = ((cx + midpoint_bottom_center[0]) // 2, (cy + midpoint_bottom_center[1]) // 2)
            midpoint_left_center2 = ((cx + midpoint_left_center[0]) // 2, (cy + midpoint_left_center[1]) // 2)
            midpoint_right_center2 = ((cx + midpoint_right_center[0]) // 2, (cy + midpoint_right_center[1]) // 2)
        
            # Calculate bottom-left and bottom-right corners
            bottom_left = (x1, y2)
            bottom_right = (x2, y2)
        
            # Calculate midpoints for bottom corners
            midpoint_left_bottom = ((x1 + bottom_left[0]) // 2, (cy + bottom_left[1]) // 2)
            midpoint_right_bottom = ((x2 + bottom_right[0]) // 2, (cy + bottom_right[1]) // 2)
        
            # Generate intermediate points between midpoint_left_bottom and midpoint_right_bottom
            num_points = 10  # Number of intermediate points
            intermediate_points = [
                (
                    int(np.linspace(midpoint_left_bottom[0], midpoint_right_bottom[0], num=num_points)[i]),
                    int(np.linspace(midpoint_left_bottom[1], midpoint_right_bottom[1], num=num_points)[i])
                )
                for i in range(num_points)
            ]
        
            # List of all calculated points
            all_points = [
                midpoint, midpoint_top_center, midpoint_bottom_center, midpoint_left_center, midpoint_right_center,
                midpoint_top, midpoint_bottom, midpoint_left, midpoint_right,
                midpoint_top_center1, midpoint_bottom_center1, midpoint_left_center1, midpoint_right_center1,
                midpoint_top_center2, midpoint_bottom_center2, midpoint_left_center2, midpoint_right_center2
            ] + intermediate_points
        
            # Filter points to keep only those inside the bounding box
            points_inside_box = [point for point in all_points if x1 <= point[0] <= x2 and y1 <= point[1] <= y2]
        
            # Points to check
            points = points_inside_box


            # Check if any point of the lines or the center lies inside the polygons
            for i, polygon in enumerate(polygons):
                if any(cv2.pointPolygonTest(np.array(polygon, np.int32), point, False) >= 0 for point in points):
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 255), 2)
                    cv2.putText(frame, str(class_name), (x1, y1), cv2.FONT_HERSHEY_COMPLEX, 0.5, (255, 255, 255), 1)
                    car_counts[i] += 1  # Count the car for this polygon
                    # No break here to allow multiple polygons to be marked
        elif 'motorcycle' in class_name or 'bicycle' in class_name:
        
             cx = (x1 + x2) // 2
             cy = (y1 + y2) // 2
        
             # Calculate midpoints
             midpoint_top = (cx, y1)
             midpoint_bottom = (cx, y2)
             midpoint_left = (x1, cy)
             midpoint_right = (x2, cy)
             midpoint = (cx, cy)
        
             # Calculate additional midpoints
             midpoint_top_center = ((cx + midpoint_top[0]) // 2, (cy + midpoint_top[1]) // 2)
             midpoint_bottom_center = ((cx + midpoint_bottom[0]) // 2, (cy + midpoint_bottom[1]) // 2)
             midpoint_left_center = ((cx + midpoint_left[0]) // 2, (cy + midpoint_left[1]) // 2)
             midpoint_right_center = ((cx + midpoint_right[0]) // 2, (cy + midpoint_right[1]) // 2)
        
             # Points to check
             points = [midpoint, midpoint_top_center, midpoint_bottom_center, midpoint_left_center, midpoint_right_center,
             ]
        
             # Check if any point of the lines or the center lies inside the polygons
             for i, polygon in enumerate(polygons):
                 if any(cv2.pointPolygonTest(np.array(polygon, np.int32), point, False) >= 0 for point in points):
                     cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 255), 2)
                     cv2.putText(frame, str(class_name), (x1, y1), cv2.FONT_HERSHEY_COMPLEX, 0.5, (255, 255, 255), 1)
                     car_counts[i] += 1  # Count the car for this polygon
                     # No break here to allow multiple polygons to be marked
                     
# =============================================================================
#         if frame_count % frame_interval != 0:
#                     continue
# =============================================================================
        # Run YOLO detection
        results = model(frame)
        
        # Access detections
        detections = results[0].boxes
        num_detections = len(detections)
        
        # Store the number of detections
        detection_counts.append(num_detections)
        
        # Store the current frame
        current_frame = frame.copy()
        
        # Check if fewer than object_detection_threshold objects were detected
        if num_detections < 2:
            if last_detection_below_threshold_time is None:
                # Record the time when detection drops below the threshold
                last_detection_below_threshold_time = time.time()
            elif time.time() - last_detection_below_threshold_time >= 10:
                # If it has been more than 60 seconds continuously
                print("Less than 100 objects detected for more than 60 seconds.")
                # Save the latest frame
                cv2.imwrite('latest_frame.jpg', current_frame)
                print("Latest image saved as 'latest_frame.jpg'.")
                image_saved = True
                # Upload the image and send notification (implement these functions as needed)
                image_url1 = upload_frame_to_firebase(current_frame, 'cam1.jpg')
                camera_notif(1, image_url1)
                # Reset the timer and image saved flag
                last_detection_below_threshold_time = None
                image_saved = False
        else:
            # Reset the timer if detection count goes back above threshold
            last_detection_below_threshold_time = None
                            
        
        
    



                
    # Display the number of available spaces
    total_spaces = len(polygons)
    occupied_spaces = sum(1 for count in car_counts if count > 0)
    available_spaces = total_spaces - occupied_spaces

    # Divide the available spaces into car and bike slots
    slots, big_car_slot_indices,car_slot_indices = allocate_slots(total_spaces)
    available_car_spaces = count_available_car_slots1(slots, car_counts)
    available_big_car_spaces = count_available_car_slots(slots, car_counts)
    available_bike_spaces = available_spaces 

    cv2.putText(frame, f"Available Car Spaces: {available_car_spaces}", (23, 80), cv2.FONT_HERSHEY_PLAIN, 2, (255, 255, 255), 2)
    cv2.putText(frame, f"Available big Car Spaces: {available_big_car_spaces}", (23, 110), cv2.FONT_HERSHEY_PLAIN, 2, (255, 255, 255), 2)
    cv2.putText(frame, f"Available Bike Spaces: {available_bike_spaces}", (23, 140), cv2.FONT_HERSHEY_PLAIN, 2, (255, 255, 255), 2)

    # Highlight polygons based on occupancy
    for i, polygon in enumerate(polygons):
        color = (0, 0, 255) if car_counts[i] > 0 else (0, 255, 0)  # Red for occupied slots, green for empty slots
        pts = np.array([polygon], np.int32)
        pts = pts.reshape((-1, 1, 2))
        cv2.polylines(frame, [pts], True, color, 2)
# =============================================================================
# 
#     # Update Firebase with available spaces
#     # Upload frame to Firebase if it's been more than 10 seconds since the last upload
#     current_time = time.time()
#     if current_time - last_upload_time >= 15:
#         
#         image_url = upload_frame_to_firebase(frame, 'latest_frame1.jpg')
#         last_upload_time = current_time
#         
#         
#         update_slots(available_car_spaces, available_big_car_spaces, available_bike_spaces,image_url)
# =============================================================================



    # Handle key presses
    key = cv2.waitKey(1) & 0xFF
    if key == ord('d'):
        if polygons:
            print("Duplicating last polygon:", polygons[-1])
            offset_x, offset_y = 20, 20  # Shift the polygon by 20 pixels in both x and y directions
            new_polygon = shift_polygon(polygons[-1], offset_x, offset_y)
            polygons.append(new_polygon)
            # Save polygons to file
            with open(polygons_file, 'w') as file:
                json.dump(polygons, file)
            print("New polygon added:", new_polygon)
        else:
            print("No polygon to duplicate.")
    elif key == ord('u'):
        undo_last_vertex()
        
    elif key == ord('r'):
      if polygons:
        removed_polygon = polygons.pop()
        print("Removed last duplicated polygon:", removed_polygon)
        # Save polygons to file
        with open(polygons_file, 'w') as file:
            json.dump(polygons, file)
      else:
        print("No polygon to remove.")   
    elif key == ord('q'):
        break

    # Display the frame
    cv2.imshow('Draw Polygons', frame)

cap.release()
#cv2.destroyAllWindows()

print("Polygons:")
for polygon in polygons:
    print(polygon)
    