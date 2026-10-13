"""
control_center/server.py  -  מרכז הבקרה של "עת רצון" (דשבורד היוצר)

אפליקציה נפרדת לגמרי ממערכת הצלצולים שבבית הספר:
  * לא נוגעת ב-main.py, ב-web_server.py או ב-schedule.json.
  * מקבלת דיווחי מצב ("דופק") ממחשבי הצלצולים דרך src/cloud_reporter.py.
  * שומרת הכל בקובץ SQLite אחד: control_center/data/control_center.db

תפקידים (התשתית לדשבורד בית ספר / רשת שייבנה בשלב 2):
  owner          - היוצר: רואה ומנהל את כל הלקוחות
  network_admin  - מנהל רשת: רואה רק את בתי הספר של הרשת שלו (צפייה)
  school_admin   - בית ספר: רואה רק את עצמו (צפייה)
כל שאילתה על לקוחות עוברת דרך scope_clause(), כך שהרחבה לשלב 2 לא דורשת לשנות את הלוגיקה.

הרצה:
  python control_center/server.py            (פורט 8600)
  python control_center/server.py --demo     (יוצר מסד נתונים לדוגמה עם 24 לקוחות)
"""

import argparse
import hashlib
import io
import json
import os
import random
import re
import secrets
import sqlite3
import sys
import threading
import zipfile
from datetime import datetime, timedelta, timezone

from flask import Flask, g, send_file, has_request_context, jsonify, request, send_from_directory, session

import ed25519_pure as ed
from werkzeug.security import check_password_hash, generate_password_hash

APP_VERSION = "2026.10.05-cc1"
PORT = 8600
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_FILE = os.path.join(DATA_DIR, "control_center.db")
SECRET_FILE = os.path.join(DATA_DIR, "secret.key")
STATIC_DIR = os.path.join(BASE_DIR, "static")
DEMO_FILE = os.path.join(DATA_DIR, "demo_clients.json")
RELEASES_DIR = os.path.join(DATA_DIR, "releases")
PINNED_KEY_FILE = os.path.join(DATA_DIR, "update_public_key.txt")   # המפתח הציבורי של העדכון הראשון; כל עדכון חייב להתאים
# מצבי עדכון אצל לקוח (מה מחשב הצלצולים מדווח)
ROLLOUT_STATUSES = {
    "pending": "ממתין למחשב", "downloading": "מוריד", "downloaded": "הורד, ממתין לסוף היום",
    "installing": "מתקין", "installed": "הותקן", "failed": "נכשל", "rolled_back": "נכשל, חזר לגרסה הקודמת",
    "cancelled": "בוטל",
}
ROLLOUT_OPEN = ("pending", "downloading", "downloaded", "installing")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
BACKUP_KEEP = 14                 # גיבוי יומי, שומרים שבועיים
LOGIN_MAX_FAILS = 5              # אחרי 5 סיסמאות שגויות לאותו משתמש - חסימה זמנית
LOGIN_BLOCK_MINUTES = 10

# גרסת התוכנה העדכנית ללקוחות (להשוואה מול מה שמדווח מהמחשבים)
LATEST_CLIENT_VERSION = "2026.10.05-3"

ONLINE_SECONDS = 180            # דיווח כל 60 שנ'; שלושה דיווחים שהוחמצו = לא מחובר
OFFLINE_CRITICAL_MINUTES = 10   # לא מחובר יותר מזה בזמן שעות הפעילות = תקלה קריטית
CLOCK_DRIFT_WARN_SECONDS = 5    # המערכת חייבת דיוק לשנייה - סטייה בשעון המחשב היא תקלה
LICENSE_WARN_DAYS = 30
HEARTBEAT_KEEP_DAYS = 14

ROLES = ("owner", "network_admin", "school_admin")
SECTORS = ["ממלכתי", "ממלכתי-דתי", "חרדי", "ערבי", "דרוזי", "בדואי", "פרטי", "אחר"]
INSTITUTION_TYPES = ["גן", "יסודי", "חטיבת ביניים", "תיכון", "שש-שנתי", "ישיבה / אולפנה", "אחר"]
LICENSE_STATUSES = {"active": "פעיל", "trial": "ניסיון", "expired": "פג תוקף", "suspended": "מושעה"}
PAYMENT_STATUSES = {"paid": "שולם", "pending": "ממתין", "overdue": "באיחור", "free": "ללא חיוב"}
CUSTOMER_STATUSES = {"pending": "ממתין לאישור", "active": "פעיל", "archived": "בארכיון"}
FEEDBACK_STATUSES = {"new": "חדש", "in_progress": "בטיפול", "done": "טופל"}
# אותיות וספרות בלי תווים שמתבלבלים בהקלדה (0/O, 1/I/L)
CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 30 * 1024 * 1024      # חבילת עדכון
_db_lock = threading.Lock()


# ------------------------------------------------------------------ זמן

def utcnow():
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def parse_iso(s):
    if not s:
        return None
    try:
        s = str(s).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


# ------------------------------------------------------------------ מסד נתונים

SCHEMA = """
CREATE TABLE IF NOT EXISTS networks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    contact_name TEXT DEFAULT '', phone TEXT DEFAULT '', email TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    api_key_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    name TEXT NOT NULL,
    sector TEXT DEFAULT '', institution_type TEXT DEFAULT '',
    country TEXT DEFAULT 'ישראל', city TEXT DEFAULT '', address TEXT DEFAULT '',
    contact_name TEXT DEFAULT '', phone TEXT DEFAULT '', email TEXT DEFAULT '',
    network_id INTEGER REFERENCES networks(id),
    plan TEXT DEFAULT 'basic',
    license_status TEXT DEFAULT 'trial',
    license_expires TEXT,
    payment_status TEXT DEFAULT 'pending',
    monthly_price REAL DEFAULT 0,
    notes TEXT DEFAULT '',
    machine_id TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    last_seen TEXT,
    last_payload TEXT
);
CREATE TABLE IF NOT EXISTS heartbeats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    received_at TEXT NOT NULL,
    ok INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_hb_customer_time ON heartbeats(customer_id, received_at);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    created_at TEXT NOT NULL,
    category TEXT DEFAULT 'general',
    rating INTEGER,
    text TEXT NOT NULL,
    contact TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'new',
    owner_note TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    display_name TEXT DEFAULT '',
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL,
    network_id INTEGER REFERENCES networks(id),
    customer_id INTEGER REFERENCES customers(id),
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS releases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version TEXT NOT NULL UNIQUE,
    notes TEXT DEFAULT '',
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size INTEGER NOT NULL,
    signature TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    uploaded_by TEXT
);
CREATE TABLE IF NOT EXISTS rollouts (
    release_id INTEGER NOT NULL REFERENCES releases(id),
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    status TEXT NOT NULL DEFAULT 'pending',
    message TEXT DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (release_id, customer_id)
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    username TEXT,
    customer_id INTEGER,
    action TEXT NOT NULL,
    details TEXT DEFAULT ''
);
"""


def connect():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_FILE, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def db():
    if "db" not in g:
        g.db = connect()
    return g.db


