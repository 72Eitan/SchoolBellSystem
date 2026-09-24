"""
web_server.py
שרת ניהול למערכת הצלצולים: API על schedule.json (כולל ייבוא/ייצוא, הגדרות, הרשאות,
לוג פעולות בלתי-ניתן-למחיקה, Undo) + הגשת ממשק הניהול הסטטי.
לא נוגע במנוע ההשמעה (main.py) - קורא/כותב לאותו schedule.json בלבד, בכתיבה אטומית.

הרצה:  python src/web_server.py       (ברירת מחדל: http://localhost:8500)
"""

import json
import os
import io
import uuid
import tempfile
import hashlib
import logging
from datetime import date, datetime

import pandas as pd
from flask import Flask, jsonify, request, send_from_directory, send_file, session

# ============================================================
# נתיבים
# ============================================================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEDULE_FILE = os.path.join(BASE_DIR, "schedule.json")
AUDIO_DIR = os.path.join(BASE_DIR, "audio")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
LOG_FILE = os.path.join(LOGS_DIR, "app.log")
ACTIONS_LOG_FILE = os.path.join(LOGS_DIR, "actions_log.jsonl")   # לוג פעולות - קובץ מעקב, לא נמחק ע"י המערכת
UNDO_STACK_FILE = os.path.join(LOGS_DIR, "undo_stack.json")      # מצבים קודמים לצורך Undo (מוגבל בכמות)
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
MAX_UNDO_STEPS = 20

os.makedirs(LOGS_DIR, exist_ok=True)
logging.basicConfig(
    filename=LOG_FILE, level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s', encoding='utf-8'
)

DAY_ORDER = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
AUDIO_DEVICES_FILE = os.path.join(BASE_DIR, "audio_devices.json")  # נכתב ע"י main.py - רשימת התקנים אמיתית מהמחשב שמחובר לרמקולים
VALID_LICENSE_VALUE = "פעיל בתוקף"  # עקבי עם main.py - זהו הערך היחיד שנחשב "רישיון תקף"

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="")
app.secret_key = os.environ.get("BELL_SECRET_KEY", "school-bell-system-local-secret")  # מספיק לשימוש מקומי ברשת בית ספר


# ============================================================
# עזר: שעה, טעינה/שמירה אטומית
# ============================================================
def migrate_time(t):
    t = str(t).strip()
    parts = t.split(":")
    if len(parts) == 2:
        return f"{parts[0].zfill(2)}:{parts[1].zfill(2)}:00"
    if len(parts) == 3:
        return f"{parts[0].zfill(2)}:{parts[1].zfill(2)}:{parts[2].zfill(2)}"
    return "00:00:00"


DEFAULT_DATA = {
    "system_status": "enabled",
    "disabled_scope": None,
    "disabled_date": None,
    "institution_info": {
        "name": "בית ספר דוגמה", "contact": "", "phone": "", "email": "",
        "country": "ישראל", "state": "", "city": "", "region": "", "address": "",
        "interface_language": "he",
        "license_key": "", "license_status": VALID_LICENSE_VALUE,
        "payment_status": "", "audio_device": ""
    },
    "appearance": {"accent_color": "#1e3c72", "font_size": "normal"},
    "permissions": {"edit_password_hash": ""},
    "weekly_schedule": {day: [] for day in DAY_ORDER}
}


def _deep_default_merge(data, defaults):
    for k, v in defaults.items():
        if k not in data:
            data[k] = v
        elif isinstance(v, dict) and isinstance(data.get(k), dict):
            _deep_default_merge(data[k], v)
    return data


