"""
web_server.py
שרת ניהול למערכת הצלצולים: API על schedule.json (כולל ייבוא/ייצוא, הגדרות, הרשאות,
לוג פעולות בלתי-ניתן-למחיקה, היסטוריית Undo עם קפיצה לאחור, שכפול קוביות/ימים)
+ הגשת ממשק הניהול הסטטי.
לא נוגע במנוע ההשמעה (main.py) - קורא/כותב לאותו schedule.json בלבד, בכתיבה אטומית.

הרצה:  python src/web_server.py       (ברירת מחדל: http://localhost:8500)
"""

import json
import os
import io
import sys
import uuid
import socket
import copy
import tempfile
import threading
import subprocess
import hashlib
import logging
import csv
import re
from datetime import date, datetime, timedelta, time as dtime

import openpyxl
from flask import Flask, jsonify, request, send_from_directory, send_file, session, has_request_context

# ============================================================
# נתיבים
# ============================================================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEDULE_FILE = os.path.join(BASE_DIR, "schedule.json")
AUDIO_DIR = os.path.join(BASE_DIR, "audio")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
LOG_FILE = os.path.join(LOGS_DIR, "app.log")
ACTIONS_LOG_FILE = os.path.join(LOGS_DIR, "actions_log.jsonl")   # לוג פעולות - קובץ מעקב, לא נמחק ע"י המערכת
UNDO_STACK_FILE = os.path.join(LOGS_DIR, "undo_stack.json")
# לוחות שמורים (רגיל / יום קצר / בחינות...) - בקובץ נפרד, כדי ש-schedule.json שהמנוע קורא כל שנייה יישאר קטן
SAVED_SCHEDULES_FILE = os.path.join(BASE_DIR, "saved_schedules.json")
# בקשת "בדיקת צליל" מהממשק: השרת כותב אותה, מנוע ההשמעה קורא, מנגן ברמקולים של מחשב הצלצולים ומוחק
TEST_REQUEST_FILE = os.path.join(BASE_DIR, "test_sound_request.json")
TEST_STATE_FILE = os.path.join(BASE_DIR, "test_sound_state.json")      # נכתב ע"י המנוע: האם צלצול ידני מתנגן
ENGINE_LOCK_PORT = 8599   # המנוע מחזיק את הפורט הזה כל זמן שהוא רץ (main.py)      # מצבים קודמים לצורך Undo עם קפיצה לאחור
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
MAX_UNDO_STEPS = 50

os.makedirs(LOGS_DIR, exist_ok=True)
# FileHandler עם קידוד מפורש (ולא basicConfig(encoding=...)) - כדי לעבוד גם על Python 3.8 / Windows 7
_log_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
_log_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logging.basicConfig(level=logging.INFO, handlers=[_log_handler])

DAY_ORDER = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
DAY_HEB = {"Sunday": "ראשון", "Monday": "שני", "Tuesday": "שלישי", "Wednesday": "רביעי",
           "Thursday": "חמישי", "Friday": "שישי", "Saturday": "שבת"}
AUDIO_DEVICES_FILE = os.path.join(BASE_DIR, "audio_devices.json")  # נכתב ע"י main.py - רשימת התקנים אמיתית מהמחשב שמחובר לרמקולים
VALID_LICENSE_VALUE = "פעיל בתוקף"  # עקבי עם main.py - זהו הערך היחיד שנחשב "רישיון תקף"

THEME_PRESETS = {
    "classic": {"accent_color": "#1e3c72", "accent_2": "#2a5298", "bg": "#eef1f6"},
    "midnight": {"accent_color": "#4c1d95", "accent_2": "#6d28d9", "bg": "#1e1b2e"},
    "forest": {"accent_color": "#14532d", "accent_2": "#16a34a", "bg": "#eef7ef"},
    "sunset": {"accent_color": "#9a3412", "accent_2": "#ea580c", "bg": "#fff4ec"},
}

# גרסת השרת - הממשק (index.html) משווה אליה ומציג אזהרה אם רץ שרת ישן יותר
SERVER_VERSION = "2026.10.13-4"
SERVER_PORT = 8500

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="")


@app.route("/api/version", methods=["GET"])
def api_version():
    return jsonify({"version": SERVER_VERSION})
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
    "appearance": {"theme": "classic", "accent_color": "#1e3c72", "font_size": "normal"},
    "permissions": {"edit_password_hash": ""},
    "show_disabled_events": True,
    "active_schedule_name": None,
    "weekly_schedule": {day: [] for day in DAY_ORDER}
}


DEFAULT_FADE_SECONDS = {"Music": 4.0, "Bell": 2.0, "Exercise": 2.0}  # עקבי עם main.py


def normalize_type(t):
    t = str(t or "Bell").strip().lower()
    return {"music": "Music", "exercise": "Exercise", "message": "Message", "הודעה": "Message",
            "מוסיקה": "Music", "תרגיל": "Exercise"}.get(t, "Bell")


# ---- אירוע מסוג "הודעה": בלי צליל, מוצג בלוח (קובייה) או בשורה הרצה מעל הלוח ----
MSG_SIZES = ("small", "normal", "large", "xlarge")
MSG_DISPLAYS = ("board", "ticker")
_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def sanitize_message_fields(ev):
    if ev.get("type") != "Message":
        for k in ("msg_body", "msg_size", "msg_color", "msg_bg", "msg_display"):
            ev.pop(k, None)
        return ev
    ev["msg_body"] = str(ev.get("msg_body") or "")[:2000]
    ev["msg_size"] = ev.get("msg_size") if ev.get("msg_size") in MSG_SIZES else "normal"
    ev["msg_display"] = ev.get("msg_display") if ev.get("msg_display") in MSG_DISPLAYS else "board"
    ev["msg_color"] = ev.get("msg_color") if _HEX_COLOR.match(str(ev.get("msg_color") or "")) else "#1e2a3a"
    ev["msg_bg"] = ev.get("msg_bg") if _HEX_COLOR.match(str(ev.get("msg_bg") or "")) else "#fff8d6"
    ev["audio"] = ""
    return ev


def migrate_fades(ev):
    """מעבר משדות fade במילישניות לשדות בשניות. ערך 0 ישן היה ברירת מחדל ולא בחירה של
    המשתמש, ולכן מוחלף בברירת המחדל החדשה לפי סוג (צלצול 2 שנ', מוזיקה 4 שנ')."""
    default = DEFAULT_FADE_SECONDS.get(ev.get("type"), 2.0)
    for key in ("fade_in", "fade_out"):
        if ev.get(f"{key}_s") is None:
            ms = ev.get(f"{key}_ms") or 0
            try:
                ms = float(ms)
            except Exception:
                ms = 0
            ev[f"{key}_s"] = round(ms / 1000.0, 1) if ms > 0 else default
        ev.pop(f"{key}_ms", None)


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
            ev["type"] = normalize_type(ev.get("type"))
            ev.setdefault("volume", 85)
            ev.setdefault("duration_seconds", 30)
            ev.setdefault("loop", False)
            migrate_fades(ev)
            sanitize_message_fields(ev)
            ev.setdefault("uid", uuid.uuid4().hex[:12])
        events.sort(key=lambda x: x.get("time", "00:00:00"))
        weekly[day] = events
    data["weekly_schedule"] = weekly

    # "השבת להיום בלבד" שהתאריך שלו כבר עבר: המנוע ממילא מנגן (הוא בודק את התאריך), אבל הקובץ
    # נשאר "מושבת" - ולכן הממשק (וכל מי שקורא את הקובץ) הציג השבתה בלי סוף. מחזירים לפעולה ושומרים.
    if (data.get("system_status") == "disabled" and data.get("disabled_scope") == "day"
            and data.get("disabled_date") != date.today().isoformat()):
        expired = data.get("disabled_date")
        data["system_status"] = "enabled"
        data["disabled_scope"] = None
        data["disabled_date"] = None
        try:
            save_data(data, log_action=True, action_name="system_status_auto",
                      action_details=f"ההשבתה להיום בלבד ({expired}) הסתיימה - המערכת חזרה לפעולה אוטומטית")
        except Exception as e:
            logging.error(f"לא ניתן היה לשמור חזרה אוטומטית לפעולה: {e}")
    return data


