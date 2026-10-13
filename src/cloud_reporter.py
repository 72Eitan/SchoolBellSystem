"""
cloud_reporter.py  -  דיווח מצב ממחשב הצלצולים אל מרכז הבקרה של "עת רצון"

תהליך נפרד לגמרי ממנוע ההשמעה:
  * רק קורא קבצים (schedule.json, logs/app.log). לא כותב ל-schedule.json ולא נוגע ב-main.py.
  * אם הוא קורס, אין לאינטרנט, או שמרכז הבקרה לא זמין - הצלצולים ממשיכים כרגיל.
  * בלי ספריות חיצוניות (רק Python רגיל), תואם Python 3.8 / Windows 7.

הגדרה: קובץ cloud_config.json בתיקיית המערכת (מורידים אותו ממרכז הבקרה כשיוצרים לקוח):
  {
    "server_url": "https://...",
    "customer_code": "ER-XXXX-XXXX",
    "api_key": "...",
    "interval_seconds": 60
  }
אם יש server_url ו-"auto_register": true בלי customer_code - המחשב נרשם לבד, מקבל קוד לקוח ייחודי,
ושומר אותו בקובץ. הלקוח מופיע אצל היוצר כ"ממתין לאישור".

הרצה:  python src/cloud_reporter.py          (רץ ברקע ומדווח כל דקה)
        python src/cloud_reporter.py --once   (דיווח אחד ומדפיס מה נשלח - לבדיקה)
"""

import json
import logging
import os
import platform
import re
import hashlib
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(BASE_DIR, "src")
SCHEDULE_FILE = os.path.join(BASE_DIR, "schedule.json")
CONFIG_FILE = os.path.join(BASE_DIR, "cloud_config.json")
AUDIO_DIR = os.path.join(BASE_DIR, "audio")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
APP_LOG = os.path.join(LOGS_DIR, "app.log")
STATE_FILE = os.path.join(LOGS_DIR, "cloud_state.json")          # התשובה האחרונה מהמרכז (רישיון וכו')
FEEDBACK_OUTBOX = os.path.join(LOGS_DIR, "feedback_outbox.jsonl")  # משוב שממתין לשליחה
REPORTER_LOG = os.path.join(LOGS_DIR, "cloud_reporter.log")
UPDATES_DIR = os.path.join(BASE_DIR, "updates")
UPDATE_STATE = os.path.join(UPDATES_DIR, "update_state.json")       # מצב העדכון (משותף עם updater.py)
UPDATE_PUBLIC_KEY = os.path.join(SRC_DIR, "update_public_key.txt")
INSTALL_AFTER_LAST_EVENT_MIN = 5       # מתקינים רק אחרי שהאירוע האחרון של היום הסתיים + 5 דק'
INSTALL_LATEST_HOUR = 23               # ולא אחרי 23:00 (כדי לסיים הרבה לפני היום הבא)
# רשימת רשויות אישורים (מ-certifi) שמגיעה עם התוכנה: Windows 7 שלא עודכן לא מכיר תעודות HTTPS חדשות
# (למשל Let's Encrypt, שבה משתמשים Tailscale ו-Cloudflare), והחיבור נכשל בלעדיה
CA_FILE = os.path.join(SRC_DIR, "certs", "cacert.pem")

VALID_LICENSE_VALUE = "פעיל בתוקף"     # זהה ל-main.py
REPORTER_LOCK_PORT = 8598              # מונע שני עותקים (המנוע משתמש ב-8599, השרת ב-8500)
WEB_PORT = 8500
DAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]  # לפי weekday()
AUDIO_EXTENSIONS = (".mp3", ".wav", ".ogg")
PROCESS_CHECK_EVERY = 1                # בדיקת תהליכים בכל דיווח (ב-Windows 7 כשנייה). כך "המנוע לא רץ" מגיע לדשבורד תוך דקה

os.makedirs(LOGS_DIR, exist_ok=True)
log = logging.getLogger("cloud_reporter")
log.setLevel(logging.INFO)
_h = RotatingFileHandler(REPORTER_LOG, maxBytes=512 * 1024, backupCount=2, encoding="utf-8")
_h.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
log.addHandler(_h)
# רץ ברקע בלי חלון (pythonw) - הדשבורד הוא מה שמראה אם הוא עובד. הפירוט המלא תמיד ב-logs/cloud_reporter.log.
# כשמריצים ידנית בחלון (למשל --once לבדיקה) - גם מדפיס למסך.
if sys.stderr is not None:
    _c = logging.StreamHandler()
    _c.setFormatter(logging.Formatter("%(asctime)s - %(message)s"))
    log.addHandler(_c)