@app.teardown_appcontext
def _close_db(exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db():
    conn = connect()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def load_secret_key():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(SECRET_FILE):
        with open(SECRET_FILE, "w") as f:
            f.write(secrets.token_hex(32))
    with open(SECRET_FILE) as f:
        return f.read().strip()


def hash_key(api_key):
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def new_customer_code(conn):
    """קוד לקוח ייחודי, קל להקראה בטלפון: ER-XXXX-XXXX"""
    while True:
        raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
        code = "ER-%s-%s" % (raw[:4], raw[4:])
        if not conn.execute("SELECT 1 FROM customers WHERE code=?", (code,)).fetchone():
            return code


def audit(conn, action, details="", customer_id=None, username=None):
    if username is None:
        username = session.get("username") if has_request_context() else None
    conn.execute("INSERT INTO audit(at, username, customer_id, action, details) VALUES (?,?,?,?,?)",
                 (iso(utcnow()), username, customer_id, action, details))


# ------------------------------------------------------------------ משתמשים והרשאות

def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    row = db().execute("SELECT * FROM users WHERE id=? AND active=1", (uid,)).fetchone()
    return dict(row) if row else None


def api_error(msg, code=400):
    return jsonify({"ok": False, "error": msg}), code


def login_required(roles=None):
    def deco(fn):
        def wrapper(*a, **kw):
            user = current_user()
            if not user:
                return api_error("נדרשת התחברות", 401)
            if roles and user["role"] not in roles:
                return api_error("אין הרשאה לפעולה זו", 403)
            g.user = user
            return fn(*a, **kw)
        wrapper.__name__ = fn.__name__
        return wrapper
    return deco


def scope_clause(user, alias="c"):
    """מחזיר (תנאי SQL, פרמטרים) שמגביל את הלקוחות שהמשתמש רשאי לראות."""
    if user["role"] == "owner":
        return "1=1", []
    if user["role"] == "network_admin":
        return "%s.network_id = ?" % alias, [user["network_id"] or -1]
    return "%s.id = ?" % alias, [user["customer_id"] or -1]


def customer_in_scope(user, customer_id):
    cond, params = scope_clause(user)
    return db().execute("SELECT 1 FROM customers c WHERE c.id=? AND " + cond,
                        [customer_id] + params).fetchone() is not None


# ------------------------------------------------------------------ חישוב מצב והתראות

def compute_health(c, now=None, update_info=None):
    """מחשב מצב ורשימת התראות ללקוח, מתוך הרשומה שלו והדיווח האחרון. לא נשמר - תמיד עדכני."""
    now = now or utcnow()
    p = json.loads(c["last_payload"]) if c["last_payload"] else {}
    last_seen = parse_iso(c["last_seen"])
    alerts = []

    def add(level, kind, text):
        alerts.append({"level": level, "kind": kind, "text": text})

    if c["status"] == "pending":
        add("info", "pending", "נרשם בהתקנה וממתין לאישור")

    # חיבור
    connectivity = "never"
    if last_seen:
        age = (now - last_seen).total_seconds()
        if age <= ONLINE_SECONDS:
            connectivity = "online"
        else:
            connectivity = "offline"
            in_hours = False
            win = p.get("day_window") or {}
            w_from, w_to = parse_iso(win.get("from")), parse_iso(win.get("to"))
            if w_from and w_to:
                in_hours = (w_from - timedelta(minutes=15)) <= now <= (w_to + timedelta(minutes=15))
            mins = int(age // 60)
            if in_hours and mins >= OFFLINE_CRITICAL_MINUTES:
                add("critical", "offline", "לא מדווח %s, בזמן שעות הצלצולים" % human_minutes(mins))
            elif mins >= 60 * 24:
                add("warning", "offline", "לא מדווח %s" % human_minutes(mins))
    elif c["status"] == "active":
        add("warning", "never", "לא דיווח מעולם - כנראה הדיווח לא הוגדר במחשב")

    if p and connectivity == "online":
        if p.get("engine_alive") is False:
            add("critical", "engine", "מנוע ההשמעה (main.py) לא רץ - הצלצולים לא יישמעו")
        if p.get("license_valid") is False:
            add("critical", "license_pc", "הרישיון במחשב לא תקף - ההשמעה חסומה")
        st = p.get("system_status")
        if st == "disabled":
            if p.get("disabled_scope") == "day":
                add("info", "disabled_day", "הושבת להיום בלבד")
            else:
                add("warning", "disabled", "המערכת מושבתת עד הפעלה ידנית")
        drift = p.get("clock_drift_s")
        if isinstance(drift, (int, float)) and abs(drift) > CLOCK_DRIFT_WARN_SECONDS:
            add("warning", "clock", "שעון המחשב סוטה ב-%d שניות" % round(drift))
        missing = p.get("missing_audio") or []
        if missing:
            add("warning", "audio", "%d קבצי שמע חסרים (%s)" % (len(missing), ", ".join(missing[:3])))
        errs = p.get("errors_24h") or 0
        if errs:
            add("warning", "errors", "%d שגיאות בלוג ב-24 השעות האחרונות" % errs)
        if p.get("web_alive") is False:
            add("warning", "web", "שרת הניהול (web_server.py) לא רץ - אי אפשר לנהל או לצלצל ידנית. "
                                  "הצלצולים המתוזמנים ממשיכים")
        v = p.get("version")
        if v and v != latest_version():
            add("info", "version", "גרסה ישנה: %s" % v)

    # רישיון ותשלום - מהרשומה במרכז
    exp = c["license_expires"]
    if c["license_status"] in ("expired", "suspended"):
        add("critical", "license", "רישיון %s" % LICENSE_STATUSES[c["license_status"]])
    elif exp:
        try:
            days = (datetime.strptime(exp, "%Y-%m-%d").date() - now.date()).days
            if days < 0:
                add("critical", "license", "הרישיון פג לפני %d ימים" % -days)
            elif days <= LICENSE_WARN_DAYS:
                add("warning", "license", "הרישיון יפוג בעוד %d ימים" % days)
        except ValueError:
            pass
    if c["payment_status"] == "overdue":
        add("warning", "payment", "תשלום באיחור")
    upd = update_info.get(c["id"]) if update_info else None
    if upd and upd["status"] in ("failed", "rolled_back"):
        add("warning", "update", "עדכון לגרסה %s %s" % (upd["version"],
            "נכשל" if upd["status"] == "failed" else "נכשל והמחשב חזר לגרסה הקודמת"))

    order = {"critical": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda a: order[a["level"]])
    if c["status"] == "archived":
        health = "archived"
    elif c["status"] == "pending" and not any(a["level"] == "critical" for a in alerts):
        health = "pending"
    elif any(a["level"] == "critical" for a in alerts):
        health = "critical"
    elif any(a["level"] == "warning" for a in alerts):
        health = "warning"
    elif connectivity == "online":
        health = "ok"
    else:
        health = "idle" if connectivity == "offline" else "never"
    return {"health": health, "connectivity": connectivity, "alerts": alerts, "payload": p}


def human_minutes(mins):
    if mins < 60:
        return "%d דק'" % mins
    if mins < 60 * 24:
        return "%d שע'" % (mins // 60)
    return "%d ימים" % (mins // (60 * 24))


def latest_updates():
    """לכל לקוח: מצב העדכון האחרון שנשלח אליו (או None)."""
    rows = db().execute(
        "SELECT r.customer_id, r.status, r.message, r.updated_at, rel.version, rel.id AS release_id FROM rollouts r "
        "JOIN releases rel ON rel.id = r.release_id WHERE r.release_id = (SELECT MAX(r2.release_id) FROM rollouts r2 "
        "WHERE r2.customer_id = r.customer_id)").fetchall()
    return {r["customer_id"]: dict(r, status_label=ROLLOUT_STATUSES.get(r["status"], r["status"])) for r in rows}


def latest_version():
    """הגרסה העדכנית: העדכון האחרון שהותקן בהצלחה, ואם אין כזה - הקבוע בקוד."""
    if "latest_version" not in g:
        # הגרסה האחרונה שהותקנה בהצלחה לפחות אצל לקוח אחד (גרסה שנכשלה אצל כולם לא נחשבת "עדכנית")
        row = db().execute("SELECT rel.version FROM releases rel WHERE rel.status='active' AND EXISTS (SELECT 1 FROM "
                           "rollouts ro WHERE ro.release_id=rel.id AND ro.status='installed') ORDER BY rel.id DESC "
                           "LIMIT 1").fetchone()
        g.latest_version = row["version"] if row else LATEST_CLIENT_VERSION
    return g.latest_version


def customer_public(c, health=None, update_info=None):
    d = {k: c[k] for k in c.keys() if k not in ("api_key_hash", "last_payload")}
    d["update"] = (update_info or {}).get(c["id"])
    h = health or compute_health(c)
    d.update({"health": h["health"], "connectivity": h["connectivity"], "alerts": h["alerts"]})
    p = h["payload"]
    d["pc"] = {
        "version": p.get("version"), "next_event": p.get("next_event"),
        "events_today": p.get("events_today"), "system_status": p.get("system_status"),
        "engine_alive": p.get("engine_alive"), "os": p.get("os"),
    }
    return d


# ------------------------------------------------------------------ דפים סטטיים

@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/robots.txt")
def robots():
    return app.response_class("User-agent: *\nDisallow: /\n", mimetype="text/plain")


@app.after_request
def _no_index(resp):
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"     # לא להופיע במנועי חיפוש
    return resp


@app.route("/static/<path:name>")
def static_files(name):
    return send_from_directory(STATIC_DIR, name)


@app.route("/api/version")
def api_version():
    return jsonify({"version": APP_VERSION})


# ------------------------------------------------------------------ התחברות

@app.route("/api/me")
def api_me():
    has_users = db().execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None
    user = current_user()
    if not user:
        return jsonify({"ok": True, "user": None, "needs_setup": not has_users,
                        "setup_in_browser": app.config.get("ALLOW_WEB_SETUP", True)})
    return jsonify({"ok": True, "needs_setup": False, "user": {
        k: user[k] for k in ("id", "username", "display_name", "role", "network_id", "customer_id")}})


@app.route("/api/setup", methods=["POST"])
def api_setup():
    """הפעלה ראשונה: יצירת חשבון היוצר. אפשרי רק כשאין אף משתמש."""
    if not app.config.get("ALLOW_WEB_SETUP", True):
        # באתר ציבורי מי שמגיע ראשון היה הופך ליוצר - שם יוצרים את החשבון מהמסוף (--create-owner)
        return api_error("יצירת חשבון היוצר נעשית מהמסוף בשרת", 403)
    conn = db()
    if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        return api_error("כבר קיים משתמש", 409)
    body = request.get_json(force=True) or {}
    username = (body.get("username") or "").strip()
    pw = body.get("password") or ""
    if len(username) < 3 or len(pw) < 8:
        return api_error("שם משתמש לפחות 3 תווים וסיסמה לפחות 8 תווים")
    conn.execute("INSERT INTO users(username, display_name, password_hash, role, created_at) VALUES (?,?,?,?,?)",
                 (username, body.get("display_name") or username, generate_password_hash(pw), "owner", iso(utcnow())))
    uid = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()["id"]
    session.clear()
    session["uid"], session["username"] = uid, username
    audit(conn, "setup", "נוצר חשבון יוצר")
    conn.commit()
    return jsonify({"ok": True})


_login_fails = {}   # username -> [מספר כישלונות, זמן החסימה]


@app.route("/api/login", methods=["POST"])
def api_login():
    body = request.get_json(force=True) or {}
    username = (body.get("username") or "").strip()
    key = username.lower()
    fails, blocked_until = _login_fails.get(key, (0, None))
    if blocked_until and utcnow() < blocked_until:
        mins = int((blocked_until - utcnow()).total_seconds() // 60) + 1
        return api_error("יותר מדי ניסיונות. נסה שוב בעוד %d דק'" % mins, 429)
    row = db().execute("SELECT * FROM users WHERE username=? AND active=1", (username,)).fetchone()
    if not row or not check_password_hash(row["password_hash"], body.get("password") or ""):
        fails += 1
        _login_fails[key] = (0, utcnow() + timedelta(minutes=LOGIN_BLOCK_MINUTES)) if fails >= LOGIN_MAX_FAILS \
            else (fails, None)
        return api_error("שם משתמש או סיסמה שגויים", 401)
    _login_fails.pop(key, None)
    session.clear()
    session["uid"], session["username"] = row["id"], row["username"]
    session.permanent = True
    return jsonify({"ok": True})


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"ok": True})


# ------------------------------------------------------------------ סקירה כללית

@app.route("/api/overview")
@login_required()
def api_overview():
    cond, params = scope_clause(g.user)
    rows = db().execute("SELECT c.* FROM customers c WHERE c.status != 'archived' AND " + cond, params).fetchall()
    now = utcnow()
    counts = {"total": 0, "online": 0, "critical": 0, "warning": 0, "pending": 0, "never": 0,
              "license_soon": 0, "overdue": 0, "mrr": 0.0}
    alerts, versions, sectors = [], {}, {}
    upd = latest_updates()
    for c in rows:
        h = compute_health(c, now, upd)
        counts["total"] += 1
        counts["online"] += h["connectivity"] == "online"
        counts["never"] += h["connectivity"] == "never"
        counts["critical"] += h["health"] == "critical"
        counts["warning"] += h["health"] == "warning"
        counts["pending"] += c["status"] == "pending"
        counts["overdue"] += c["payment_status"] == "overdue"
        counts["license_soon"] += any(a["kind"] == "license" for a in h["alerts"])
        if c["license_status"] in ("active",) and c["payment_status"] in ("paid", "pending"):
            counts["mrr"] += c["monthly_price"] or 0
        v = h["payload"].get("version")
        if v:
            versions[v] = versions.get(v, 0) + 1
        sectors[c["sector"] or "לא צוין"] = sectors.get(c["sector"] or "לא צוין", 0) + 1
        for a in h["alerts"]:
            if a["level"] in ("critical", "warning"):
                alerts.append(dict(a, customer_id=c["id"], customer=c["name"], code=c["code"],
                                   last_seen=c["last_seen"]))
    alerts.sort(key=lambda a: (0 if a["level"] == "critical" else 1, a["customer"]))

    fb_new = 0
    if g.user["role"] == "owner":
        fb_new = db().execute("SELECT COUNT(*) n FROM feedback WHERE status='new'").fetchone()["n"]

    # זמינות כוללת ב-24 שעות: לכל שעה, כמה לקוחות דיווחו לפחות פעם אחת
    since = now - timedelta(hours=24)
    hb = db().execute(
        "SELECT substr(h.received_at,1,13) hr, COUNT(DISTINCT h.customer_id) n FROM heartbeats h "
        "JOIN customers c ON c.id=h.customer_id WHERE h.received_at >= ? AND c.status != 'archived' AND "
        + cond + " GROUP BY hr", [iso(since)] + params).fetchall()
    by_hour = {r["hr"]: r["n"] for r in hb}
    hours = []
    for i in range(24, 0, -1):
        t = now - timedelta(hours=i - 1)
        key = t.strftime("%Y-%m-%dT%H")
        hours.append({"hour": key + ":00:00Z", "reporting": by_hour.get(key, 0)})

    return jsonify({"ok": True, "counts": counts, "alerts": alerts[:200], "versions": versions,
                    "sectors": sectors, "hours": hours, "feedback_new": fb_new,
                    "latest_version": latest_version(), "now": iso(now)})


# ------------------------------------------------------------------ לקוחות

EDITABLE_FIELDS = ("name", "sector", "institution_type", "country", "city", "address", "contact_name",
                   "phone", "email", "network_id", "plan", "license_status", "license_expires",
                   "payment_status", "monthly_price", "notes", "status")


FIELD_LABELS = {"name": "שם", "sector": "מגזר", "institution_type": "סוג מוסד", "country": "ארץ", "city": "עיר",
                "address": "כתובת", "contact_name": "איש קשר", "phone": "טלפון", "email": "דוא\"ל", "network_id": "רשת",
                "plan": "מסלול", "license_status": "רישיון", "license_expires": "רישיון עד", "payment_status": "תשלום",
                "monthly_price": "מחיר", "notes": "הערות", "status": "סטטוס"}


def clean_customer_fields(body):
    out = {}
    for k in EDITABLE_FIELDS:
        if k not in body:
            continue
        v = body[k]
        if k == "network_id":
            v = int(v) if v not in (None, "", "null") else None
        elif k == "monthly_price":
            try:
                v = float(v or 0)
            except (TypeError, ValueError):
                raise ValueError("מחיר חודשי לא תקין")
        elif k == "license_expires":
            v = (v or "").strip() or None
            if v:
                try:
                    datetime.strptime(v, "%Y-%m-%d")
                except ValueError:
                    raise ValueError("תאריך רישיון לא תקין (צריך להיות YYYY-MM-DD)")
        elif k == "license_status" and v not in LICENSE_STATUSES:
            raise ValueError("סטטוס רישיון לא מוכר")
        elif k == "payment_status" and v not in PAYMENT_STATUSES:
            raise ValueError("סטטוס תשלום לא מוכר")
        elif k == "status" and v not in CUSTOMER_STATUSES:
            raise ValueError("סטטוס לקוח לא מוכר")
        else:
            v = (str(v) if v is not None else "").strip()
        out[k] = v
    if "name" in out and not out["name"]:
        raise ValueError("שם המוסד חובה")
    return out


@app.route("/api/customers")
@login_required()
def api_customers():
    cond, params = scope_clause(g.user)
    sql = ("SELECT c.*, n.name AS network_name FROM customers c LEFT JOIN networks n ON n.id=c.network_id "
           "WHERE " + cond)
    if request.args.get("include_archived") != "1":
        sql += " AND c.status != 'archived'"
    rows = db().execute(sql + " ORDER BY c.name", params).fetchall()
    now = utcnow()
    upd = latest_updates()
    out = []
    for c in rows:
        d = customer_public(c, compute_health(c, now, upd), upd)
        d["network_name"] = c["network_name"]
        out.append(d)
    return jsonify({"ok": True, "customers": out})


@app.route("/api/customers", methods=["POST"])
@login_required(roles=("owner",))
def api_create_customer():
    try:
        fields = clean_customer_fields(request.get_json(force=True) or {})
    except ValueError as e:
        return api_error(str(e))
    if not fields.get("name"):
        return api_error("שם המוסד חובה")
    conn = db()
    with _db_lock:
        code = new_customer_code(conn)
        api_key = secrets.token_urlsafe(24)
        fields.setdefault("status", "active")
        cols = ["code", "api_key_hash", "created_at"] + list(fields.keys())
        vals = [code, hash_key(api_key), iso(utcnow())] + list(fields.values())
        cur = conn.execute("INSERT INTO customers(%s) VALUES (%s)" % (",".join(cols), ",".join("?" * len(cols))), vals)
        audit(conn, "customer_create", fields["name"], cur.lastrowid)
        conn.commit()
    return jsonify({"ok": True, "id": cur.lastrowid, "code": code, "api_key": api_key,
                    "config": client_config_text(code, api_key)})


def client_config_text(code, api_key):
    return json.dumps({"server_url": app.config.get("PUBLIC_URL") or request.host_url.rstrip("/"), "customer_code": code,
                       "api_key": api_key, "interval_seconds": 60}, ensure_ascii=False, indent=2)


@app.route("/api/customers/<int:cid>")
@login_required()
def api_customer(cid):
    if not customer_in_scope(g.user, cid):
        return api_error("לא נמצא", 404)
    c = db().execute("SELECT c.*, n.name AS network_name FROM customers c LEFT JOIN networks n "
                     "ON n.id=c.network_id WHERE c.id=?", (cid,)).fetchone()
    now = utcnow()
    upd = latest_updates()
    h = compute_health(c, now, upd)
    d = customer_public(c, h, upd)
    d["network_name"] = c["network_name"]
    d["payload"] = h["payload"]
    # רצועת זמינות: 7 ימים, משבצת לכל שעה (1 = דיווח, 0 = לא)
    since = now - timedelta(days=7)
    rows = db().execute("SELECT DISTINCT substr(received_at,1,13) hr FROM heartbeats WHERE customer_id=? "
                        "AND received_at >= ?", (cid, iso(since))).fetchall()
    seen = {r["hr"] for r in rows}
    strip = []
    for i in range(7 * 24, 0, -1):
        t = now - timedelta(hours=i - 1)
        strip.append(1 if t.strftime("%Y-%m-%dT%H") in seen else 0)
    d["availability"] = {"hours": strip, "end": iso(now)}
    if g.user["role"] == "owner":
        d["feedback"] = [dict(r) for r in db().execute(
            "SELECT * FROM feedback WHERE customer_id=? ORDER BY created_at DESC LIMIT 20", (cid,))]
        d["audit"] = [dict(r) for r in db().execute(
            "SELECT * FROM audit WHERE customer_id=? ORDER BY id DESC LIMIT 30", (cid,))]
    return jsonify({"ok": True, "customer": d})


@app.route("/api/customers/<int:cid>", methods=["PATCH"])
@login_required(roles=("owner",))
def api_update_customer(cid):
    conn = db()
    old = conn.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()
    if not old:
        return api_error("לא נמצא", 404)
    try:
        fields = clean_customer_fields(request.get_json(force=True) or {})
    except ValueError as e:
        return api_error(str(e))
    if not fields:
        return jsonify({"ok": True})
    changes = ["%s: %s → %s" % (FIELD_LABELS.get(k, k), old[k] if old[k] not in (None, "") else "—", v if v not in (None, "") else "—")
               for k, v in fields.items() if str(old[k] if old[k] is not None else "") != str(v if v is not None else "")]
    conn.execute("UPDATE customers SET %s WHERE id=?" % ",".join("%s=?" % k for k in fields),
                 list(fields.values()) + [cid])
    if changes:
        audit(conn, "customer_update", "; ".join(changes), cid)
    conn.commit()
    return jsonify({"ok": True})


@app.route("/api/customers/<int:cid>/rotate-key", methods=["POST"])
@login_required(roles=("owner",))
def api_rotate_key(cid):
    conn = db()
    c = conn.execute("SELECT code FROM customers WHERE id=?", (cid,)).fetchone()
    if not c:
        return api_error("לא נמצא", 404)
    api_key = secrets.token_urlsafe(24)
    conn.execute("UPDATE customers SET api_key_hash=? WHERE id=?", (hash_key(api_key), cid))
    audit(conn, "rotate_key", "הונפק מפתח חיבור חדש (הישן בוטל)", cid)
    conn.commit()
    return jsonify({"ok": True, "code": c["code"], "api_key": api_key, "config": client_config_text(c["code"], api_key)})


# ------------------------------------------------------------------ רשתות ומשתמשים (תשתית לשלב 2)

@app.route("/api/networks")
@login_required()
def api_networks():
    if g.user["role"] == "owner":
        rows = db().execute("SELECT n.*, (SELECT COUNT(*) FROM customers c WHERE c.network_id=n.id AND "
                            "c.status!='archived') AS customers FROM networks n ORDER BY n.name").fetchall()
    else:
        rows = db().execute("SELECT n.*, 0 AS customers FROM networks n WHERE n.id=?", (g.user["network_id"] or -1,)).fetchall()
    return jsonify({"ok": True, "networks": [dict(r) for r in rows]})


@app.route("/api/networks", methods=["POST"])
@login_required(roles=("owner",))
def api_create_network():
    body = request.get_json(force=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return api_error("שם הרשת חובה")
    conn = db()
    try:
        cur = conn.execute("INSERT INTO networks(name, contact_name, phone, email, created_at) VALUES (?,?,?,?,?)",
                           (name, body.get("contact_name", ""), body.get("phone", ""), body.get("email", ""), iso(utcnow())))
    except sqlite3.IntegrityError:
        return api_error("כבר קיימת רשת בשם הזה")
    audit(conn, "network_create", name)
    conn.commit()
    return jsonify({"ok": True, "id": cur.lastrowid})


@app.route("/api/users")
@login_required(roles=("owner",))
def api_users():
    rows = db().execute("SELECT u.id, u.username, u.display_name, u.role, u.network_id, u.customer_id, u.active, "
                        "u.created_at, n.name AS network_name, c.name AS customer_name FROM users u "
                        "LEFT JOIN networks n ON n.id=u.network_id LEFT JOIN customers c ON c.id=u.customer_id "
                        "ORDER BY u.role, u.username").fetchall()
    return jsonify({"ok": True, "users": [dict(r) for r in rows]})


@app.route("/api/users", methods=["POST"])
@login_required(roles=("owner",))
def api_create_user():
    body = request.get_json(force=True) or {}
    username = (body.get("username") or "").strip()
    role = body.get("role")
    pw = body.get("password") or ""
    if role not in ROLES:
        return api_error("תפקיד לא מוכר")
    if len(username) < 3 or len(pw) < 8:
        return api_error("שם משתמש לפחות 3 תווים וסיסמה לפחות 8 תווים")
    network_id = int(body["network_id"]) if body.get("network_id") else None
    customer_id = int(body["customer_id"]) if body.get("customer_id") else None
    if role == "network_admin" and not network_id:
        return api_error("מנהל רשת חייב להיות משויך לרשת")
    if role == "school_admin" and not customer_id:
        return api_error("משתמש בית ספר חייב להיות משויך ללקוח")
    conn = db()
    try:
        conn.execute("INSERT INTO users(username, display_name, password_hash, role, network_id, customer_id, created_at) "
                     "VALUES (?,?,?,?,?,?,?)", (username, body.get("display_name") or username,
                                                 generate_password_hash(pw), role, network_id, customer_id, iso(utcnow())))
    except sqlite3.IntegrityError:
        return api_error("שם המשתמש תפוס")
    audit(conn, "user_create", "%s (%s)" % (username, role), customer_id)
    conn.commit()
    return jsonify({"ok": True})


# ------------------------------------------------------------------ משוב

@app.route("/api/feedback")
@login_required(roles=("owner",))
def api_feedback():
    rows = db().execute("SELECT f.*, c.name AS customer, c.code FROM feedback f JOIN customers c ON c.id=f.customer_id "
                        "ORDER BY CASE f.status WHEN 'new' THEN 0 WHEN 'in_progress' THEN 1 ELSE 2 END, f.created_at DESC "
                        "LIMIT 500").fetchall()
    return jsonify({"ok": True, "feedback": [dict(r) for r in rows]})


@app.route("/api/feedback/<int:fid>", methods=["PATCH"])
@login_required(roles=("owner",))
def api_update_feedback(fid):
    body = request.get_json(force=True) or {}
    conn = db()
    row = conn.execute("SELECT * FROM feedback WHERE id=?", (fid,)).fetchone()
    if not row:
        return api_error("לא נמצא", 404)
    status = body.get("status", row["status"])
    if status not in FEEDBACK_STATUSES:
        return api_error("סטטוס לא מוכר")
    conn.execute("UPDATE feedback SET status=?, owner_note=? WHERE id=?",
                 (status, body.get("owner_note", row["owner_note"]), fid))
    if status != row["status"]:
        audit(conn, "feedback_status", "משוב #%d: %s" % (fid, FEEDBACK_STATUSES[status]), row["customer_id"])
    conn.commit()
    return jsonify({"ok": True})


@app.route("/api/audit")
@login_required(roles=("owner",))
def api_audit():
    rows = db().execute("SELECT a.*, c.name AS customer FROM audit a LEFT JOIN customers c ON c.id=a.customer_id "
                        "ORDER BY a.id DESC LIMIT 300").fetchall()
    return jsonify({"ok": True, "audit": [dict(r) for r in rows]})


@app.route("/api/meta")
@login_required()
def api_meta():
    return jsonify({"ok": True, "sectors": SECTORS, "institution_types": INSTITUTION_TYPES,
                    "license_statuses": LICENSE_STATUSES, "payment_statuses": PAYMENT_STATUSES,
                    "customer_statuses": CUSTOMER_STATUSES, "feedback_statuses": FEEDBACK_STATUSES,
                    "latest_version": latest_version()})


# ------------------------------------------------------------------ עדכוני תוכנה (מרחוק)

def pinned_public_key():
    if os.path.exists(PINNED_KEY_FILE):
        with open(PINNED_KEY_FILE) as f:
            return f.read().strip().lower()
    return None


@app.route("/api/releases")
@login_required(roles=("owner",))
def api_releases():
    conn = db()
    rels = [dict(r) for r in conn.execute("SELECT * FROM releases ORDER BY id DESC").fetchall()]
    for r in rels:
        r.pop("signature", None)
        rows = conn.execute("SELECT ro.customer_id, ro.status, ro.message, ro.updated_at, c.name, c.code FROM rollouts ro "
                            "JOIN customers c ON c.id = ro.customer_id WHERE ro.release_id=? ORDER BY c.name",
                            (r["id"],)).fetchall()
        r["targets"] = [dict(x, status_label=ROLLOUT_STATUSES.get(x["status"], x["status"])) for x in rows]
        counts = {}
        for x in rows:
            counts[x["status"]] = counts.get(x["status"], 0) + 1
        r["counts"] = counts
    return jsonify({"ok": True, "releases": rels, "statuses": ROLLOUT_STATUSES, "public_key": pinned_public_key()})


@app.route("/api/releases", methods=["POST"])
@login_required(roles=("owner",))
def api_upload_release():
    """העלאת חבילת עדכון: קובץ ה-zip + קובץ החתימה (‎.sig.json) שיצר build_update.bat."""
    pkg = request.files.get("package")
    sigf = request.files.get("signature")
    if not pkg or not sigf:
        return api_error("צריך להעלות את שני הקבצים: החבילה (zip) והחתימה (sig.json)")
    try:
        sig = json.loads(sigf.read().decode("utf-8"))
        version = str(sig["version"]).strip()
        signature = bytes.fromhex(sig["signature"])
        public = bytes.fromhex(sig["public_key"])
    except Exception:
        return api_error("קובץ החתימה לא תקין")
    if not re.match(r"^[0-9A-Za-z._-]{1,40}$", version):
        return api_error("מספר גרסה לא תקין")
    data = pkg.read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != str(sig.get("sha256", "")).lower():
        return api_error("החבילה לא תואמת לקובץ החתימה (ייתכן שהועלו קבצים של גרסאות שונות)")
    if not ed.verify(public, ed.update_message(version, digest), signature):
        return api_error("החתימה לא תקינה")
    pinned = pinned_public_key()
    if pinned and pinned != public.hex():
        return api_error("החבילה נחתמה במפתח אחר מהעדכונים הקודמים. מחשבי בתי הספר ידחו אותה.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            manifest = json.loads(z.read("manifest.json").decode("utf-8"))
    except Exception:
        return api_error("החבילה לא תקינה (חסר manifest.json)")
    if manifest.get("version") != version:
        return api_error("הגרסה בחבילה שונה מהגרסה בחתימה")
    conn = db()
    if conn.execute("SELECT 1 FROM releases WHERE version=?", (version,)).fetchone():
        return api_error("גרסה %s כבר הועלתה. כדי לשלוח גרסה חדשה צריך מספר גרסה חדש." % version, 409)
    os.makedirs(RELEASES_DIR, exist_ok=True)
    filename = "etratzon-update-%s.zip" % version
    with open(os.path.join(RELEASES_DIR, filename), "wb") as f:
        f.write(data)
    if not pinned:
        with open(PINNED_KEY_FILE, "w") as f:
            f.write(public.hex())
    notes = (request.form.get("notes") or sig.get("notes") or manifest.get("notes") or "").strip()[:2000]
    cur = conn.execute("INSERT INTO releases(version, notes, filename, sha256, size, signature, created_at, uploaded_by) "
                       "VALUES (?,?,?,?,?,?,?,?)", (version, notes, filename, digest, len(data), signature.hex(),
                                                    iso(utcnow()), g.user["username"]))
    audit(conn, "release_upload", "גרסה %s (%d קבצים)" % (version, len(manifest.get("files", []))))
    conn.commit()
    return jsonify({"ok": True, "id": cur.lastrowid, "version": version, "files": len(manifest.get("files", []))})


@app.route("/api/releases/<int:rid>/targets", methods=["POST"])
@login_required(roles=("owner",))
def api_release_targets(rid):
    """שליחת גרסה לבתי ספר נבחרים. שליחה חוזרת ללקוח שנכשל מאפסת אותו ל'ממתין'."""
    conn = db()
    rel = conn.execute("SELECT * FROM releases WHERE id=?", (rid,)).fetchone()
    if not rel or rel["status"] != "active":
        return api_error("הגרסה לא קיימת או בוטלה", 404)
    ids = [int(x) for x in (request.get_json(force=True) or {}).get("customer_ids") or []]
    if not ids:
        return api_error("לא נבחרו בתי ספר")
    now, added = iso(utcnow()), []
    for cid in ids:
        c = conn.execute("SELECT name FROM customers WHERE id=? AND status != 'archived'", (cid,)).fetchone()
        if not c:
            continue
        old = conn.execute("SELECT status FROM rollouts WHERE release_id=? AND customer_id=?", (rid, cid)).fetchone()
        if old and old["status"] in ROLLOUT_OPEN + ("installed",):
            continue
        conn.execute("INSERT OR REPLACE INTO rollouts(release_id, customer_id, status, message, updated_at) "
                     "VALUES (?,?,?,?,?)", (rid, cid, "pending", "", now))
        audit(conn, "release_target", "גרסה %s נשלחה" % rel["version"], cid)
        added.append(cid)
    conn.commit()
    return jsonify({"ok": True, "added": len(added)})


@app.route("/api/releases/<int:rid>/targets/<int:cid>", methods=["DELETE"])
@login_required(roles=("owner",))
def api_release_cancel(rid, cid):
    conn = db()
    row = conn.execute("SELECT status FROM rollouts WHERE release_id=? AND customer_id=?", (rid, cid)).fetchone()
    if not row:
        return api_error("לא נמצא", 404)
    if row["status"] not in ("pending", "downloading", "downloaded"):
        return api_error("אי אפשר לבטל: המחשב כבר %s" % ROLLOUT_STATUSES.get(row["status"], row["status"]))
    conn.execute("UPDATE rollouts SET status='cancelled', updated_at=? WHERE release_id=? AND customer_id=?",
                 (iso(utcnow()), rid, cid))
    audit(conn, "release_cancel", "בוטל עדכון", cid)
    conn.commit()
    return jsonify({"ok": True})


@app.route("/api/releases/<int:rid>/withdraw", methods=["POST"])
@login_required(roles=("owner",))
def api_release_withdraw(rid):
    """ביטול גרסה: מפסיקים להציע אותה. מחשב שכבר באמצע התקנה ממשיך (ויחזור לאחור אם תיכשל)."""
    conn = db()
    rel = conn.execute("SELECT version FROM releases WHERE id=?", (rid,)).fetchone()
    if not rel:
        return api_error("לא נמצא", 404)
    conn.execute("UPDATE releases SET status='withdrawn' WHERE id=?", (rid,))
    conn.execute("UPDATE rollouts SET status='cancelled', updated_at=? WHERE release_id=? AND status IN "
                 "('pending','downloading','downloaded')", (iso(utcnow()), rid))
    audit(conn, "release_withdraw", "גרסה %s בוטלה" % rel["version"])
    conn.commit()
    return jsonify({"ok": True})


def update_offer(conn, c, reported_version):
    """העדכון שמחכה למחשב הזה (או None). אם המחשב כבר מדווח על הגרסה - מסמנים 'הותקן'."""
    row = conn.execute(
        "SELECT ro.status, rel.* FROM rollouts ro JOIN releases rel ON rel.id = ro.release_id "
        "WHERE ro.customer_id=? AND rel.status='active' AND ro.status IN ('pending','downloading','downloaded','installing') "
        "ORDER BY rel.id DESC LIMIT 1", (c["id"],)).fetchone()
    if not row:
        return None
    if reported_version and reported_version == row["version"]:
        conn.execute("UPDATE rollouts SET status='installed', message='', updated_at=? WHERE release_id=? AND customer_id=?",
                     (iso(utcnow()), row["id"], c["id"]))
        audit(conn, "update_installed", "גרסה %s הותקנה" % row["version"], c["id"], username="(מחשב)")
        return None
    return {"release_id": row["id"], "version": row["version"], "sha256": row["sha256"], "size": row["size"],
            "signature": row["signature"], "url": "/api/agent/update/%d/package" % row["id"]}


# ------------------------------------------------------------------ API למחשבי הצלצולים

def agent_customer():
    body = request.get_json(force=True, silent=True) or {}
    code = (request.headers.get("X-Customer-Code") or body.get("customer_code") or "").strip().upper()
    key = request.headers.get("X-Api-Key") or body.get("api_key") or ""
    if not code or not key:
        return None, body
    c = db().execute("SELECT * FROM customers WHERE code=?", (code,)).fetchone()
    if not c or not secrets.compare_digest(c["api_key_hash"], hash_key(key)) or c["status"] == "archived":
        return None, body
    return c, body


def license_reply(c):
    return {"status": c["license_status"], "status_label": LICENSE_STATUSES.get(c["license_status"]),
            "expires": c["license_expires"], "customer_status": c["status"]}


@app.route("/api/agent/register", methods=["POST"])
def api_agent_register():
    """רישום עצמי בזמן התקנה: המחשב מקבל קוד לקוח ייחודי ומפתח, והלקוח מופיע אצל היוצר כ'ממתין לאישור'."""
    body = request.get_json(force=True, silent=True) or {}
    info = body.get("institution") or {}
    name = (info.get("name") or "").strip() or "מוסד חדש (לא הוזן שם)"
    conn = db()
    with _db_lock:
        code = new_customer_code(conn)
        api_key = secrets.token_urlsafe(24)
        cur = conn.execute(
            "INSERT INTO customers(code, api_key_hash, status, name, country, city, address, contact_name, phone, "
            "email, machine_id, license_status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (code, hash_key(api_key), "pending", name[:120], info.get("country") or "ישראל", info.get("city", ""),
             info.get("address", ""), info.get("contact", ""), info.get("phone", ""), info.get("email", ""),
             (body.get("machine_id") or "")[:80], "trial", iso(utcnow())))
        audit(conn, "agent_register", "נרשם מהתקנה: %s" % name, cur.lastrowid, username="(מחשב)")
        conn.commit()
    return jsonify({"ok": True, "customer_code": code, "api_key": api_key})


@app.route("/api/agent/heartbeat", methods=["POST"])
def api_agent_heartbeat():
    c, body = agent_customer()
    if not c:
        return api_error("קוד לקוח או מפתח שגויים", 401)
    now = utcnow()
    payload = body.get("status") or {}
    sent = parse_iso(payload.get("pc_time_utc"))
    if sent:
        payload["clock_drift_s"] = round((sent - now).total_seconds(), 1)
    payload = {k: payload[k] for k in list(payload)[:60]}       # הגבלת גודל
    conn = db()
    conn.execute("UPDATE customers SET last_seen=?, last_payload=? WHERE id=?",
                 (iso(now), json.dumps(payload, ensure_ascii=False)[:20000], c["id"]))
    conn.execute("INSERT INTO heartbeats(customer_id, received_at, ok) VALUES (?,?,?)",
                 (c["id"], iso(now), 1 if payload.get("engine_alive", True) else 0))
    if not app.config.get("BACKUP_THREAD"):
        maybe_backup()
    if random.random() < 0.01:   # ניקוי היסטוריה ישנה מדי פעם
        conn.execute("DELETE FROM heartbeats WHERE received_at < ?", (iso(now - timedelta(days=HEARTBEAT_KEEP_DAYS)),))
    offer = update_offer(conn, c, payload.get("version"))
    conn.commit()
    return jsonify({"ok": True, "server_time_utc": iso(now), "license": license_reply(c),
                    "latest_version": latest_version(), "update": offer})


@app.route("/api/agent/update/<int:rid>/package")
def api_agent_update_package(rid):
    c, _ = agent_customer()
    if not c:
        return api_error("קוד לקוח או מפתח שגויים", 401)
    row = db().execute("SELECT rel.filename FROM rollouts ro JOIN releases rel ON rel.id = ro.release_id WHERE "
                       "ro.release_id=? AND ro.customer_id=? AND rel.status='active'", (rid, c["id"])).fetchone()
    if not row:
        return api_error("אין עדכון כזה ללקוח הזה", 404)
    return send_file(os.path.join(RELEASES_DIR, row["filename"]), mimetype="application/zip", as_attachment=True)


@app.route("/api/agent/update/status", methods=["POST"])
def api_agent_update_status():
    c, body = agent_customer()
    if not c:
        return api_error("קוד לקוח או מפתח שגויים", 401)
    status = body.get("status")
    if status not in ROLLOUT_STATUSES or status in ("pending", "cancelled"):
        return api_error("מצב לא מוכר")
    conn = db()
    row = conn.execute("SELECT ro.status, rel.version FROM rollouts ro JOIN releases rel ON rel.id=ro.release_id "
                       "WHERE ro.release_id=? AND ro.customer_id=?", (body.get("release_id"), c["id"])).fetchone()
    if not row:
        return api_error("לא נמצא", 404)
    if row["status"] == "cancelled" and status in ("downloading", "downloaded"):
        return jsonify({"ok": True, "cancelled": True})
    msg = str(body.get("message") or "")[:500]
    conn.execute("UPDATE rollouts SET status=?, message=?, updated_at=? WHERE release_id=? AND customer_id=?",
                 (status, msg, iso(utcnow()), body.get("release_id"), c["id"]))
    if status in ("installed", "failed", "rolled_back") and status != row["status"]:
        audit(conn, "update_" + status, "גרסה %s: %s%s" % (row["version"], ROLLOUT_STATUSES[status],
                                                         (" - " + msg) if msg else ""), c["id"], username="(מחשב)")
    conn.commit()
    return jsonify({"ok": True, "cancelled": row["status"] == "cancelled"})


@app.route("/api/agent/feedback", methods=["POST"])
def api_agent_feedback():
    c, body = agent_customer()
    if not c:
        return api_error("קוד לקוח או מפתח שגויים", 401)
    text = (body.get("text") or "").strip()
    if not text:
        return api_error("תוכן המשוב ריק")
    rating = body.get("rating")
    try:
        rating = max(1, min(5, int(rating))) if rating not in (None, "") else None
    except (TypeError, ValueError):
        rating = None
    conn = db()
    conn.execute("INSERT INTO feedback(customer_id, created_at, category, rating, text, contact) VALUES (?,?,?,?,?,?)",
                 (c["id"], iso(utcnow()), (body.get("category") or "general")[:30], rating, text[:4000],
                  (body.get("contact") or "")[:200]))
    conn.commit()
    return jsonify({"ok": True})


# ------------------------------------------------------------------ נתוני דוגמה

def seed_demo():
    """מסד נתונים לדוגמה כדי לראות את הדשבורד מלא. לא לשימוש עם לקוחות אמיתיים."""
    if os.path.exists(DB_FILE):
        os.replace(DB_FILE, DB_FILE + ".bak-%s" % datetime.now().strftime("%Y%m%d%H%M%S"))
    for ext in ("-wal", "-shm"):
        if os.path.exists(DB_FILE + ext):
            os.remove(DB_FILE + ext)
    init_db()
    rnd = random.Random(7)
    conn = connect()
    now = utcnow()
    nets = ["רשת אור", "רשת נתיבות", "רשת בית יעקב"]
    for n in nets:
        conn.execute("INSERT INTO networks(name, created_at) VALUES (?,?)", (n, iso(now - timedelta(days=200))))
    cities = ["ירושלים", "בני ברק", "חיפה", "באר שבע", "פתח תקווה", "אלעד", "נצרת", "מודיעין", "אשדוד", "צפת",
              "בית שמש", "רחובות"]
    names = ["בית ספר אורות", "תלמוד תורה בית אהרן", "אולפנת נווה", "חטיבת הביניים רמות", "בית ספר הגפן",
             "תיכון מקיף א'", "בית ספר נועם", "סמינר בית יעקב", "בית ספר אל-נור", "בית ספר שקד",
             "ישיבת אור עציון", "בית ספר הרימון", "בית ספר נחשון", "תיכון דרכי נועם", "בית ספר רעות",
             "בית ספר אל-אמל", "בית ספר נתיבות משה", "תלמוד תורה חכמי לב", "חטיבת ביניים אלון", "בית ספר דקל",
             "בית ספר עין הקורא", "אולפנת צביה", "בית ספר מעלות", "תיכון הגליל"]
    scenarios = (["ok"] * 13 + ["offline_school"] * 2 + ["engine_down", "clock", "audio", "disabled",
                 "license_soon", "overdue", "never", "pending", "old_version"])
    rnd.shuffle(scenarios)
    demo_clients = []
    for i, name in enumerate(names):
        sc = scenarios[i]
        sector = "ערבי" if "אל-" in name else rnd.choice(["ממלכתי", "ממלכתי-דתי", "חרדי"]) if "תלמוד" not in name and \
            "בית יעקב" not in name else "חרדי"
        created = now - timedelta(days=rnd.randint(20, 400))
        exp = (now + timedelta(days=rnd.randint(60, 330))).date().isoformat()
        lic, pay = "active", rnd.choice(["paid", "paid", "paid", "pending"])
        if sc == "license_soon":
            exp = (now + timedelta(days=12)).date().isoformat()
        if sc == "overdue":
            pay = "overdue"
        status = "pending" if sc == "pending" else "active"
        if sc == "pending":
            lic, exp, pay = "trial", None, "pending"
        net = rnd.choice([1, 2, 3, None, None]) if sector != "ערבי" else None
        if "בית יעקב" in name:
            net = 3
        conn.execute(
            "INSERT INTO customers(code, api_key_hash, status, name, sector, institution_type, city, contact_name, phone, "
            "email, network_id, plan, license_status, license_expires, payment_status, monthly_price, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (new_customer_code(conn), hash_key("demo-key-%d" % i), status, name, sector,
             rnd.choice(INSTITUTION_TYPES[1:6]), rnd.choice(cities),
             rnd.choice(["יוסי כהן", "מרים לוי", "אחמד חסן", "רחל פרידמן", "דוד מזרחי", "שרה אברהם"]),
             "05%d-%07d" % (rnd.randint(0, 8), rnd.randint(0, 9999999)), "office%d@school.example" % i,
             net, rnd.choice(["basic", "basic", "pro"]), lic, exp, pay, rnd.choice([79, 99, 149]), iso(created)))
        cid = i + 1
        # היסטוריית דיווחים: 7 ימים, דיווח לשעה (במקום 60 בשעה, כדי לשמור על מסד קטן)
        gaps = set()
        if rnd.random() < 0.3:
            start = rnd.randint(0, 150)
            gaps = set(range(start, start + rnd.randint(2, 14)))
        if sc not in ("never", "pending"):
            for h in range(7 * 24, 0, -1):
                if h in gaps:
                    continue
                if sc == "offline_school" and h <= 3:
                    continue
                conn.execute("INSERT INTO heartbeats(customer_id, received_at) VALUES (?,?)",
                             (cid, iso(now - timedelta(hours=h - 1, minutes=rnd.randint(1, 50)))))
        if sc in ("never", "pending"):
            last_seen = now - timedelta(minutes=5) if sc == "pending" else None
        elif sc == "offline_school":
            last_seen = now - timedelta(minutes=rnd.randint(25, 140))
        else:
            last_seen = now - timedelta(seconds=rnd.randint(5, 70))
        today_from = now.replace(hour=4, minute=30, second=0)      # 07:30 שעון ישראל
        today_to = now.replace(hour=13, minute=30, second=0)       # 16:30
        if sc == "offline_school":
            today_from, today_to = now - timedelta(hours=4), now + timedelta(hours=3)
        payload = {
            "version": "2026.09.28-2" if sc == "old_version" else LATEST_CLIENT_VERSION,
            "engine_alive": sc != "engine_down", "web_alive": True, "license_valid": True,
            "system_status": "disabled" if sc == "disabled" else "enabled",
            "disabled_scope": "indefinite" if sc == "disabled" else None,
            "events_today": rnd.randint(14, 32), "events_week": rnd.randint(80, 160),
            "next_event": {"time": "%02d:%02d:00" % (rnd.randint(8, 15), rnd.choice([0, 15, 45, 50])),
                           "label": rnd.choice(["הפסקה גדולה", "שיעור 4", "תפילה", "סוף יום"])},
            "clock_drift_s": 38.0 if sc == "clock" else round(rnd.uniform(-1.5, 1.5), 1),
            "missing_audio": ["break_song.mp3", "end_of_day.mp3"] if sc == "audio" else [],
            "errors_24h": 3 if sc == "audio" else 0,
            "audio_device": "Speakers (Realtek High Definition Audio)",
            "os": rnd.choice(["Windows 7 SP1", "Windows 7 SP1", "Windows 10", "Windows 11"]),
            "python": rnd.choice(["3.8.10", "3.8.10", "3.11.9"]),
            "day_window": {"from": iso(today_from), "to": iso(today_to)},
            "institution_name": name,
        }
        conn.execute("UPDATE customers SET last_seen=?, last_payload=? WHERE id=?",
                     (iso(last_seen), json.dumps(payload, ensure_ascii=False) if last_seen else None, cid))
        code = conn.execute("SELECT code FROM customers WHERE id=?", (cid,)).fetchone()["code"]
        if sc not in ("never", "offline_school"):
            p2 = dict(payload)
            p2.pop("clock_drift_s", None)
            demo_clients.append({"code": code, "api_key": "demo-key-%d" % i, "scenario": sc, "status": p2})
        conn.execute("INSERT INTO audit(at, username, customer_id, action, details) VALUES (?,?,?,?,?)",
                     (iso(created), "admin", cid, "customer_create", name))
    fbs = [(3, "feature", 5, "המערכת מצוינת! אפשר להוסיף כפתור לכריזה מהטלפון?"),
           (7, "bug", 2, "ביום שישי הצלצול של 12:45 לא נשמע, בדקנו והרמקולים תקינים."),
           (11, "general", 4, "המורים מאוד אוהבים את המוסיקה בהפסקות."),
           (15, "feature", None, "נשמח לאפשרות להגדיר חופשות של החגים מראש לכל השנה."),
           (2, "bug", 3, "אחרי הפסקת חשמל המחשב עלה והמערכת לא עלתה לבד.")]
    for k, (cid, cat, rating, text) in enumerate(fbs):
        conn.execute("INSERT INTO feedback(customer_id, created_at, category, rating, text, status) VALUES (?,?,?,?,?,?)",
                     (cid, iso(now - timedelta(days=k * 2, hours=k)), cat, rating, text,
                      "new" if k < 3 else "in_progress" if k == 3 else "done"))
    conn.execute("INSERT INTO users(username, display_name, password_hash, role, created_at) VALUES (?,?,?,?,?)",
                 ("admin", "היוצר", generate_password_hash("demo1234"), "owner", iso(now)))
    conn.execute("INSERT INTO users(username, display_name, password_hash, role, network_id, created_at) "
                 "VALUES (?,?,?,?,?,?)", ("or-net", "מנהל רשת אור", generate_password_hash("demo1234"),
                                          "network_admin", 1, iso(now)))
    conn.commit()
    conn.close()
    with open(DEMO_FILE, "w", encoding="utf-8") as f:
        json.dump(demo_clients, f, ensure_ascii=False, indent=1)
    print("נוצר מסד נתונים לדוגמה: 24 לקוחות, 3 רשתות.")
    print("כניסה: admin / demo1234   (מנהל רשת לדוגמה: or-net / demo1234)")


def backup_now():
    """עותק של מסד הנתונים (דרך מנגנון הגיבוי של SQLite, בטוח גם בזמן שהשרת כותב)."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    target = os.path.join(BACKUP_DIR, "control_center-%s.db" % datetime.now().strftime("%Y%m%d-%H%M"))
    src = connect()
    dst = sqlite3.connect(target)
    with dst:
        src.backup(dst)
    dst.close()
    src.close()
    old = sorted(f for f in os.listdir(BACKUP_DIR) if f.startswith("control_center-") and f.endswith(".db"))
    for f in old[:-BACKUP_KEEP]:
        os.remove(os.path.join(BACKUP_DIR, f))
    return target


_last_backup = {"t": None}


def maybe_backup():
    """גיבוי יומי בלי תהליך רקע (מתאים לאירוח כמו PythonAnywhere): נקרא מבקשות רגילות,
    ומגבה אם עברו 24 שעות מהגיבוי האחרון."""
    now = datetime.now()
    if _last_backup["t"] is None:
        files = sorted(f for f in os.listdir(BACKUP_DIR)) if os.path.isdir(BACKUP_DIR) else []
        _last_backup["t"] = datetime.fromtimestamp(os.path.getmtime(os.path.join(BACKUP_DIR, files[-1]))) \
            if files else datetime.min
    if now - _last_backup["t"] < timedelta(hours=24):
        return
    _last_backup["t"] = now
    try:
        backup_now()
    except Exception as e:
        print("הגיבוי נכשל:", e)


@app.route("/api/backup/download")
@login_required(roles=("owner",))
def api_backup_download():
    path = backup_now()
    _last_backup["t"] = datetime.now()
    return send_from_directory(BACKUP_DIR, os.path.basename(path), as_attachment=True)


def run_backups():
    import time
    while True:
        try:
            print("גיבוי:", backup_now())
        except Exception as e:
            print("הגיבוי נכשל:", e)
        time.sleep(24 * 3600)


def run_demo_simulator(port):
    """בתצוגת הדגמה: 'מחשבי בית ספר' מדומים שולחים דופק אמיתי דרך ה-API כל דקה,
    כדי שהדשבורד יישאר חי. מופעל רק עם --demo או --simulate."""
    import time
    import urllib.request
    clients = json.load(open(DEMO_FILE, encoding="utf-8"))
    time.sleep(2)
    while True:
        for cl in clients:
            st = dict(cl["status"])
            drift = 38 if cl["scenario"] == "clock" else random.uniform(-1.2, 1.2)
            st["pc_time_utc"] = (datetime.now(timezone.utc) + timedelta(seconds=drift)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            req = urllib.request.Request("http://127.0.0.1:%d/api/agent/heartbeat" % port,
                                         data=json.dumps({"status": st}).encode("utf-8"), method="POST",
                                         headers={"Content-Type": "application/json", "X-Customer-Code": cl["code"],
                                                  "X-Api-Key": cl["api_key"]})
            try:
                urllib.request.urlopen(req, timeout=10).read()
            except Exception as e:
                print("simulator:", cl["code"], e)
        time.sleep(60)


# ------------------------------------------------------------------ הפעלה

def configure(public_url="", web_setup=True, backup_thread=False):
    """הגדרות משותפות להרצה מקומית ול-WSGI (PythonAnywhere)."""
    init_db()
    app.secret_key = load_secret_key()
    app.config["PUBLIC_URL"] = (public_url or "").rstrip("/")
    app.config["ALLOW_WEB_SETUP"] = web_setup
    app.config["BACKUP_THREAD"] = backup_thread
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    if not web_setup and (public_url or "").startswith("https://"):
        app.config["SESSION_COOKIE_SECURE"] = True      # אתר ציבורי: עוגיית ההתחברות רק ב-HTTPS
    app.permanent_session_lifetime = timedelta(days=14)
    return app


def create_owner(username):
    """יצירת חשבון יוצר מהמסוף: python server.py --create-owner שם"""
    import getpass
    init_db()
    conn = connect()
    if conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        print("שם המשתמש כבר קיים.")
        return 1
    pw = getpass.getpass("סיסמה (לפחות 8 תווים): ")
    if len(pw) < 8 or pw != getpass.getpass("שוב: "):
        print("הסיסמה קצרה מדי או לא תואמת.")
        return 1
    conn.execute("INSERT INTO users(username, display_name, password_hash, role, created_at) VALUES (?,?,?,?,?)",
                 (username, username, generate_password_hash(pw), "owner", iso(utcnow())))
    conn.execute("INSERT INTO audit(at, username, action, details) VALUES (?,?,?,?)",
                 (iso(utcnow()), "(מסוף)", "setup", "נוצר חשבון יוצר: %s" % username))
    conn.commit()
    conn.close()
    print("נוצר חשבון יוצר:", username)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="יוצר מסד נתונים לדוגמה (המסד הקיים נשמר כגיבוי)")
    ap.add_argument("--simulate", action="store_true", help="ממשיך להריץ את המחשבים המדומים של ההדגמה")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--public-url", default=os.environ.get("CC_PUBLIC_URL", ""),
                    help="הכתובת שמחשבי בתי הספר מגיעים אליה (נכנסת לקובץ cloud_config.json)")
    ap.add_argument("--create-owner", metavar="USERNAME", help="יוצר חשבון יוצר ויוצא")
    args = ap.parse_args()
    if args.create_owner:
        return create_owner(args.create_owner)
    if args.demo:
        seed_demo()
    configure(args.public_url, web_setup=True, backup_thread=not args.demo)
    if (args.demo or args.simulate) and os.path.exists(DEMO_FILE):
        threading.Thread(target=run_demo_simulator, args=(args.port,), daemon=True).start()
        print("מצב הדגמה: מחשבים מדומים מדווחים כל דקה")
    if not args.demo:
        threading.Thread(target=run_backups, daemon=True).start()
    print("מרכז הבקרה רץ: http://localhost:%d" % args.port)
    if app.config["PUBLIC_URL"]:
        print("כתובת ציבורית (לבתי הספר):", app.config["PUBLIC_URL"])
    try:
        from waitress import serve        # שרת יציב יותר משרת הפיתוח של Flask
        print("שרת: waitress")
        serve(app, host=args.host, port=args.port, threads=8)
    except ImportError:
        app.run(host=args.host, port=args.port, debug=False, threaded=True)


app.secret_key = None
if __name__ == "__main__":
    sys.exit(main())
