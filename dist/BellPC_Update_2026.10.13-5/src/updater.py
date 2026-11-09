"""
updater.py  -  התקנת עדכון שהורד ממרכז הבקרה, עם גיבוי וחזרה אוטומטית לגרסה הקודמת

מופעל ע"י cloud_reporter.py רק אחרי שהחבילה הורדה ונבדקה (hash + חתימה דיגיטלית של היוצר),
ורק אחרי האירוע האחרון של היום. רץ בחלון נפרד וממוזער, כדי שאפשר יהיה לראות שמשהו קורה.

השלבים:
  1. בדיקה חוזרת של החבילה: חתימה, hash של כל קובץ, ושאין בה נתיבים אסורים
  2. גיבוי של כל קובץ שיוחלף, ל-updates/backup/<גרסה-קודמת>_<זמן>
  3. עצירת המערכת (stop_all.py)
  4. החלפת הקבצים
  5. הפעלה מחדש (start_all.bat) ובדיקה: המנוע רץ, ושרת הניהול עונה עם הגרסה החדשה
  6. אם הבדיקה נכשלה: עצירה, שחזור הגיבוי, הפעלה מחדש -> "חזר לגרסה הקודמת"
  7. הפעלה מחדש של הדיווח (cloud_reporter.py) ודיווח התוצאה למרכז

לעולם לא נוגע ב: schedule.json, cloud_config.json, saved_schedules.json, audio, logs, updates.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from datetime import datetime

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SRC_DIR)
sys.path.insert(0, SRC_DIR)
import cloud_reporter as cr  # noqa: E402  (נטען לזיכרון לפני שהקבצים מוחלפים)
import ed25519_pure as ed    # noqa: E402

LOG_FILE = os.path.join(cr.LOGS_DIR, "update.log")
BACKUP_ROOT = os.path.join(cr.UPDATES_DIR, "backup")
HEALTH_TIMEOUT = int(os.environ.get("BELL_UPDATER_HEALTH_TIMEOUT", "150"))   # שניות לחכות שהמערכת תעלה (מחשב איטי / Windows 7)
HEALTH_STABLE = 25            # ואחרי שעלתה - לוודא שהמנוע לא נפל מיד
PROTECTED_FILES = {"schedule.json", "cloud_config.json", "saved_schedules.json", "audio_devices.json",
                   "test_sound_state.json", "license.json"}
PROTECTED_DIRS = ("logs/", "audio/", "updates/", ".git/", ".1 versions/", "control_center/", ".bell_window/")
KEEP_BACKUPS = 5


def log(msg):
    line = "%s  %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def save_state(state):
    cr.write_json(cr.UPDATE_STATE, state)


# ------------------------------------------------------------------ בדיקת החבילה

def safe_path(rel):
    """נתיב יחסי בתוך תיקיית המערכת, שמותר להחליף. אחרת None."""
    rel = rel.replace("\\", "/")
    if not rel or rel.startswith("/") or ":" in rel or ".." in rel.split("/"):
        return None
    low = rel.lower()
    if low in PROTECTED_FILES or any(low.startswith(d) for d in PROTECTED_DIRS):
        return None
    full = os.path.normpath(os.path.join(BASE_DIR, rel))
    if not full.startswith(os.path.normpath(BASE_DIR) + os.sep):
        return None
    return full


def load_package(state):
    pkg = state.get("package")
    err = cr.verify_package(pkg, state["version"], state["sha256"], state["signature"])
    if err:
        raise RuntimeError(err)
    z = zipfile.ZipFile(pkg)
    manifest = json.loads(z.read("manifest.json").decode("utf-8"))
    if manifest.get("version") != state["version"]:
        raise RuntimeError("הגרסה בחבילה לא תואמת")
    files = []
    for f in manifest["files"]:
        full = safe_path(f["path"])
        if not full:
            raise RuntimeError("החבילה מנסה לכתוב לנתיב אסור: %s" % f["path"])
        data = z.read("files/" + f["path"])
        if hashlib.sha256(data).hexdigest() != f["sha256"]:
            raise RuntimeError("קובץ פגום בחבילה: %s" % f["path"])
        files.append((f["path"], full, data))
    return files


# ------------------------------------------------------------------ גיבוי, החלפה, שחזור

def backup(files, from_version):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = os.path.join(BACKUP_ROOT, "%s_%s" % (from_version or "unknown", stamp))
    os.makedirs(folder, exist_ok=True)
    existed, added = [], []
    for rel, full, _ in files:
        if os.path.exists(full):
            dst = os.path.join(folder, "files", rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(full, dst)
            existed.append(rel)
        else:
            added.append(rel)
    with open(os.path.join(folder, "backup.json"), "w", encoding="utf-8") as f:
        json.dump({"from_version": from_version, "existed": existed, "added": added}, f, ensure_ascii=False, indent=1)
    # מחיקת גיבויים ישנים (שומרים את האחרונים)
    old = sorted(d for d in os.listdir(BACKUP_ROOT) if os.path.isdir(os.path.join(BACKUP_ROOT, d)))
    for d in old[:-KEEP_BACKUPS]:
        shutil.rmtree(os.path.join(BACKUP_ROOT, d), ignore_errors=True)
    return folder


def write_files(files):
    for rel, full, data in files:
        os.makedirs(os.path.dirname(full), exist_ok=True)
        tmp = full + ".new"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, full)


def restore(folder):
    with open(os.path.join(folder, "backup.json"), encoding="utf-8") as f:
        info = json.load(f)
    for rel in info["existed"]:
        src = os.path.join(folder, "files", rel)
        dst = os.path.join(BASE_DIR, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
    for rel in info["added"]:
        try:
            os.remove(os.path.join(BASE_DIR, rel))
        except OSError:
            pass


# ------------------------------------------------------------------ עצירה, הפעלה, בדיקה

def _test_cmd(name):
    """בבדיקות (לא ב-Windows) אפשר להחליף את פקודות העצירה/ההפעלה דרך משתנה סביבה."""
    cmds = json.loads(os.environ.get("BELL_UPDATER_TEST_CMDS", "{}"))
    return cmds.get(name)


def stop_system():
    log("עוצר את המערכת...")
    cmd = _test_cmd("stop")
    if cmd:
        subprocess.run(cmd, shell=True, cwd=BASE_DIR, timeout=120)
    else:
        subprocess.run([sys.executable, os.path.join(SRC_DIR, "stop_all.py")], cwd=BASE_DIR, timeout=180)
    time.sleep(3)


def start_system():
    log("מפעיל את המערכת...")
    cmd = _test_cmd("start")
    if cmd:
        subprocess.Popen(cmd, shell=True, cwd=BASE_DIR, start_new_session=True)
    else:
        subprocess.Popen('start "Bell Start" /min cmd /c "%s"' % os.path.join(BASE_DIR, "start_all.bat"),
                         shell=True, cwd=BASE_DIR)


def web_version():
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/version" % cr.WEB_PORT, timeout=5) as r:
            return json.loads(r.read().decode("utf-8")).get("version")
    except Exception:
        return None


def current_engine_pids():
    res = cr.engine_pids()
    return set(res[0]) if res else set()


def wait_engine_stopped(timeout=30):
    """אחרי העצירה: מוודאים שהמנוע הישן באמת לא רץ, לפני שנוגעים בקבצים."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not current_engine_pids():
            return True
        time.sleep(2)
    return False


