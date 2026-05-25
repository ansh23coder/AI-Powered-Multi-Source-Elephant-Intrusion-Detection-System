import csv
import time
from io import StringIO

from flask import Blueprint, Response, current_app, jsonify, request
from werkzeug.utils import secure_filename

from config import ALLOWED_VIDEO_EXTENSIONS, DATABASE_PATH, UPLOAD_FOLDER
from services import db_service
from services.detector import detector
from services.drone_service import drone_service
from services.multi_camera_service import multi_camera_manager
from services.video_service import get_monitoring_status, set_conf_threshold, stop_monitoring

api_bp = Blueprint("api", __name__)

def _allowed_video(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_VIDEO_EXTENSIONS

@api_bp.route("/get-detections")
def get_detections():
    return jsonify(db_service.get_detections())


@api_bp.route("/get-alerts")
def get_alerts():
    return jsonify(db_service.get_alerts())


@api_bp.route("/get-map-data")
def get_map_data():
    detections = db_service.get_detections(100)
    villages = db_service.get_villages()
    sources = db_service.get_sources()
    heat_points = [
        [item["latitude"], item["longitude"], max(0.4, float(item["confidence"]))]
        for item in detections
    ]
    return jsonify(
        {
            "detections": detections,
            "villages": villages,
            "sources": sources,
            "camera_status": multi_camera_manager.get_status(),
            "heat_points": heat_points,
            "drone": drone_service.status(),
        }
    )


@api_bp.route("/get-analytics")
def get_analytics():
    return jsonify(db_service.get_analytics())


@api_bp.route("/system-status")
def system_status():
    sources = db_service.get_sources()
    runtime_by_id = {item["source_id"]: item for item in multi_camera_manager.get_status()}
    merged_sources = []
    for source in sources:
        enriched = dict(source)
        runtime = runtime_by_id.get(source["id"])
        if runtime:
            enriched.update(runtime)
            enriched["status"] = "running"
        merged_sources.append(enriched)
    return jsonify(
        {
            "sources": merged_sources,
            "yolo": {
                "status": "Active" if detector.model and not detector.model_error else "Unavailable",
                "model_error": detector.model_error,
            },
            "database": {
                "status": "Running" if DATABASE_PATH.exists() else "Unavailable",
                "path": str(DATABASE_PATH),
            },
            "socketio": {"status": "Live"},
            "monitoring": {
                **get_monitoring_status(),
                "active_cameras": multi_camera_manager.get_status(),
                "confidence_threshold": multi_camera_manager.conf_threshold,
            },
        }
    )


@api_bp.route("/settings/confidence-threshold", methods=["POST"])
def confidence_threshold():
    payload = request.get_json(force=True)
    try:
        threshold = set_conf_threshold(payload.get("threshold", 0.5))
        multi_camera_manager.set_conf_threshold(threshold)
    except (TypeError, ValueError):
        return jsonify({"error": "threshold must be a number between 0.30 and 0.90"}), 400
    return jsonify({"status": "saved", "threshold": threshold})


@api_bp.route("/sources")
def get_sources():
    return jsonify(db_service.get_sources())


@api_bp.route("/sources", methods=["POST"])
def save_source():
    payload = request.get_json(force=True)
    source_type = payload.get("source_type", "ip_camera")
    source_input = str(payload.get("source_input", payload.get("stream_url", ""))).strip()
    stream_url = source_input if source_type == "ip_camera" else payload.get("stream_url", "")
    try:
        source_id = db_service.save_source(
            payload.get("source_name", "Camera Source").strip(),
            source_type,
            stream_url,
            float(payload["latitude"]),
            float(payload["longitude"]),
            payload.get("notes", ""),
            payload.get("id"),
            source_input,
            payload.get("enabled", 1),
        )
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "source_name, latitude, and longitude are required"}), 400
    return jsonify({"status": "saved", "id": source_id})


