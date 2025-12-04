# Smart Parking System - Database Schema Documentation

## 1\. Overview

This schema is designed for a **Realtime Database (Firebase)**. It follows a strictly decoupled pattern to ensure system stability:

- **`config`**: Read-Only for Edge Nodes / Writeable by Admin Panel.
- **`live_status`**: Write-Only for Edge Nodes / Read-Only for Mobile Apps.
- **`system_health`**: Heartbeat and diagnostic data.

## 2\. The Full JSON Schema

```jsonc
{
  "locations": {
    // NODE IDENTIFIER: Unique ID for the physical Edge Device
    "Node_01_Main_Campus": {
      // ==========================================
      // SECTION 1: SYSTEM HEALTH (Heartbeat)
      // Used by Admin Panel to detect offline nodes
      // ==========================================
      "system_health": {
        "status": "online", // "online" | "offline"
        "last_seen": "2025-12-04T18:30:00.000Z",
        "ip_address": "192.168.1.50" // Local IP for debugging
      },

      // ==========================================
      // SECTION 2: CONFIGURATION
      // The "Source of Truth". Admin draws polygons here.
      // Edge Node listens to changes here to hot-reload.
      // ==========================================
      "config": {
        "cameras": {
          // --- TYPE A: OFF-STREET (Marked Bays) ---
          "cam_01_parking_lot_A": {
            "enabled": true,
            "type": "offstreet",
            "rtsp_url": "rtsp://admin:pass@192.168.1.101/stream1",
            "polygons": {
              // Keys are the SLOT IDs (displayed in App)
              "C1": {
                "type": "car",
                "points": [
                  [399, 519],
                  [407, 549],
                  [468, 643],
                  [399, 519]
                ]
              },
              "C2": {
                "type": "car",
                "points": [
                  [693, 505],
                  [748, 618],
                  [690, 488],
                  [693, 505]
                ]
              },
              "B1": {
                "type": "bike",
                "points": [
                  [100, 100],
                  [150, 100],
                  [150, 150],
                  [100, 150]
                ]
              }
            }
          },

          // --- TYPE B: ON-STREET (Linear/Ruler) ---
          "cam_02_curbside": {
            "enabled": true,
            "type": "onstreet",
            "rtsp_url": "rtsp://admin:pass@192.168.1.102/stream1",
            // The "Ruler": A chain of small segments along the curb
            "roi_polygons": {
              "segment_01": {
                "points": [
                  [10, 10],
                  [20, 10],
                  [20, 20],
                  [10, 20]
                ]
              },
              "segment_02": {
                "points": [
                  [20, 10],
                  [30, 10],
                  [30, 20],
                  [20, 20]
                ]
              },
              "segment_03": {
                "points": [
                  [30, 10],
                  [40, 10],
                  [40, 20],
                  [30, 20]
                ]
              },
              "segment_04": {
                "points": [
                  [40, 10],
                  [50, 10],
                  [50, 20],
                  [40, 20]
                ]
              },
              "segment_05": {
                "points": [
                  [50, 10],
                  [60, 10],
                  [60, 20],
                  [50, 20]
                ]
              }
            },
            // Calibration: How many segments = 1 vehicle?
            "calibration": {
              "segments_per_car": 5,
              "segments_per_bike": 2
            }
          },

          // --- TYPE C: GATE (Combined Stream) ---
          "cam_03_main_gate": {
            "enabled": true,
            "type": "gate",
            "gate_mode": "combined", // One camera sees both In and Out
            "streams": {
              "primary": {
                "rtsp_url": "rtsp://admin:pass@192.168.1.103/stream1",
                // Roboflow LineZone: [Start_X, Start_Y, End_X, End_Y]
                "line_coords": [100, 500, 800, 500],
                // Logic: Does crossing Top-to-Bottom mean 'Entering' or 'Exiting'?
                "entry_direction": "top_to_bottom"
              }
            }
          },

          // --- TYPE D: GATE (Separate Streams) ---
          "cam_04_back_gate": {
            "enabled": true,
            "type": "gate",
            "gate_mode": "separate", // Two physical cameras
            "streams": {
              "entry_cam": {
                "rtsp_url": "rtsp://admin:pass@192.168.1.104/stream1",
                "line_coords": [50, 200, 400, 200]
                // Note: Any crossing here is an ENTRY
              },
              "exit_cam": {
                "rtsp_url": "rtsp://admin:pass@192.168.1.105/stream1",
                "line_coords": [50, 200, 400, 200]
                // Note: Any crossing here is an EXIT
              }
            }
          }
        }
      },

      // ==========================================
      // SECTION 3: LIVE STATUS
      // High-frequency updates from Edge.
      // Mobile App and Admin Panel listens here.
      // ==========================================
      "live_status": {
        // OFF-STREET STATUS
        "cam_01_parking_lot_A": {
          "last_updated": "2025-12-04T18:30:05.123Z",
          "snapshot": {
            "url": "https://firebasestorage.googleapis.com/v0/b/your-app.appspot.com/o/snapshots%2Fcam_01.jpg?alt=media&token=...",
            "timestamp": "2025-12-04T18:35:00.000Z" // Helps Admin Panel know if image is stale
          },
          "slots": {
            "C1": "occupied",
            "C2": "free",
            "B1": "free"
          },
          // Aggregated counts for quick UI display
          "summary": {
            "total_free_cars": 1,
            "total_free_bikes": 1
          }
        },

        // ON-STREET STATUS
        "cam_02_curbside": {
          "last_updated": "2025-12-04T18:30:05.456Z",
          "snapshot": {
            "url": "https://firebasestorage.googleapis.com/.../cam_03.jpg?...",
            "timestamp": "2025-12-04T18:35:05.000Z"
          },
          "metrics": {
            "total_length_units": 15, // Total segments drawn
            "occupied_units": 5, // How many segments are covered
            "estimated_free_cars": 2 // (15 - 5) / 5 = 2
          }
        },

        // GATE STATUS (Counts)
        "cam_03_main_gate": {
          "last_updated": "2025-12-04T18:30:10.000Z",
          "snapshot": {
            "url": "https://firebasestorage.googleapis.com/.../cam_03.jpg?...",
            "timestamp": "2025-12-04T18:35:05.000Z"
          },
          "counts": {
            "current_inside": 45, // Logic: (initial + in - out). Never < 0.
            "total_in": 120, // Total entries today
            "total_out": 75 // Total exits today
          }
        },

        "cam_04_back_gate": {
          "last_updated": "2025-12-04T18:30:12.000Z",
          "snapshot": {
            "entry_cam": {
              "url": "https://firebasestorage.googleapis.com/.../cam_03.jpg?...",
              "timestamp": "2025-12-04T18:35:05.000Z"
            },
            "exit_cam": {
              "url": "https://firebasestorage.googleapis.com/.../cam_03.jpg?...",
              "timestamp": "2025-12-04T18:35:05.000Z"
            }
          },
          "counts": {
            "current_inside": 12,
            "total_in": 50,
            "total_out": 38
          }
        }
      }
    }
  }
}
```

