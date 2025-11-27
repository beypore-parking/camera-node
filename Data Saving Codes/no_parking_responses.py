# -*- coding: utf-8 -*-
"""
Created on Fri Aug 30 15:06:46 2024

@author: DELL
"""

import os
import pandas as pd
from google.cloud import firestore
from openpyxl import load_workbook

# Set the environment variable for Google credentials
os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = r'firebase admin json file path'

# Initialize Firestore client
db = firestore.Client()

# Specify the Firestore collection path
collection_name = 'responses'
collection_ref = db.collection(collection_name)

# Excel filename to update
excel_filename = 'firestore_data_3.xlsx'

# Function to update Excel file
def update_excel(data):
    try:
        # Check if the file exists and try to open it
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

# Firestore listener callback function
def on_snapshot(col_snapshot, changes, read_time):
    new_data = []
    for change in changes:
        if change.type.name == 'ADDED' or change.type.name == 'MODIFIED':
            doc_dict = change.document.to_dict()
            doc_dict['Document ID'] = change.document.id  # Include Document ID if needed
            new_data.append(doc_dict)

    if new_data:
        # Convert the list of new dictionaries to a DataFrame
        df = pd.DataFrame(new_data)
        update_excel(df)

# Attach Firestore listener to collection
doc_watch = collection_ref.on_snapshot(on_snapshot)

print("Listening for changes in Firestore collection...")

# Keep the script running
try:
    while True:
        pass  # Keep the script alive to listen for updates
except KeyboardInterrupt:
    print("Stopped listening for changes.")

# Cleanup: detach listener if needed
doc_watch.unsubscribe()
