import sqlite3
from contextlib import contextmanager
from datetime import datetime

from config import DATABASE_PATH, DEFAULT_CAMERA_SOURCES, DETECTION_FOLDER, SCHEMA_PATH, VILLAGES


def now_iso():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@contextmanager
def get_connection():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_connection() as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        _migrate_existing_database(conn)
        for name, lat, lng, risk in VILLAGES:
            conn.execute(
                """
                INSERT OR IGNORE INTO villages
                (village_name, latitude, longitude, risk_level)
                VALUES (?, ?, ?, ?)
                """,
                (name, lat, lng, risk),
            )
        for source_name, source_type, source_input, lat, lng, status, notes in DEFAULT_CAMERA_SOURCES:
            conn.execute(
                """
                INSERT INTO camera_sources
                (source_name, source_type, source_input, stream_url, latitude, longitude, status, notes, created_at)
                SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?
                WHERE NOT EXISTS (
                    SELECT 1 FROM camera_sources WHERE source_name = ?
                )
                """,
                (
                    source_name,
                    source_type,
                    source_input,
                    source_input if source_type == "ip_camera" else None,
                    lat,
                    lng,
                    status,
                    notes,
                    now_iso(),
                    source_name,
                ),
            )
        conn.execute(
            """
            UPDATE camera_sources
            SET status = 'idle'
            WHERE status IN ('running', 'starting', 'connected', 'recording')
            """
        )
        _cleanup_duplicate_sources(conn)


def _migrate_existing_database(conn):
    detection_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(detections)").fetchall()
    }
    if "source_name" not in detection_columns:
        conn.execute("ALTER TABLE detections ADD COLUMN source_name TEXT")
    if "source_id" not in detection_columns:
        conn.execute("ALTER TABLE detections ADD COLUMN source_id INTEGER")

    source_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(camera_sources)").fetchall()
    }
    if "source_input" not in source_columns:
        conn.execute("ALTER TABLE camera_sources ADD COLUMN source_input TEXT")
        conn.execute(
            """
            UPDATE camera_sources
            SET source_input = COALESCE(stream_url, CASE WHEN source_type = 'webcam' THEN '0' ELSE '' END)
            """
        )
    if "enabled" not in source_columns:
        conn.execute("ALTER TABLE camera_sources ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1")
    if "last_detection_time" not in source_columns:
        conn.execute("ALTER TABLE camera_sources ADD COLUMN last_detection_time TEXT")
    conn.execute(
        """
        UPDATE camera_sources
        SET source_input = 'auto'
        WHERE source_type = 'usb_webcam'
          AND source_name = 'USB Webcam - Forest Gate'
          AND COALESCE(source_input, '') IN ('', '1')
        """
    )
    conn.execute(
        """
        UPDATE camera_sources
        SET source_input = '0'
        WHERE source_type = 'webcam'
          AND source_name = 'Laptop Webcam - Forest Checkpost'
          AND COALESCE(source_input, '') = ''
        """
    )


def _cleanup_duplicate_sources(conn):
    default_names = (
        "Laptop Webcam - Forest Checkpost",
        "Mobile IP Camera - Watch Tower",
        "Drone Mode - Future SDK Slot",
        "USB Webcam - Forest Gate",
    )
    conn.execute(
        f"""
        DELETE FROM camera_sources
        WHERE source_name IN ({",".join("?" for _ in default_names)})
        """,
        default_names,
    )

    default_pairs = {
        ("webcam", "Laptop Webcam - Forest Checkpost"),
        ("usb_webcam", "USB Webcam - Forest Gate"),
    }
    for source_type, default_name in default_pairs:
        default_row = conn.execute(
            "SELECT id FROM camera_sources WHERE source_type = ? AND source_name = ? ORDER BY id ASC LIMIT 1",
            (source_type, default_name),
        ).fetchone()
        if default_row:
            conn.execute(
                """
                DELETE FROM camera_sources
                WHERE source_type = ?
                  AND source_name IN ('Laptop Webcam Source', 'USB Webcam Source')
                  AND id <> ?
                """,
                (source_type, default_row["id"]),
            )

    duplicate_rows = conn.execute(
        """
        SELECT
            COALESCE(
                MIN(CASE WHEN last_detection_time IS NOT NULL THEN id END),
                MIN(id)
            ) AS keep_id,
            GROUP_CONCAT(id) AS ids
        FROM camera_sources
        GROUP BY source_type, COALESCE(source_input, ''), COALESCE(stream_url, '')
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    for row in duplicate_rows:
        ids = [int(value) for value in row["ids"].split(",")]
        delete_ids = [source_id for source_id in ids if source_id != row["keep_id"]]
        for source_id in delete_ids:
            conn.execute("DELETE FROM camera_sources WHERE id = ?", (source_id,))


def rows_to_dicts(rows):
    return [dict(row) for row in rows]


def add_detection(confidence, image_path, source):
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO detections
            (timestamp, confidence, image_path, source_type, source_name, source_id, latitude, longitude)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now_iso(),
                confidence,
                image_path,
                source["source_type"],
                source["source_name"],
                source["id"],
                source["latitude"],
                source["longitude"],
            ),
        )
        return cur.lastrowid


def add_alert(message, severity="high", status="active"):
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO alerts (message, timestamp, severity, status)
            VALUES (?, ?, ?, ?)
            """,
            (message, now_iso(), severity, status),
        )
        return cur.lastrowid


