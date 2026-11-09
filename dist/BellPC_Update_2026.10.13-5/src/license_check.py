"""
license_check.py  -  רישיון חתום של "עת רצון" (משותף ל-main.py, web_server.py ו-cloud_reporter.py)

הרישיון הוא "טוקן" שמרכז הבקרה חותם עליו דיגיטלית (Ed25519). המחשב בבית הספר רק מאמת - אין לו את
המפתח הפרטי, ולכן אי אפשר "לתקן" רישיון בעריכת קובץ או בהגדרות.

איך הטוקן מגיע למחשב:
  * אוטומטית: cloud_reporter.py מקבל אותו בכל דיווח למרכז הבקרה ושומר ב-license.json.
  * ידנית (מחשב בלי אינטרנט / מסנן חוסם): היוצר מעתיק "קוד הפעלה" מהדשבורד, ובית הספר מדביק אותו
    בהגדרות ← רישיון. זה אותו טוקן בדיוק.
  הרישיון עובד עד תאריך התפוגה שבו גם בלי שום חיבור.

מדיניות (החלטות היוצר, 13/10/2026):
  * 14 יום לפני התפוגה - אזהרה בממשק.
  * אחרי התפוגה - 7 ימי חסד עם פס אדום, ואז חסימת השמעה מלאה.
  * השעיה ידנית (suspended) - חסימה מיידית.
  * תשלום באיחור - רק הודעה בממשק, בלי חסימה.
  * מחשב שעוד אין בו רישיון חתום (התקנה חדשה / שדרוג) - 30 יום ניסיון מהפעם הראשונה.

בטיחות: המערכת קריטית. כל שגיאה לא צפויה בבדיקה (באג, קובץ פגום באמצע כתיבה) = "לא חוסמים".
חוסמים רק כשיש החלטה ברורה: טוקן תקין שפג / הושעה, או אין טוקן ו-30 יום הניסיון עברו.

Python רגיל בלבד, תואם 3.8 / Windows 7.
"""

import base64
import json
import os
import sys
import threading
from datetime import date, datetime, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)       # ed25519_pure.py נמצא באותה תיקייה

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(BASE_DIR, "src")
LICENSE_FILE = os.path.join(BASE_DIR, "license.json")
PUBLIC_KEY_FILE = os.path.join(SRC_DIR, "license_public_key.txt")
CLOUD_CONFIG_FILE = os.path.join(BASE_DIR, "cloud_config.json")

TOKEN_PREFIX = "ER1"
SIGN_CONTEXT = b"etratzon-license\n"
WARN_DAYS = 14
GRACE_DAYS = 7
NO_LICENSE_TRIAL_DAYS = 30

# מצבים (state):
#   ok         - תקף
#   warning    - תקף, יפוג בקרוב
#   grace      - פג, בתקופת חסד (עדיין מצלצל)
#   blocked    - חסום (פג + עברו ימי החסד / הושעה / אין רישיון וה-30 יום עברו)
#   no_license - אין רישיון חתום, בתוך 30 יום הניסיון
LEVELS = {"ok": "ok", "warning": "warning", "grace": "danger", "blocked": "danger", "no_license": "warning"}

_lock = threading.Lock()


# ------------------------------------------------------------------ קבצים

