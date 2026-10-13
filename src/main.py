"""
main.py
מנוע ההשמעה הקריטי של מערכת הצלצולים.
תפקיד יחיד ופשוט בכוונה: לבדוק כל שנייה את השעה מול schedule.json ולהשמיע את
האירועים המתאימים, לפי עדיפויות שמע: תרגיל/חירום > צלצול > מוסיקה, אף פעם לא שני צלילים יחד
(מוסיקה דועכת כ-2 שנ' לפני צלצול ונגמרת בדיוק כשהוא מתחיל; אירוע בעדיפות נמוכה ממתין שהגבוה יסתיים). אין כאן שום קוד ממשק/שרת - זה מכוון, כדי לשמור על יציבות
מקסימלית של הרכיב הקריטי במערכת.

קובץ זה שוחזר מאפס (המקורי אבד) לפי התיאור והקבצים (schedule.json) שסופקו.
בדוק אותו היטב לפני הסתמכות עליו בסביבה אמיתית.

הרצה:  python src/main.py
"""

import json
import math
import os
import sys
import time
import random
import socket
import logging
from datetime import datetime, date

import pygame

# ============================================================
# נתיבים
# ============================================================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEDULE_FILE = os.path.join(BASE_DIR, "schedule.json")
AUDIO_DIR = os.path.join(BASE_DIR, "audio")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
LOG_FILE = os.path.join(LOGS_DIR, "app.log")
AUDIO_DEVICES_FILE = os.path.join(BASE_DIR, "audio_devices.json")

os.makedirs(LOGS_DIR, exist_ok=True)
# FileHandler עם קידוד מפורש (ולא basicConfig(encoding=...)) - כדי לעבוד גם על Python 3.8 / Windows 7
_log_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
_log_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logging.basicConfig(level=logging.INFO, handlers=[_log_handler])
# גם הדפסה למסך, כדי שיהיה נוח לראות מה קורה בחלון שבו הריצה
console = logging.StreamHandler()
console.setLevel(logging.INFO)
console.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logging.getLogger().addHandler(console)

DAY_ORDER = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
VALID_LICENSE_VALUE = "פעיל בתוקף"  # עקבי עם web_server.py

# כמה שניות "חלון תפיסה" אחרי הזמן המתוכנן עדיין ייחשב "עכשיו" (סופג עיכובים קלים בלולאה)
CATCH_UP_WINDOW_SECONDS = 4

# מרווח בדיקה של הלולאה הראשית
POLL_INTERVAL_SECONDS = 1

# כל כמה טיקים לרענן את רשימת התקני השמע הזמינים ולכתוב אותה ל-audio_devices.json
DEVICE_REFRESH_INTERVAL_TICKS = 60

# "מנעול" נגד הפעלה כפולה: המנוע מחזיק את הפורט המקומי הזה כל זמן שהוא רץ.
# עותק שני שמנסה לעלות מגלה שהפורט תפוס ויוצא - כך אף צלצול לא יתנגן פעמיים.
ENGINE_LOCK_PORT = 8599

# כל כמה טיקים לחזור ולהזכיר בלוג שהמערכת מושבתת/רישיון לא תקף (לא בכל שנייה, כדי לא להציף את app.log)
STATUS_LOG_THROTTLE_TICKS = 60

# ---- עדיפויות שמע (Context.md סעיף 3): חירום/תרגיל > צלצול > מוסיקה, אף פעם לא שני צלילים יחד ----
PRIORITY_MUSIC, PRIORITY_BELL, PRIORITY_EXERCISE = 1, 2, 3
PRIORITY_NAMES = {PRIORITY_MUSIC: "מוסיקה", PRIORITY_BELL: "צלצול", PRIORITY_EXERCISE: "תרגיל/חירום"}
# fade של מוסיקה לפני צלצול/תרגיל: מתחיל 2 טיקים (כ-2 שניות) לפני הטיק שבו הצלצול יתחיל, ונגמר בדיוק בו.
# (Context.md מבקש 1.5 שנ'. הלולאה רצה פעם בשנייה, ולכן 2 שניות הוא הקרוב ביותר שנגמר בדיוק בזמן הצלצול)
PREEMPT_LOOKAHEAD_TICKS = 2
# בדיקת צליל מהממשק (כפתור "בדיקה"): השרת כותב את הבקשה לקובץ הזה, המנוע מנגן ומוחק
TEST_REQUEST_FILE = os.path.join(BASE_DIR, "test_sound_request.json")
# המנוע כותב כאן אם צלצול ידני/בדיקה מתנגנים כרגע - כך הכפתור בממשק יודע להציג "עצור" (נכתב רק כשהמצב משתנה)
TEST_STATE_FILE = os.path.join(BASE_DIR, "test_sound_state.json")
TEST_MAX_SECONDS = 60          # שיר ארוך / תיקיית מוסיקה: עד דקה (ואפשר לעצור בלחיצה נוספת על הכפתור)
TEST_STOP_FADE_MS = 300
TEST_REQUEST_MAX_AGE = 30      # בקשה ישנה (למשל מלפני שהמנוע עלה) - לא מנגנים אותה
# עצירה "מיידית" של צליל באותה עדיפות או נמוכה ממנו שעוד מתנגן - fade קצרצר רק כדי למנוע "קליק" ברמקולים
QUICK_STOP_MS = 150