def healthy(expected_version, old_pids):
    """מנוע חדש רץ (לא תהליך שנשאר מלפני העצירה), ושרת הניהול עונה עם הגרסה הצפויה -
    ונשאר כך גם אחרי HEALTH_STABLE שניות."""
    deadline = time.time() + HEALTH_TIMEOUT
    last = ""

    def engine_new():
        pids = current_engine_pids()
        return bool(pids) and not (pids & old_pids)

    while time.time() < deadline:
        engine, ver = engine_new(), web_version()
        if engine and ver == expected_version:
            time.sleep(HEALTH_STABLE)
            if engine_new() and web_version() == expected_version:
                return True, ""
            last = "המנוע או השרת נפלו אחרי שעלו"
        else:
            last = "מנוע: %s, שרת: %s" % ("רץ" if engine else "לא רץ", ver or "לא עונה")
        time.sleep(5)
    return False, last


def restart_reporter():
    """הדיווח רץ מקוד ישן - מפעילים אותו מחדש מהקוד החדש."""
    if _test_cmd("reporter"):
        subprocess.Popen(_test_cmd("reporter"), shell=True, cwd=BASE_DIR, start_new_session=True)
        return
    if os.name != "nt":
        return
    try:
        procs = cr._list_processes()
        me = os.getpid()
        by_pid = {p.get("ProcessId"): p for p in procs}
        for p in procs:
            cl = (p.get("CommandLine") or "").lower()
            if "cloud_reporter.py" in cl and p.get("ProcessId") != me and "updater.py" not in cl \
                    and (p.get("Name") or "").lower().startswith("python"):
                # בלי /T: לא להרוג את updater עצמו אם הוא נחשב צאצא. גם חלון ה-cmd שלו נסגר (שלא יישארו חלונות ריקים)
                subprocess.run(["taskkill", "/PID", str(p["ProcessId"]), "/F"], capture_output=True)
                parent = by_pid.get(p.get("ParentProcessId")) or {}
                if (parent.get("Name") or "").lower() == "cmd.exe" and "cloud_reporter.py" in (parent.get("CommandLine") or "").lower():
                    subprocess.run(["taskkill", "/PID", str(parent["ProcessId"]), "/F"], capture_output=True)
        time.sleep(2)
        if os.path.exists(cr.CONFIG_FILE):
            # ברקע בלי חלון, כמו ב-start_all.bat (pythonw), ובלי קשר לתהליך הזה
            pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
            exe = pyw if os.path.exists(pyw) else sys.executable
            subprocess.Popen([exe, os.path.join(SRC_DIR, "cloud_reporter.py")], cwd=BASE_DIR,
                             creationflags=0x00000008 | 0x00000200)   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    except Exception as e:
        log("הפעלה מחדש של הדיווח נכשלה: %s" % e)


