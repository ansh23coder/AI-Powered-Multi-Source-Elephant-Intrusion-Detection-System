from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "database" / "elephant.db"
SCHEMA_PATH = BASE_DIR / "database" / "schema.sql"
UPLOAD_FOLDER = BASE_DIR / "uploads"
DETECTION_FOLDER = BASE_DIR / "detections"
RECORDING_FOLDER = BASE_DIR / "recordings"
ULTRALYTICS_CONFIG_DIR = BASE_DIR / ".ultralytics"

SECRET_KEY = "forest-ai-demo-secret"
ALLOWED_VIDEO_EXTENSIONS = {"mp4", "avi", "mov", "webm"}

VILLAGES = [
    ("Kamalpur", 21.2529, 81.6319, "High"),
    ("Arang Forest Border", 21.2642, 81.6434, "Medium"),
    ("Mahanadi Para", 21.2775, 81.6540, "High"),
    ("Bhoramdeo Hamlet", 21.2860, 81.6612, "Low"),
]

DEFAULT_CAMERA_SOURCES = []

YOLO_MODEL = "yolov8n.pt"
FRAME_SKIP = 6
DETECTION_WIDTH = 416
STREAM_WIDTH = 640
STREAM_HEIGHT = 360
STREAM_FPS = 12
JPEG_QUALITY = 72
ALERT_COOLDOWN_SECONDS = 10
