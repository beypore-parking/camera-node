# -*- coding: utf-8 -*-
"""
Created on Tue Aug 20 11:16:12 2024

@author: DELL
"""

# -*- coding: utf-8 -*-
"""
Created on Thu Jul 18 13:36:41 2024

@author: DELL
"""

import cv2
import torch
import numpy as np
import json
import os
import firebase_admin
from firebase_admin import db,storage,firestore
from ultralytics import YOLO
from ultralytics import RTDETR
import time
from collections import deque

# Check if CUDA is available
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
print(f"Using device: {device}")

# Load YOLO model
#model = YOLO("yolov8x.pt")
model = RTDETR("rtdetr-x.pt")
model.to(device)  # Move model to GPU

# Initialize the list to store the coordinates of the polygons
polygons = []
current_polygon = []  # Initialize the current polygon being drawn
selected_polygon_index = None  # Index of the selected polygon for dragging
dragging = False  # Flag to indicate if a polygon is being dragged
drag_offset = (0, 0)  # Offset between the click point and the polygon's origin
current_mode = 'car'  # Current mode for drawing (car or bike)

# File to save polygons
polygons_file = 'polygon json file'

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
            if cv2.pointPolygonTest(np.array(polygon['points'], np.int32), (x, y), False) >= 0:
                selected_polygon_index = i
                drag_offset = (x - polygon['points'][0][0], y - polygon['points'][0][1])
                dragging = True
                return

        # If not clicking inside a polygon, start drawing a new one
        current_polygon.append((x, y))

    elif event == cv2.EVENT_MOUSEMOVE:
        if dragging and selected_polygon_index is not None:
            # Calculate new position of the polygon based on the drag offset
            new_polygon = shift_polygon(polygons[selected_polygon_index]['points'], x - polygons[selected_polygon_index]['points'][0][0] - drag_offset[0], y - polygons[selected_polygon_index]['points'][0][1] - drag_offset[1])
            polygons[selected_polygon_index]['points'] = new_polygon

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
            polygons.append({'points': current_polygon.copy(), 'type': current_mode})
            current_polygon = []
            # Save polygons to file
            with open(polygons_file, 'w') as file:
                json.dump(polygons, file)

    elif event == cv2.EVENT_LBUTTONDBLCLK:
        if len(current_polygon) > 2:
            # Close the polygon
            cv2.line(param, current_polygon[-1], current_polygon[0], (0, 255, 0), 2)
            polygons.append({'points': current_polygon.copy(), 'type': current_mode})
            current_polygon = []
            # Save polygons to file
            with open(polygons_file, 'w') as file:
                json.dump(polygons, file)






# Initialize dictionaries to track occupancy times
car_occupancy_times = {}  # NEW: Track the time each car slot was first detected as occupied
bike_occupancy_times = {}  # NEW: Track the time each bike slot was first detected as occupied
occupied_car_slots = []    # NEW: Track currently occupied car slots
occupied_bike_slots = []   # NEW: Track currently occupied bike slots




# Load class list for YOLO
with open(r"C:\Users\DELL\Desktop\smartparking\coco.txt" ) as my_file:
    class_list = my_file.read().split("\n")

# Initialize Firebase app
cred_obj = firebase_admin.credentials.Certificate(r'path to firebase admin json file')
default_app = firebase_admin.initialize_app(cred_obj, {
    'databaseURL': 'credentials to database url'
    
})

firestore_db = firestore.client()
doc_ref = firestore_db.collection('admins').document('dummy')
doc = doc_ref.get()
def update_parked_slot(key,slot):
    if doc.exists:
        doc_data = doc.to_dict()
        print(f'Firestore document data: {doc_data}')
    # To fetch the field with key '1'
        field_value = doc_data.get(str(key))
        print(f'Field value from Firestore: {field_value}')
        user_ref= firestore_db.collection('users').document(field_value)
        user_ref.update({'parkedSlot':slot})
        return field_value
    else:
        print('No such document in Firestore!')
        return None


def initialize_video_capture():
    cap = cv2.VideoCapture(r'rtsp://admin:parking_12@192.168.0.64')
    #cap = cv2.VideoCapture("output_video_pranav2.avi")
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






# Frame rate (frames per second)
frame_rate = cap.get(cv2.CAP_PROP_FPS)  # Set this to your actual frame rate
frame_time = 1 / frame_rate  # Time duration of each frame in seconds