def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_json_atomic(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    os.replace(tmp, path)


def public_key():
    """המפתח הציבורי של מרכז הבקרה (hex), או None אם עוד לא קיים במחשב."""
    try:
        with open(PUBLIC_KEY_FILE, "r", encoding="utf-8") as f:
            k = f.read().strip().lower()
        bytes.fromhex(k)
        return k if len(k) == 64 else None
    except Exception:
        return None


def save_public_key_if_missing(hex_key):
    """נשמר רק בפעם הראשונה (מהתשובה של מרכז הבקרה ב-HTTPS). מפתח קיים לעולם לא מוחלף."""
    if public_key():
        return False
    try:
        k = (hex_key or "").strip().lower()
        if len(bytes.fromhex(k)) != 32:
            return False
    except Exception:
        return False
    with open(PUBLIC_KEY_FILE, "w", encoding="utf-8") as f:
        f.write(k + "\n")
    return True


def configured_customer_code():
    cfg = _read_json(CLOUD_CONFIG_FILE) or {}
    return (cfg.get("customer_code") or "").strip().upper() or None


# ------------------------------------------------------------------ טוקן

def _b64d(s):
    s = s.strip()
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def parse_token(token, pub_hex=None):
    """מאמת טוקן ומחזיר את התוכן (dict). זורק ValueError עם הסבר בעברית אם לא תקין."""
    import ed25519_pure as ed   # noqa: E402  (נמצא באותה תיקייה)
    token = "".join((token or "").split())       # מדביקים לפעמים עם רווחים / ירידות שורה
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != TOKEN_PREFIX:
        raise ValueError("הקוד לא בפורמט הנכון. ודאו שהעתקתם את כולו.")
    try:
        raw, sig = _b64d(parts[1]), _b64d(parts[2])
    except Exception:
        raise ValueError("הקוד פגום. ודאו שהעתקתם את כולו.")
    pub_hex = pub_hex or public_key()
    if not pub_hex:
        raise ValueError("עוד אין במחשב את מפתח האימות של מרכז הבקרה. צריך חיבור אחד למרכז הבקרה או עדכון תוכנה.")
    if not ed.verify(bytes.fromhex(pub_hex), SIGN_CONTEXT + raw, sig):
        raise ValueError("החתימה על הקוד לא תקינה - הקוד לא הונפק ע\"י מרכז הבקרה או שהשתנה.")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        raise ValueError("תוכן הקוד פגום.")
    if not isinstance(payload, dict) or not payload.get("c") or payload.get("s") not in (
            "active", "trial", "expired", "suspended"):
        raise ValueError("תוכן הקוד לא מוכר.")
    return payload


def _parse_date(s):
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date() if s else None
    except ValueError:
        return None


def fmt(d):
    return d.strftime("%d/%m/%Y") if d else ""


# ------------------------------------------------------------------ חישוב מצב

def evaluate_payload(payload, today):
    """ממפה רישיון תקין (אחרי אימות) למצב. today = date."""
    status = payload.get("s")
    expires = _parse_date(payload.get("e"))
    out = {"status": status, "expires": expires.isoformat() if expires else None,
           "customer_code": payload.get("c"), "issued": payload.get("i"),
           "payment_overdue": payload.get("p") == "overdue", "days_left": None, "block_date": None}
    if status == "suspended":
        out.update(state="blocked", message="הרישיון הושעה ע\"י ספק המערכת - הצלצולים חסומים. יש לפנות לספק.")
        return out
    if status == "expired" and not expires:
        expires = _parse_date(payload.get("i")) or today
        out["expires"] = expires.isoformat()
    if not expires:
        out.update(state="ok", message="רישיון בתוקף ללא הגבלת זמן")
        return out
    days_left = (expires - today).days
    block_date = expires + timedelta(days=GRACE_DAYS + 1)    # היום הראשון שבו חסום
    out.update(days_left=days_left, block_date=block_date.isoformat())
    kind = "תקופת הניסיון" if status == "trial" else "הרישיון"
    if status != "expired" and days_left >= 0:
        if days_left <= WARN_DAYS:
            when = "היום" if days_left == 0 else ("מחר" if days_left == 1 else "בעוד %d ימים" % days_left)
            out.update(state="warning", message="%s מסתיים %s (%s). יש לפנות לספק לחידוש." % (kind, when, fmt(expires)))
        else:
            out.update(state="ok", message="%s בתוקף עד %s" % ("ניסיון" if status == "trial" else "רישיון", fmt(expires)))
        return out
    if today < block_date:
        left = (block_date - today).days
        out.update(state="grace", message="%s הסתיים ב-%s. הצלצולים ייחסמו ב-%s (בעוד %s). יש לפנות לספק לחידוש." % (
            kind, fmt(expires), fmt(block_date), "יום אחד" if left == 1 else "%d ימים" % left))
    else:
        out.update(state="blocked", message="%s הסתיים ב-%s - הצלצולים חסומים. יש לפנות לספק לחידוש." % (kind, fmt(expires)))
    return out


def _no_license_state(lic, today):
    first = _parse_date((lic or {}).get("first_run"))
    if not first:
        # עוד לא נרשם תאריך התחלה (web_server/cloud_reporter רושמים אותו) - לא חוסמים
        return {"state": "no_license", "message": "אין עדיין רישיון חתום במחשב. המערכת פועלת בתקופת ניסיון.",
                "days_left": NO_LICENSE_TRIAL_DAYS, "block_date": None, "expires": None}
    end = first + timedelta(days=NO_LICENSE_TRIAL_DAYS)
    left = (end - today).days
    if left > 0:
        return {"state": "no_license", "days_left": left, "block_date": end.isoformat(), "expires": None,
                "message": "אין עדיין רישיון במחשב. יש %s לחבר את המחשב למרכז הבקרה או להזין קוד הפעלה "
                           "(עד %s)." % ("יום אחד" if left == 1 else "%d ימים" % left, fmt(end - timedelta(days=1)))}
    return {"state": "blocked", "days_left": left, "block_date": end.isoformat(), "expires": None,
            "message": "לא הוזן רישיון במחשב - הצלצולים חסומים. יש להזין קוד הפעלה מהספק בהגדרות ← רישיון."}


_cache = {"key": None, "result": None}


def current_state(today=None):
    """המצב העדכני. נשמר בזיכרון לפי (זמן שינוי הקובץ, תאריך, מפתח), כדי לא לאמת חתימה בכל שנייה.
    מחזיר dict עם state, blocked, level, message ועוד."""
    today = today or date.today()
    try:
        try:
            mtime = os.path.getmtime(LICENSE_FILE)
        except OSError:
            mtime = None
        pub = public_key()
        key = (mtime, today.isoformat(), pub, configured_customer_code())
        with _lock:
            if _cache["key"] == key and _cache["result"] is not None:
                return dict(_cache["result"])
        lic = _read_json(LICENSE_FILE) if mtime is not None else None
        if mtime is not None and lic is None:
            raise ValueError("license.json לא קריא")        # כנראה באמצע כתיבה - לא חוסמים (except למטה)
        token = (lic or {}).get("token")
        res = None
        if token:
            try:
                payload = parse_token(token, pub)
                code = configured_customer_code()
                if code and payload.get("c", "").upper() != code:
                    raise ValueError("הרישיון שייך לקוד לקוח אחר (%s)" % payload.get("c"))
                res = evaluate_payload(payload, today)
                res["source"] = lic.get("source") or ""
                res["received"] = lic.get("received") or ""
            except ValueError as e:
                res = _no_license_state(lic, today)
                res["token_error"] = str(e)
        if res is None:
            res = _no_license_state(lic, today)
        res["blocked"] = res["state"] == "blocked"
        res["level"] = LEVELS.get(res["state"], "warning")
        res.setdefault("payment_overdue", False)
        with _lock:
            _cache.update(key=key, result=dict(res))
        return res
    except Exception as e:      # fail-open: באג או קובץ פגום לא משתיקים בית ספר
        return {"state": "error", "blocked": False, "level": "warning", "payment_overdue": False,
                "message": "בדיקת הרישיון נכשלה (%s) - המערכת ממשיכה לפעול." % e, "error": str(e)}


def ensure_first_run():
    """רושם את תאריך ההתחלה של 30 יום הניסיון, אם עוד אין license.json. מחזיר True אם נוצר."""
    with _lock:
        if os.path.exists(LICENSE_FILE):
            return False
        _write_json_atomic(LICENSE_FILE, {"first_run": date.today().isoformat(), "token": None})
        return True


def install_token(token, source, today=None):
    """מתקין טוקן חדש (מהדיווח או מקוד ידני). מחזיר (ok, message, state).
    קוד ידני ישן מהקיים נדחה, כדי שלא יחזירו רישיון ישן אחרי השעיה."""
    token = "".join((token or "").split())
    try:
        payload = parse_token(token)
    except ValueError as e:
        return False, str(e), None
    code = configured_customer_code()
    if code and payload.get("c", "").upper() != code:
        return False, "הקוד שייך ללקוח אחר (%s), והמחשב הזה רשום כ-%s." % (payload.get("c"), code), None
    with _lock:
        lic = _read_json(LICENSE_FILE) or {}
    old = lic.get("token")
    if old == token:
        return True, "הרישיון כבר מותקן", current_state(today)
    if source == "manual" and old:
        try:
            old_p = parse_token(old)
            if str(payload.get("i") or "") <= str(old_p.get("i") or ""):
                return False, "זה קוד ישן יותר מהרישיון שכבר מותקן במחשב. בקשו קוד עדכני מהספק.", None
        except ValueError:
            pass
    lic.update(token=token, source=source, received=datetime.now().isoformat(timespec="seconds"))
    lic.setdefault("first_run", date.today().isoformat())
    with _lock:
        _write_json_atomic(LICENSE_FILE, lic)
    st = current_state(today)
    return True, st.get("message", ""), st
