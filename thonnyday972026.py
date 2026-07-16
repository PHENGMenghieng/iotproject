"""
Face verification server — runs on your PC.

Flow:
 1. RFID ESP32 sends a POST request here after a valid card scan,
    with the student_id that was matched.
 2. This server grabs a fresh frame from the ESP32-CAM.
 3. It compares that frame's face against ALL stored reference photos
    (multiple angles) for that student_id — match if ANY angle matches.
 4. It replies with {"match": true/false}, which the ESP32 uses to
    decide what to show on the LCD and whether to log attendance.

SETUP REQUIRED:
 1. pip install flask face_recognition opencv-python requests
    (see install notes below for the tricky dlib dependency)
 2. Create a folder called reference_faces/ next to this script.
 3. Inside reference_faces/, create ONE SUBFOLDER PER STUDENT ID,
    named exactly <student_id>, e.g.:
        reference_faces/2024188/angle1.jpg
        reference_faces/2024188/angle2.jpg
        reference_faces/2023517/front.jpg
    You can put as many angle photos as you want in each subfolder —
    3-8 angles per student is a good range.
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
# student_id -> list of face encodings (one per angle photo)
reference_encodings = {}


def load_reference_faces():
    print("Loading reference faces from:", REFERENCE_DIR)
    if not os.path.isdir(REFERENCE_DIR):
        print("WARNING: reference_faces/ folder not found. Create it and add photos.")
        return

    for student_id in os.listdir(REFERENCE_DIR):
        folder_path = os.path.join(REFERENCE_DIR, student_id)

        # skip stray files sitting directly in reference_faces/ (only folders count now)
        if not os.path.isdir(folder_path):
            print(f"  Skipping '{student_id}' — not a folder. "
                  f"Each student needs their own subfolder now.")
            continue

        encodings_list = []
        for filename in os.listdir(folder_path):
            if not filename.lower().endswith((".jpg", ".jpeg", ".png")):
                continue
            path = os.path.join(folder_path, filename)

            image = Image.open(path).convert("RGB")
            image = np.ascontiguousarray(np.array(image), dtype=np.uint8)
            encodings = face_recognition.face_encodings(image)

            if len(encodings) == 0:
                print(f"  WARNING: no face found in {student_id}/{filename} — skipping")
                continue

            encodings_list.append(encodings[0])

        if encodings_list:
            reference_encodings[student_id] = encodings_list
            print(f"  Loaded {len(encodings_list)} angle(s) for student_id={student_id}")
        else:
            print(f"  WARNING: no usable photos found for student_id={student_id}")

    total_photos = sum(len(v) for v in reference_encodings.values())
    print(f"Total students loaded: {len(reference_encodings)} "
          f"({total_photos} reference photos total)")


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
        print("  No reference photos on file for this student_id.")
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

    # ─── Check the live face against EVERY known student, not just the
    # claimed one. This lets us catch "right card, wrong face" cases,
    # where someone else's face happens to pass the claimed student's
    # threshold but is actually a much better match for a different
    # known student.
    best_overall_id = None
    best_overall_distance = None
    claimed_distance = None

    for known_id, encodings_list in reference_encodings.items():
        if not encodings_list:
            continue
        distances = face_recognition.face_distance(encodings_list, live_encoding)
        this_best = float(np.min(distances))

        if known_id == student_id:
            claimed_distance = this_best

        if best_overall_distance is None or this_best < best_overall_distance:
            best_overall_distance = this_best
            best_overall_id = known_id

    if claimed_distance is None:
        # shouldn't happen since we already checked student_id is in
        # reference_encodings above, but guard anyway
        print("  Claimed student_id has no usable reference encodings.")
        return jsonify({"match": False, "reason": "no_reference_photo"})

    claimed_is_match = claimed_distance <= MATCH_TOLERANCE
    claimed_is_best = (best_overall_id == student_id)
    someone_else_matches = (
        best_overall_id is not None
        and best_overall_id != student_id
        and best_overall_distance <= MATCH_TOLERANCE
    )

    is_match = claimed_is_match and claimed_is_best

    print(f"  Claimed student_id={student_id}: distance={claimed_distance:.3f} "
          f"(tolerance {MATCH_TOLERANCE})")
    print(f"  Best match overall: student_id={best_overall_id}, "
          f"distance={best_overall_distance:.3f}")

    if is_match:
        print("  -> MATCH: face confirms claimed identity.")
        return jsonify({"match": True, "distance": claimed_distance})

    if someone_else_matches:
        # A DIFFERENT known student is within tolerance of this live face —
        # regardless of whether the claimed student also happens to pass.
        # This is the "right card, wrong person" case.
        print(f"  -> FALSE IDENTITY: face matches known student "
              f"{best_overall_id} (distance {best_overall_distance:.3f}), "
              f"not the claimed card holder.")
        return jsonify({
            "match": False,
            "reason": "false_identity",
            "detected_id": best_overall_id,
            "distance": claimed_distance,
        })

    print("  -> NO MATCH: face does not match claimed card holder.")
    return jsonify({"match": False, "reason": "no_match", "distance": claimed_distance})


if __name__ == "__main__":
    load_reference_faces()
    print("\nFace verification server starting on port 5000...")
    app.run(host="0.0.0.0", port=5000)