# Get the frame rate of the video
fps = cap.get(cv2.CAP_PROP_FPS)
frame_interval = int(fps / 3)  # Calculate frame interval for 3 FPS



# Variables to track time and state
last_detection_below_threshold_time = None
image_saved = False
frame_count = 0
object_detection_threshold = 100  # Threshold for the number of objects
duration_threshold = 1000  # Duration threshold in seconds

# Initialize previously occupied slots set (this should be outside the main loop)
previously_occupied_slots = set()
previously_unoccupied_slots = set()  # To track slots that became unoccupied

# Main loop
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

    # Process one frame per second
    if frame_count % int(fps) == 0:
        # Set the mouse callback function for drawing and dragging polygons
        cv2.setMouseCallback('Draw Polygons', handle_mouse, frame)
    
        # YOLO object detection with confidence threshold
        confidence_threshold = 0.25  # Set your desired confidence threshold
        results = model.predict(frame, conf=confidence_threshold, device=device)
        detections = results[0].boxes.data.cpu().numpy()  # Move detections to CPU and convert to numpy

        # Initialize lists to count detected cars and bikes in each polygon
        car_counts = [0] * len(polygons)
        bike_counts = [0] * len(polygons)

        # Draw all the polygons on the frame
        for polygon in polygons:
            pts = np.array([polygon['points']], np.int32)
            pts = pts.reshape((-1, 1, 2))
            color = (0, 255, 0) if polygon['type'] == 'car' else (255, 0, 0)
            cv2.polylines(frame, [pts], True, color, 2)

        # Draw the current polygon being drawn
        for i in range(1, len(current_polygon)):
            cv2.line(frame, current_polygon[i-1], current_polygon[i], (0, 255, 0), 2)

        # Process each detection
        for row in detections:
            x1, y1, x2, y2, conf, cls = map(int, row[:6])
            class_name = class_list[cls]

            if 'car' in class_name or 'truck' in class_name:
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
                points = [midpoint, midpoint_top_center, midpoint_bottom_center, midpoint_left_center, midpoint_right_center]

                # Check if any point of the lines or the center lies inside the polygons
                for i, polygon in enumerate(polygons):
                    if any(cv2.pointPolygonTest(np.array(polygon['points'], np.int32), point, False) >= 0 for point in points):
                        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 255), 2)
                        cv2.putText(frame, str(class_name), (x1, y1), cv2.FONT_HERSHEY_COMPLEX, 0.5, (255, 255, 255), 1)
                        if polygon['type'] == 'car':
                            car_counts[i] += 1  # Count the car for this polygon
                            
                        elif polygon['type'] == 'bike':
                            bike_counts[i] += 1 
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
                points = [midpoint]

                # Check if any point of the lines or the center lies inside the polygons
                for i, polygon in enumerate(polygons):
                    if any(cv2.pointPolygonTest(np.array(polygon['points'], np.int32), point, False) >= 0 for point in points):
                        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 255), 2)
                        cv2.putText(frame, str(class_name), (x1, y1), cv2.FONT_HERSHEY_COMPLEX, 0.5, (255, 255, 255), 1)
                        if polygon['type'] == 'bike':
                            bike_counts[i] += 1  # Count the bike for this polygon
                            
                            #update_parked_slot(1,f"B{i+1}")
                            
                        elif polygon['type'] == 'car':
                            car_counts[i] += 1 
                            #update_parked_slot(1,f"C{i+1}")
                        # No break here to allow multiple polygons to be marked
                        



       

        # Display the number of available spaces
        total_car_spaces = sum(1 for polygon in polygons if polygon['type'] == 'car')
        occupied_car_spaces = sum(1 for i, polygon in enumerate(polygons) if polygon['type'] == 'car' and car_counts[i] > 0)
        available_car_spaces = total_car_spaces - occupied_car_spaces

        total_bike_spaces = sum(1 for polygon in polygons if polygon['type'] == 'bike')
        occupied_bike_spaces = sum(1 for i, polygon in enumerate(polygons) if polygon['type'] == 'bike' and bike_counts[i] > 0)
        available_bike_spaces = total_bike_spaces - occupied_bike_spaces

        cv2.putText(frame, f"Available Car Spaces: {available_car_spaces}", (23, 80), cv2.FONT_HERSHEY_PLAIN, 2, (255, 255, 255), 2)
        cv2.putText(frame, f"Available Bike Spaces: {available_bike_spaces}", (23, 120), cv2.FONT_HERSHEY_PLAIN, 2, (255, 255, 255), 2)
       
        
        # Highlight polygons based on occupancy and add numbers
        car_slot_number = 1
        bike_slot_number = 1
        currently_occupied_slots = []  # List to store currently occupied car and bike slots
        
        for i, polygon in enumerate(polygons):
            color = (0, 0, 255) if (polygon['type'] == 'car' and car_counts[i] > 0) or (polygon['type'] == 'bike' and bike_counts[i] > 0) else (0, 255, 0)
            pts = np.array([polygon['points']], np.int32)
            pts = pts.reshape((-1, 1, 2))
            cv2.polylines(frame, [pts], True, color, 2)
        
            # Calculate the center of the polygon to place the number
            M = cv2.moments(pts)
            if M["m00"] != 0:
                cX = int(M["m10"] / M["m00"])
                cY = int(M["m01"] / M["m00"])
            else:
                cX, cY = pts[0][0]
        
            if polygon['type'] == 'car':
                slot_id = f"C{car_slot_number}"
                cv2.putText(frame, slot_id, (cX - 10, cY + 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
                if car_counts[i] > 0:
                    currently_occupied_slots.append(slot_id)  # Add car slot to the list of currently occupied slots
                car_slot_number += 1
            else:
                slot_id = f"B{bike_slot_number}"
                cv2.putText(frame, slot_id, (cX - 10, cY + 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
                if bike_counts[i] > 0:
                    currently_occupied_slots.append(slot_id)  # Add bike slot to the list of currently occupied slots
                bike_slot_number += 1
        
        # Convert to set for easier comparison
        currently_occupied_set = set(currently_occupied_slots)
        
        # Determine newly or re-occupied slots
        newly_occupied_slots = currently_occupied_set - previously_occupied_slots
        re_occupied_slots = previously_unoccupied_slots & currently_occupied_set
        
        # Combine newly occupied and re-occupied slots
        occupied_slots = newly_occupied_slots | re_occupied_slots
        
        # Update and print only the latest occupied slot if any
        if occupied_slots:
            latest_occupied_slot = list(occupied_slots)[-1]
            update_parked_slot(1, latest_occupied_slot)  # Call the update function for the last occupied slot
            print(f"Latest occupied slot: {latest_occupied_slot}")
        
        # Track unoccupied slots (slots that were occupied previously but are now unoccupied)
        newly_unoccupied_slots = previously_occupied_slots - currently_occupied_set
        previously_unoccupied_slots.update(newly_unoccupied_slots)
        
        # Update the previously occupied slots with the current state
        previously_occupied_slots = currently_occupied_set
                
        

# =============================================================================
# 
#         # Update Firebase with available spaces and occupied slots
#         update_slots(available_car_spaces, available_bike_spaces, occupied_car_slots, occupied_bike_slots)
# 
# =============================================================================
        # Display the frame
        cv2.imshow('Draw Polygons', frame)

    # Handle key presses
    key = cv2.waitKey(1) & 0xFF
    if key == ord('d'):
        if polygons:
            print("Duplicating last polygon:", polygons[-1])
            offset_x, offset_y = 20, 20  # Shift the polygon by 20 pixels in both x and y directions
            new_polygon = shift_polygon(polygons[-1]['points'], offset_x, offset_y)
            polygons.append({'points': new_polygon, 'type': polygons[-1]['type']})
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
            print("Deleting last polygon:", polygons[-1])
            polygons.pop()
            # Save polygons to file
            with open(polygons_file, 'w') as file:
                json.dump(polygons, file)
            print("Last polygon deleted.")
    elif key == ord('m'):
        # Toggle mode between 'car' and 'bike'
        current_mode = 'bike' if current_mode == 'car' else 'car'
        print(f"Switched to {current_mode} mode")
    elif key == ord('q'):
        break

    frame_count += 1
print(occupied_car_slots)
cap.release()
cv2.destroyAllWindows()

print("Polygons:")
for polygon in polygons:
    print(polygon)
print(occupied_car_slots)
