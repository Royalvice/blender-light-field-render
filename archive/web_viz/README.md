# Light Field Camera Array Visualization

This project visualizes the Off-Axis Perspective Camera Array for Light Field Rendering using Three.js.

## Setup

Since `npm` might not be available, this project is configured to use CDN modules.

1. Ensure you have Python installed (which you do).
2. Open a terminal in this directory (`web_viz`).
3. Run the built-in Python HTTP server:
   ```bash
   python -m http.server 8000
   ```
4. Open your browser to `http://localhost:8000`.

## Features

- **Camera Array**: Visualizes $N$ cameras arranged linearly.
- **Off-Axis Projection**: Shows the skewed frustums converging at the focal plane.
- **Focal Plane**: Visualizes the shared focal plane at distance $d_f$.
- **Display Cube**: Shows the depth range of the target display.
- **Interactive Controls**: Adjust $N$, $d_f$, FOV, etc. in real-time.
- **View Through**: Toggle "View Through Active" to see what the selected camera sees (verifying the skew).