# ------------------------------------------------------------------ קבצים

def read_json(path, retries=3):
    """קריאה סובלנית: אם הממשק בדיוק כותב את הקובץ, מנסים שוב אחרי רגע."""
    for i in range(retries):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return None
        except (ValueError, OSError):
            time.sleep(0.3 * (i + 1))
    return None


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def client_version():
    """גרסת התוכנה = SERVER_VERSION שב-web_server.py (קוראים כטקסט, לא מייבאים את השרת)."""
    try:
        with open(os.path.join(SRC_DIR, "web_server.py"), "r", encoding="utf-8") as f:
            m = re.search(r'^SERVER_VERSION\s*=\s*"([^"]+)"', f.read(), re.M)
            return m.group(1) if m else None
    except OSError:
        return None


# ------------------------------------------------------------------ מצב המערכת

def to_utc_iso(local_dt):
    return local_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def time_to_seconds(t):
    try:
        parts = [int(x) for x in str(t).split(":")]
        while len(parts) < 3:
            parts.append(0)
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    except (TypeError, ValueError):
        return None


def resolve_audio(audio_field):
    """אותו היגיון כמו resolve_audio_target ב-main.py: קובץ/תיקייה ב-audio, או נתיב מלא."""
    if not audio_field:
        return None
    audio_field = str(audio_field)
    if os.path.isabs(audio_field) or (len(audio_field) > 1 and audio_field[1] == ":"):
        path = audio_field
        if not os.path.exists(path):
            local = os.path.join(AUDIO_DIR, os.path.basename(path.replace("\\", "/").rstrip("/")))
            if os.path.exists(local):
                path = local
    else:
        path = os.path.join(AUDIO_DIR, audio_field)
    return path


def audio_ok(path):
    if not path or not os.path.exists(path):
        return False
    if os.path.isdir(path):
        try:
            return any(n.lower().endswith(AUDIO_EXTENSIONS) for n in os.listdir(path))
        except OSError:
            return False
    return True


def schedule_status(data, now):
    """מה המערכת אמורה לעשות היום, לפי schedule.json."""
    out = {}
    if not data:
        out["schedule_error"] = "schedule.json חסר או לא קריא"
        return out
    info = data.get("institution_info") or {}
    today_iso = now.date().isoformat()
    status = data.get("system_status")
    scope = data.get("disabled_scope")
    # השבתה "להיום בלבד" שתאריכה עבר כבר לא בתוקף (כמו is_system_disabled ב-main.py)
    if status == "disabled" and scope == "day" and data.get("disabled_date") != today_iso:
        status = "enabled"
    out.update({
        "system_status": status,
        "disabled_scope": scope if status == "disabled" else None,
        "license_valid": info.get("license_status") == VALID_LICENSE_VALUE,
        "institution_name": info.get("name") or "",
        "audio_device": info.get("audio_device") or "",
    })

    weekly = data.get("weekly_schedule") or {}
    day_key = DAY_ORDER[now.weekday()]
    todays = []
    for ev in weekly.get(day_key) or []:
        if not ev.get("enabled", True) or ev.get("skip_date") == today_iso:
            continue
        sec = time_to_seconds(ev.get("time"))
        if sec is not None:
            todays.append((sec, ev))
    todays.sort(key=lambda x: x[0])
    out["events_today"] = len(todays)
    out["events_week"] = sum(1 for evs in weekly.values() for ev in (evs or []) if ev.get("enabled", True))

    now_sec = now.hour * 3600 + now.minute * 60 + now.second
    nxt = next((ev for sec, ev in todays if sec > now_sec), None)
    out["next_event"] = {"time": nxt.get("time"), "label": nxt.get("label", ""), "type": nxt.get("type", "")} if nxt else None
    if todays:
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        first = midnight + timedelta(seconds=todays[0][0])
        last_ev = todays[-1][1]
        last = midnight + timedelta(seconds=todays[-1][0] + int(last_ev.get("duration_seconds") or 0))
        out["day_window"] = {"from": to_utc_iso(first), "to": to_utc_iso(last)}
    else:
        out["day_window"] = None

    missing = set()
    for evs in weekly.values():
        for ev in evs or []:
            if ev.get("enabled", True) and ev.get("audio") and not audio_ok(resolve_audio(ev["audio"])):
                missing.add(os.path.basename(str(ev["audio"]).replace("\\", "/").rstrip("/")))
    out["missing_audio"] = sorted(missing)[:20]
    return out


