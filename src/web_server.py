"""
web_server.py
שרת ניהול קליל למערכת הצלצולים: מגיש API על schedule.json + את ממשק הניהול הסטטי.
לא נוגע במנוע ההשמעה (main.py) ולא בבוט הטלגרם - כולם קוראים/כותבים לאותו schedule.json.

הרצה:  python src/web_server.py
ברירת מחדל: http://localhost:8500
"""

import json
import os
import tempfile
import logging
from datetime import date

from flask import Flask, jsonify, request, send_from_directory

# ============================================================
# נתיבים
# ============================================================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEDULE_FILE = os.path.join(BASE_DIR, "schedule.json")
AUDIO_DIR = os.path.join(BASE_DIR, "audio")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
LOG_FILE = os.path.join(LOGS_DIR, "app.log")
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

os.makedirs(LOGS_DIR, exist_ok=True)
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    encoding='utf-8'
)

DAY_ORDER = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="")


# ============================================================
# עזר: נרמול שעה, טעינה/שמירה אטומית
# ============================================================
def migrate_time(t):
    t = str(t).strip()
    parts = t.split(":")
    if len(parts) == 2:
        return f"{parts[0].zfill(2)}:{parts[1].zfill(2)}:00"
    if len(parts) == 3:
        return f"{parts[0].zfill(2)}:{parts[1].zfill(2)}:{parts[2].zfill(2)}"
    return "00:00:00"


def load_data():
    if not os.path.exists(SCHEDULE_FILE):
        default_data = {
            "system_status": "enabled",
            "disabled_scope": None,
            "disabled_date": None,
            "institution_info": {
                "name": "בית ספר דוגמה", "contact": "", "phone": "", "email": "",
                "address": "", "license_status": "פעיל בתוקף"
            },
            "weekly_schedule": {day: [] for day in DAY_ORDER}
        }
        save_data(default_data)
        return default_data

    with open(SCHEDULE_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    weekly = data.get("weekly_schedule", {})
    for day in DAY_ORDER:
        events = weekly.get(day, [])
        for ev in events:
            ev["time"] = migrate_time(ev.get("time", "00:00:00"))
            ev.setdefault("enabled", True)
            ev.setdefault("type", "Bell")
            ev.setdefault("volume", 85)
            ev.setdefault("duration_seconds", 30)
        events.sort(key=lambda x: x.get("time", "00:00:00"))
        weekly[day] = events
    data["weekly_schedule"] = weekly
    data.setdefault("system_status", "enabled")
    data.setdefault("disabled_scope", None)
    data.setdefault("disabled_date", None)
    return data


def save_data(data):
    """כתיבה אטומית: כתיבה לקובץ זמני ואז החלפה, כדי לא להשאיר schedule.json חצי-כתוב
    בזמן שמנוע ההשמעה (main.py) עשוי לקרוא אותו במקביל."""
    dir_name = os.path.dirname(SCHEDULE_FILE)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, prefix=".schedule_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        os.replace(tmp_path, SCHEDULE_FILE)
        logging.info("schedule.json עודכן בהצלחה (דרך web_server).")
    except Exception as e:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        logging.error(f"שגיאה בשמירת schedule.json: {e}")
        raise


def get_audio_files():
    if not os.path.exists(AUDIO_DIR):
        os.makedirs(AUDIO_DIR, exist_ok=True)
    files = [f for f in os.listdir(AUDIO_DIR) if f.lower().endswith((".mp3", ".wav", ".ogg"))]
    return files if files else ["Tune 4.mp3", "Ring 1.mp3"]


# ============================================================
# Static: הגשת הממשק
# ============================================================
@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


# ============================================================
# API
# ============================================================
@app.route("/api/schedule", methods=["GET"])
def api_get_schedule():
    return jsonify(load_data())


@app.route("/api/audio-files", methods=["GET"])
def api_audio_files():
    return jsonify(get_audio_files())


@app.route("/api/event", methods=["POST"])
def api_add_event():
    body = request.get_json(force=True)
    day = body.get("day")
    event = body.get("event")
    if day not in DAY_ORDER or not event:
        return jsonify({"error": "בקשה לא תקינה"}), 400

    event["time"] = migrate_time(event.get("time", "00:00:00"))
    data = load_data()
    data["weekly_schedule"][day].append(event)
    data["weekly_schedule"][day].sort(key=lambda x: x["time"])
    save_data(data)
    return jsonify(data)


@app.route("/api/event", methods=["PUT"])
def api_update_event():
    body = request.get_json(force=True)
    day = body.get("day")
    index = body.get("index")
    event = body.get("event")
    if day not in DAY_ORDER or index is None or not event:
        return jsonify({"error": "בקשה לא תקינה"}), 400

    data = load_data()
    events = data["weekly_schedule"].get(day, [])
    if index < 0 or index >= len(events):
        return jsonify({"error": "אינדקס לא תקין"}), 404

    event["time"] = migrate_time(event.get("time", "00:00:00"))
    events[index] = event
    events.sort(key=lambda x: x["time"])
    data["weekly_schedule"][day] = events
    save_data(data)
    return jsonify(data)


@app.route("/api/event", methods=["DELETE"])
def api_delete_event():
    body = request.get_json(force=True)
    day = body.get("day")
    index = body.get("index")
    if day not in DAY_ORDER or index is None:
        return jsonify({"error": "בקשה לא תקינה"}), 400

    data = load_data()
    events = data["weekly_schedule"].get(day, [])
    if index < 0 or index >= len(events):
        return jsonify({"error": "אינדקס לא תקין"}), 404

    del events[index]
    data["weekly_schedule"][day] = events
    save_data(data)
    return jsonify(data)


@app.route("/api/system", methods=["POST"])
def api_set_system():
    body = request.get_json(force=True)
    mode = body.get("mode")  # "enabled" | "today" | "indefinite"
    data = load_data()

    if mode == "enabled":
        data["system_status"] = "enabled"
        data["disabled_scope"] = None
        data["disabled_date"] = None
    elif mode == "today":
        data["system_status"] = "disabled"
        data["disabled_scope"] = "day"
        data["disabled_date"] = date.today().isoformat()
    elif mode == "indefinite":
        data["system_status"] = "disabled"
        data["disabled_scope"] = "indefinite"
        data["disabled_date"] = None
    else:
        return jsonify({"error": "מצב לא מוכר"}), 400

    save_data(data)
    return jsonify(data)


if __name__ == "__main__":
    load_data()  # ליצור schedule.json בברירת מחדל אם חסר
    app.run(host="0.0.0.0", port=8500, debug=False)
