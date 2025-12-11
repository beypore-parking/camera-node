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
    // PHYSICAL EDGE NODE IDENTIFIER
    "Node_01_Main_Campus": {
      // ==========================================
      // SECTION 1: SYSTEM HEALTH (Heartbeat)
      // ==========================================
      "system_health": {
        "status": "online",
        "last_seen": "2025-12-11T10:00:00Z",
        "ip_address": "192.168.1.50"
      },

      // ==========================================
      // SECTION 2: CONFIGURATION
      // The "Static" definition of the world.
      // ==========================================
      "config": {
        "parking_lots": {
          // --- LOGICAL UNIT 1: GATED PARKING LOT ---
          "lot_A_faculty": {
            "enabled": true,
            "display_name": "Faculty Parking Lot A",
            "type": "offstreet", // "offstreet" | "onstreet | gate"
            "lat": 11.321,
            "long": 75.933,

            // Total Capacity of this entire closed area - > defined from the admin panel when drawing bounding boxes
            "capacities": {
              "car": 50,
              "bike": 20,
              "big_car": 5
            },

            // The sensors (cameras) assigned to this lot
            "cameras": {
              "cam_01": {
                "enabled": true,
                "role": "occupancy_tracker", // Uses Polygons
                "rtsp_url": "rtsp://admin:pass@192.168.1.101/stream1",
                "polygons": {
                  "C1": {
                    "type": "car",
                    "points": [
                      [0, 0],
                      [10, 10],
                      [10, 0]
                    ]
                  },
                  "C2": {
                    "type": "car",
                    "points": [
                      [20, 20],
                      [30, 30],
                      [30, 20]
                    ]
                  }
                }
              },

              // CAMERA 2: The Entry Gate (Counts Inflows)
              "cam_02_front_gate": {
                "enabled": true,
                "role": "gate_counter", // Uses Lines
                "rtsp_url": "rtsp://admin:pass@192.168.1.102/stream1",
                "gate_logic": {
                  "mode": "entry_only", // "entry_only" | "exit_only" | "combined"
                  "line_coords": [100, 500, 800, 500]
                  // Crossing this line increments 'total_in' for Lot A
                }
              },

              // CAMERA 3: The Exit Gate (Counts Outflows)
              "cam_03_back_gate": {
                "enabled": true,
                "role": "gate_counter",
                "rtsp_url": "rtsp://admin:pass@192.168.1.103/stream1",
                "gate_logic": {
                  "mode": "exit_only",
                  "line_coords": [50, 200, 400, 200]
                  // Crossing this line increments 'total_out' for Lot A
                }
              }
            }
          },

          // --- LOGICAL UNIT 2: CURBSIDE (Open Street) ---
          "lot_B_market_road": {
            "enabled": true,
            "display_name": "Market Road Curbside",
            "type": "onstreet",
            "lat": 11.325,
            "long": 75.938,

            "capacities": {
              "total_length_units": 100 // Abstract units for linear parking
            },

            "cameras": {
              "cam_04_street_view": {
                "enabled": true,
                "role": "linear_tracker",
                "rtsp_url": "rtsp://admin:pass@192.168.1.104/stream1",
                "roi_polygon": [
                  [0, 0],
                  [1920, 0],
                  [1920, 500],
                  [0, 500]
                ],
                "calibration": { "pixels_per_unit": 50 }
              }
            }
          },

          // Gate style
          "lot_C_faculty": {
            "enabled": true,
            "display_name": "Faculty Parking Lot C",
            "type": "gate",
            "lat": 11.321,
            "long": 75.933,

            // Total Capacity of this gated area
            "capacities": {
              "combined": false, // if its not combined, if car leaves -> increase bike slots by 2 && two bikes will count as one car
              "car": 50,
              "bike": 20,
              "total": null
            },

            // The sensors (cameras) assigned to this lot
            "cameras": {
              // CAMERA 1
              "cam_02_front_gate": {
                "enabled": true,
                "role": "gate_counter", // Uses Lines
                "rtsp_url": "rtsp://admin:pass@192.168.1.102/stream1",
                "gate_logic": {
                  "line_coords": [100, 500, 800, 500],
                  "direction": "top_to_bottom"
                }
              },

              // CAMERA 2
              "cam_02_back_gate": {
                "enabled": true,
                "role": "gate_counter",
                "rtsp_url": "rtsp://admin:pass@192.168.1.103/stream1",
                "gate_logic": {
                  "line_coords": [50, 200, 400, 200],
                  "direction": "top_to_bottom"
                }
              }
            }
          }
        }
      },

      // ==========================================
      // SECTION 3: LIVE STATUS
      // The "Dynamic" State. Aggregated by Edge Node.
      // ==========================================
      "live_status": {
        // STATUS FOR LOT A
        "lot_A_faculty": {
          "last_updated": "2025-12-11T10:05:00Z",

          // 1. Calculated Global Counts (Logic: Total - (In - Out))
          "occupancy": {
            "current_cars": 25, // The vital number for the User App
            "current_bikes": 5,
            "analytics": {
              "total_in_today": 150,
              "total_out_today": 125
            }
          },

          // 2. Granular Slot Map (Green/Red boxes)
          "slots": {
            "C1": "occupied",
            "C2": "free",
            "C3": "occupied"
          },

          // 3. Visual Proof (Snapshots from child cameras)
          "snapshots": {
            "cam_01": {
              "url": "https://storage.../cam_01.jpg",
              "timestamp": "..."
            }
          }
        },

        // STATUS FOR LOT B
        "lot_B_market_road": {
          "last_updated": "2025-12-11T10:05:05Z",
          "occupancy": {
            "estimated_free": 4, // Calculated via linear logic
            "occupancy_level": "medium" // low | medium | high
          },
          "snapshots": {
            "cam_04_street_view": {
              "url": "...",
              "timestamp": "..."
            }
          }
        },

        // STATUS FOR LOT B
        "lot_C_faculty": {
          "last_updated": "2025-12-11T10:05:05Z",
          "occupancy": {
            "current_in": 25, // The vital number for the User App
            "analytics": {
              "total_in_today": 150,
              "total_out_today": 125
            }
          },
          "snapshots": {
            "cam_02_front_gate": {
              "url": "...",
              "timestamp": "..."
            },
            "cam_02_back_gate": {
              "url": "...",
              "timestamp": "..."
            }
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
2.  **Gate Mode:**
    - **One + RTSP stream:** One or more RTSP streams. The logic relies on `entry_direction` (e.g., did the car cross the line Top-to-Bottom or Bottom-to-Top?). The total count is maintained in the node process.

### D. Live Status (`live_status`)

This data is **dynamic** (changes frequently).

The snapshot section stores the a recent frame from the camera which the admin panel can use to draw the polygons and lines. The gate camera with separate entry exit gates will sotre two frames.

1.  **Off-Street:** We store individual slot status (`"C1": "free"`). This allows the mobile app to draw a Green/Red box on a map for _specific_ spots.
2.  **On-Street:** We do **not** store specific slot IDs (because they are unmarked curbs). We store `estimated_free_cars` so the app can say "Approx. 2 spots left".
3.  **Gate:** We store **Counts**, not individual vehicle events.
    - `current_inside`: The number of cars currently in the parking lot.
    - `total_in` / `total_out`: Analytics for the dashboard (e.g., "120 cars visited today").