def save_data(data, log_action=True, action_name="update", action_details=""):
    """כתיבה אטומית + (אופציונלי) רישום ללוג פעולות."""
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
# לוג פעולות (audit trail - לא ניתן למחיקה דרך ה-API) + היסטוריית Undo עם קפיצה לאחור
# ============================================================
def append_action_log(action_name, details=""):
    entry = {
        "id": uuid.uuid4().hex[:10],
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "action": action_name,
        "details": details,
        "user_role": session.get("role", "viewer") if has_request_context() else "system"
    }
    try:
        with open(ACTIONS_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logging.error(f"שגיאה בכתיבה ללוג הפעולות: {e}")
    return entry


def read_action_log(limit=300):
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


def load_undo_stack():
    if not os.path.exists(UNDO_STACK_FILE):
        return []
    try:
        with open(UNDO_STACK_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_undo_stack(stack):
    with open(UNDO_STACK_FILE, "w", encoding="utf-8") as f:
        json.dump(stack[-MAX_UNDO_STEPS:], f, ensure_ascii=False)


def push_undo_snapshot(pre_change_data, action_name, log_entry_id, details=""):
    """שומר עותק של המצב *לפני* שינוי, לצורך שחזור עתידי (כולל קפיצה לאחור מספר צעדים)."""
    stack = load_undo_stack()
    stack.append({
        "snapshot": pre_change_data,
        "action_name": action_name,
        "details": details,
        "log_entry_id": log_entry_id,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    })
    save_undo_stack(stack)


def list_undo_history():
    """רשימת נקודות שחזור זמינות, מהאחרונה לישנה ביותר - עבור תצוגת 'היסטוריה כמו בכרום'."""
    stack = load_undo_stack()
    out = []
    for i, entry in enumerate(reversed(stack)):
        name = entry.get("action_name", "")
        out.append({
            "steps_back": i + 1,
            "action_name": name,
            "title": ACTION_TITLES.get(name, name),
            "details": entry.get("details", ""),
            "timestamp": entry.get("timestamp", ""),
        })
    return out


def jump_to_undo_step(steps_back):
    """קופץ אחורה N צעדים בבת אחת - משחזר את המצב מנקודת ההיסטוריה הרלוונטית
    ומסיר אותה ואת כל מה שאחריה מהמחסנית (כמו history back בדפדפן)."""
    stack = load_undo_stack()
    if steps_back < 1 or steps_back > len(stack):
        return None
    target_index = len(stack) - steps_back
    snapshot_entry = stack[target_index]
    stack = stack[:target_index]
    save_undo_stack(stack)
    return snapshot_entry["snapshot"]


# ---- תיאור פעולות בעברית להיסטוריה וללוג: "הנמכת עוצמה (85%→60%), שלישי, 09:40 · שיעור 2" ----
ACTION_TITLES = {
    "add_event": "הוספת אירוע", "edit_event": "עריכת אירוע", "quick_edit_event": "עדכון אירוע",
    "quick_edit_events": "עדכון אירוע", "delete_event": "מחיקת אירוע", "duplicate_event": "שכפול אירוע",
    "duplicate_day": "שכפול יום", "replace_audio_all": "החלפת קובץ שמע", "system_status": "מצב מערכת",
    "update_settings": "עדכון הגדרות", "import_excel": "ייבוא Excel", "schedule_save": "שמירת לוח",
    "schedule_load": "טעינת לוח", "undo": "ביטול פעולה",
}
FIELD_NAMES = {"label": "תיאור", "type": "סוג", "duration_seconds": "משך", "loop": "השמעה חוזרת",
               "fade_in_s": "Fade In", "fade_out_s": "Fade Out", "shuffle": "השמעה אקראית",
               "msg_body": "תוכן ההודעה", "msg_display": "אופן הצגה", "msg_size": "גודל טקסט",
               "msg_color": "צבע טקסט", "msg_bg": "צבע רקע"}


def short_time(t):
    t = str(t or "")
    return t[:5] if t.endswith(":00") else t


def ddmm(iso):
    try:
        y, m, d = str(iso).split("-")
        return f"{d}/{m}"
    except ValueError:
        return str(iso)


def describe_changes(old, new):
    """רשימת שינויים קריאה בין שתי גרסאות של אירוע."""
    out = []
    if old.get("enabled", True) is not False and new.get("enabled", True) is False:
        out.append("השבתת אירוע")
    elif old.get("enabled", True) is False and new.get("enabled", True) is not False:
        out.append("הפעלת אירוע")
    if old.get("skip_date") != new.get("skip_date"):
        out.append(f"השבתה חד-פעמית ({ddmm(new['skip_date'])})" if new.get("skip_date") else "ביטול השבתה חד-פעמית")
    ov, nv = old.get("volume", 85), new.get("volume", 85)
    if ov != nv:
        out.append(f"{'הנמכת' if nv < ov else 'הגברת'} עוצמה ({ov}%→{nv}%)")
    if old.get("time") != new.get("time"):
        out.append(f"שינוי שעה ({short_time(old.get('time'))}→{short_time(new.get('time'))})")
    if str(old.get("audio") or "") != str(new.get("audio") or ""):
        name = os.path.basename(str(new.get("audio") or "").replace("\\", "/").rstrip("/")) or "—"
        out.append(f"החלפת קובץ שמע ({name})")
    other = [FIELD_NAMES[k] for k in FIELD_NAMES if old.get(k) != new.get(k) and not (k == "shuffle" and old.get(k) is None and new.get(k) is True)]
    if other:
        out.append("עדכון " + ", ".join(other))
    return out


def event_ref(days, ev):
    """", שלישי, 09:40 · שיעור 2" """
    names = ", ".join(DAY_HEB[d] for d in DAY_ORDER if d in days)
    return f"{names}, {short_time(ev.get('time'))} · {ev.get('label', '')}"


def mutate(action_name, details, mutator_fn):
    """עוטף כל שינוי: שומר snapshot לפני, מפעיל את השינוי, שומר + רושם ללוג + דוחף להיסטוריית Undo."""
    data = load_data()
    pre_snapshot = json.loads(json.dumps(data))
    result = mutator_fn(data)
    if callable(details):          # תיאור שמחושב אחרי השינוי (למשל "הנמכת עוצמה 85%→60%")
        details = details()
    save_data(data, log_action=True, action_name=action_name, action_details=details)
    log_entries = read_action_log(limit=1)
    log_id = log_entries[0]["id"] if log_entries else None
    push_undo_snapshot(pre_snapshot, action_name, log_id, details)
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
    main.py. אם main.py לא רץ עדיין, ננסה sounddevice אם מותקן, ואם לא - רשימת ברירת מחדל."""
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
# סדר העמודות בקובץ הייצוא. בייבוא העמודות נקראות לפי השם, כך שהסדר בקובץ המיובא לא משנה
LOOP_COLUMN = "השמעה חוזרת"
# לפי המבנה של Ex_Import\Schedule_Import.xlsx (04/10/2026)
IMPORT_COLUMNS = ["מספר", "תיאור הארוע", "שעת התחלה", "סוג ארוע", "קובץ שמע", "משך השמעה בשניות",
                  LOOP_COLUMN, "עוצמת שמע", 1, 2, 3, 4, 5, 6, 7, "פעיל",
                  "תוכן הודעה", "תצוגת הודעה", "גודל טקסט", "צבע טקסט", "צבע רקע", "השמעה אקראית"]
# עמודות ההודעה (סוג ארוע = Message) - בסוף, כך שקובץ ייבוא ישן בלי העמודות האלה עדיין עובד
# שורת תיאור באנגלית מתחת לכותרות (כמו בקובץ הייבוא). בייבוא היא מזוהה ומדולגת
EXPORT_SUBHEADERS = ["ID", "Text", "(HH:MM:SS)", "Event_type", "file_audio", "duration_seconds",
                     "Loop", "volume", "Sun", "Mon", "Tur", "Wed", "Thur", "Fri", "Sat", "Active",
                     "Message", "board / ticker", "small/normal/large/xlarge", "#RRGGBB", "#RRGGBB", "Shuffle"]
MSG_DISPLAY_IMPORT = {"board": "board", "לוח": "board", "קובייה": "board",
                      "ticker": "ticker", "שורה רצה": "ticker", "רצה": "ticker"}


def _cell_text(v):
    """ערך תא כטקסט. שעה ב-Excel יכולה להגיע כ-time או datetime - ממירים ל-HH:MM:SS."""
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%H:%M:%S")
    if isinstance(v, dtime):
        return v.strftime("%H:%M:%S")
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _cell_bool(v, default=False):
    t = _cell_text(v).lower()
    if t in ("1", "true", "כן", "v", "✓", "yes"):
        return True
    if t in ("0", "false", "לא", "no", ""):
        return default if t == "" else False
    return default


def _cell_int(v, default):
    try:
        return int(float(v)) if v not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _read_rows(file_stream, filename=""):
    """מחזיר רשימת שורות כמילונים לפי שורת הכותרת. xlsx דרך openpyxl (בלי pandas/numpy - חבילה קטנה
    שעובדת גם ב-Windows 7), ו-csv דרך המודול המובנה."""
    if str(filename).lower().endswith(".csv"):
        text = file_stream.read()
        if isinstance(text, bytes):
            text = text.decode("utf-8-sig", errors="replace")
        grid = list(csv.reader(io.StringIO(text)))
    else:
        wb = openpyxl.load_workbook(file_stream, read_only=True, data_only=True)
        grid = [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]
    if not grid:
        return []
    # כותרות: עמודות הימים הן 1..7 (מספר או טקסט "1"/"1.0")
    headers = [_cell_text(h) for h in grid[0]]
    rows = []
    for raw in grid[1:]:
        if raw is None or all(c in (None, "") for c in raw):
            continue
        rows.append({headers[i]: raw[i] for i in range(min(len(headers), len(raw))) if headers[i]})
    return rows


def parse_import_excel(file_stream, filename=""):
    rows = _read_rows(file_stream, filename)
    # שורה ראשונה שמתארת סוגי נתונים (Text / HH:MM:SS) - מדלגים
    if rows and (_cell_text(rows[0].get('תיאור הארוע')) == 'Text' or
                 'HH:MM:SS' in _cell_text(rows[0].get('שעת התחלה'))):
        rows = rows[1:]

    weekly_schedule = {day: [] for day in DAY_ORDER}
    for row in rows:
        label, start = _cell_text(row.get('תיאור הארוע')), _cell_text(row.get('שעת התחלה'))
        if not label or not start:
            continue
        event = {
            "label": label,
            "time": migrate_time(start),
            "duration_seconds": _cell_int(row.get('משך השמעה בשניות'), 30),
            "audio": _cell_text(row.get('קובץ שמע')) or 'Tune 4.mp3',
            "type": _cell_text(row.get('סוג ארוע')) or 'Bell',
            "volume": _cell_int(row.get('עוצמת שמע'), 85),
            "enabled": bool(_cell_int(row.get('פעיל'), 1)),
            "loop": _cell_bool(row.get(LOOP_COLUMN), False),
            "shuffle": _cell_bool(row.get("השמעה אקראית"), True),
            "uid": uuid.uuid4().hex[:12]
        }
        event["type"] = normalize_type(event["type"])
        if event["type"] == "Message":
            event.update({
                "audio": "",
                "msg_body": _cell_text(row.get("תוכן הודעה")),
                "msg_display": MSG_DISPLAY_IMPORT.get(_cell_text(row.get("תצוגת הודעה")).lower(), "board"),
                "msg_size": _cell_text(row.get("גודל טקסט")).lower(),
                "msg_color": _cell_text(row.get("צבע טקסט")),
                "msg_bg": _cell_text(row.get("צבע רקע")),
            })
            sanitize_message_fields(event)
        for d_idx in range(7):
            if _cell_text(row.get(str(d_idx + 1))) in ['1', '1.0', 'True', 'true']:
                weekly_schedule[DAY_ORDER[d_idx]].append(event.copy())

    for day in DAY_ORDER:
        weekly_schedule[day].sort(key=lambda x: x["time"])
    return weekly_schedule


def build_export_excel(data):
    weekly = data.get("weekly_schedule", {})
    # שורה אחת לכל צירוף זהה של כל השדות, עם סימון הימים שבהם הוא מופיע. אירועים שנבדלים
    # בשדה כלשהו (עוצמה, משך, פעיל וכו') ביום מסוים - מקבלים שורה נפרדת, כדי שלא יאבדו בייבוא חוזר
    all_events = {}
    for day in DAY_ORDER:
        for ev in weekly.get(day, []):
            row = {
                "label": ev.get('label'), "time": ev.get('time'),
                "duration_seconds": ev.get('duration_seconds', 30),
                "audio": ev.get('audio', 'Tune 4.mp3'), "type": ev.get('type', 'Bell'),
                "volume": ev.get('volume', 85), "enabled": ev.get('enabled', True) is not False,
                "loop": bool(ev.get('loop', False)),
                "shuffle": ev.get('shuffle', True) is not False,
            }
            if ev.get("type") == "Message":
                row.update({"audio": "", "msg_body": ev.get("msg_body", ""), "msg_display": ev.get("msg_display", "board"),
                            "msg_size": ev.get("msg_size", "normal"), "msg_color": ev.get("msg_color", ""),
                            "msg_bg": ev.get("msg_bg", "")})
            key = tuple(sorted((k, str(v)) for k, v in row.items()))
            if key not in all_events:
                all_events[key] = dict(row, days={d: 0 for d in range(1, 8)})
            all_events[key]["days"][DAY_ORDER.index(day) + 1] = 1
    all_events = dict(sorted(all_events.items(), key=lambda kv: (kv[1]["time"] or "", str(kv[1]["label"]))))

    wb = openpyxl.Workbook()
    sheet = wb.active
    sheet.title = 'גיליון1'
    sheet.sheet_view.rightToLeft = True
    sheet.append(IMPORT_COLUMNS)
    sheet.append(EXPORT_SUBHEADERS)
    for cell in sheet[1]:
        cell.font = openpyxl.styles.Font(bold=True)
    for cell in sheet[2]:
        cell.font = openpyxl.styles.Font(italic=True, color="666666")
    for idx, ev in enumerate(all_events.values(), 1):
        values = {"מספר": idx, "תיאור הארוע": ev['label'], "שעת התחלה": ev['time'],
                  "משך השמעה בשניות": ev['duration_seconds'], "קובץ שמע": ev['audio'], "סוג ארוע": ev['type'],
                  "עוצמת שמע": ev['volume'], "פעיל": 1 if ev['enabled'] else 0, LOOP_COLUMN: 1 if ev['loop'] else 0,
                  "תוכן הודעה": ev.get("msg_body", ""), "תצוגת הודעה": ev.get("msg_display", ""),
                  "גודל טקסט": ev.get("msg_size", ""), "צבע טקסט": ev.get("msg_color", ""), "צבע רקע": ev.get("msg_bg", ""),
                  "השמעה אקראית": 1 if ev.get("shuffle", True) else 0}
        values.update({d: ev['days'][d] for d in range(1, 8)})
        sheet.append([values[c] for c in IMPORT_COLUMNS])
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output


# ============================================================
# Static
# ============================================================
@app.route("/")
def index():
    resp = send_from_directory(STATIC_DIR, "index.html")
    resp.headers["Cache-Control"] = "no-store"   # שהחלון תמיד יטען את הגרסה העדכנית של הממשק
    return resp


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


@app.route("/api/themes", methods=["GET"])
def api_themes():
    return jsonify(THEME_PRESETS)


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
    # days: אפשר להוסיף את אותו אירוע לכמה ימים בבת אחת (פעולה אחת ב-Undo)
    days = [d for d in DAY_ORDER if d in (body.get("days") or [day])]
    if day not in days:
        days.insert(0, day)
    event["time"] = migrate_time(event.get("time", "00:00:00"))
    event["type"] = normalize_type(event.get("type"))
    migrate_fades(event)
    sanitize_message_fields(event)
    event.pop("uid", None)

    def do(data):
        for d in days:
            ev = copy.deepcopy(event)
            ev["uid"] = uuid.uuid4().hex[:12]
            data["weekly_schedule"][d].append(ev)
            data["weekly_schedule"][d].sort(key=lambda x: x["time"])

    kind = "הוספת הודעה" if event.get("type") == "Message" else "הוספת אירוע"
    data = mutate("add_event", f"{kind}, {event_ref(days, event)}", do)
    return jsonify(data)


@app.route("/api/event", methods=["PUT"])
def api_update_event():
    """עריכת אירוע. אפשר גם להחיל את העריכה על אותו אירוע בימים אחרים, בפעולה אחת (וב-Undo אחד):
    also:   [{day, index}] - אותו אירוע בימים אחרים, שיקבל את אותם ערכים (שומר על ה-uid שלו)
    add_days: [day]        - ימים שבהם האירוע עוד לא קיים - יתווסף אליהם עותק
    remove: [{day, index}] - ימים שהוסרו מהסימון - האירוע יימחק מהם
    האינדקסים לפי הלוח לפני השינוי."""
    guard = require_editor()
    if guard:
        return guard
    body = request.get_json(force=True)
    day, index, event = body.get("day"), body.get("index"), body.get("event")
    if day not in DAY_ORDER or index is None or not event:
        return jsonify({"error": "בקשה לא תקינה"}), 400
    event["time"] = migrate_time(event.get("time", "00:00:00"))
    event["type"] = normalize_type(event.get("type"))
    migrate_fades(event)
    sanitize_message_fields(event)
    also = [it for it in (body.get("also") or []) if it.get("day") in DAY_ORDER and it.get("day") != day]
    remove = [it for it in (body.get("remove") or []) if it.get("day") in DAY_ORDER and it.get("day") != day]
    add_days = [d for d in DAY_ORDER if d in (body.get("add_days") or []) and d != day]

    before = {}

    def do(data):
        weekly = data["weekly_schedule"]
        for it in [{"day": day, "index": index}] + also + remove:
            if not isinstance(it.get("index"), int) or it["index"] < 0 or it["index"] >= len(weekly.get(it["day"], [])):
                raise IndexError("אינדקס לא תקין")
        before["ev"] = copy.deepcopy(weekly[day][index])
        weekly[day][index] = event
        for it in also:
            twin = weekly[it["day"]][it["index"]]
            ev = copy.deepcopy(event)
            ev["uid"] = twin.get("uid") or uuid.uuid4().hex[:12]
            ev.pop("skip_date", None)          # השבתה חד-פעמית שייכת לתאריך של כל יום בנפרד
            if twin.get("skip_date") and ev.get("enabled", True):
                ev["skip_date"] = twin["skip_date"]
            weekly[it["day"]][it["index"]] = ev
        for d in add_days:
            ev = copy.deepcopy(event)
            ev["uid"] = uuid.uuid4().hex[:12]
            ev.pop("skip_date", None)
            weekly[d].append(ev)
        for it in sorted(remove, key=lambda x: -x["index"]):
            del weekly[it["day"]][it["index"]]
        for d in DAY_ORDER:
            weekly[d].sort(key=lambda x: x["time"])

    touched = [day] + [it["day"] for it in also]

    def details():
        changes = describe_changes(before.get("ev", {}), event) or ["עריכת אירוע"]
        text = f"{', '.join(changes)}, {event_ref(touched, event)}"
        if add_days:
            text += f" · נוסף ל: {', '.join(DAY_HEB[d] for d in DAY_ORDER if d in add_days)}"
        if remove:
            text += f" · נמחק מ: {', '.join(DAY_HEB[it['day']] for it in remove)}"
        return text
    try:
        data = mutate("edit_event", details, do)
    except IndexError:
        return jsonify({"error": "אינדקס לא תקין"}), 404
    return jsonify(data)


@app.route("/api/event/quick", methods=["PATCH"])
def api_quick_update_event():
    """עדכון מהיר בשדה בודד (פעיל/עוצמה/שעה) ישירות מהקובייה - שמירה אוטומטית, ללא מודל."""
    guard = require_editor()
    if guard:
        return guard
    body = request.get_json(force=True)
    day, index, fields = body.get("day"), body.get("index"), body.get("fields", {})
    if day not in DAY_ORDER or index is None or not fields:
        return jsonify({"error": "בקשה לא תקינה"}), 400

    desc = {}

    def do(data):
        events = data["weekly_schedule"].get(day, [])
        if index < 0 or index >= len(events):
            raise IndexError("אינדקס לא תקין")
        old = copy.deepcopy(events[index])
        apply_quick_fields(events[index], fields)
        desc["text"] = f"{', '.join(describe_changes(old, events[index]) or ['עדכון'])}, {event_ref([day], old)}"
        events.sort(key=lambda x: x["time"])
        data["weekly_schedule"][day] = events

    try:
        data = mutate("quick_edit_event", lambda: desc.get("text", ""), do)
    except IndexError:
        return jsonify({"error": "אינדקס לא תקין"}), 404
    return jsonify(data)


def apply_quick_fields(ev, fields):
    if "enabled" in fields:
        ev["enabled"] = bool(fields["enabled"])
    if "volume" in fields:
        ev["volume"] = max(0, min(100, int(fields["volume"])))
    if "time" in fields:
        ev["time"] = migrate_time(fields["time"])
    if "skip_date" in fields:
        # השבתה חד-פעמית (להיום / להופעה הקרובה בלבד). None = ביטול ההשבתה החד-פעמית.
        if fields["skip_date"]:
            ev["skip_date"] = str(fields["skip_date"])
        else:
            ev.pop("skip_date", None)
    if "audio" in fields:
        ev["audio"] = str(fields["audio"])


@app.route("/api/events/quick", methods=["PATCH"])
def api_quick_update_many():
    """עדכון מהיר של כמה אירועים בבת אחת (למשל השבתת אותו אירוע בכל ימי השבוע) - פעולה אחת ב-Undo.
    items: [{day, index, fields}] - האינדקסים לפי הלוח לפני השינוי."""
    guard = require_editor()
    if guard:
        return guard
    body = request.get_json(force=True)
    items = body.get("items") or []
    if not items or any(it.get("day") not in DAY_ORDER or it.get("index") is None or not it.get("fields") for it in items):
        return jsonify({"error": "בקשה לא תקינה"}), 400
    current = load_data()["weekly_schedule"]
    try:
        first = current[items[0]["day"]][items[0]["index"]]
    except (IndexError, KeyError, TypeError):
        return jsonify({"error": "אינדקס לא תקין"}), 404
    desc = {}

    def do(data):
        for it in items:
            events = data["weekly_schedule"].get(it["day"], [])
            if it["index"] < 0 or it["index"] >= len(events):
                raise IndexError("אינדקס לא תקין")
        groups = {}      # אותו שינוי בכמה ימים -> שורה אחת
        for it in items:
            ev = data["weekly_schedule"][it["day"]][it["index"]]
            old = copy.deepcopy(ev)
            apply_quick_fields(ev, it["fields"])
            key = ", ".join(describe_changes(old, ev) or ["עדכון"])
            groups.setdefault(key, []).append(it["day"])
        desc["text"] = " | ".join(f"{k}, {event_ref(ds, first)}" for k, ds in groups.items())
        for d in {it["day"] for it in items}:
            data["weekly_schedule"][d].sort(key=lambda x: x["time"])

    try:
        data = mutate("quick_edit_events", lambda: desc.get("text", ""), do)
    except IndexError:
        return jsonify({"error": "אינדקס לא תקין"}), 404
    return jsonify(data)


@app.route("/api/event", methods=["DELETE"])
def api_delete_event():
    """מחיקת אירוע. items: [{day, index}] מוחק את אותו אירוע בכמה ימים בפעולה אחת (וב-Undo אחד)."""
    guard = require_editor()
    if guard:
        return guard
    body = request.get_json(force=True)
    items = body.get("items") or [{"day": body.get("day"), "index": body.get("index")}]
    if any(it.get("day") not in DAY_ORDER or not isinstance(it.get("index"), int) for it in items):
        return jsonify({"error": "בקשה לא תקינה"}), 400
    desc = {}

    def do(data):
        weekly = data["weekly_schedule"]
        for it in items:
            if it["index"] < 0 or it["index"] >= len(weekly.get(it["day"], [])):
                raise IndexError("אינדקס לא תקין")
        first = copy.deepcopy(weekly[items[0]["day"]][items[0]["index"]])
        for it in sorted(items, key=lambda x: -x["index"]):
            del weekly[it["day"]][it["index"]]
        kind = "מחיקת הודעה" if first.get("type") == "Message" else "מחיקת אירוע"
        desc["text"] = f"{kind}, {event_ref([it['day'] for it in items], first)}"

    try:
        data = mutate("delete_event", lambda: desc.get("text", ""), do)
    except IndexError:
        return jsonify({"error": "אינדקס לא תקין"}), 404
    return jsonify(data)


@app.route("/api/event/duplicate", methods=["POST"])
def api_duplicate_event():
    """שכפול קוביה בודדת - לאותו יום (ברירת מחדל) או ליום אחר שנבחר."""
    guard = require_editor()
    if guard:
        return guard
    body = request.get_json(force=True)
    day, index = body.get("day"), body.get("index")
    target_day = body.get("target_day", day)
    if day not in DAY_ORDER or target_day not in DAY_ORDER or index is None:
        return jsonify({"error": "בקשה לא תקינה"}), 400

    def do(data):
        events = data["weekly_schedule"].get(day, [])
        if index < 0 or index >= len(events):
            raise IndexError("אינדקס לא תקין")
        clone = copy.deepcopy(events[index])
        clone["uid"] = uuid.uuid4().hex[:12]
        clone["label"] = clone.get("label", "") + " (עותק)"
        data["weekly_schedule"][target_day].append(clone)
        data["weekly_schedule"][target_day].sort(key=lambda x: x["time"])
        desc["text"] = f"שכפול אירוע, {event_ref([day], events[index])} → {DAY_HEB[target_day]}"

    desc = {}
    try:
        data = mutate("duplicate_event", lambda: desc.get("text", ""), do)
    except IndexError:
        return jsonify({"error": "אינדקס לא תקין"}), 404
    return jsonify(data)


@app.route("/api/day/duplicate", methods=["POST"])
def api_duplicate_day():
    """שכפול יום שלם אל יום יעד (מחליף את כל האירועים של יום היעד)."""
    guard = require_editor()
    if guard:
        return guard
    body = request.get_json(force=True)
    source_day, target_day = body.get("source_day"), body.get("target_day")
    if source_day not in DAY_ORDER or target_day not in DAY_ORDER:
        return jsonify({"error": "בקשה לא תקינה"}), 400

    def do(data):
        source_events = data["weekly_schedule"].get(source_day, [])
        cloned = []
        for ev in source_events:
            c = copy.deepcopy(ev)
            c["uid"] = uuid.uuid4().hex[:12]
            cloned.append(c)
        cloned.sort(key=lambda x: x["time"])
        data["weekly_schedule"][target_day] = cloned

    data = mutate("duplicate_day", f"שכפול יום, {DAY_HEB[source_day]} → {DAY_HEB[target_day]} (כל האירועים ביום היעד הוחלפו)", do)
    return jsonify(data)


# ============================================================
# API: החלפת קובץ שמע בכל האירועים שמשתמשים בו (כל השבוע)
# ============================================================
@app.route("/api/audio/usage", methods=["GET"])
def api_audio_usage():
    audio = request.args.get("audio", "")
    data = load_data()
    count = sum(1 for day in DAY_ORDER for ev in data["weekly_schedule"].get(day, []) if ev.get("audio") == audio)
    return jsonify({"audio": audio, "count": count})


def audio_value_exists(value):
    """אותה בדיקה שהמנוע (main.py: resolve_audio_target) עושה: שם קובץ -> תיקיית audio;
    נתיב מלא -> הנתיב עצמו, ואם הוא לא קיים - קובץ/תיקייה באותו שם בתיקיית audio."""
    value = str(value or "").strip()
    if not value:
        return False
    if os.path.isabs(value) or (len(value) > 1 and value[1] == ":"):
        if os.path.exists(value):
            return True
        return os.path.exists(os.path.join(AUDIO_DIR, os.path.basename(value.replace("\\", "/").rstrip("/"))))
    return os.path.exists(os.path.join(AUDIO_DIR, value))


@app.route("/api/audio/missing", methods=["GET"])
def api_audio_missing():
    """ערכי audio בלוח שהמנוע לא ימצא - הממשק מסמן את הקוביות האלה באדום."""
    data = load_data()
    values = {str(ev.get("audio") or "") for day in DAY_ORDER for ev in data["weekly_schedule"].get(day, [])
              if ev.get("type") != "Message"}
    return jsonify({"missing": sorted(v for v in values if not audio_value_exists(v))})


def standard_bell_audio():
    """"הצלצול הסטנדרטי": הקובץ שהכי הרבה אירועי צלצול בלוח משתמשים בו (ושקיים במחשב)."""
    data = load_data()
    counts = {}
    for day in DAY_ORDER:
        for ev in data["weekly_schedule"].get(day, []):
            a = str(ev.get("audio") or "")
            if ev.get("type") == "Bell" and a and audio_value_exists(a):
                counts[a] = counts.get(a, 0) + 1
    if counts:
        return max(counts, key=counts.get)
    files = [f for f in os.listdir(AUDIO_DIR) if f.lower().endswith((".mp3", ".wav", ".ogg"))] if os.path.isdir(AUDIO_DIR) else []
    return "bell_standard.mp3" if "bell_standard.mp3" in files else (files[0] if files else "")


def engine_running():
    try:
        socket.create_connection(("127.0.0.1", ENGINE_LOCK_PORT), timeout=0.5).close()
        return True
    except OSError:
        return False


def test_sound_playing():
    try:
        with open(TEST_STATE_FILE, "r", encoding="utf-8") as f:
            return bool(json.load(f).get("playing"))
    except Exception:
        return False


def write_test_request(req):
    fd, tmp_path = tempfile.mkstemp(dir=BASE_DIR, prefix=".test_", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(req, f, ensure_ascii=False)
    os.replace(tmp_path, TEST_REQUEST_FILE)


@app.route("/api/test-sound", methods=["GET"])
def api_test_sound_info():
    running = engine_running()
    return jsonify({"standard": standard_bell_audio(), "engine_running": running,
                    "playing": running and test_sound_playing()})


@app.route("/api/test-sound", methods=["POST"])
def api_test_sound():
    """בדיקת צליל: מושמע ע"י מנוע ההשמעה ברמקולים של מחשב הצלצולים (גם כשלוחצים מטלפון)."""
    guard = require_editor()
    if guard:
        return guard
    body = request.get_json(silent=True) or {}
    if body.get("action") == "stop":
        # עצירת הצלצול הידני / הבדיקה שמתנגנים כרגע
        write_test_request({"action": "stop", "ts": datetime.now().timestamp()})
        logging.info("בקשת עצירה לצלצול ידני / בדיקה")
        return jsonify({"ok": True, "stopped": True})
    audio = str(body.get("audio") or "").strip() or standard_bell_audio()
    if not audio:
        return jsonify({"error": "אין קובץ צלצול לבדיקה"}), 404
    if not audio_value_exists(audio):
        return jsonify({"error": f"הקובץ לא נמצא במחשב: {audio}"}), 404
    if not engine_running():
        return jsonify({"error": "מנוע ההשמעה לא פועל - אין מי שינגן. הפעילו את המערכת (start_all.bat)."}), 409
    data = load_data()
    if data.get("institution_info", {}).get("license_status") != VALID_LICENSE_VALUE:
        return jsonify({"error": "הרישיון לא בתוקף - המערכת לא משמיעה צלילים."}), 409
    try:
        volume = max(0, min(100, int(body.get("volume", 85))))
    except (TypeError, ValueError):
        volume = 85
    # גם כשהמערכת מושבתת: ההשבתה עוצרת את הלוח, וצלצול ידני הוא פעולה יזומה
    write_test_request({"action": "play", "audio": audio, "volume": volume, "ts": datetime.now().timestamp()})
    logging.info(f"בקשת בדיקת צליל: {audio} (עוצמה {volume}%)")
    return jsonify({"ok": True, "audio": audio})


@app.route("/api/audio/replace", methods=["POST"])
def api_audio_replace():
    guard = require_editor()
    if guard:
        return guard
    body = request.get_json(force=True)
    old, new = body.get("old", ""), body.get("new", "")
    if not new:
        return jsonify({"error": "לא נבחר קובץ חדש"}), 400
    counter = {"n": 0}

    def do(data):
        for day in DAY_ORDER:
            for ev in data["weekly_schedule"].get(day, []):
                if ev.get("audio") == old:
                    ev["audio"] = new
                    counter["n"] += 1

    data = mutate("replace_audio_all", f"'{old}' הוחלף ב-'{new}' בכל האירועים", do)
    return jsonify({"replaced": counter["n"], "data": data})


# ============================================================
# API: עיון בקבצים ותיקיות במחשב שמריץ את השרת (כפתור Browse)
# דפדפן לא יכול להחזיר נתיב אמיתי, ולכן השרת מציג את מבנה התיקיות בעצמו.
# ============================================================
AUDIO_EXTENSIONS = (".mp3", ".wav", ".ogg")


def list_roots():
    if os.name == "nt":
        # GetLogicalDrives מחזיר את הכוננים בלי לגשת אליהם (בלי השהיות של כונן ריק / כונן רשת מנותק)
        try:
            import ctypes
            import string
            mask = ctypes.windll.kernel32.GetLogicalDrives()
            return [f"{letter}:\\" for i, letter in enumerate(string.ascii_uppercase) if mask & (1 << i)]
        except Exception:
            return ["C:\\", "D:\\"]
    return ["/"]


def to_stored_audio_value(path):
    """קובץ שנמצא ישירות בתיקיית audio נשמר בשמו בלבד (כמו עד היום); כל נתיב אחר נשמר מלא."""
    abs_path = os.path.abspath(path)
    same_dir = os.path.normcase(os.path.dirname(abs_path)) == os.path.normcase(os.path.abspath(AUDIO_DIR))
    if os.path.isfile(abs_path) and same_dir:
        return os.path.basename(abs_path)
    return abs_path


@app.route("/api/browse", methods=["GET"])
def api_browse():
    guard = require_editor()
    if guard:
        return guard
    path = request.args.get("path") or os.path.abspath(AUDIO_DIR)
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        return jsonify({"error": f"התיקייה לא נמצאה: {path}"}), 404
    dirs, files = [], []
    try:
        names = sorted(os.listdir(path), key=str.lower)
    except PermissionError:
        return jsonify({"error": "אין הרשאת גישה לתיקייה זו"}), 403
    except OSError as e:
        return jsonify({"error": f"לא ניתן לקרוא את התיקייה: {e}"}), 400
    for name in names:
        if name.startswith(".") or name.startswith("$") or name == "System Volume Information":
            continue
        full = os.path.join(path, name)
        try:
            if os.path.isdir(full):
                dirs.append(name)
            elif name.lower().endswith(AUDIO_EXTENSIONS):
                files.append(name)
        except OSError:
            continue
    parent = os.path.dirname(path)
    return jsonify({
        "path": path,
        "parent": parent if parent != path else None,
        "dirs": dirs,
        "files": files,
        "roots": list_roots(),
        "audio_dir": os.path.abspath(AUDIO_DIR),
    })


@app.route("/api/browse/resolve", methods=["POST"])
def api_browse_resolve():
    """ממיר נתיב שנבחר לערך שנשמר בשדה audio."""
    body = request.get_json(force=True)
    return jsonify({"value": to_stored_audio_value(body.get("path", ""))})


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
        data = mutate("system_status", {"enabled": "הפעלת המערכת (ירוק)", "today": f"השבתה להיום בלבד (צהוב), חוזר ב-{ddmm((date.today() + timedelta(days=1)).isoformat())}",
                                        "indefinite": "השבתה עד הפעלה ידנית (אדום)"}.get(mode, str(mode)), do)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(data)


# ============================================================
# API: הגדרות (מוסד, שפה, מראה/ערכת נושא, התקן שמע, רישיון, הרשאות, הצגת אירועים לא פעילים)
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
        if "show_disabled_events" in body:
            data["show_disabled_events"] = bool(body["show_disabled_events"])
        if "new_edit_password" in body and body["new_edit_password"] is not None:
            pw = body["new_edit_password"]
            data["permissions"]["edit_password_hash"] = (
                hashlib.sha256(pw.encode("utf-8")).hexdigest() if pw else ""
            )

    parts = [{"institution_info": "פרטי מוסד", "audio_device": "התקן שמע", "appearance": "מראה",
               "show_disabled_events": "הצגת לא-פעילים", "new_edit_password": "סיסמת עריכה"}[k] for k in
             ("institution_info", "audio_device", "appearance", "show_disabled_events", "new_edit_password") if body.get(k) is not None]
    data = mutate("update_settings", "עדכון הגדרות: " + (", ".join(parts) or "—"), do)
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
        new_weekly = parse_import_excel(file, file.filename)
    except Exception as e:
        logging.error(f"שגיאה בייבוא Excel: {e}")
        return jsonify({"error": f"שגיאה בקריאת הקובץ: {e}"}), 400

    def do(data):
        data["weekly_schedule"] = new_weekly
        data["active_schedule_name"] = None   # הלוח הפעיל כבר לא אחד הלוחות השמורים

    data = mutate("import_excel", f"ייבוא לוח מקובץ {file.filename} (הלוח הקודם הוחלף)", do)
    return jsonify(data)


def schedule_summary(weekly):
    """ספירות לחלון האישור: כמה אירועים, כמה בכל יום, וכמה עם קובץ שמע שהמנוע לא ימצא."""
    events = [ev for day in DAY_ORDER for ev in weekly.get(day, [])]
    return {
        "total": len(events),
        "per_day": {day: len(weekly.get(day, [])) for day in DAY_ORDER},
        "missing_audio": sum(1 for ev in events if not audio_value_exists(ev.get("audio"))),
    }


@app.route("/api/import/preview", methods=["POST"])
def api_import_preview():
    """קורא את הקובץ בלי לשנות כלום - לחלון האישור שלפני הייבוא."""
    guard = require_editor()
    if guard:
        return guard
    if "file" not in request.files:
        return jsonify({"error": "לא נשלח קובץ"}), 400
    file = request.files["file"]
    try:
        new_weekly = parse_import_excel(file, file.filename)
    except Exception as e:
        return jsonify({"error": f"שגיאה בקריאת הקובץ: {e}"}), 400
    return jsonify({"current": schedule_summary(load_data()["weekly_schedule"]),
                    "incoming": schedule_summary(new_weekly)})


# ============================================================
# לוחות שמורים: שמירת הלוח הנוכחי בשם, טעינה (מחליפה את הלוח הפעיל, ניתנת לביטול ב-Undo), מחיקה
# ============================================================
def load_saved_schedules():
    if not os.path.exists(SAVED_SCHEDULES_FILE):
        return {}
    try:
        with open(SAVED_SCHEDULES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"שגיאה בקריאת saved_schedules.json: {e}")
        return {}


def write_saved_schedules(saved):
    fd, tmp_path = tempfile.mkstemp(dir=BASE_DIR, prefix=".saved_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(saved, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, SAVED_SCHEDULES_FILE)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def _clean_name(name):
    return " ".join(str(name or "").split())[:60]


@app.route("/api/schedules", methods=["GET"])
def api_list_schedules():
    saved = load_saved_schedules()
    out = [{"name": n, "saved_at": v.get("saved_at", ""), "total": schedule_summary(v.get("weekly_schedule", {}))["total"]}
           for n, v in saved.items()]
    out.sort(key=lambda x: x["name"])
    data = load_data()
    active = data.get("active_schedule_name")
    modified = None
    if active in saved:
        def norm(weekly):
            w = copy.deepcopy(weekly)
            for day in DAY_ORDER:
                for ev in w.get(day, []):
                    ev.pop("skip_date", None)
            return json.dumps({day: w.get(day, []) for day in DAY_ORDER}, sort_keys=True, ensure_ascii=False)
        modified = norm(data["weekly_schedule"]) != norm(saved[active]["weekly_schedule"])
    return jsonify({"schedules": out, "active": active if active in saved else None, "active_modified": modified})


@app.route("/api/schedules/save", methods=["POST"])
def api_save_schedule():
    guard = require_editor()
    if guard:
        return guard
    name = _clean_name((request.get_json(force=True) or {}).get("name"))
    if not name:
        return jsonify({"error": "יש לתת שם ללוח"}), 400
    data = load_data()
    weekly = copy.deepcopy(data["weekly_schedule"])
    for day in DAY_ORDER:                      # השבתה חד-פעמית שייכת לתאריך מסוים, לא ללוח
        for ev in weekly.get(day, []):
            ev.pop("skip_date", None)
    saved = load_saved_schedules()
    existed = name in saved
    saved[name] = {"saved_at": datetime.now().isoformat(timespec="seconds"), "weekly_schedule": weekly}
    write_saved_schedules(saved)

    def do(d):
        d["active_schedule_name"] = name
    mutate("schedule_save", f"{'עודכן' if existed else 'נשמר'} לוח בשם '{name}'", do)
    return jsonify({"ok": True, "name": name, "replaced": existed})


@app.route("/api/schedules/load", methods=["POST"])
def api_load_schedule():
    guard = require_editor()
    if guard:
        return guard
    name = _clean_name((request.get_json(force=True) or {}).get("name"))
    saved = load_saved_schedules()
    if name not in saved:
        return jsonify({"error": "הלוח לא נמצא"}), 404
    weekly = copy.deepcopy(saved[name]["weekly_schedule"])

    def do(d):
        d["weekly_schedule"] = {day: weekly.get(day, []) for day in DAY_ORDER}
        d["active_schedule_name"] = name
    data = mutate("schedule_load", f"נטען הלוח '{name}'", do)
    return jsonify(data)


@app.route("/api/schedules/delete", methods=["POST"])
def api_delete_schedule():
    guard = require_editor()
    if guard:
        return guard
    name = _clean_name((request.get_json(force=True) or {}).get("name"))
    saved = load_saved_schedules()
    if name not in saved:
        return jsonify({"error": "הלוח לא נמצא"}), 404
    del saved[name]
    write_saved_schedules(saved)
    append_action_log("schedule_delete", f"נמחק הלוח השמור '{name}'")
    data = load_data()
    if data.get("active_schedule_name") == name:   # הלוח הפעיל כבר לא קשור ללוח שמור
        data["active_schedule_name"] = None
        save_data(data, log_action=False)
    return jsonify({"ok": True})


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
# API: לוג פעולות + Undo (כולל קפיצה לאחור בהיסטוריה, כמו בכרום)
# ============================================================
@app.route("/api/log", methods=["GET"])
def api_log():
    return jsonify(read_action_log())


@app.route("/api/undo/history", methods=["GET"])
def api_undo_history():
    return jsonify(list_undo_history())


@app.route("/api/undo", methods=["POST"])
def api_undo():
    """צעד אחד אחורה (התנהגות ברירת מחדל - שקולה ל-jump עם steps_back=1)."""
    guard = require_editor()
    if guard:
        return guard
    body = request.get_json(silent=True) or {}
    steps_back = int(body.get("steps_back", 1))
    snapshot = jump_to_undo_step(steps_back)
    if snapshot is None:
        return jsonify({"error": "אין פעולות לשחזור"}), 400
    save_data(snapshot, log_action=True, action_name="undo",
              action_details=f"שוחזר מצב קודם ({steps_back} צעדים אחורה)")
    return jsonify(snapshot)


# ============================================================
# סגירת חלון הניהול הראשי (X) = עצירת כל המערכת, כמו STOP
# רק החלון ש-start_all.bat פותח במחשב הצלצולים (‎?main=1, מ-localhost) - לא דפדפן בטלפון או במחשב אחר.
# בדרך כלל window_guard.py חוסם את ה-X (החלון נשאר) ומקפיץ את השאלה בעצמו. לכאן מגיעים רק אם החלון
# בכל זאת נסגר (למשל "סגור חלון" משורת המשימות): מיד חלון Windows "סגירת המערכת תשבית את הצלצולים. האם להמשיך?"
# (close_confirm.py). עד שעונים - המערכת ממשיכה לפעול. כן -> stop_all. לא -> חלון הניהול נפתח מחדש.
# רענון (F5) לא נחשב סגירה: הדף מסמן אותו, והשאלה מוצגת רק אם החלון לא חזר תוך כמה שניות.
# ============================================================
RELOAD_GRACE_SECONDS = 8          # רק לרענון; סגירה ב-X - מיד
STOP_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stop_all.py")
_window_close_timer = None
_window_lock = threading.Lock()


def _request_is_local():
    return request.remote_addr in ("127.0.0.1", "::1")


def _stop_whole_system():
    logging.warning("=== חלון הניהול הראשי נסגר והמשתמש אישר - עוצר את כל המערכת (כמו STOP) ===")
    append_action_log("system_stop", "חלון הניהול הראשי נסגר והעצירה אושרה - המערכת נעצרה (מנוע + שרת)")
    if os.name != "nt":
        return
    try:
        # בלי חלון; stop_all סוגר את השומר (ואיתו המנוע והשרת) ואת חלון הניהול
        subprocess.Popen([sys.executable, STOP_SCRIPT], cwd=BASE_DIR, creationflags=0x08000000)
    except Exception as e:
        logging.error(f"לא ניתן היה להפעיל את stop_all.py: {e}")


def _ask_before_stop():
    """חלון הניהול נסגר: חלון Windows שואל אם להשבית. עד שעונים - המערכת ממשיכה לפעול."""
    logging.warning("=== חלון הניהול הראשי נסגר - מוצגת שאלה אם להשבית את המערכת ===")
    if os.name != "nt":
        return
    try:
        # תהליך עצמאי (לא נסגר יחד עם המערכת), pythonw - בלי חלון שחור
        confirm = os.path.join(os.path.dirname(os.path.abspath(__file__)), "close_confirm.py")
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        subprocess.Popen([pyw if os.path.exists(pyw) else sys.executable, confirm], cwd=BASE_DIR,
                         creationflags=0x00000008 | 0x00000200 | 0x08000000)   # DETACHED | NEW_GROUP | NO_WINDOW
    except Exception as e:
        logging.error(f"לא ניתן היה להציג את שאלת הסגירה: {e}")


@app.route("/api/window/stop-confirmed", methods=["POST"])
def api_window_stop_confirmed():
    """המשתמש ענה "כן" בחלון השאלה (close_confirm.py) - עוצרים."""
    if not _request_is_local():
        return jsonify({"error": "רק מהמחשב עצמו"}), 403
    threading.Timer(0.3, _stop_whole_system).start()
    return ("", 204)


@app.route("/api/window/closing", methods=["POST"])
def api_window_closing():
    global _window_close_timer
    if not _request_is_local():
        return jsonify({"error": "רק מהמחשב עצמו"}), 403
    kind = request.get_data(as_text=True).strip().lower()
    with _window_lock:
        if _window_close_timer:
            _window_close_timer.cancel()
            _window_close_timer = None
        if kind == "reload":
            _window_close_timer = threading.Timer(RELOAD_GRACE_SECONDS, _ask_before_stop)
            _window_close_timer.daemon = True
            _window_close_timer.start()
    if kind == "reload":
        logging.info(f"חלון הניהול הראשי מתרענן - אם הוא לא יחזור תוך {RELOAD_GRACE_SECONDS} שניות תוצג שאלה אם להשבית.")
    else:
        _ask_before_stop()
    return ("", 204)


@app.route("/api/window/opened", methods=["POST"])
def api_window_opened():
    global _window_close_timer
    with _window_lock:
        if _window_close_timer:
            _window_close_timer.cancel()
            _window_close_timer = None
            logging.info("חלון הניהול חזר (רענון) - העצירה בוטלה.")
    return ("", 204)


def port_in_use(port):
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
        return True
    except OSError:
        return False


if __name__ == "__main__":
    # ב-Windows שרת שני יכול "להצטרף" לפורט של שרת קיים בלי שגיאה, והבקשות מתחלקות ביניהם
    # (כך שרת ישן המשיך לענות אחרי עדכון). לכן: אם הפורט כבר תפוס - לא עולים בכלל.
    if port_in_use(SERVER_PORT):
        msg = (f"שרת ניהול אחר כבר פועל על פורט {SERVER_PORT}. העותק הזה לא יעלה.\n"
               f"כדי להפעיל מחדש: הריצו stop_all.bat ואז start_all.bat (או את start_all.bat בלבד - הוא עוצר עותקים ישנים).")
        logging.error(msg)
        print(msg)
        sys.exit(1)
    load_data()
    logging.info(f"=== שרת הניהול מופעל (גרסה {SERVER_VERSION}) ===")
    app.run(host="0.0.0.0", port=SERVER_PORT, debug=False)
