"""
watchdog.py  -  "שומר" של מערכת הצלצולים

מפעיל את מנוע ההשמעה (main.py) ואת שרת הניהול (web_server.py) ברקע, בלי חלונות,
ומפעיל מחדש תוך כמה שניות כל אחד מהם שנופל.
מפעיל גם את window_guard.py (שמירה על חלון הניהול מפני סגירה ב-X).

* start_all.bat מפעיל את השומר (pythonw - בלי חלון), והשומר מפעיל את שניהם.
* עצירה מכוונת (STOP, סגירת חלון הניהול, stop_all.bat) עוצרת קודם את השומר, ואיתו את שניהם -
  ולכן הוא לא "מחייה" מערכת שעצרו בכוונה.
* אם תהליך נופל שוב ושוב (למשל קובץ פגום), השומר מפסיק לנסות אחרי כמה פעמים, כדי לא להיכנס ללולאה.
  הדשבורד במרכז הבקרה יציג אז "המנוע לא רץ".
* הפלט של כל תהליך נשמר ב-logs/engine_console.log ו-logs/web_console.log (במקום חלון).
"""

import os
import socket
import subprocess
import sys
import time
from datetime import datetime

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SRC_DIR)
LOGS_DIR = os.path.join(BASE_DIR, "logs")
LOG_FILE = os.path.join(LOGS_DIR, "watchdog.log")
LOCK_PORT = 8597                 # עותק יחיד של השומר (המנוע 8599, הדיווח 8598, השרת 8500)
CHECK_SECONDS = 3
MAX_RESTARTS = 5                 # מקסימום הפעלות מחדש...
RESTART_WINDOW = 600             # ...בתוך 10 דקות. מעבר לזה - מפסיקים לנסות
GIVE_UP_PAUSE = 900              # ואחרי רבע שעה מנסים שוב (אולי הבעיה נפתרה)
CREATE_NO_WINDOW = 0x08000000

PROCS = [
    {"name": "engine", "script": "main.py", "log": "engine_console.log", "label": "מנוע ההשמעה"},
    {"name": "web", "script": "web_server.py", "log": "web_console.log", "label": "שרת הניהול"},
    # שומר על חלון הניהול: X / Alt+F4 לא סוגרים אותו, אלא מקפיצים שאלה (רק ב-Windows)
    {"name": "guard", "script": "window_guard.py", "log": "guard_console.log", "label": "שמירת חלון הניהול",
     "windows_only": True},
]


def log(msg):
    line = "%s  %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 1024 * 1024:
            os.replace(LOG_FILE, LOG_FILE + ".1")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def acquire_lock():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        s.bind(("127.0.0.1", LOCK_PORT))
        s.listen(1)
        return s
    except OSError:
        s.close()
        return None


def python_exe():
    """pythonw אם יש (בלי חלון), אחרת python."""
    exe = sys.executable
    if os.name == "nt":
        d = os.path.dirname(exe)
        pyw = os.path.join(d, "pythonw.exe")
        if os.path.exists(pyw):
            return pyw
    return exe


def start(p):
    path = os.path.join(LOGS_DIR, p["log"])
    try:
        if os.path.exists(path) and os.path.getsize(path) > 2 * 1024 * 1024:   # לא לתת לקובץ לגדול בלי סוף
            os.replace(path, path + ".1")
    except OSError:
        pass
    out = open(path, "a", encoding="utf-8", errors="replace")
    out.write("\n===== %s  הפעלה =====\n" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    out.flush()
    kw = {"creationflags": CREATE_NO_WINDOW} if os.name == "nt" else {}
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    proc = subprocess.Popen([python_exe(), os.path.join(SRC_DIR, p["script"])], cwd=BASE_DIR,
                            stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, env=env, **kw)
    out.close()           # לתהליך יש עותק משלו של הקובץ
    p["proc"], p["started"] = proc, time.time()
    log("הופעל %s (pid %d)" % (p["label"], proc.pid))


def main():
    os.makedirs(LOGS_DIR, exist_ok=True)
    lock = acquire_lock()
    if lock is None:
        log("שומר אחר כבר רץ - יוצא")
        return 1
    log("=== השומר מופעל ===")
    PROCS[:] = [p for p in PROCS if os.name == "nt" or not p.get("windows_only")]
    for p in PROCS:
        p["restarts"], p["gave_up_until"] = [], 0
        start(p)
        time.sleep(2)          # המנוע קודם, ואז השרת (כמו start_all.bat)
    while True:
        time.sleep(CHECK_SECONDS)
        now = time.time()
        for p in PROCS:
            proc = p.get("proc")
            if proc is not None and proc.poll() is None:
                continue
            if p["gave_up_until"] and now < p["gave_up_until"]:
                continue
            if proc is not None and p.get("reported_exit") is not proc:
                log("%s נעצר (קוד יציאה %s, אחרי %d שניות)" % (p["label"], proc.returncode, now - p["started"]))
                p["reported_exit"] = proc
            p["restarts"] = [t for t in p["restarts"] if now - t < RESTART_WINDOW]
            if len(p["restarts"]) >= MAX_RESTARTS:
                p["gave_up_until"] = now + GIVE_UP_PAUSE
                p["restarts"] = []
                log("%s נפל %d פעמים ב-%d דקות - מפסיק לנסות ל-%d דקות. בדוק את logs\\%s"
                    % (p["label"], MAX_RESTARTS, RESTART_WINDOW // 60, GIVE_UP_PAUSE // 60, p["log"]))
                continue
            p["restarts"].append(now)
            p["gave_up_until"] = 0
            start(p)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log("שגיאה לא צפויה בשומר: %s" % e)
        raise