# ============================================================
# טעינת/נרמול schedule.json (עקבי עם web_server.py)
# ============================================================
def migrate_time(t):
    t = str(t).strip()
    parts = t.split(":")
    if len(parts) == 2:
        return f"{parts[0].zfill(2)}:{parts[1].zfill(2)}:00"
    if len(parts) == 3:
        return f"{parts[0].zfill(2)}:{parts[1].zfill(2)}:{parts[2].zfill(2)}"
    return "00:00:00"


def load_schedule():
    try:
        with open(SCHEDULE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        logging.error(f"קובץ {SCHEDULE_FILE} לא נמצא. ממתין...")
        return None
    except json.JSONDecodeError as e:
        # יכול לקרות אם נתפסים בדיוק באמצע כתיבה לא-אטומית ממקור חיצוני; לא קורס, פשוט מדלג לבדיקה הבאה
        logging.warning(f"schedule.json לא תקין כרגע ({e}), מדלג לבדיקה הבאה.")
        return None
    except Exception as e:
        logging.error(f"שגיאה בקריאת schedule.json: {e}")
        return None

    weekly = data.get("weekly_schedule", {})
    for day in DAY_ORDER:
        events = weekly.get(day, [])
        for ev in events:
            ev["time"] = migrate_time(ev.get("time", "00:00:00"))
        weekly[day] = events
    data["weekly_schedule"] = weekly
    return data


def is_system_disabled(data, today_iso):
    """בודק אם המערכת מושבתת כרגע. גם דואג לבטל אוטומטית השבתה 'להיום בלבד'
    כשמגיע יום חדש (הבדיקה בפועל של תאריך - לא כותב בחזרה לקובץ, זה תפקיד הממשק)."""
    if data.get("system_status") != "disabled":
        return False
    scope = data.get("disabled_scope")
    if scope == "indefinite":
        return True
    if scope == "day":
        return data.get("disabled_date") == today_iso
    return False


def is_license_valid(data):
    """המערכת מוגבלת רישיון: אם הרישיון לא תקף, לא מתבצעת השמעה בכלל -
    בלי קשר למצב system_status. עדיין ניתן להיכנס לממשק ולעדכן רישיון."""
    return data.get("institution_info", {}).get("license_status") == VALID_LICENSE_VALUE


# ============================================================
# ניהול התקן שמע: רשימת התקנים זמינים (נכתבת לקובץ שהממשק קורא ממנו)
# ובחירת/החלפת התקן בפועל לפי מה שנבחר ב-schedule.json -> institution_info.audio_device
# ============================================================
def get_available_devices():
    try:
        from pygame._sdl2 import audio as sdl2_audio
        devices = sdl2_audio.get_audio_device_names(False)
        return list(devices) if devices else ["ברירת מחדל של המערכת"]
    except Exception as e:
        logging.warning(f"לא ניתן היה למנות התקני שמע ({e}); משתמש בברירת מחדל בלבד.")
        return ["ברירת מחדל של המערכת"]


def write_available_devices():
    try:
        devices = get_available_devices()
        with open(AUDIO_DEVICES_FILE, "w", encoding="utf-8") as f:
            json.dump(devices, f, ensure_ascii=False, indent=2)
        return devices
    except Exception as e:
        logging.error(f"שגיאה בכתיבת רשימת התקני שמע: {e}")
        return []


def init_mixer(devicename=None):
    """אתחול/החלפת מנוע השמע. devicename=None או 'ברירת מחדל של המערכת' -> התקן ברירת המחדל."""
    try:
        pygame.mixer.quit()
    except Exception:
        pass
    try:
        if devicename and devicename != "ברירת מחדל של המערכת":
            pygame.mixer.init(devicename=devicename)
        else:
            pygame.mixer.init()
        pygame.mixer.set_num_channels(16)
        logging.info(f"מנוע השמע אותחל בהצלחה (התקן: {devicename or 'ברירת מחדל'}).")
        return True
    except Exception as e:
        logging.error(f"שגיאה באתחול מנוע השמע עם התקן '{devicename}': {e}. חוזר לברירת המחדל.")
        try:
            pygame.mixer.init()
            pygame.mixer.set_num_channels(16)
            return True
        except Exception as e2:
            logging.error(f"כשל גם באתחול ברירת המחדל: {e2}")
            return False


# ============================================================
# השמעה
# ============================================================
def resolve_audio_target(audio_field):
    """קובע אם audio_field מצביע על קובץ בודד או על תיקייה שלמה (השמעה אקראית ממנה)."""
    if not audio_field:
        return None, None
    audio_field = str(audio_field)
    if os.path.isabs(audio_field) or (len(audio_field) > 1 and audio_field[1] == ":"):
        path = audio_field
        if not os.path.exists(path):
            # נתיב מלא ממחשב אחר (למשל D:\Projects\SchoolBellSystem\audio\Tune 4.mp3 אחרי העברה למחשב
            # הצלצולים): אם יש קובץ/תיקייה באותו שם בתיקיית audio של ההתקנה הנוכחית - משתמשים בו
            local = os.path.join(AUDIO_DIR, os.path.basename(path.replace("\\", "/").rstrip("/")))
            if os.path.exists(local):
                path = local
    else:
        path = os.path.join(AUDIO_DIR, audio_field)
    if os.path.isdir(path):
        return "folder", path
    return "file", path


def list_folder_audio_files(folder_path):
    try:
        return [os.path.join(folder_path, f) for f in os.listdir(folder_path)
                if f.lower().endswith((".mp3", ".wav", ".ogg"))]
    except Exception as e:
        logging.error(f"שגיאה בקריאת תיקיית שמע '{folder_path}': {e}")
        return []


DEFAULT_FADE_SECONDS = {"Music": 4.0, "Bell": 2.0, "Exercise": 2.0}


def normalize_type(t):
    t = str(t or "Bell").lower()
    return {"music": "Music", "exercise": "Exercise", "message": "Message"}.get(t, "Bell")


def is_sound_event(ev):
    """אירוע מסוג "הודעה" מוצג בלוח בלבד - אין לו צליל, והמנוע מתעלם ממנו לגמרי."""
    return normalize_type(ev.get("type")) != "Message"


def get_fade_seconds(ev, key):
    """מחזיר ערך fade בשניות. תומך בשדות החדשים (fade_in_s/fade_out_s), בשדות הישנים
    במילישניות (fade_in_ms/fade_out_ms) ובברירת מחדל לפי סוג: מוזיקה 4 שנ', צלצול 2 שנ'."""
    s_key = f"{key}_s"
    ms_key = f"{key}_ms"
    if ev.get(s_key) is not None:
        try:
            return max(0.0, float(ev[s_key]))
        except Exception:
            pass
    try:
        ms = float(ev.get(ms_key) or 0)
        if ms > 0:
            return ms / 1000.0
    except Exception:
        pass
    return DEFAULT_FADE_SECONDS.get(normalize_type(ev.get("type")), 2.0)


def fit_fades(duration, fade_in, fade_out):
    """אם סכום ה-fades ארוך מהמשך בפועל - מקטינים את שניהם באופן יחסי, כך שהצליל
    לא ינוגן כולו בעוצמה חלקית ולא יחרוג מזמן הסיום."""
    total = fade_in + fade_out
    if duration > 0 and total > duration:
        ratio = duration / total
        return fade_in * ratio, fade_out * ratio
    return fade_in, fade_out


def stop_playback_record(rec):
    """עצירה סופית בזמן הסיום (ה-fade-out עצמו כבר רץ לפני כן, כך שהשקט מגיע בדיוק בזמן)."""
    channel = rec.get("channel")
    if not channel:
        return
    try:
        channel.stop()
    except Exception:
        pass


def begin_fade_out(rec, now_mono):
    """מתחיל fade-out כך שיסתיים בדיוק ב-stop_at."""
    rec["fading_out"] = True
    channel = rec.get("channel")
    remaining_ms = int(max(0.0, rec["stop_at"] - now_mono) * 1000)
    if channel is None:
        return
    try:
        if remaining_ms > 0 and channel.get_busy():
            channel.fadeout(remaining_ms)
            logging.info(f"'{rec['label']}': מתחיל fade-out ({remaining_ms / 1000:.1f} שנ' עד הסיום).")
    except Exception:
        pass


def play_event(ev, remaining_seconds=None):
    """מתחיל אירוע ומחזיר 'רשומת נגינה' שמתוחזקת ע"י הלולאה הראשית (קובץ בודד/לולאה/תיקייה אקראית).
    remaining_seconds - כשמוגדר (לדוגמה בהמשכת אירוע שהיה באמצע השמעה כשהמערכת הושבתה/הופעלה מחדש),
    זהו מספר השניות שנותרו לניגון במקום duration_seconds המלא של האירוע."""
    label = ev.get("label", "")
    audio_field = ev.get("audio", "")
    volume = max(0, min(100, int(ev.get("volume", 85)))) / 100.0
    duration = float(ev.get("duration_seconds", 30))
    if remaining_seconds is not None:
        duration = max(1.0, float(remaining_seconds))
    loop = bool(ev.get("loop", False))
    fade_in_s = get_fade_seconds(ev, "fade_in")
    fade_out_s = get_fade_seconds(ev, "fade_out")

    kind, path = resolve_audio_target(audio_field)
    if kind is None:
        logging.error(f"לא הוגדר קובץ/תיקיית שמע עבור האירוע '{label}' (זמן {ev.get('time')}).")
        return None

    start = time.monotonic()
    rec = {"label": label, "volume": volume, "channel": None, "fading_out": False,
           "priority": event_priority(ev), "started_at": start, "is_test": ev.get("uid") == "test-sound"}
    resumed_note = f" (זמן מקוצר: {int(duration)}s נותרו עד זמן הסיום המקורי)" if remaining_seconds is not None else ""

    if kind == "file":
        if not os.path.exists(path):
            logging.error(f"קובץ שמע לא נמצא עבור האירוע '{label}' (זמן {ev.get('time')}): '{audio_field}' -> '{path}'")
            return None
        try:
            sound = pygame.mixer.Sound(path)
            # קובץ קצר בלי לולאה מסתיים מעצמו - ה-fade-out צריך להסתיים בסוף הקובץ, לא בסוף הזמן המוקצב
            effective = duration if loop else min(duration, sound.get_length())
            fade_in_s, fade_out_s = fit_fades(effective, fade_in_s, fade_out_s)
            sound.set_volume(volume)
            channel = sound.play(loops=-1 if loop else 0, fade_ms=int(fade_in_s * 1000))
            rec.update({
                "kind": "single", "channel": channel,
                "stop_at": start + effective, "fade_out_at": start + effective - fade_out_s,
            })
            logging.info(
                f"מנגן: '{label}' | שעה {ev.get('time')} | קובץ '{path}' | עוצמה {int(volume*100)}% | "
                f"משך {effective:.0f}s | loop={loop} | fade_in={fade_in_s:.1f}s | fade_out={fade_out_s:.1f}s{resumed_note}"
            )
            return rec
        except Exception as e:
            logging.error(f"שגיאה בהשמעת '{label}' מהקובץ '{path}': {e}")
            return None

    # kind == "folder": השמעה אקראית של קבצים מתוך התיקייה, ברצף, עד תום duration_seconds
    files = list_folder_audio_files(path)
    if not files:
        logging.error(f"תיקיית השמע '{path}' עבור האירוע '{label}' ריקה או לא קיימת.")
        return None
    fade_in_s, fade_out_s = fit_fades(duration, fade_in_s, fade_out_s)
    rec.update({
        "kind": "folder", "folder_files": files, "last_file": None, "first_track_played": False,
        "folder_path": os.path.normcase(os.path.abspath(path)), "shuffle": ev.get("shuffle", True) is not False,
        "fade_in_s": fade_in_s, "stop_at": start + duration, "fade_out_at": start + duration - fade_out_s,
    })
    logging.info(
        f"מתחיל השמעה מתיקייה ({'אקראית' if ev.get('shuffle', True) is not False else 'לפי הסדר'}): '{label}' | שעה {ev.get('time')} | תיקייה '{path}' "
        f"({len(files)} קבצים) | משך כולל {duration:.0f}s | fade_in={fade_in_s:.1f}s | fade_out={fade_out_s:.1f}s{resumed_note}"
    )
    return rec


# תיקיית מוסיקה: תור השירים נשמר בין הפסקה להפסקה (כל זמן שהמנוע רץ).
# אקראי (shuffle) - כל סבב מערבב מחדש, וכל שיר מתנגן פעם אחת בסבב; לפי הסדר - לפי שם הקובץ, וממשיך מהמקום שנעצר
FOLDER_QUEUES = {}
FOLDER_LAST = {}


def next_folder_track(rec):
    files = sorted(rec["folder_files"], key=lambda f: os.path.basename(f).lower())
    key = (rec.get("folder_path"), rec.get("shuffle", True))
    queue = [f for f in FOLDER_QUEUES.get(key, []) if f in files]
    if not queue:
        if rec.get("shuffle", True):
            queue = random.sample(files, len(files))
            # תחילת סבב חדש: לא לפתוח בשיר שרק הסתיים בסוף הסבב הקודם
            if len(queue) > 1 and queue[0] == FOLDER_LAST.get(key):
                queue.append(queue.pop(0))
            logging.info(f"תיקיית שמע '{rec['label']}': סבב אקראי חדש ({len(queue)} שירים, כל שיר פעם אחת)")
        else:
            queue = list(files)
    track = queue.pop(0)
    FOLDER_QUEUES[key] = queue
    FOLDER_LAST[key] = track
    return track


def advance_folder_session(rec, now_mono):
    """מפעיל את הקטע הבא בתיקייה כשהקודם הסתיים (או מתחיל את הראשון), כל עוד לא חרגנו מהזמן הכולל."""
    channel = rec.get("channel")
    if channel is not None and channel.get_busy():
        return  # עדיין מתנגן קטע - אין מה לעשות

    if now_mono >= rec["stop_at"] or rec.get("fading_out"):
        return  # הזמן נגמר / כבר בדעיכה - לא מתחילים קטע חדש

    candidate = next_folder_track(rec)

    try:
        sound = pygame.mixer.Sound(candidate)
        sound.set_volume(rec["volume"])
        fade_ms = int(rec["fade_in_s"] * 1000) if not rec["first_track_played"] else 0
        rec["channel"] = sound.play(fade_ms=fade_ms)
        rec["last_file"] = candidate
        rec["first_track_played"] = True
        logging.info(f"תיקיית שמע '{rec['label']}': מנגן '{os.path.basename(candidate)}'")
    except Exception as e:
        logging.error(f"שגיאה בהשמעת קטע מתיקייה עבור '{rec['label']}' מהקובץ '{candidate}': {e}")
        rec["channel"] = None


# ============================================================
# עזר לחישובי זמן/אירועים (משותף לבדיקת "אירוע פעיל עכשיו" ולהתחלה רגילה)
# ============================================================
def event_time_seconds(ev):
    try:
        h, m, s = [int(x) for x in ev["time"].split(":")]
        return h * 3600 + m * 60 + s
    except Exception:
        return None


def is_skipped_on(ev, date_iso):
    """אירוע שהושבת 'להיום בלבד' / 'להופעה הקרובה בלבד' - מדלגים עליו רק בתאריך הזה."""
    return bool(ev.get("skip_date")) and ev.get("skip_date") == date_iso


def find_event_in_progress(data, now):
    """בודק אם יש אירוע פעיל (מהיום הנוכחי) שזמנו כבר הגיע אך עדיין לא הסתיים לפי
    duration_seconds - כלומר אירוע שהיה 'אמור להתנגן עכשיו'. מוחזר יחד עם מספר
    השניות שנותרו לו. משמש כדי להמשיך השמעה שנקטעה ע"י השבתה/רישיון לא תקף,
    ברגע שהמערכת חוזרת לפעולה תוך כדי חלון הזמן המקורי של האירוע."""
    day_key = now.strftime("%A")
    if day_key not in DAY_ORDER:
        return None, None
    now_seconds = now.hour * 3600 + now.minute * 60 + now.second
    today_iso = now.date().isoformat()
    for ev in data.get("weekly_schedule", {}).get(day_key, []):
        if not ev.get("enabled", True) or is_skipped_on(ev, today_iso) or not is_sound_event(ev):
            continue
        ev_seconds = event_time_seconds(ev)
        if ev_seconds is None:
            continue
        duration = int(ev.get("duration_seconds", 30))
        ev_end = ev_seconds + duration
        if ev_seconds <= now_seconds < ev_end:
            remaining = ev_end - now_seconds
            return ev, remaining
    return None, None


# ============================================================
# עדיפויות שמע: מי מתנגן, מי נקטע ומי ממתין
# ============================================================
def event_priority(ev):
    t = str(ev.get("type") or "Bell").lower()
    if t in ("exercise", "emergency"):
        return PRIORITY_EXERCISE
    if t == "music":
        return PRIORITY_MUSIC
    return PRIORITY_BELL


def record_finished(rec, now_mono):
    """רשומה שסיימה (הגיע זמן הסיום, או קובץ בודד שכבר לא מתנגן בפועל) לא חוסמת יותר אירועים אחרים."""
    if now_mono >= rec["stop_at"]:
        return True
    if rec.get("kind") == "single" and now_mono - rec.get("started_at", now_mono) > 0.5:
        channel = rec.get("channel")
        try:
            return channel is None or not channel.get_busy()
        except Exception:
            return True
    return False


def silence_record(rec, fade_ms, reason):
    rec["fading_out"] = True
    channel = rec.get("channel")
    if channel is not None:
        try:
            if fade_ms > 0 and channel.get_busy():
                channel.fadeout(int(fade_ms))
            else:
                channel.stop()
        except Exception:
            pass
    if reason:
        logging.info(f"'{rec['label']}' ({PRIORITY_NAMES.get(rec['priority'])}) נעצר: {reason}")


def start_event(ev, active_playbacks, pending_events, remaining_seconds=None):
    """נקודת הכניסה היחידה להשמעת אירוע. אוכפת את חוקי העדיפויות:
    * אם מתנגן משהו בעדיפות גבוהה יותר - האירוע נכנס לתור ויתחיל כשהוא יסתיים (עד זמן הסיום המקורי שלו).
    * אחרת - כל מה שמתנגן בעדיפות זהה או נמוכה נעצר, ואז האירוע מתחיל.
      תרגיל/חירום עוצר מיד בלי fade. מוסיקה לפני צלצול כבר דעכה מראש (prefade_for_upcoming)."""
    prio = event_priority(ev)
    label = ev.get("label", "")
    now_mono = time.monotonic()
    duration = float(remaining_seconds if remaining_seconds is not None else ev.get("duration_seconds", 30))

    higher = [r for r in active_playbacks if r["priority"] > prio and not record_finished(r, now_mono)]
    if higher:
        pending_events.append({"ev": ev, "priority": prio, "deadline": now_mono + duration})
        logging.info(
            f"'{label}' ({PRIORITY_NAMES[prio]}) ממתין: כרגע מתנגן '{higher[0]['label']}' "
            f"({PRIORITY_NAMES[higher[0]['priority']]}). יתחיל כשהוא יסתיים, עד תום {int(duration)} השניות שלו."
        )
        return None

    for r in list(active_playbacks):
        if r["priority"] <= prio:
            if r.get("preempted"):
                # כבר דעך לפני הצלצול (prefade_for_upcoming) - רק מוודאים שהערוץ שקט
                silence_record(r, 0, None)
            elif prio == PRIORITY_EXERCISE:
                silence_record(r, 0, f"קטיעה מיידית - מתחיל '{label}' (תרגיל/חירום)")
            else:
                silence_record(r, QUICK_STOP_MS, f"מתחיל '{label}' ({PRIORITY_NAMES[prio]})")
            active_playbacks.remove(r)

    rec = play_event(ev, remaining_seconds=remaining_seconds)
    if rec:
        active_playbacks.append(rec)
    return rec


def start_pending_events(active_playbacks, pending_events):
    """מפעיל אירועים שחיכו בתור, כשכבר אין מעליהם צליל בעדיפות גבוהה יותר. אירוע שזמנו עבר בזמן ההמתנה - מבוטל."""
    pending_events.sort(key=lambda p: -p["priority"])
    for p in list(pending_events):
        now_mono = time.monotonic()
        remaining = p["deadline"] - now_mono
        label = p["ev"].get("label", "")
        if remaining < 1.0:
            pending_events.remove(p)
            logging.info(f"'{label}' לא הושמע: זמנו הסתיים בזמן שהמתין לצליל בעדיפות גבוהה יותר.")
            continue
        if any(r["priority"] > p["priority"] and not record_finished(r, now_mono) for r in active_playbacks):
            continue
        pending_events.remove(p)
        logging.info(f"'{label}' מתחיל אחרי המתנה ({int(remaining)} שניות נותרו עד זמן הסיום המקורי).")
        start_event(p["ev"], active_playbacks, pending_events, remaining_seconds=remaining)


def prefade_for_upcoming(events, day_key, today_iso, now, active_playbacks, played_today):
    """מוסיקה שמתנגנת כשמתקרב צלצול/תרגיל: מתחילים fade-out שנגמר בדיוק בטיק שבו האירוע מתחיל,
    כך שהצלצול מתחיל בדיוק בזמנו ולא חופף למוסיקה."""
    if not active_playbacks:
        return
    now_sec = now.hour * 3600 + now.minute * 60 + now.second + now.microsecond / 1_000_000
    for ev in events:
        if not ev.get("enabled", True) or is_skipped_on(ev, today_iso) or not is_sound_event(ev):
            continue
        event_uid = ev.get("uid") or f"noid|{ev.get('label')}"
        if f"{day_key}|{event_uid}|{ev.get('time')}" in played_today:
            continue
        ev_seconds = event_time_seconds(ev)
        if ev_seconds is None:
            continue
        until = ev_seconds - now_sec
        if until <= 0:
            continue
        # הצלצול יתחיל בטיק הראשון של הלולאה שאחרי זמנו. מכוונים את סוף ה-fade בדיוק לטיק הזה,
        # ומתחילים כשנשארו 2 טיקים - כך ה-fade נמשך כ-2 שניות ונגמר ברגע שהצלצול מתחיל (בלי שקט ובלי חפיפה)
        ticks_ahead = math.ceil(until / POLL_INTERVAL_SECONDS - 1e-9)
        if ticks_ahead > PREEMPT_LOOKAHEAD_TICKS:
            continue
        prio = event_priority(ev)
        for r in active_playbacks:
            if r["priority"] < prio and not r.get("preempted") and not record_finished(r, time.monotonic()):
                fade_s = max(0.2, ticks_ahead * POLL_INTERVAL_SECONDS - 0.05)
                r["preempted"] = True
                r["stop_at"] = min(r["stop_at"], time.monotonic() + fade_s)
                silence_record(r, fade_s * 1000,
                               f"fade-out של {fade_s:.1f} שנ' לפני '{ev.get('label', '')}' ({PRIORITY_NAMES[prio]}) בשעה {ev.get('time')}")


def check_test_request(active_playbacks, pending_events):
    """בקשה מהממשק (כפתור "צלצול ידני" / "בדיקה"): action=play מנגן מיד את הקובץ שנבחר (עד דקה),
    בעדיפות צלצול; action=stop עוצר את מה שהתחיל מהכפתור. עובד גם כשהמערכת מושבתת (פעולה יזומה)."""
    if not os.path.exists(TEST_REQUEST_FILE):
        return
    try:
        with open(TEST_REQUEST_FILE, "r", encoding="utf-8") as f:
            req = json.load(f)
    except Exception:
        req = None
    try:
        os.remove(TEST_REQUEST_FILE)
    except Exception:
        pass
    if not req or time.time() - float(req.get("ts", 0)) > TEST_REQUEST_MAX_AGE:
        return
    stop_test_playbacks(active_playbacks, pending_events, "עצירה מהממשק")
    if req.get("action") == "stop":
        return
    ev = {"uid": "test-sound", "label": "צלצול ידני / בדיקה", "time": datetime.now().strftime("%H:%M:%S"),
          "type": "Bell", "audio": req.get("audio", ""), "volume": req.get("volume", 85),
          "duration_seconds": TEST_MAX_SECONDS, "loop": False, "fade_in_s": 0, "fade_out_s": 1.0}
    logging.info(f"צלצול ידני / בדיקה מהממשק: '{ev['audio']}' (עוצמה {ev['volume']}%)")
    start_event(ev, active_playbacks, pending_events)


def stop_test_playbacks(active_playbacks, pending_events, reason):
    for r in list(active_playbacks):
        if r.get("is_test"):
            silence_record(r, TEST_STOP_FADE_MS, reason)
            active_playbacks.remove(r)
    for p in list(pending_events):
        if p["ev"].get("uid") == "test-sound":
            pending_events.remove(p)


def test_is_playing(active_playbacks, pending_events):
    now_mono = time.monotonic()
    return (any(r.get("is_test") and not r.get("fading_out") and not record_finished(r, now_mono) for r in active_playbacks)
            or any(p["ev"].get("uid") == "test-sound" for p in pending_events))


def write_test_state(playing):
    """רק כשהמצב משתנה (לא כתיבה לדיסק בכל שנייה)."""
    if getattr(write_test_state, "last", None) == playing:
        return
    try:
        tmp = TEST_STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"playing": bool(playing), "ts": time.time()}, f)
        os.replace(tmp, TEST_STATE_FILE)
        write_test_state.last = playing
    except Exception as e:
        logging.warning(f"לא ניתן לכתוב את מצב הצלצול הידני: {e}")