def load_data():
    if not os.path.exists(SCHEDULE_FILE):
        save_data(json.loads(json.dumps(DEFAULT_DATA)), log_action=False)
        return json.loads(json.dumps(DEFAULT_DATA))

    with open(SCHEDULE_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    data = _deep_default_merge(data, DEFAULT_DATA)
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
    return data


def save_data(data, log_action=True, action_name="update", action_details=""):
    """כתיבה אטומית + (אופציונלי) רישום ללוג פעולות + שמירת snapshot ל-Undo."""
    dir_name = os.path.dirname(SCHEDULE_FILE)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, prefix=".schedule_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        os.replace(tmp_path, SCHEDULE_FILE)
        logging.info(f"schedule.json עודכן ({action_name}).")
    except Exception as e:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        logging.error(f"שגיאה בשמירת schedule.json: {e}")
        raise

    if log_action:
        append_action_log(action_name, action_details)


# ============================================================
# לוג פעולות (audit trail - לא ניתן למחיקה דרך ה-API) + Undo
# ============================================================
def append_action_log(action_name, details=""):
    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "action": action_name,
        "details": details,
        "user_role": session.get("role", "viewer") if session else "system"
    }
    try:
        with open(ACTIONS_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logging.error(f"שגיאה בכתיבה ללוג הפעולות: {e}")


def read_action_log(limit=200):
    if not os.path.exists(ACTIONS_LOG_FILE):
        return []
    with open(ACTIONS_LOG_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()
    entries = []
    for line in lines[-limit:]:
        try:
            entries.append(json.loads(line))
        except Exception:
            continue
    entries.reverse()
    return entries


def push_undo_snapshot(pre_change_data):
    """שומר עותק של המצב *לפני* שינוי, לצורך שחזור עתידי."""
    stack = []
    if os.path.exists(UNDO_STACK_FILE):
        try:
            with open(UNDO_STACK_FILE, "r", encoding="utf-8") as f:
                stack = json.load(f)
        except Exception:
            stack = []
    stack.append(pre_change_data)
    stack = stack[-MAX_UNDO_STEPS:]
    with open(UNDO_STACK_FILE, "w", encoding="utf-8") as f:
        json.dump(stack, f, ensure_ascii=False)


def pop_undo_snapshot():
    if not os.path.exists(UNDO_STACK_FILE):
        return None
    try:
        with open(UNDO_STACK_FILE, "r", encoding="utf-8") as f:
            stack = json.load(f)
    except Exception:
        return None
    if not stack:
        return None
    snapshot = stack.pop()
    with open(UNDO_STACK_FILE, "w", encoding="utf-8") as f:
        json.dump(stack, f, ensure_ascii=False)
    return snapshot


def mutate(action_name, details, mutator_fn):
    """עוטף כל שינוי: שומר snapshot לפני, מפעיל את השינוי, שומר + רושם ללוג."""
    data = load_data()
    pre_snapshot = json.loads(json.dumps(data))
    result = mutator_fn(data)
    push_undo_snapshot(pre_snapshot)
    save_data(data, log_action=True, action_name=action_name, action_details=details)
    return result if result is not None else data


# ============================================================
# הרשאות (עריכה / צפייה בלבד)
# ============================================================
def is_editor():
    data = load_data()
    pw_hash = data.get("permissions", {}).get("edit_password_hash", "")
    if not pw_hash:
        return True  # לא הוגדרה סיסמת עריכה -> אין נעילה (ברירת מחדל תואמת אחורה)
    return session.get("role") == "editor"


def require_editor():
    if not is_editor():
        return jsonify({"error": "פעולה זו דורשת התחברות כעורך"}), 403
    return None


# ============================================================
# קבצי שמע
# ============================================================
def get_audio_files():
    if not os.path.exists(AUDIO_DIR):
        os.makedirs(AUDIO_DIR, exist_ok=True)
    files = [f for f in os.listdir(AUDIO_DIR) if f.lower().endswith((".mp3", ".wav", ".ogg"))]
    return files if files else ["Tune 4.mp3", "Ring 1.mp3"]


def get_audio_output_devices():
    """רשימת התקני פלט שמע אמיתית. המקור הראשי הוא audio_devices.json שנכתב ע"י
    main.py (שכבר משתמש ב-pygame לזיהוי התקנים בפועל, בלי תלות נוספת). אם main.py
    לא רץ עדיין (הקובץ לא קיים), ננסה sounddevice אם מותקן, ואם לא - רשימת ברירת מחדל."""
    if os.path.exists(AUDIO_DEVICES_FILE):
        try:
            with open(AUDIO_DEVICES_FILE, "r", encoding="utf-8") as f:
                devices = json.load(f)
            if devices:
                return devices
        except Exception as e:
            logging.info(f"שגיאה בקריאת audio_devices.json ({e}).")

    try:
        import sounddevice as sd
        devices = sd.query_devices()
        names = []
        for d in devices:
            if d.get("max_output_channels", 0) > 0:
                name = d.get("name")
                if name and name not in names:
                    names.append(name)
        if names:
            return names
    except Exception as e:
        logging.info(f"לא ניתן לאתר התקני שמע בפועל ({e}) - מציג רשימת ברירת מחדל.")
    return ["ברירת מחדל של המערכת"]


# ============================================================
# ייבוא / ייצוא Excel
# ============================================================
def parse_import_excel(file_stream):
    df_import = pd.read_excel(file_stream)
    # שורת תיאור פורמט משנית (Text / HH:MM:SS / ...) - מדלגים עליה אם קיימת
    if len(df_import) and (
        str(df_import.iloc[0].get('תיאור הארוע')) == 'Text' or
        'HH:MM:SS' in str(df_import.iloc[0].get('שעת התחלה', ''))
    ):
        df_import = df_import.iloc[1:].reset_index(drop=True)

    weekly_schedule = {day: [] for day in DAY_ORDER}
    day_cols = [1, 2, 3, 4, 5, 6, 7]

    for _, row in df_import.iterrows():
        if pd.isna(row.get('תיאור הארוע')) or pd.isna(row.get('שעת התחלה')):
            continue
        event = {
            "label": str(row['תיאור הארוע']),
            "time": migrate_time(row['שעת התחלה']),
            "duration_seconds": int(row.get('משך השמעה בשניות', 30)),
            "audio": str(row.get('קובץ שמע', 'Tune 4.mp3')),
            "type": str(row.get('סוג ארוע', 'Bell')),
            "volume": int(row.get('עוצמת שמע', 85)),
            "enabled": bool(int(row.get('פעיל', 1))),
            "uid": uuid.uuid4().hex[:12]
        }
        for d_idx, d_col in enumerate(day_cols):
            if d_col in row and str(row[d_col]).strip() in ['1', '1.0', 'True', 'true']:
                weekly_schedule[DAY_ORDER[d_idx]].append(event.copy())

    for day in DAY_ORDER:
        weekly_schedule[day].sort(key=lambda x: x["time"])
    return weekly_schedule


def build_export_excel(data):
    weekly = data.get("weekly_schedule", {})
    all_events = {}
    for day in DAY_ORDER:
        for ev in weekly.get(day, []):
            key = (ev['time'], ev['label'], ev['audio'])
            if key not in all_events:
                all_events[key] = {
                    "label": ev.get('label'), "time": ev.get('time'),
                    "duration_seconds": ev.get('duration_seconds', 30),
                    "audio": ev.get('audio', 'Tune 4.mp3'), "type": ev.get('type', 'Bell'),
                    "volume": ev.get('volume', 85), "enabled": ev.get('enabled', True),
                    "days": {d: 0 for d in range(1, 8)}
                }
            day_idx = DAY_ORDER.index(day) + 1
            all_events[key]["days"][day_idx] = 1 if ev.get('enabled', True) else 0

    rows = []
    for idx, (k, ev) in enumerate(all_events.items(), 1):
        rows.append({
            "מספר": idx, "תיאור הארוע": ev['label'], "שעת התחלה": ev['time'],
            "משך השמעה בשניות": ev['duration_seconds'], "קובץ שמע": ev['audio'],
            "סוג ארוע": ev['type'], "עוצמת שמע": ev['volume'],
            1: ev['days'][1], 2: ev['days'][2], 3: ev['days'][3], 4: ev['days'][4],
            5: ev['days'][5], 6: ev['days'][6], 7: ev['days'][7],
            "פעיל": 1 if ev['enabled'] else 0
        })

    df_out = pd.DataFrame(rows)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df_out.to_excel(writer, index=False, sheet_name='גיליון1')
    output.seek(0)
    return output


# ============================================================
# Static
# ============================================================
@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


# ============================================================
# API: סטטוס תצוגה כללי
# ============================================================
@app.route("/api/schedule", methods=["GET"])
def api_get_schedule():
    return jsonify(load_data())


@app.route("/api/audio-files", methods=["GET"])
def api_audio_files():
    return jsonify(get_audio_files())


@app.route("/api/audio-devices", methods=["GET"])
def api_audio_devices():
    return jsonify(get_audio_output_devices())


# ============================================================
# API: הרשאות
# ============================================================
@app.route("/api/session", methods=["GET"])
def api_session():
    data = load_data()
    has_password = bool(data.get("permissions", {}).get("edit_password_hash"))
    return jsonify({"role": "editor" if is_editor() else "viewer", "password_set": has_password})


@app.route("/api/login", methods=["POST"])
def api_login():
    body = request.get_json(force=True)
    password = body.get("password", "")
    data = load_data()
    pw_hash = data.get("permissions", {}).get("edit_password_hash", "")
    if not pw_hash or hashlib.sha256(password.encode("utf-8")).hexdigest() == pw_hash:
        session["role"] = "editor"
        return jsonify({"ok": True, "role": "editor"})
    return jsonify({"ok": False, "error": "סיסמה שגויה"}), 401


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session["role"] = "viewer"
    return jsonify({"ok": True, "role": "viewer"})


# ============================================================
# API: אירועים (מוגן בהרשאת עריכה)
# ============================================================
@app.route("/api/event", methods=["POST"])
def api_add_event():
    guard = require_editor()
    if guard:
        return guard
    body = request.get_json(force=True)
    day, event = body.get("day"), body.get("event")
    if day not in DAY_ORDER or not event:
        return jsonify({"error": "בקשה לא תקינה"}), 400
    event["time"] = migrate_time(event.get("time", "00:00:00"))
    event.setdefault("uid", uuid.uuid4().hex[:12])

    def do(data):
        data["weekly_schedule"][day].append(event)
        data["weekly_schedule"][day].sort(key=lambda x: x["time"])

    data = mutate("add_event", f"{day}: {event.get('label')}", do)
    return jsonify(data)


@app.route("/api/event", methods=["PUT"])
def api_update_event():
    guard = require_editor()
    if guard:
        return guard
    body = request.get_json(force=True)
    day, index, event = body.get("day"), body.get("index"), body.get("event")
    if day not in DAY_ORDER or index is None or not event:
        return jsonify({"error": "בקשה לא תקינה"}), 400
    event["time"] = migrate_time(event.get("time", "00:00:00"))

    def do(data):
        events = data["weekly_schedule"].get(day, [])
        if index < 0 or index >= len(events):
            raise IndexError("אינדקס לא תקין")
        events[index] = event
        events.sort(key=lambda x: x["time"])
        data["weekly_schedule"][day] = events

    try:
        data = mutate("edit_event", f"{day}: {event.get('label')}", do)
    except IndexError:
        return jsonify({"error": "אינדקס לא תקין"}), 404
    return jsonify(data)


@app.route("/api/event", methods=["DELETE"])
def api_delete_event():
    guard = require_editor()
    if guard:
        return guard
    body = request.get_json(force=True)
    day, index = body.get("day"), body.get("index")
    if day not in DAY_ORDER or index is None:
        return jsonify({"error": "בקשה לא תקינה"}), 400

    label_holder = {}

    def do(data):
        events = data["weekly_schedule"].get(day, [])
        if index < 0 or index >= len(events):
            raise IndexError("אינדקס לא תקין")
        label_holder["label"] = events[index].get("label", "")
        del events[index]
        data["weekly_schedule"][day] = events

    try:
        data = mutate("delete_event", f"{day}", do)
    except IndexError:
        return jsonify({"error": "אינדקס לא תקין"}), 404
    return jsonify(data)


# ============================================================
# API: סטטוס מערכת
# ============================================================
@app.route("/api/system", methods=["POST"])
def api_set_system():
    guard = require_editor()
    if guard:
        return guard
    body = request.get_json(force=True)
    mode = body.get("mode")

    def do(data):
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
            raise ValueError("מצב לא מוכר")

    try:
        data = mutate("system_status", mode, do)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(data)


# ============================================================
# API: הגדרות (מוסד, שפה, מראה, התקן שמע, רישיון, הרשאות)
# ============================================================
@app.route("/api/settings", methods=["POST"])
def api_set_settings():
    guard = require_editor()
    if guard:
        return guard
    body = request.get_json(force=True)

    def do(data):
        if "institution_info" in body:
            data["institution_info"].update(body["institution_info"])
        if "audio_device" in body:
            data["institution_info"]["audio_device"] = body["audio_device"]
        if "appearance" in body:
            data["appearance"].update(body["appearance"])
        if "new_edit_password" in body and body["new_edit_password"] is not None:
            pw = body["new_edit_password"]
            data["permissions"]["edit_password_hash"] = (
                hashlib.sha256(pw.encode("utf-8")).hexdigest() if pw else ""
            )

    data = mutate("update_settings", "", do)
    safe = json.loads(json.dumps(data))
    safe.get("permissions", {}).pop("edit_password_hash", None)
    return jsonify(safe)


# ============================================================
# API: ייבוא / ייצוא
# ============================================================
@app.route("/api/import", methods=["POST"])
def api_import():
    guard = require_editor()
    if guard:
        return guard
    if "file" not in request.files:
        return jsonify({"error": "לא נשלח קובץ"}), 400
    file = request.files["file"]
    try:
        new_weekly = parse_import_excel(file)
    except Exception as e:
        logging.error(f"שגיאה בייבוא Excel: {e}")
        return jsonify({"error": f"שגיאה בקריאת הקובץ: {e}"}), 400

    def do(data):
        data["weekly_schedule"] = new_weekly

    data = mutate("import_excel", file.filename, do)
    return jsonify(data)


@app.route("/api/export", methods=["GET"])
def api_export():
    data = load_data()
    output = build_export_excel(data)
    return send_file(
        output, as_attachment=True,
        download_name=f"Schedule_Export_{datetime.now().strftime('%Y%m%d')}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


# ============================================================
# API: לוג פעולות + Undo
# ============================================================
@app.route("/api/log", methods=["GET"])
def api_log():
    return jsonify(read_action_log())


@app.route("/api/undo", methods=["POST"])
def api_undo():
    guard = require_editor()
    if guard:
        return guard
    snapshot = pop_undo_snapshot()
    if snapshot is None:
        return jsonify({"error": "אין פעולות לשחזור"}), 400
    save_data(snapshot, log_action=True, action_name="undo", action_details="שוחזר מצב קודם")
    return jsonify(snapshot)


if __name__ == "__main__":
    load_data()
    app.run(host="0.0.0.0", port=8500, debug=False)
