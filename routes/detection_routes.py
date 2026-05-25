from pathlib import Path

from flask import Blueprint, Response, current_app, jsonify, request
from werkzeug.utils import secure_filename

from config import ALLOWED_VIDEO_EXTENSIONS, RECORDING_FOLDER, UPLOAD_FOLDER
from services import db_service
from services.drone_service import drone_service
from services.video_service import generate_frames, set_monitoring, stop_monitoring


detection_bp = Blueprint("detection", __name__)


def allowed_video(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_VIDEO_EXTENSIONS


def _save_video(file_storage, folder):
    folder.mkdir(parents=True, exist_ok=True)
    filename = secure_filename(file_storage.filename or "recording.webm")
    path = folder / filename
    file_storage.save(path)
    return path


@detection_bp.route("/video-feed")
def video_feed():
    return Response(
        generate_frames(current_app.extensions["socketio"]),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@detection_bp.route("/start-webcam", methods=["POST"])
def start_webcam():
    source = db_service.get_source_by_type("webcam")
    set_monitoring(source)
    return jsonify({"status": "started", "source": source})


@detection_bp.route("/start-ip-camera", methods=["POST"])
def start_ip_camera():
    payload = request.get_json(force=True)
    stream_url = payload.get("stream_url", "").strip()
    if not stream_url:
        return jsonify({"error": "Enter the mobile IP camera stream URL"}), 400

    source_id = db_service.update_source_location(
        "ip_camera",
        payload.get("source_name", "Mobile IP Camera").strip() or "Mobile IP Camera",
        float(payload.get("latitude", 21.2642)),
        float(payload.get("longitude", 81.6434)),
        stream_url,
    )
    source = db_service.get_source(source_id)
    set_monitoring(source, stream_url=stream_url)
    return jsonify({"status": "started", "source": source})


@detection_bp.route("/upload-video", methods=["POST"])
def upload_video():
    file = request.files.get("video")
    source_type = request.form.get("source_type", "upload")
    if not file or not allowed_video(file.filename):
        return jsonify({"error": "Upload a supported video file: mp4, avi, mov, webm"}), 400
    path = _save_video(file, UPLOAD_FOLDER)
    source_id = db_service.update_source_location(
        source_type,
        request.form.get("source_name", "Uploaded Video Source"),
        float(request.form.get("latitude", 21.2775)),
        float(request.form.get("longitude", 81.6540)),
    )
    db_service.save_source(
        request.form.get("source_name", "Uploaded Video Source"),
        source_type,
        "",
        float(request.form.get("latitude", 21.2775)),
        float(request.form.get("longitude", 81.6540)),
        "Uploaded video source",
        source_id,
        str(path),
    )
    source = db_service.get_source(source_id)
    set_monitoring(source, path)
    return jsonify({"status": "uploaded", "source": source, "filename": path.name})


@detection_bp.route("/start-drone", methods=["POST"])
def start_drone():
    drone = drone_service.connect()
    return jsonify({"status": "connected", "source_type": "drone", "drone": drone})


@detection_bp.route("/start-recording", methods=["POST"])
def start_recording():
    db_service.set_camera_status("recording", "recording")
    return jsonify({"status": "browser-recording-ready"})


@detection_bp.route("/stop-recording", methods=["POST"])
def stop_recording():
    file = request.files.get("video")
    if not file:
        return jsonify({"error": "No recording received"}), 400
    path = _save_video(file, RECORDING_FOLDER)
    source_id = db_service.update_source_location(
        "recording",
        request.form.get("source_name", "Browser Recording Source"),
        float(request.form.get("latitude", 21.2560)),
        float(request.form.get("longitude", 81.6350)),
    )
    db_service.save_source(
        request.form.get("source_name", "Browser Recording Source"),
        "recording",
        "",
        float(request.form.get("latitude", 21.2560)),
        float(request.form.get("longitude", 81.6350)),
        "Browser recording source",
        source_id,
        str(path),
    )
    source = db_service.get_source(source_id)
    set_monitoring(source, path)
    return jsonify({"status": "recording-saved", "filename": Path(path).name})


@detection_bp.route("/stop-monitoring", methods=["POST"])
def stop_monitoring_route():
    stop_monitoring()
    return jsonify({"status": "stopped"})