@api_bp.route("/sources/upload-video", methods=["POST"])
def save_upload_source():
    file = request.files.get("video")
    if not file or not _allowed_video(file.filename):
        return jsonify({"error": "Upload a supported video file: mp4, avi, mov, webm"}), 400
    UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
    filename = secure_filename(file.filename)
    path = UPLOAD_FOLDER / filename
    file.save(path)
    try:
        source_type = request.form.get("source_type", "upload")
        source_id = db_service.save_source(
            request.form.get("source_name", "Uploaded Video Feed").strip(),
            source_type,
            "",
            float(request.form["latitude"]),
            float(request.form["longitude"]),
            "Uploaded or recorded video source",
            None,
            str(path),
        )
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "source_name, latitude, and longitude are required"}), 400
    return jsonify({"status": "saved", "source": db_service.get_source(source_id)})


@api_bp.route("/sources/<int:source_id>", methods=["DELETE"])
def remove_source(source_id):
    multi_camera_manager.stop_source(source_id)
    db_service.remove_source(source_id)
    return jsonify({"status": "removed", "source_id": source_id})


@api_bp.route("/sources/<int:source_id>/enabled", methods=["POST"])
def source_enabled(source_id):
    payload = request.get_json(force=True)
    enabled = bool(payload.get("enabled"))
    if not enabled:
        multi_camera_manager.stop_source(source_id)
    db_service.set_source_enabled(source_id, enabled)
    return jsonify({"status": "updated", "source": db_service.get_source(source_id)})


@api_bp.route("/camera-status")
def camera_status():
    return jsonify(multi_camera_manager.get_status())


@api_bp.route("/probe-webcams")
def probe_webcams():
    max_index = request.args.get("max", 3, type=int)
    return jsonify(multi_camera_manager.probe_webcams(max(0, min(max_index, 10))))


@api_bp.route("/camera-feed/<int:source_id>")
def camera_feed(source_id):
    def frames():
        while multi_camera_manager.is_running(source_id):
            frame = multi_camera_manager.latest_frame(source_id)
            if frame:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
                )
            time.sleep(0.05)

    return Response(frames(), mimetype="multipart/x-mixed-replace; boundary=frame")


@api_bp.route("/sources/<int:source_id>/start", methods=["POST"])
def start_camera_source(source_id):
    source = db_service.get_source(source_id)
    try:
        status = multi_camera_manager.start_source(source, current_app.extensions["socketio"])
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"status": "started", "camera": status, "source": db_service.get_source(source_id)})


@api_bp.route("/sources/<int:source_id>/stop", methods=["POST"])
def stop_camera_source(source_id):
    multi_camera_manager.stop_source(source_id)
    return jsonify({"status": "stopped", "source": db_service.get_source(source_id)})


@api_bp.route("/sources/stop-all", methods=["POST"])
def stop_all_camera_sources():
    multi_camera_manager.stop_all()
    return jsonify({"status": "stopped"})


@api_bp.route("/detections/<int:detection_id>")
def get_detection(detection_id):
    detection = db_service.get_detection(detection_id)
    if not detection:
        return jsonify({"error": "Detection not found"}), 404
    return jsonify(detection)


@api_bp.route("/export/logs.csv")
def export_logs_csv():
    rows = db_service.export_log_rows()
    output = StringIO()
    fieldnames = [
        "detection_id",
        "detection_time",
        "confidence",
        "source_type",
        "source_name",
        "latitude",
        "longitude",
        "image_path",
        "alert_message",
        "alert_severity",
        "alert_status",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=elephant_monitoring_logs.csv"},
    )


@api_bp.route("/reset-demo-logs", methods=["POST"])
def reset_demo_logs():
    stop_monitoring()
    multi_camera_manager.stop_all()
    return jsonify({"status": "cleared", **db_service.reset_demo_logs(delete_screenshots=True)})


@api_bp.route("/drone-status")
def drone_status():
    return jsonify(drone_service.status())
