from flask import Flask, send_from_directory
from flask_socketio import SocketIO

from config import (
    BASE_DIR,
    DATABASE_PATH,
    DETECTION_FOLDER,
    RECORDING_FOLDER,
    SECRET_KEY,
    ULTRALYTICS_CONFIG_DIR,
    UPLOAD_FOLDER,
)

from routes.api_routes import api_bp
from routes.dashboard_routes import dashboard_bp
from routes.detection_routes import detection_bp
from services.db_service import init_db


socketio = SocketIO(cors_allowed_origins="*", async_mode="threading")


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = SECRET_KEY
    app.config["MAX_CONTENT_LENGTH"] = 600 * 1024 * 1024

    for folder in (
        DATABASE_PATH.parent,
        UPLOAD_FOLDER,
        DETECTION_FOLDER,
        RECORDING_FOLDER,
        ULTRALYTICS_CONFIG_DIR,
    ):
        folder.mkdir(parents=True, exist_ok=True)

    init_db()

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(detection_bp)

    socketio.init_app(app)
    app.extensions["socketio"] = socketio

    @app.route("/detections/<path:filename>")
    def detection_file(filename):
        return send_from_directory(DETECTION_FOLDER, filename)

    @app.route("/recordings/<path:filename>")
    def recording_file(filename):
        return send_from_directory(RECORDING_FOLDER, filename)

    @app.route("/uploads/<path:filename>")
    def upload_file(filename):
        return send_from_directory(UPLOAD_FOLDER, filename)

    return app


app = create_app()


if __name__ == "__main__":
    socketio.run(
        app,
        host="0.0.0.0",
        port=5000,
        debug=True,
        use_reloader=False,
        allow_unsafe_werkzeug=True,
    )

    