import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
import cv2

from config import (
    ALERT_COOLDOWN_SECONDS,
    DETECTION_WIDTH,
    FRAME_SKIP,
    JPEG_QUALITY,
    STREAM_FPS,
    STREAM_HEIGHT,
    STREAM_WIDTH,
)
from services import alert_service, db_service
from services.detector import detector

VIDEO_SOURCE_TYPES = {"upload", "recording"}
CAMERA_SOURCE_TYPES = {"webcam", "usb_webcam", "ip_camera"}

@dataclass
class CameraRuntime:
    source: dict
    socketio: object
    stop_event: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    latest_jpeg: bytes | None = None
    connection_status: str = "Starting"
    detection_status: str = "No detection"
    last_detection_time: str | None = None
    latest_confidence: float = 0.0
    detection_count: int = 0
    frames_processing: int = 0
    last_error: str = ""
    last_alert_at: float = 0.0

    def public_status(self):
        return {
            "source_id": self.source["id"],
            "source_name": self.source["source_name"],
            "source_type": self.source["source_type"],
            "source_input": self.source.get("source_input") or self.source.get("stream_url") or "",
            "latitude": self.source["latitude"],
            "longitude": self.source["longitude"],
            "connection_status": self.connection_status,
            "detection_status": self.detection_status,
            "last_detection_time": self.last_detection_time,
            "latest_confidence": self.latest_confidence,
            "detection_count": self.detection_count,
            "frames_processing": self.frames_processing,
            "last_error": self.last_error,
        }