LOG_LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ - (ERROR|CRITICAL) - (.*)")


def recent_errors(now, max_bytes=2 * 1024 * 1024):
    """סופר שגיאות ב-app.log ב-24 השעות האחרונות (קורא רק את סוף הקובץ)."""
    count, last = 0, None
    try:
        size = os.path.getsize(APP_LOG)
        with open(APP_LOG, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return 0, None
    since = now - timedelta(hours=24)
    for line in text.splitlines():
        m = LOG_LINE_RE.match(line)
        if not m:
            continue
        try:
            t = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if t >= since:
            count += 1
            last = m.group(3)[:200]
    return count, last


# ------------------------------------------------------------------ תהליכים

def run_hidden(cmd, timeout):
    """הרצת פקודה בלי שיקפוץ חלון. הדיווח רץ ברקע בלי חלון (pythonw), ובלי זה Windows פותח לכל
    פקודה (PowerShell / wmic) חלון לרגע - הבהוב כל דקה."""
    kw = {}
    if os.name == "nt":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0                       # SW_HIDE
        kw = {"startupinfo": si, "creationflags": 0x08000000}   # CREATE_NO_WINDOW
    return subprocess.run(cmd, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL, **kw).stdout


def _list_processes():
    if os.name == "nt":
        sys.path.insert(0, SRC_DIR)
        try:
            import stop_all          # רק פונקציות הפענוח; את הפקודות מריצים כאן במצב מוסתר
        finally:
            sys.path.pop(0)
        try:                         # PowerShell 3+ (Windows 8 ומעלה)
            out = stop_all.decode_output(run_hidden(
                ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command",
                 "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
                 "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,CommandLine "
                 "| ConvertTo-Json -Compress"], 60)).strip().lstrip("\ufeff")
            if out:
                data = json.loads(out)
                procs = data if isinstance(data, list) else [data]
                if procs:
                    return procs
        except Exception:
            pass
        # Windows 7 (PowerShell 2): wmic
        out = stop_all.decode_output(run_hidden(
            ["wmic", "process", "get", "CommandLine,Name,ParentProcessId,ProcessId", "/format:csv"], 60))
        return stop_all.parse_wmic_csv(out)
    out = run_hidden(["ps", "-eo", "pid=,ppid=,args="], 20)
    procs = []
    for line in out.decode("utf-8", "replace").splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3:
            procs.append({"ProcessId": int(parts[0]), "ParentProcessId": int(parts[1]),
                          "Name": os.path.basename(parts[2].split()[0]), "CommandLine": parts[2]})
    return procs


ENGINE_RE = re.compile(r"(^|[\\/\s\"'])(src[\\/])?main\.py\b", re.I)


def engine_pids():
    """(מספרי התהליכים של main.py של הפרויקט הזה, האם יש main.py שאי אפשר לשייך). None אם אי אפשר לבדוק."""
    try:
        procs = _list_processes()
    except Exception as e:
        log.warning("בדיקת תהליכים נכשלה: %s", e)
        return None
    project = os.path.basename(BASE_DIR).lower()
    by_pid = {p.get("ProcessId"): p for p in procs}
    ours, ambiguous = [], False
    for p in procs:
        name = (p.get("Name") or "").lower()
        cl = (p.get("CommandLine") or "").lower()
        if "python" not in name or not ENGINE_RE.search(cl):
            continue
        # main.py של הפרויקט הזה: הנתיב בשורת הפקודה שלו, או באחד ההורים (חלון ה-cmd ש-start_all.bat פותח)
        chain, cur = [cl], p
        for _ in range(3):
            cur = by_pid.get(cur.get("ParentProcessId"))
            if not cur:
                break
            chain.append((cur.get("CommandLine") or "").lower())
        if any(project in c for c in chain):
            ours.append(p.get("ProcessId"))
        else:
            ambiguous = True        # main.py כלשהו רץ, אבל אי אפשר לדעת אם הוא שלנו
    return ours, ambiguous


def engine_running():
    """True אם main.py של הפרויקט הזה רץ. None אם אי אפשר לבדוק."""
    res = engine_pids()
    if res is None:
        return None
    ours, ambiguous = res
    if ours:
        return True
    return None if ambiguous else False


