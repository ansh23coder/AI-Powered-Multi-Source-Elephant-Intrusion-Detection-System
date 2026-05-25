import time
import cv2
from services import alert_service, db_service
from services.detector import detector

class MonitoringState:
    def __init__(self):
        self.running = False
        self.source = None
        self.video_path = None
        self.stream_url = None
        self.last_alert_at = 0
        self.alert_cooldown_seconds = 10
        self.conf_threshold = 0.5

state = MonitoringState()

def set_conf_threshold(value):
    state.conf_threshold = max(0.3, min(0.9, float(value)))
    return state.conf_threshold

def get_monitoring_status():
    return {
        "running": state.running,
        "source": state.source,
        "confidence_threshold": state.conf_threshold,
        "alert_cooldown_seconds": state.alert_cooldown_seconds,
    }

def set_monitoring(source, video_path=None, stream_url=None):
    if state.source and state.source["id"] != source["id"]:
        old_status = "connected" if state.source["source_type"] == "drone" else "idle"
        db_service.set_camera_status_by_id(state.source["id"], old_status)
    state.running = True
    state.source = source
    state.video_path = video_path
    state.stream_url = stream_url if stream_url is not None else source.get("stream_url")
    state.last_alert_at = 0
    db_service.set_camera_status_by_id(source["id"], "running")

def stop_monitoring():
    if state.source:
        status = "connected" if state.source["source_type"] == "drone" else "idle"
        db_service.set_camera_status_by_id(state.source["id"], status)
    state.running = False
    state.source = None
    state.video_path = None
    state.stream_url = None


def _capture_for_state():
    if not state.source:
        return None

    source_type = state.source["source_type"]
    if source_type == "webcam":
        return cv2.VideoCapture(0)
    if source_type == "ip_camera" and state.stream_url:
        return cv2.VideoCapture(state.stream_url)
    if state.video_path:
        return cv2.VideoCapture(str(state.video_path))
    return None


def _emit_detection(socketio, annotated, detections):
    if not detections or not state.source:
        return

    now = time.time()
    if now - state.last_alert_at < state.alert_cooldown_seconds:
        return
    state.last_alert_at = now

    first = detections[0]
    image_path = detector.save_snapshot(annotated, state.source["source_type"])
    detection_id = db_service.add_detection(first["confidence"], image_path, state.source)
    alert = alert_service.create_elephant_alert(first["confidence"], state.source)

    socketio.emit(
        "elephant_detected",
        {
            "detection_id": detection_id,
            "confidence": round(first["confidence"] * 100, 1),
            "image_path": image_path,
            "source_id": state.source["id"],
            "source_name": state.source["source_name"],
            "source_type": state.source["source_type"],
            "latitude": state.source["latitude"],
            "longitude": state.source["longitude"],
            "alert": alert,
        },
    )


def generate_frames(socketio):
    cap = _capture_for_state()
    frame_count = 0

    try:
        while state.running:
            source_type = state.source["source_type"] if state.source else "idle"
            if detector.model_error and not detector.model:
                frame = detector.status_frame(f"YOLO model unavailable: {detector.model_error}")
            elif cap is None or not cap.isOpened():
                frame = detector.status_frame(f"Unable to open {source_type} input")
            else:
                ok, frame = cap.read()
                if not ok:
                    if source_type in {"upload", "recording"}:
                        stop_monitoring()
                        break
                    frame = detector.status_frame(f"Waiting for frames from {source_type}")

            frame_count += 1
            if frame_count % 5 == 0 and detector.model and cap is not None and cap.isOpened():
                annotated, detections = detector.detect_frame(frame, state.conf_threshold)
                _emit_detection(socketio, annotated, detections)
            else:
                annotated = frame

            ok, buffer = cv2.imencode(".jpg", annotated)
            if not ok:
                continue

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + buffer.tobytes() + b"\r\n"
            )
            time.sleep(0.07)
    finally:
        if cap is not None:
            cap.release()
