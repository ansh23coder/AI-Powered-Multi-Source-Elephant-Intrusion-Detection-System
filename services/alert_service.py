from services import db_service


def create_elephant_alert(confidence, source):
    message = f"WARNING: Elephant detected at {source['source_name']}"
    alert_id = db_service.add_alert(message, severity="critical", status="active")
    return {
        "id": alert_id,
        "message": message,
        "severity": "critical",
        "confidence": round(confidence * 100, 1),
        "latitude": source["latitude"],
        "longitude": source["longitude"],
        "source_id": source["id"],
        "source_name": source["source_name"],
        "source_type": source["source_type"],
        "timestamp": db_service.now_iso(),
    }