def set_camera_status(source_type, status):
    with get_connection() as conn:
        conn.execute(
            "UPDATE camera_sources SET status = ? WHERE source_type = ?",
            (status, source_type),
        )


def set_camera_status_by_id(source_id, status):
    with get_connection() as conn:
        conn.execute("UPDATE camera_sources SET status = ? WHERE id = ?", (status, source_id))


def set_camera_last_detection(source_id):
    timestamp = now_iso()
    with get_connection() as conn:
        conn.execute(
            "UPDATE camera_sources SET last_detection_time = ? WHERE id = ?",
            (timestamp, source_id),
        )
    return timestamp


def get_source(source_id):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM camera_sources WHERE id = ?", (source_id,)).fetchone()
        return dict(row) if row else None


def get_source_by_type(source_type):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM camera_sources WHERE source_type = ? ORDER BY id ASC LIMIT 1",
            (source_type,),
        ).fetchone()
        return dict(row) if row else None


def get_sources():
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM camera_sources ORDER BY id ASC").fetchall()
        return rows_to_dicts(rows)


def save_source(
    source_name,
    source_type,
    stream_url,
    latitude,
    longitude,
    notes="",
    source_id=None,
    source_input=None,
    enabled=1,
):
    source_input = source_input if source_input is not None else stream_url
    with get_connection() as conn:
        if source_id:
            conn.execute(
                """
                UPDATE camera_sources
                SET source_name = ?, source_type = ?, source_input = ?, stream_url = ?,
                    latitude = ?, longitude = ?, notes = ?, enabled = ?
                WHERE id = ?
                """,
                (
                    source_name,
                    source_type,
                    source_input,
                    stream_url,
                    latitude,
                    longitude,
                    notes,
                    int(enabled),
                    source_id,
                ),
            )
            return source_id

        existing = conn.execute(
            """
            SELECT id
            FROM camera_sources
            WHERE source_type = ?
              AND COALESCE(source_input, '') = COALESCE(?, '')
            ORDER BY id ASC
            LIMIT 1
            """,
            (source_type, source_input),
        ).fetchone()
        if existing and source_type in {"webcam", "usb_webcam", "ip_camera", "upload", "recording"}:
            conn.execute(
                """
                UPDATE camera_sources
                SET source_name = ?, stream_url = ?, latitude = ?, longitude = ?,
                    notes = ?, enabled = 1
                WHERE id = ?
                """,
                (
                    source_name,
                    stream_url,
                    latitude,
                    longitude,
                    notes,
                    existing["id"],
                ),
            )
            return existing["id"]

        cur = conn.execute(
            """
            INSERT INTO camera_sources
            (source_name, source_type, source_input, stream_url, latitude, longitude, status, enabled, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'idle', ?, ?, ?)
            """,
            (
                source_name,
                source_type,
                source_input,
                stream_url,
                latitude,
                longitude,
                int(enabled),
                notes,
                now_iso(),
            ),
        )
        return cur.lastrowid


def remove_source(source_id):
    with get_connection() as conn:
        conn.execute("DELETE FROM camera_sources WHERE id = ?", (source_id,))


def set_source_enabled(source_id, enabled):
    with get_connection() as conn:
        conn.execute(
            "UPDATE camera_sources SET enabled = ?, status = CASE WHEN ? = 0 THEN 'disabled' ELSE status END WHERE id = ?",
            (int(enabled), int(enabled), source_id),
        )