def finish(state, status, message):
    state.update(status=status, message=message, finished=datetime.now().isoformat(timespec="seconds"),
                 reported=False)
    save_state(state)
    cfg = cr.read_json(cr.CONFIG_FILE) or {}
    if cfg.get("server_url"):
        for _ in range(3):
            ok, _ = cr.report_update(cfg, state["release_id"], status, message)
            if ok:
                state["reported"] = True
                save_state(state)
                break
            time.sleep(10)
    log("סיום: %s %s" % (status, message))


# ------------------------------------------------------------------ ראשי

def main():
    os.makedirs(cr.LOGS_DIR, exist_ok=True)
    state = cr.read_json(cr.UPDATE_STATE) or {}
    if state.get("status") != "installing":
        log("אין עדכון שממתין להתקנה (מצב: %s)" % state.get("status"))
        return 1
    version, from_version = state["version"], state.get("from_version") or cr.client_version()
    log("=== עדכון מגרסה %s לגרסה %s ===" % (from_version, version))
    try:
        files = load_package(state)
    except Exception as e:
        finish(state, "failed", str(e))
        return 2
    log("החבילה תקינה: %d קבצים" % len(files))
    folder = backup(files, from_version)
    state["backup"] = folder
    save_state(state)
    log("גיבוי: %s" % folder)

    stopped = False
    old_pids = current_engine_pids()
    try:
        stop_system()
        stopped = True
        if not wait_engine_stopped():
            raise RuntimeError("המנוע הישן לא נעצר, ולכן הקבצים לא הוחלפו")
        write_files(files)
        log("הקבצים הוחלפו")
        start_system()
        ok, why = healthy(version, old_pids)
        if ok:
            log("המערכת עלתה בגרסה החדשה ועובדת")
            finish(state, "installed", "")
            restart_reporter()
            return 0
        reason = "הגרסה החדשה לא עלתה תקין: %s" % why
    except Exception as e:
        reason = "שגיאה בהתקנה: %s" % e
        if not stopped:
            # עוד לא עצרנו כלום - המערכת הישנה ממשיכה לרוץ כרגיל
            finish(state, "failed", reason)
            return 3

    # כל כישלון אחרי שהמערכת נעצרה: חוזרים לגרסה הקודמת ומפעילים אותה
    log(reason + " - חוזר לגרסה הקודמת")
    try:
        stop_system()
        wait_engine_stopped()
    except Exception as e:
        log("עצירה לפני שחזור נכשלה: %s" % e)
    try:
        restore(folder)
    except Exception as e:
        log("שחזור הגיבוי נכשל: %s" % e)
    start_system()
    ok2, why2 = healthy(from_version, set())     # אחרי שחזור מספיק שהמערכת עובדת, בכל תהליך
    if ok2:
        finish(state, "rolled_back", reason)
    else:
        finish(state, "failed", "%s. גם הגרסה הקודמת לא עלתה אחרי השחזור (%s) - נדרשת בדיקה במחשב." % (reason, why2))
    restart_reporter()
    return 4

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:      # שגיאה לא צפויה: לפחות לרשום ולדווח
        log("שגיאה לא צפויה: %s" % e)
        st = cr.read_json(cr.UPDATE_STATE) or {}
        if st.get("status") == "installing":
            finish(st, "failed", "שגיאה לא צפויה בהתקנה: %s" % e)
        sys.exit(5)