def port_open(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.5)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def machine_id():
    return "%s-%012x" % (socket.gethostname()[:20], uuid.getnode())


# ------------------------------------------------------------------ תקשורת

_ssl_ctx = {}


def ssl_context(cfg):
    """הקשר HTTPS: קובץ ca_file מההגדרות, או cacert.pem שמגיע עם התוכנה, או ברירת המחדל של המחשב."""
    ca = cfg.get("ca_file") or (CA_FILE if os.path.exists(CA_FILE) else None)
    if ca not in _ssl_ctx:
        ctx = ssl.create_default_context(cafile=ca) if ca else ssl.create_default_context()
        if ca:
            # גם התעודות של Windows: מסנן אינטרנט שבודק HTTPS (נפוץ בבתי ספר) מתקין תעודה משלו שם,
            # ובלעדיה כל חיבור דרכו נכשל
            try:
                ctx.load_default_certs()
            except Exception:
                pass
        _ssl_ctx[ca] = ctx
    return _ssl_ctx[ca]


def post(cfg, path, body, timeout=15):
    url = cfg["server_url"].rstrip("/") + path
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if cfg.get("customer_code"):
        headers["X-Customer-Code"] = cfg["customer_code"]
        headers["X-Api-Key"] = cfg.get("api_key", "")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        ctx = ssl_context(cfg) if url.lower().startswith("https") else None
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8") or "{}")
        except ValueError:
            return e.code, {}


def download(cfg, path, dest, timeout=120):
    """הורדת קובץ מהמרכז (עם קוד הלקוח והמפתח) לקובץ זמני, ואז החלפה."""
    url = cfg["server_url"].rstrip("/") + path
    req = urllib.request.Request(url, headers={"X-Customer-Code": cfg["customer_code"],
                                               "X-Api-Key": cfg.get("api_key", "")})
    ctx = ssl_context(cfg) if url.lower().startswith("https") else None
    tmp = dest + ".part"
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r, open(tmp, "wb") as f:
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            f.write(chunk)
    os.replace(tmp, dest)


def ensure_registered(cfg):
    if cfg.get("customer_code") and cfg.get("api_key"):
        return True
    if not cfg.get("auto_register"):
        log.error("ב-cloud_config.json חסרים customer_code / api_key")
        return False
    data = read_json(SCHEDULE_FILE) or {}
    info = data.get("institution_info") or {}
    status, resp = post(cfg, "/api/agent/register", {"machine_id": machine_id(), "institution": {
        k: info.get(k, "") for k in ("name", "contact", "phone", "email", "country", "city", "address")}})
    if status != 200 or not resp.get("customer_code"):
        log.error("הרישום למרכז הבקרה נכשל (%s): %s", status, resp.get("error"))
        return False
    cfg["customer_code"], cfg["api_key"] = resp["customer_code"], resp["api_key"]
    cfg.pop("auto_register", None)
    write_json(CONFIG_FILE, cfg)
    log.info("נרשם למרכז הבקרה. קוד לקוח: %s", cfg["customer_code"])
    return True


def flush_feedback(cfg):
    """שולח משוב שנכתב ל-logs/feedback_outbox.jsonl (שורה = {"text","category","rating","contact"})."""
    if not os.path.exists(FEEDBACK_OUTBOX):
        return
    with open(FEEDBACK_OUTBOX, "r", encoding="utf-8") as f:
        lines = [l for l in f.read().splitlines() if l.strip()]
    remaining = []
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        try:
            status, _ = post(cfg, "/api/agent/feedback", item)
        except Exception:
            status = 0
        if status != 200 and status != 400:      # 400 = משוב פגום, לא ננסה שוב
            remaining.append(line)
    tmp = FEEDBACK_OUTBOX + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(remaining) + ("\n" if remaining else ""))
    os.replace(tmp, FEEDBACK_OUTBOX)


# ------------------------------------------------------------------ עדכון מרחוק
# המרכז מציע גרסה -> מורידים -> בודקים hash וחתימה דיגיטלית -> מחכים לסוף יום הצלצולים ->
# מפעילים את updater.py (תהליך נפרד) שמגבה, מחליף, מפעיל מחדש, בודק, ואם צריך - מחזיר לגרסה הקודמת.