def maintain_playbacks(active_playbacks):
    """תחזוקת נגינות פעילות: התקדמות תיקיות אקראיות + fade-out ועצירה בתום הזמן שהוגדר."""
    now_mono = time.monotonic()
    still_active = []
    for rec in active_playbacks:
        if record_finished(rec, now_mono):
            stop_playback_record(rec)
            continue
        if not rec.get("fading_out") and now_mono >= rec.get("fade_out_at", rec["stop_at"]):
            begin_fade_out(rec, now_mono)
        if rec["kind"] == "folder":
            advance_folder_session(rec, now_mono)
        still_active.append(rec)
    return still_active


# ============================================================
# לולאה ראשית
# ============================================================
def acquire_single_instance_lock():
    """מחזיר socket מאזין אם זה העותק היחיד של המנוע, או None אם כבר רץ מנוע אחר."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):   # Windows: בלי זה שני תהליכים יכולים לתפוס אותו פורט
        s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        s.bind(("127.0.0.1", ENGINE_LOCK_PORT))
        s.listen(16)
        s.setblocking(False)
        return s
    except OSError:
        s.close()
        return None


def drain_lock_connections(lock):
    """השרת בודק "האם המנוע פועל" בהתחברות לפורט המנעול. מקבלים וסוגרים את החיבורים האלה בכל טיק -
    אחרת תור החיבורים מתמלא, והבדיקה נכשלת ("המנוע לא פועל") למרות שהמנוע רץ."""
    for _ in range(64):
        try:
            conn, _addr = lock.accept()
        except (BlockingIOError, OSError):
            return
        try:
            conn.close()
        except Exception:
            pass


def main():
    lock = acquire_single_instance_lock()
    if lock is None:
        logging.error("=== מנוע השמעה אחר כבר פועל - העותק הזה נסגר כדי שצלצולים לא יתנגנו פעמיים ===")
        time.sleep(3)
        sys.exit(1)
    main.lock = lock  # מחזיקים את המנעול כל זמן שהתהליך חי
    logging.info("=== מנוע ההשמעה מופעל ===")

    startup_data = load_schedule()
    startup_device = (startup_data or {}).get("institution_info", {}).get("audio_device") or None
    init_mixer(startup_device)
    current_device = startup_device
    write_available_devices()
    write_test_state(False)

    played_today = set()      # מפתחות uid|time שכבר נוגנו היום (עמיד לשינויי סדר/עריכה במהלך היום)
    active_playbacks = []     # רשימת רשומות נגינה פעילות (קובץ בודד/לולאה/תיקייה אקראית)
    pending_events = []       # אירועים שממתינים שצליל בעדיפות גבוהה יותר יסתיים (למשל מוסיקה שמתחילה יחד עם צלצול)
    current_date = date.today()
    tick_counter = 0
    last_status_log_tick = -STATUS_LOG_THROTTLE_TICKS
    playback_halted_for_stop_request = False  # דגל: כבר עצרנו השמעה פעילה בעקבות השבתה/רישיון לא תקף
    was_blocked_last_tick = False             # דגל: הטיק הקודם היה במצב חסום (מושבת/רישיון לא תקף) - לזיהוי "חזרה לפעולה"

    # באתחול: לא "לתפוס" ולנגן בבת אחת את כל האירועים שכבר עברו היום עד כה
    startup_now_str = datetime.now().strftime("%H:%M:%S")
    logging.info(f"מדלג על אירועים שכבר עברו את שעתם היום (לפני {startup_now_str}) כדי לא להציף בהשמעות בהפעלה.")

    while True:
        try:
            tick_counter += 1
            drain_lock_connections(lock)
            now = datetime.now()
            today_iso = date.today().isoformat()

            # איפוס מעקב "נוגן היום" עם תחילת יום חדש
            if date.today() != current_date:
                played_today.clear()
                current_date = date.today()
                logging.info(f"יום חדש ({current_date.isoformat()}) - מאפס מעקב אירועים שנוגנו.")

            data = load_schedule()
            if data is None:
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            # רענון תקופתי של רשימת ההתקנים הזמינים (לא בכל טיק - זו קריאה יקרה יחסית)
            if tick_counter % DEVICE_REFRESH_INTERVAL_TICKS == 0:
                write_available_devices()

            # אם נבחר התקן שמע חדש דרך הממשק - מחליפים בפועל
            wanted_device = data.get("institution_info", {}).get("audio_device") or None
            if wanted_device != current_device:
                logging.info(f"זוהה שינוי בבחירת התקן השמע: '{current_device}' -> '{wanted_device}'. מחליף התקן.")
                init_mixer(wanted_device)
                current_device = wanted_device

            blocked_now = (not is_license_valid(data)) or is_system_disabled(data, today_iso)

            # רישיון לא תקף = אין שום השמעה, בלי קשר למצב system_status.
            # אם משהו כרגע מתנגן בפועל - עוצרים אותו מידית (לא מחכים שהוא יסתיים לבד).
            if not is_license_valid(data):
                if not playback_halted_for_stop_request:
                    pygame.mixer.stop()
                    active_playbacks = []
                    pending_events = []
                    playback_halted_for_stop_request = True
                    logging.warning("הרישיון אינו תקף - כל השמעה פעילה נעצרה מידית.")
                    write_test_state(False)
                if tick_counter - last_status_log_tick >= STATUS_LOG_THROTTLE_TICKS:
                    logging.warning("הרישיון אינו תקף - ההשמעה מושבתת. יש לעדכן רישיון תקף בהגדרות המערכת.")
                    last_status_log_tick = tick_counter
                was_blocked_last_tick = True
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            if is_system_disabled(data, today_iso):
                if not playback_halted_for_stop_request:
                    pygame.mixer.stop()
                    active_playbacks = []
                    pending_events = []
                    playback_halted_for_stop_request = True
                    logging.warning("המערכת הושבתה ע\"י המשתמש - כל השמעה פעילה נעצרה מידית (למשל: לצורך מבחן).")
                if tick_counter - last_status_log_tick >= STATUS_LOG_THROTTLE_TICKS:
                    logging.info("המערכת מושבתת כרגע (לפי הגדרת המשתמש) - אין השמעה מהלוח.")
                    last_status_log_tick = tick_counter
                # צלצול ידני מהכותרת עובד גם כשהמערכת מושבתת - זו פעולה יזומה (ההשבתה עוצרת רק את הלוח)
                check_test_request(active_playbacks, pending_events)
                active_playbacks = maintain_playbacks(active_playbacks)
                pending_events = [p for p in pending_events if p["ev"].get("uid") == "test-sound"]
                write_test_state(test_is_playing(active_playbacks, pending_events))
                was_blocked_last_tick = True
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            # צלצול ידני / בדיקה מהממשק (לא בלי רישיון תקף)
            check_test_request(active_playbacks, pending_events)

            # המערכת פעילה ומורשית - מאפסים את הדגל כדי שההשבתה הבאה תעצור מיידית שוב
            playback_halted_for_stop_request = False

            # ---- זיהוי "חזרה לפעולה" מתוך מצב חסום: אם היינו חסומים בטיק הקודם וכעת פעילים,
            # ובדיוק כרגע יש אירוע שזמנו כבר הגיע אך עוד לא הסתיים (לפי הלוח) - ממשיכים
            # להשמיע אותו מהרגע הזה ועד לזמן הסיום המקורי שלו, ולא מחכים לאירוע הבא. ----
            if was_blocked_last_tick:
                in_progress_ev, remaining = find_event_in_progress(data, now)
                if in_progress_ev is not None and remaining and remaining > 0:
                    day_key_now = now.strftime("%A")
                    event_uid = in_progress_ev.get("uid") or f"noid|{in_progress_ev.get('label')}"
                    play_key = f"{day_key_now}|{event_uid}|{in_progress_ev.get('time')}"
                    played_today.add(play_key)  # לא לנגן אותו שוב מההתחלה בהמשך הטיקים הרגילים
                    logging.info(
                        f"חזרה לפעולה תוך כדי חלון הזמן של אירוע פעיל - ממשיך להשמיע "
                        f"'{in_progress_ev.get('label')}' למשך {remaining} שניות נוספות (עד תום הזמן המתוכנן)."
                    )
                    start_event(in_progress_ev, active_playbacks, pending_events, remaining_seconds=remaining)
            was_blocked_last_tick = False

            # יום השבוע הנוכחי בפורמט תואם ל-DAY_ORDER (Python: Monday=0 .. Sunday=6)
            day_key = now.strftime("%A")
            if day_key not in DAY_ORDER:
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            events = data.get("weekly_schedule", {}).get(day_key, [])
            now_time = now.time()

            # מוסיקה שמתנגנת ומתקרב צלצול/תרגיל - fade-out מתחיל עכשיו, כך שהצלצול יתחיל בזמנו בלי חפיפה
            prefade_for_upcoming(events, day_key, today_iso, now, active_playbacks, played_today)

            due_now = []
            for ev in events:
                if not ev.get("enabled", True) or is_skipped_on(ev, today_iso) or not is_sound_event(ev):
                    continue
                event_uid = ev.get("uid") or f"noid|{ev.get('label')}"
                play_key = f"{day_key}|{event_uid}|{ev.get('time')}"
                if play_key in played_today:
                    continue

                try:
                    ev_h, ev_m, ev_s = [int(x) for x in ev["time"].split(":")]
                except Exception:
                    continue
                ev_seconds = ev_h * 3600 + ev_m * 60 + ev_s
                now_seconds = now_time.hour * 3600 + now_time.minute * 60 + now_time.second
                diff = now_seconds - ev_seconds

                if diff < 0:
                    continue  # עוד לא הגיע הזמן

                if diff == 0 or (0 < diff <= CATCH_UP_WINDOW_SECONDS and play_key not in played_today):
                    # בהפעלה ראשונה של התהליך - לא "לתפוס" אירועים ישנים מהיום (diff גדול)
                    pass
                elif diff > CATCH_UP_WINDOW_SECONDS:
                    # עבר זמנו מזמן (כנראה מלפני שהתהליך עלה) - מסמנים כ"נוגן" כדי לא לנסות שוב, בלי להשמיע
                    played_today.add(play_key)
                    continue
                else:
                    continue

                played_today.add(play_key)
                due_now.append(ev)

            # כמה אירועים באותה שנייה (למשל צלצול "הפסקה" ומוסיקה ב-12:15): קודם העדיפות הגבוהה,
            # והנמוכה נכנסת לתור ומתחילה כשהגבוהה מסתיימת
            due_now.sort(key=lambda e: -event_priority(e))
            for ev in due_now:
                start_event(ev, active_playbacks, pending_events)

            # תחזוקת נגינות פעילות: התקדמות תיקיות אקראיות + עצירה בתום הזמן שהוגדר
            active_playbacks = maintain_playbacks(active_playbacks)

            # אירועים שחיכו לצליל בעדיפות גבוהה יותר - מתחילים ברגע שהוא הסתיים
            if pending_events:
                start_pending_events(active_playbacks, pending_events)
            write_test_state(test_is_playing(active_playbacks, pending_events))

        except Exception as e:
            # שום שגיאה לא תפיל את הלולאה - זה הרכיב הקריטי במערכת
            logging.error(f"שגיאה בלתי צפויה בלולאה הראשית: {e}")

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logging.info("=== מנוע ההשמעה נעצר ידנית (Ctrl+C) ===")
