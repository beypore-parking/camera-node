# -*- coding: utf-8 -*-
"""
Created on Wed Aug 28 16:04:36 2024

@author: DELL
"""

import os
import time
import pandas as pd
import firebase_admin
from firebase_admin import credentials, db
from datetime import datetime

# Initialize Firebase Admin SDK with your service account key
cred = credentials.Certificate(r'path to firebase admin json file')  # Replace with your service account path
firebase_admin.initialize_app(cred, {
    'databaseURL': 'path to database url'  # Replace with your Realtime Database URL
})

# Specify the database path where the data is located
database_path = 'path to database'  # Change to your specific database path
excel_filename = 'xlsx file path'  # Name of the Excel file to save the data

# Function to update Excel file with new data
def update_excel(data):
    try:
        if os.path.exists(excel_filename):
            # Load existing Excel file
            existing_df = pd.read_excel(excel_filename)
            # Append new data to existing data
            updated_df = pd.concat([existing_df, data], ignore_index=True)
        else:
            # If Excel file does not exist, create a new DataFrame
            updated_df = data

        # Save updated data back to Excel
        updated_df.to_excel(excel_filename, index=False)
        print(f"Excel file '{excel_filename}' updated successfully.")

    except PermissionError as e:
        print(f"Permission error while accessing '{excel_filename}': {e}")
        print("Please ensure the file is not open in another application or locked by another process.")
    except Exception as e:
        print(f"An error occurred: {e}")

# Function to fetch data from Firebase Realtime Database
def fetch_data():
    try:
        # Fetch data from the specified path in the Firebase Realtime Database
        ref = db.reference(database_path)
        data = ref.get()

        if data:
            # Check if 'bike', 'bigCarSpots', and 'car' are lists
            bike_data = data.get('bike', [None])[0] if isinstance(data.get('bike'), list) else None
            big_car_data = data.get('bigCarSpots', [None])[0] if isinstance(data.get('bigCarSpots'), list) else None
            car_data = data.get('car', [None])[0] if isinstance(data.get('car'), list) else None
           
            # Get the current timestamp
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
           
            # Prepare the data to be saved to Excel
            df_data = {
                'Timestamp': [timestamp],
                'Bike': [bike_data],
                'Big Car': [big_car_data],
                'Car': [car_data]
            }

            # Convert the data to a DataFrame
            df = pd.DataFrame(df_data)

            # Update Excel file
            update_excel(df)
        else:
            print("No data found at the specified path.")

    except Exception as e:
        print(f"Error fetching data from Firebase: {e}")

# Main function to periodically fetch and save data
def main():
    print("Starting to periodically fetch data from Firebase Realtime Database...")
    try:
        while True:
            fetch_data()
            time.sleep(30)  # Wait for 10 seconds before fetching data again
    except KeyboardInterrupt:
        print("Stopped periodic data fetching.")

if __name__ == "__main__":
    main()