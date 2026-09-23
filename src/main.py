"""
main.py
מנוע ההשמעה הקריטי של מערכת הצלצולים.
תפקיד יחיד ופשוט בכוונה: לבדוק כל שנייה את השעה מול schedule.json ולהשמיע את
האירועים המתאימים. אין כאן שום קוד ממשק/שרת - זה מכוון, כדי לשמור על יציבות
מקסימלית של הרכיב הקריטי במערכת.

קובץ זה שוחזר מאפס (המקורי אבד) לפי התיאור והקבצים (schedule.json) שסופקו.
בדוק אותו היטב לפני הסתמכות עליו בסביבה אמיתית.

הרצה:  python src/main.py
"""

import json
import os
import time
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
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    encoding='utf-8'
)
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

# כל כמה טיקים לחזור ולהזכיר בלוג שהמערכת מושבתת/רישיון לא תקף (לא בכל שנייה, כדי לא להציף את app.log)
STATUS_LOG_THROTTLE_TICKS = 60


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
def resolve_audio_path(audio_field):
    """תומך גם בשם קובץ פשוט (יחסי ל-audio/) וגם בנתיב מלא שנשמר בעבר בתוך ה-JSON."""
    if not audio_field:
        return None
    audio_field = str(audio_field)
    # נתיב מלא (Windows: יש ':' אחרי אות הכונן, או שכבר קיים כקובץ)
    if os.path.isabs(audio_field) or (len(audio_field) > 1 and audio_field[1] == ":"):
        return audio_field
    return os.path.join(AUDIO_DIR, audio_field)


def play_event(ev, day_heb_label=""):
    label = ev.get("label", "")
    audio_field = ev.get("audio", "")
    volume = max(0, min(100, int(ev.get("volume", 85)))) / 100.0
    duration = int(ev.get("duration_seconds", 30))

    path = resolve_audio_path(audio_field)
    if not path or not os.path.exists(path):
        logging.error(f"קובץ שמע לא נמצא עבור האירוע '{label}' (זמן {ev.get('time')}): '{audio_field}' -> '{path}'")
        return

    try:
        sound = pygame.mixer.Sound(path)
        sound.set_volume(volume)
        channel = sound.play()
        logging.info(f"מנגן: '{label}' | שעה {ev.get('time')} | קובץ '{path}' | עוצמה {int(volume*100)}% | משך {duration}s")
        return channel, time.monotonic() + duration
    except Exception as e:
        logging.error(f"שגיאה בהשמעת '{label}' מהקובץ '{path}': {e}")
        return None


# ============================================================
# לולאה ראשית
# ============================================================
def main():
    logging.info("=== מנוע ההשמעה מופעל ===")

    startup_data = load_schedule()
    startup_device = (startup_data or {}).get("institution_info", {}).get("audio_device") or None
    init_mixer(startup_device)
    current_device = startup_device
    write_available_devices()

    played_today = set()   # מפתחות uid|time שכבר נוגנו היום (עמיד לשינויי סדר/עריכה במהלך היום)
    scheduled_stops = []   # [(channel, stop_at_monotonic), ...] לעצירת קטעים לפי duration_seconds
    current_date = date.today()
    tick_counter = 0
    last_status_log_tick = -STATUS_LOG_THROTTLE_TICKS

    # באתחול: לא "לתפוס" ולנגן בבת אחת את כל האירועים שכבר עברו היום עד כה
    startup_now_str = datetime.now().strftime("%H:%M:%S")
    logging.info(f"מדלג על אירועים שכבר עברו את שעתם היום (לפני {startup_now_str}) כדי לא להציף בהשמעות בהפעלה.")

    while True:
        try:
            tick_counter += 1
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

            # רישיון לא תקף = אין שום השמעה, בלי קשר למצב system_status
            if not is_license_valid(data):
                if tick_counter - last_status_log_tick >= STATUS_LOG_THROTTLE_TICKS:
                    logging.warning("הרישיון אינו תקף - ההשמעה מושבתת. יש לעדכן רישיון תקף בהגדרות המערכת.")
                    last_status_log_tick = tick_counter
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            if is_system_disabled(data, today_iso):
                if tick_counter - last_status_log_tick >= STATUS_LOG_THROTTLE_TICKS:
                    logging.info("המערכת מושבתת כרגע (לפי הגדרת המשתמש) - אין השמעה.")
                    last_status_log_tick = tick_counter
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            # יום השבוע הנוכחי בפורמט תואם ל-DAY_ORDER (Python: Monday=0 .. Sunday=6)
            day_key = now.strftime("%A")
            if day_key not in DAY_ORDER:
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            events = data.get("weekly_schedule", {}).get(day_key, [])
            now_time = now.time()

            for ev in events:
                if not ev.get("enabled", True):
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
                result = play_event(ev)
                if result:
                    channel, stop_at = result
                    scheduled_stops.append((channel, stop_at))

            # עצירת קטעים שהגיעו לסוף משך ההשמעה שהוגדר להם
            now_mono = time.monotonic()
            still_pending = []
            for channel, stop_at in scheduled_stops:
                if now_mono >= stop_at:
                    try:
                        channel.stop()
                    except Exception:
                        pass
                else:
                    still_pending.append((channel, stop_at))
            scheduled_stops = still_pending

        except Exception as e:
            # שום שגיאה לא תפיל את הלולאה - זה הרכיב הקריטי במערכת
            logging.error(f"שגיאה בלתי צפויה בלולאה הראשית: {e}")

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logging.info("=== מנוע ההשמעה נעצר ידנית (Ctrl+C) ===")
