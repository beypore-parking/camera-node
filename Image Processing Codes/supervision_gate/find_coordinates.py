import cv2

# This script is just a helper to find the cordinates of the frame of the line and use them in the supervision_test script 
VIDEO_PATH = "./vid2.mp4" 

# Global variables
start_point = None
clean_frame = None 

def click_event(event, x, y, flags, params):
    global start_point, frame, clean_frame

    if event == cv2.EVENT_LBUTTONDOWN:
        if start_point is None:
            
            # Set the Anchor point, follwing this logic the anchor point cant be changed
            start_point = (x, y)
            print(f"Start point set at: {start_point}")
            print("Now click anywhere to set the end point (keep clicking to adjust).")
            
            # Draw just the start dot
            cv2.circle( frame, start_point, 5, (0, 0, 255), -1 )
            cv2.imshow( 'Click to find coordinates', frame) 
            
        else:
            # Updating the line ons subsequent clicks of the second point
            end_point = (x, y)
            
            # Refresh the frame ( Erase old line )
            # We copy 'clean_frame' back into 'frame' to wipe previous drawings
            frame = clean_frame.copy()
            
            # Re-draw the Start Point
            cv2.circle(frame, start_point, 5, (0, 0, 255), -1)
            
            # Draw the new Line and End Point
            cv2.line(frame, start_point, end_point, (0, 255, 0), 2)
            cv2.circle(frame, end_point, 5, (0, 0, 255), -1)
            
            # D. Show and Print
            cv2.imshow('Click to find coordinates', frame)
            print(f"Updated Line: Start {start_point} -> End {end_point}")

    # Right click to fully reset everything
    elif event == cv2.EVENT_RBUTTONDOWN:
        start_point = None
        frame = clean_frame.copy()
        cv2.imshow('Click to find coordinates', frame)
        print("Click to set a new Start Point")


cap = cv2.VideoCapture(VIDEO_PATH)
ret, frame = cap.read()

if ret:
    # Save a clean copy so we can "undo" drawings
    clean_frame = frame.copy()

    cv2.imshow('Click to find coordinates', frame)
    cv2.setMouseCallback('Click to find coordinates', click_event)
    
    print("INSTRUCTIONS:")
    print("1. Left Click once to set the Start Point.")
    print("2. Left Click AGAIN to set the End Point.")
    print("(You can keep clicking to move the End Point).")
    print("3. Right Click to clear everything and restart.")
    print("4. Press any key to close.")
    
    cv2.waitKey(0)
    cv2.destroyAllWindows()
else:
    print(f"Error: Could not read video file '{VIDEO_PATH}'.")