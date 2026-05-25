from services import db_service


class DroneService:
    def __init__(self):
        self.connected = False
        self.battery = None
        self.signal = None
        self.flight_mode = "Standby"

    def connect(self):
        self.connected = True
        self.flight_mode = "SDK integration pending"
        db_service.set_camera_status("drone", "connected")
        return self.status()

    def disconnect(self):
        self.connected = False
        self.flight_mode = "Standby"
        db_service.set_camera_status("drone", "disconnected")
        return self.status()

    def status(self):
        return {
            "connection_status": "Connected" if self.connected else "Disconnected",
            "battery": self.battery,
            "signal": self.signal,
            "flight_mode": self.flight_mode,
            "feed_mode": "UI-only future SDK slot. No drone telemetry is generated.",
        }


drone_service = DroneService()