---

## 3\. Schema Logic Explanation

### A. Locations (Root)

We wrap everything in `locations/{Node_Name}`.

- This allows the system to scale. If we add a second parking lot ("Node_02_Downtown"), we can simply add a new key at the root.

### B. System Health (`system_health`)

- **Purpose:** This acts as the "Heartbeat."
- **Workflow:** The Orchestrator's main thread runs a loop that updates `last_seen` every 30-60 seconds.
- **Admin Panel Logic:** If `last_seen` is older than 2 minutes, show a **RED** "Offline" badge on the dashboard.

### C. Configuration (`config`)

This data is **static** (it doesn't change every second).

1.  **Polygons as a Dictionary:** We use `"C1": {...}` instead of a list `[...]`. This allows the Edge node to instantly know which ID to update in the database without searching through a list.
2.  **Gate Mode (Combined vs. Separate):**
    - **Combined:** One RTSP stream. The logic relies on `entry_direction` (e.g., did the car cross the line Top-to-Bottom or Bottom-to-Top?).
    - **Separate:** Two RTSP streams. The logic is simpler: Camera A _only_ counts entries. Camera B _only_ counts exits.

### D. Live Status (`live_status`)

This data is **dynamic** (changes frequently).

The snapshot section stores the a recent frame from the camera which the admin panel can use to draw the polygons and lines. The gate camera with separate entry exit gates will sotre two frames.

1.  **Off-Street:** We store individual slot status (`"C1": "free"`). This allows the mobile app to draw a Green/Red box on a map for _specific_ spots.
2.  **On-Street:** We do **not** store specific slot IDs (because they are unmarked curbs). We store `estimated_free_cars` so the app can say "Approx. 2 spots left".
3.  **Gate:** We store **Counts**, not individual vehicle events.
    - `current_inside`: The number of cars currently in the parking lot.
    - `total_in` / `total_out`: Analytics for the dashboard (e.g., "120 cars visited today").