def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_package(path, version, sha256_hex, signature_hex):
    """None אם החבילה תקינה וחתומה ע"י היוצר, אחרת סיבת הכישלון."""
    sys.path.insert(0, SRC_DIR)
    try:
        import ed25519_pure as ed
    finally:
        sys.path.pop(0)
    if not os.path.exists(UPDATE_PUBLIC_KEY):
        return "חסר src\\update_public_key.txt - אי אפשר לאמת עדכונים"
    with open(UPDATE_PUBLIC_KEY) as f:
        public = bytes.fromhex(f.read().strip())
    digest = sha256_of(path)
    if digest != (sha256_hex or "").lower():
        return "הקובץ שהורד לא תקין (hash לא תואם)"
    if not ed.verify(public, ed.update_message(version, digest), bytes.fromhex(signature_hex or "")):
        return "החתימה הדיגיטלית לא תקינה - העדכון נדחה"
    return None


def report_update(cfg, release_id, status, message=""):
    try:
        code, resp = post(cfg, "/api/agent/update/status", {"release_id": release_id, "status": status,
                                                            "message": message})
        return code == 200, bool(resp.get("cancelled"))
    except Exception as e:
        log.warning("דיווח מצב עדכון נכשל: %s", e)
        return False, False


def in_install_window(status, now=None):
    """אחרי האירוע האחרון של היום (או ביום בלי אירועים), ולפני 23:00."""
    now = now or datetime.now()
    if now.hour >= INSTALL_LATEST_HOUR:
        return False
    win = status.get("day_window")
    if not win:
        return True
    try:
        end_utc = datetime.strptime(win["to"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (KeyError, ValueError):
        return False
    return datetime.now(timezone.utc) >= end_utc + timedelta(minutes=INSTALL_AFTER_LAST_EVENT_MIN)


def launch_updater():
    script = os.path.join(SRC_DIR, "updater.py")
    if os.name == "nt":
        # דרך start: חלון נפרד וממוזער, שלא תלוי בתהליך הזה (updater עוצר ומפעיל מחדש גם את הדיווח)
        py = os.path.join(os.path.dirname(sys.executable), "python.exe")   # לעדכון כן רוצים חלון (ממוזער)
        subprocess.Popen('start "Bell Updater - updater.py" /min cmd /c ""%s" "%s""'
                         % (py if os.path.exists(py) else sys.executable, script),
                         shell=True, cwd=BASE_DIR, creationflags=0x08000000)
    else:
        subprocess.Popen([sys.executable, script], cwd=BASE_DIR, start_new_session=True)


def handle_update(cfg, offer, status):
    state = read_json(UPDATE_STATE) or {}
    # תוצאה סופית מ-updater שעוד לא דווחה (למשל: לא היה אינטרנט בסוף ההתקנה)
    if state.get("status") in ("installed", "failed", "rolled_back") and not state.get("reported"):
        ok, _ = report_update(cfg, state.get("release_id"), state["status"], state.get("message", ""))
        if ok:
            state["reported"] = True
            write_json(UPDATE_STATE, state)
    if not offer:
        return
    rid, version = offer["release_id"], offer["version"]
    if state.get("release_id") != rid:
        state = {"release_id": rid, "version": version, "from_version": status.get("version"),
                 "sha256": offer["sha256"], "signature": offer["signature"], "status": "new"}
    if state.get("status") == "installing":
        return                         # updater כבר רץ (או נתקע - הוא מדווח בעצמו)
    if state.get("status") in ("failed", "rolled_back", "installed") and state.get("reported"):
        return
    os.makedirs(os.path.join(UPDATES_DIR, "staging"), exist_ok=True)
    pkg = os.path.join(UPDATES_DIR, "staging", "etratzon-update-%s.zip" % version)
    if state.get("status") != "downloaded" or not os.path.exists(pkg):
        report_update(cfg, rid, "downloading")
        try:
            download(cfg, offer["url"], pkg)
        except Exception as e:
            log.warning("הורדת עדכון %s נכשלה (ננסה שוב בדיווח הבא): %s", version, e)
            return
        err = verify_package(pkg, version, offer["sha256"], offer["signature"])
        if err:
            log.error("עדכון %s נדחה: %s", version, err)
            os.remove(pkg)
            state.update(status="failed", message=err, reported=False)
            write_json(UPDATE_STATE, state)
            ok, _ = report_update(cfg, rid, "failed", err)
            state["reported"] = ok
            write_json(UPDATE_STATE, state)
            return
        state.update(status="downloaded", package=pkg, message="")
        write_json(UPDATE_STATE, state)
        _, cancelled = report_update(cfg, rid, "downloaded")
        log.info("עדכון לגרסה %s הורד ואומת. יותקן אחרי סוף יום הצלצולים.", version)
        if cancelled:
            return
    if not in_install_window(status):
        return
    # בדיקה אחרונה לפני התקנה: החבילה עדיין שלמה וחתומה
    err = verify_package(pkg, version, state["sha256"], state["signature"])
    if err:
        state.update(status="new")
        write_json(UPDATE_STATE, state)
        return
    state.update(status="installing", started=datetime.now().isoformat(timespec="seconds"), reported=False)
    write_json(UPDATE_STATE, state)
    report_update(cfg, rid, "installing")
    log.info("מתחיל התקנת גרסה %s", version)
    launch_updater()


_tick = {"n": 0, "engine": None}


def build_status():
    now = datetime.now()
    status = schedule_status(read_json(SCHEDULE_FILE), now)
    if _tick["n"] % PROCESS_CHECK_EVERY == 0 or _tick["engine"] is not True:
        _tick["engine"] = engine_running()
    _tick["n"] += 1
    errs, last_err = recent_errors(now)
    status.update({
        "version": client_version(),
        "engine_alive": _tick["engine"],
        "web_alive": port_open(WEB_PORT),
        "errors_24h": errs,
        "last_error": last_err,
        "os": "%s %s" % (platform.system(), platform.release()) + (" " + platform.version() if os.name != "nt" else ""),
        "python": platform.python_version(),
        "machine_id": machine_id(),
        "pc_time_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    })
    if os.name == "nt":
        try:
            sp = platform.win32_ver()[2]
            status["os"] = "Windows %s%s" % (platform.release(), " " + sp if sp else "")
        except Exception:
            pass
    return status


def send_heartbeat(cfg):
    status = build_status()
    code, resp = post(cfg, "/api/agent/heartbeat", {"status": status})
    if code == 200:
        write_json(STATE_FILE, {"last_ok": datetime.now().isoformat(timespec="seconds"),
                                "license": resp.get("license"), "latest_version": resp.get("latest_version"),
                                "server_time_utc": resp.get("server_time_utc")})
    return code, resp, status


def acquire_lock():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        s.bind(("127.0.0.1", REPORTER_LOCK_PORT))
        s.listen(1)
        return s
    except OSError:
        s.close()
        return None


def main():
    once = "--once" in sys.argv
    cfg = read_json(CONFIG_FILE)
    if not cfg or not cfg.get("server_url"):
        log.error("לא נמצא cloud_config.json עם server_url - אין לאן לדווח. יוצא.")
        return 1
    lock = None if once else acquire_lock()
    if not once and lock is None:
        log.error("עותק אחר של cloud_reporter כבר רץ. יוצא.")
        return 1
    interval = max(20, int(cfg.get("interval_seconds") or 60))
    log.info("מדווח למרכז הבקרה %s כל %d שניות", cfg["server_url"], interval)
    failures = 0
    while True:
        try:
            if ensure_registered(cfg):
                code, resp, status = send_heartbeat(cfg)
                if once:
                    print(json.dumps({"http": code, "response": resp, "sent": status}, ensure_ascii=False, indent=2))
                    return 0 if code == 200 else 2
                if code == 200:
                    if failures:
                        log.info("החיבור למרכז הבקרה חזר")
                    failures = 0
                    flush_feedback(cfg)
                    try:
                        handle_update(cfg, resp.get("update"), status)
                    except Exception as e:
                        log.exception("טיפול בעדכון נכשל: %s", e)
                else:
                    failures += 1
                    log.warning("מרכז הבקרה החזיר %s: %s", code, resp.get("error"))
        except Exception as e:
            failures += 1
            if isinstance(getattr(e, "reason", e), ssl.SSLError) or isinstance(e, ssl.SSLError):
                e = "%s - בעיית תעודת HTTPS. בדוק שהקובץ src\\certs\\cacert.pem קיים ושהשעון במחשב נכון" % e
            # בלי אינטרנט זה צפוי - לא מציפים את הלוג: רושמים בפעם הראשונה ואז פעם בשעה
            if failures == 1 or failures % max(1, 3600 // interval) == 0:
                log.warning("אין חיבור למרכז הבקרה (%s)", e)
            if once:
                print("שגיאה:", e)
                return 2
        time.sleep(interval if failures < 3 else min(interval * 3, 300))


if __name__ == "__main__":
    sys.exit(main())