class MultiCameraManager:
    def __init__(self):
        self._runtimes: dict[int, CameraRuntime] = {}
        self._lock = threading.Lock()
        self.conf_threshold = 0.5

    def set_conf_threshold(self, value):
        self.conf_threshold = max(0.3, min(0.9, float(value)))
        return self.conf_threshold

    def get_status(self):
        with self._lock:
            return [runtime.public_status() for runtime in self._runtimes.values()]

    def is_running(self, source_id):
        with self._lock:
            runtime = self._runtimes.get(int(source_id))
            return bool(runtime and runtime.thread and runtime.thread.is_alive())

    def start_source(self, source, socketio):
        if not source:
            raise ValueError("Camera source not found.")
        if not int(source.get("enabled", 1)):
            raise ValueError("Camera source is disabled.")
        if source["source_type"] == "drone":
            db_service.set_camera_status_by_id(source["id"], "future_ready")
            return None

        self.stop_source(source["id"])
        runtime = CameraRuntime(source=source, socketio=socketio)
        runtime.thread = threading.Thread(target=self._run_camera, args=(runtime,), daemon=True)
        with self._lock:
            self._runtimes[source["id"]] = runtime
        db_service.set_camera_status_by_id(source["id"], "starting")
        runtime.thread.start()
        return runtime.public_status()

    def stop_source(self, source_id):
        source_id = int(source_id)
        with self._lock:
            runtime = self._runtimes.pop(source_id, None)
        if not runtime:
            source = db_service.get_source(source_id)
            if source:
                db_service.set_camera_status_by_id(source_id, "idle")
            return
        runtime.stop_event.set()
        if runtime.thread and runtime.thread.is_alive():
            runtime.thread.join(timeout=2)
        db_service.set_camera_status_by_id(source_id, "idle")

    def stop_all(self):
        with self._lock:
            source_ids = list(self._runtimes.keys())
        for source_id in source_ids:
            self.stop_source(source_id)

    def latest_frame(self, source_id):
        with self._lock:
            runtime = self._runtimes.get(int(source_id))
        if not runtime:
            return None
        with runtime.lock:
            return runtime.latest_jpeg

    def active_capture_indices(self):
        with self._lock:
            runtimes = list(self._runtimes.values())
        indices = set()
        for runtime in runtimes:
            if runtime.source["source_type"] in {"webcam", "usb_webcam"}:
                try:
                    indices.add(int(runtime.source.get("source_input") or 0))
                except (TypeError, ValueError):
                    continue
        return indices

    def probe_webcams(self, max_index=5):
        found = []
        used = self.active_capture_indices()
        for index in range(max_index + 1):
            cap = self._create_webcam_capture(index)
            ok = False
            if cap and cap.isOpened():
                ok, _ = cap.read()
            if cap:
                cap.release()
            if ok:
                found.append(
                    {
                        "index": index,
                        "status": "busy" if index in used else "available",
                        "label": "Laptop Webcam" if index == 0 else f"USB Webcam {index}",
                    }
                )
        return found

    def _create_webcam_capture(self, index):
        try:
            cap = cv2.VideoCapture(int(index), cv2.CAP_DSHOW)
        except Exception:
            cap = cv2.VideoCapture(int(index))
        if cap and not cap.isOpened():
            cap.release()
            cap = cv2.VideoCapture(int(index))
        self._tune_capture(cap)
        return cap

    def _open_capture(self, source):
        source_type = source["source_type"]
        source_input = source.get("source_input") or source.get("stream_url") or ""
        if source_type in {"webcam", "usb_webcam"}:
            try:
                index = self._resolve_webcam_index(source_type, source_input)
                source["source_input"] = str(index)
                return self._create_webcam_capture(index)
            except ValueError:
                cap = cv2.VideoCapture(source_input)
                self._tune_capture(cap)
                return cap
        if source_type == "ip_camera":
            cap = cv2.VideoCapture(source_input)
            self._tune_capture(cap)
            return cap
        if source_type in VIDEO_SOURCE_TYPES and source_input:
            cap = cv2.VideoCapture(str(Path(source_input)))
            self._tune_capture(cap)
            return cap
        return None

    def _resolve_webcam_index(self, source_type, source_input):
        if str(source_input).lower() in {"", "auto"} and source_type == "usb_webcam":
            used = self.active_capture_indices()
            for candidate in range(1, 6):
                if candidate not in used:
                    return candidate
            return 1
        if str(source_input).lower() in {"", "auto"}:
            return 0
        return int(source_input)

    def _tune_capture(self, cap):
        if not cap:
            return
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, STREAM_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, STREAM_HEIGHT)
        cap.set(cv2.CAP_PROP_FPS, STREAM_FPS)

    def _resize_for_detection(self, frame):
        height, width = frame.shape[:2]
        if width <= DETECTION_WIDTH:
            return frame
        scale = DETECTION_WIDTH / float(width)
        return cv2.resize(frame, (DETECTION_WIDTH, int(height * scale)))

    def _run_camera(self, runtime):
        cap = self._open_capture(runtime.source)
        source_id = runtime.source["id"]
        source_type = runtime.source["source_type"]
        frame_number = 0

        try:
            if cap is None or not cap.isOpened():
                runtime.connection_status = "Disconnected"
                runtime.last_error = "Unable to open source input."
                db_service.set_camera_status_by_id(source_id, "disconnected")
                self._set_status_frame(runtime, runtime.last_error)
                return

            runtime.connection_status = "Connected"
            db_service.set_camera_status_by_id(source_id, "running")

            while not runtime.stop_event.is_set():
                ok, frame = cap.read()
                if not ok:
                    if source_type in VIDEO_SOURCE_TYPES:
                        runtime.connection_status = "Completed"
                        db_service.set_camera_status_by_id(source_id, "completed")
                        break
                    runtime.connection_status = "No Frames"
                    runtime.last_error = "Waiting for frames."
                    self._set_status_frame(runtime, runtime.last_error)
                    time.sleep(0.25)
                    continue

                frame_number += 1
                runtime.frames_processing = frame_number
                display_frame = self._resize_for_stream(frame)
                annotated = display_frame
                detections = []

                if detector.model and frame_number % FRAME_SKIP == 0:
                    detection_frame = self._resize_for_detection(display_frame)
                    annotated, detections = detector.detect_frame(
                        detection_frame,
                        conf_threshold=self.conf_threshold,
                    )
                elif detector.model_error and not detector.model:
                    runtime.last_error = f"YOLO unavailable: {detector.model_error}"

                if detections:
                    self._handle_detection(runtime, annotated, detections)
                else:
                    runtime.detection_status = "No detection"

                self._store_frame(runtime, annotated)
                time.sleep(max(0.02, 1 / max(STREAM_FPS, 1)))
        finally:
            if cap is not None:
                cap.release()
            if runtime.connection_status not in {"Completed", "Disconnected"}:
                runtime.connection_status = "Stopped"
            with self._lock:
                self._runtimes.pop(source_id, None)
            if db_service.get_source(source_id):
                if runtime.connection_status == "Disconnected":
                    final_status = "disconnected"
                elif runtime.connection_status == "Completed":
                    final_status = "completed"
                else:
                    final_status = "idle"
                db_service.set_camera_status_by_id(source_id, final_status)

    def _handle_detection(self, runtime, annotated, detections):
        now = time.time()
        first = detections[0]
        confidence = first["confidence"]
        runtime.detection_status = "Elephant detected"
        runtime.latest_confidence = round(confidence * 100, 1)

        if now - runtime.last_alert_at < ALERT_COOLDOWN_SECONDS:
            return

        runtime.last_alert_at = now
        runtime.detection_count += 1
        image_path = detector.save_snapshot(annotated, runtime.source["source_type"])
        detection_id = db_service.add_detection(confidence, image_path, runtime.source)
        last_detection_time = db_service.set_camera_last_detection(runtime.source["id"])
        runtime.last_detection_time = last_detection_time
        alert = alert_service.create_elephant_alert(confidence, runtime.source)
        runtime.socketio.emit(
            "elephant_detected",
            {
                "detection_id": detection_id,
                "confidence": round(confidence * 100, 1),
                "image_path": image_path,
                "source_id": runtime.source["id"],
                "source_name": runtime.source["source_name"],
                "source_type": runtime.source["source_type"],
                "latitude": runtime.source["latitude"],
                "longitude": runtime.source["longitude"],
                "alert": alert,
            },
        )

    def _store_frame(self, runtime, frame):
        ok, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
        if not ok:
            return
        with runtime.lock:
            runtime.latest_jpeg = buffer.tobytes()

    def _set_status_frame(self, runtime, message):
        frame = detector.status_frame(message)
        self._store_frame(runtime, frame)

    def _resize_for_stream(self, frame):
        height, width = frame.shape[:2]
        if width <= STREAM_WIDTH:
            return frame
        scale = STREAM_WIDTH / float(width)
        return cv2.resize(frame, (STREAM_WIDTH, int(height * scale)))


multi_camera_manager = MultiCameraManager()
