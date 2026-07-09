"""
Face verification server — runs on your PC.

Flow:
 1. RFID ESP32 sends a POST request here after a valid card scan,
    with the student_id that was matched.
 2. This server grabs a fresh frame from the ESP32-CAM.
 3. It compares that frame's face against the stored reference photo
    for that student_id.
 4. It replies with {"match": true/false}, which the ESP32 uses to
    decide what to show on the LCD and whether to log attendance.

SETUP REQUIRED:
 1. pip install flask face_recognition opencv-python requests
    (see install notes below for the tricky dlib dependency)
 2. Create a folder called reference_faces/ next to this script.
 3. Add one clear, front-facing photo per student, named exactly
    <student_id>.jpg  (e.g. reference_faces/2023517.jpg)
 4. Update ESP32_CAM_URL below to match your ESP32-CAM's actual IP.
 5. Run this script: python face_check_server.py
 6. Update FACE_CHECK_URL in the ESP32's main.py to point to this
    PC's IP address and port 5000.

INSTALLING dlib/face_recognition ON WINDOWS:
 dlib requires a C++ compiler. The easiest path:
   pip install cmake
   pip install dlib
   pip install face_recognition
 If `pip install dlib` fails with a compiler error, install
 "Build Tools for Visual Studio" (just the C++ build tools workload,
 not the full IDE) from https://visualstudio.microsoft.com/downloads/
 then retry `pip install dlib`.
 Alternatively, search "dlib whl windows <your Python version>" for
 a prebuilt wheel and `pip install <path-to-wheel>.whl` instead.
"""

from flask import Flask, request, jsonify
import face_recognition
import requests
import numpy as np
import cv2
import os
from PIL import Image

app = Flask(__name__)

# ─── CONFIG ────────────────────────────────────────────────
ESP32_CAM_URL = "http://10.30.0.22/capture"  # update to your ESP32-CAM IP
REFERENCE_DIR = "reference_faces"
MATCH_TOLERANCE = 0.5  # lower = stricter match. 0.6 is face_recognition's default.

# ─── CACHE REFERENCE ENCODINGS AT STARTUP ──────────────────
reference_encodings = {}  # student_id -> face encoding


def load_reference_faces():
    print("Loading reference faces from:", REFERENCE_DIR)
    if not os.path.isdir(REFERENCE_DIR):
        print("WARNING: reference_faces/ folder not found. Create it and add photos.")
        return

    for filename in os.listdir(REFERENCE_DIR):
        if not filename.lower().endswith((".jpg", ".jpeg", ".png")):
            continue
        student_id = os.path.splitext(filename)[0]
        path = os.path.join(REFERENCE_DIR, filename)

        image = Image.open(path).convert("RGB")
        image = np.ascontiguousarray(np.array(image), dtype=np.uint8)
        encodings = face_recognition.face_encodings(image)

        if len(encodings) == 0:
            print(f"  WARNING: no face found in {filename} — skipping")
            continue

        reference_encodings[student_id] = encodings[0]
        print(f"  Loaded reference for student_id={student_id}")

    print(f"Total reference faces loaded: {len(reference_encodings)}")


def capture_frame_from_esp32cam():
    """Fetch a single JPEG snapshot from the ESP32-CAM's /capture endpoint."""
    resp = requests.get(ESP32_CAM_URL, timeout=5)
    resp.raise_for_status()
    img_array = np.frombuffer(resp.content, dtype=np.uint8)
    frame = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
    return frame


@app.route("/check_face", methods=["POST"])
def check_face():
    data = request.get_json()
    student_id = str(data.get("student_id", ""))

    print(f"\nFace check requested for student_id={student_id}")

    if student_id not in reference_encodings:
        print("  No reference photo on file for this student_id.")
        return jsonify({"match": False, "reason": "no_reference_photo"})

    try:
        frame = capture_frame_from_esp32cam()
    except Exception as e:
        print("  Failed to capture from ESP32-CAM:", e)
        return jsonify({"match": False, "reason": "camera_error"})

    # face_recognition expects RGB, OpenCV gives BGR
    rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    rgb_frame = np.ascontiguousarray(rgb_frame, dtype=np.uint8)
    live_encodings = face_recognition.face_encodings(rgb_frame)

    if len(live_encodings) == 0:
        print("  No face detected in captured frame.")
        return jsonify({"match": False, "reason": "no_face_detected"})

    live_encoding = live_encodings[0]
    reference_encoding = reference_encodings[student_id]

    face_distance = face_recognition.face_distance([reference_encoding], live_encoding)[0]
    is_match = face_distance <= MATCH_TOLERANCE

    print(f"  Face distance: {face_distance:.3f} (tolerance {MATCH_TOLERANCE}) -> match={is_match}")

    return jsonify({"match": bool(is_match), "distance": float(face_distance)})


if __name__ == "__main__":
    load_reference_faces()
    print("\nFace verification server starting on port 5000...")
    app.run(host="0.0.0.0", port=5000)