def update_source_location(source_type, source_name, latitude, longitude, stream_url=None):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM camera_sources WHERE source_type = ? ORDER BY id ASC LIMIT 1",
            (source_type,),
        ).fetchone()
        if row:
            conn.execute(
                """
                UPDATE camera_sources
                SET source_name = ?, latitude = ?, longitude = ?, stream_url = COALESCE(?, stream_url)
                WHERE id = ?
                """,
                (source_name, latitude, longitude, stream_url, row["id"]),
            )
            return row["id"]
        cur = conn.execute(
            """
            INSERT INTO camera_sources
            (source_name, source_type, stream_url, latitude, longitude, status, notes, created_at)
            VALUES (?, ?, ?, ?, ?, 'idle', '', ?)
            """,
            (source_name, source_type, stream_url, latitude, longitude, now_iso()),
        )
        return cur.lastrowid


def get_detections(limit=50):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM detections ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return rows_to_dicts(rows)


def get_alerts(limit=30):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM alerts ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return rows_to_dicts(rows)


def get_detection(detection_id):
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT
                d.*,
                (
                    SELECT severity
                    FROM alerts
                    WHERE timestamp >= d.timestamp
                    ORDER BY timestamp ASC
                    LIMIT 1
                ) AS alert_severity
            FROM detections d
            WHERE d.id = ?
            """,
            (detection_id,),
        ).fetchone()
        return dict(row) if row else None


def get_villages():
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM villages ORDER BY id ASC").fetchall()
        return rows_to_dicts(rows)


def get_analytics():
    with get_connection() as conn:
        total = conn.execute("SELECT COUNT(*) AS count FROM detections").fetchone()["count"]
        active = conn.execute(
            "SELECT COUNT(*) AS count FROM alerts WHERE status = 'active'"
        ).fetchone()["count"]
        today = datetime.now().strftime("%Y-%m-%d")
        today_alerts = conn.execute(
            "SELECT COUNT(*) AS count FROM alerts WHERE timestamp LIKE ?",
            (f"{today}%",),
        ).fetchone()["count"]
        avg_conf = conn.execute("SELECT AVG(confidence) AS avg_conf FROM detections").fetchone()[
            "avg_conf"
        ]
        zones = conn.execute("SELECT COUNT(*) AS count FROM villages").fetchone()["count"]
        sources = conn.execute("SELECT COUNT(*) AS count FROM camera_sources").fetchone()["count"]
        connected = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM camera_sources
            WHERE status IN ('running', 'connected', 'recording')
            """
        ).fetchone()["count"]
        per_source_rows = conn.execute(
            """
            SELECT
                COALESCE(source_name, source_type) AS source_name,
                source_type,
                COUNT(*) AS detection_count,
                ROUND(AVG(confidence) * 100, 1) AS average_confidence
            FROM detections
            GROUP BY COALESCE(source_name, source_type), source_type
            ORDER BY detection_count DESC
            """
        ).fetchall()
        screenshots = len(list(DETECTION_FOLDER.glob("*.jpg"))) if DETECTION_FOLDER.exists() else 0
        return {
            "total_detections": total,
            "active_threats": active,
            "today_alerts": today_alerts,
            "monitored_zones": zones,
            "configured_sources": sources,
            "connected_sources": connected,
            "total_screenshots": screenshots,
            "average_confidence": round((avg_conf or 0) * 100, 1),
            "per_source": rows_to_dicts(per_source_rows),
        }


def export_log_rows():
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                d.id AS detection_id,
                d.timestamp AS detection_time,
                d.confidence,
                d.source_type,
                COALESCE(d.source_name, d.source_type) AS source_name,
                d.latitude,
                d.longitude,
                d.image_path,
                a.message AS alert_message,
                a.severity AS alert_severity,
                a.status AS alert_status
            FROM detections d
            LEFT JOIN alerts a
                ON a.timestamp = (
                    SELECT timestamp
                    FROM alerts
                    WHERE timestamp >= d.timestamp
                    ORDER BY timestamp ASC
                    LIMIT 1
                )
            ORDER BY d.id DESC
            """
        ).fetchall()
        return rows_to_dicts(rows)


def reset_demo_logs(delete_screenshots=True):
    with get_connection() as conn:
        conn.execute("DELETE FROM detections")
        conn.execute("DELETE FROM alerts")
        conn.execute(
            "UPDATE camera_sources SET status = CASE WHEN source_type = 'drone' THEN 'disconnected' ELSE 'idle' END"
        )

    deleted = 0
    if delete_screenshots and DETECTION_FOLDER.exists():
        for image_path in DETECTION_FOLDER.glob("*.jpg"):
            image_path.unlink(missing_ok=True)
            deleted += 1
    return {"detections": 0, "alerts": 0, "screenshots_deleted": deleted}
