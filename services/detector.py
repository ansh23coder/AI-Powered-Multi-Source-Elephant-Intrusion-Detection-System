import os
import time
import threading

import cv2
import numpy as np

from config import DETECTION_FOLDER, ULTRALYTICS_CONFIG_DIR, YOLO_MODEL

ULTRALYTICS_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("YOLO_CONFIG_DIR", str(ULTRALYTICS_CONFIG_DIR))


class ElephantDetector:
    def __init__(self):
        self.model = None
        self.model_error = None
        self.elephant_class_ids = set()
        self._lock = threading.Lock()
        self._load_model()

    def _load_model(self):
        try:
            from ultralytics import YOLO

            self.model = YOLO(YOLO_MODEL)
            names = self.model.names or {}
            self.elephant_class_ids = {
                class_id for class_id, name in names.items() if str(name).lower() == "elephant"
            }
            if not self.elephant_class_ids:
                self.model_error = "Loaded YOLO model does not contain an elephant class."
        except Exception as exc:
            self.model = None
            self.model_error = str(exc)

    def detect_frame(self, frame, conf_threshold=0.5):
        detections = []
        annotated = frame.copy()

        if self.model and self.elephant_class_ids:
            with self._lock:
                results = self.model(frame, conf=conf_threshold, verbose=False)
            for result in results:
                for box in result.boxes:
                    class_id = int(box.cls[0])
                    confidence = float(box.conf[0])
                    if class_id not in self.elephant_class_ids or confidence < conf_threshold:
                        continue
                    x1, y1, x2, y2 = [int(v) for v in box.xyxy[0]]
                    detections.append(
                        {
                            "confidence": confidence,
                            "box": [x1, y1, x2, y2],
                            "label": f"Elephant {confidence:.2f}",
                        }
                    )

        for detection in detections:
            x1, y1, x2, y2 = detection["box"]
            cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 55, 255), 3)
            label_width = max(190, len(detection["label"]) * 15)
            cv2.rectangle(
                annotated,
                (x1, max(0, y1 - 34)),
                (min(annotated.shape[1] - 1, x1 + label_width), y1),
                (0, 55, 255),
                -1,
            )
            cv2.putText(
                annotated,
                detection["label"],
                (x1 + 8, y1 - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.75,
                (255, 255, 255),
                2,
            )

        return annotated, detections

    def save_snapshot(self, frame, source_type):
        DETECTION_FOLDER.mkdir(parents=True, exist_ok=True)
        filename = f"{source_type}_{int(time.time() * 1000)}.jpg"
        path = DETECTION_FOLDER / filename
        cv2.imwrite(str(path), frame)
        return f"/detections/{filename}"

    def status_frame(self, message):
        frame = np.zeros((540, 960, 3), dtype=np.uint8)
        frame[:] = (10, 18, 14)
        cv2.putText(
            frame,
            "No live frame available",
            (280, 220),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.2,
            (210, 235, 215),
            3,
        )
        cv2.putText(
            frame,
            message[:82],
            (70, 285),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.72,
            (170, 196, 170),
            2,
        )
        return frame


detector = ElephantDetector()
