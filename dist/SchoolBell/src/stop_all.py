"""
stop_all.py
עוצר כל עותק רץ של מנוע ההשמעה (main.py) ושל שרת הניהול (web_server.py) של הפרויקט הזה.
מופעל אוטומטית ע"י start_all.bat לפני הפעלה מחדש, כדי שלא יישארו עותקים ישנים ברקע
(ב-Windows שרת ישן ושרת חדש יכולים להאזין לאותה כתובת במקביל, ושני מנועים ינגנו כל צלצול פעמיים).
אפשר להריץ גם ידנית דרך stop_all.bat כדי לעצור את המערכת.

מה נחשב "שלנו":
  * חלון cmd ששורת הפקודה שלו כוללת את שם תיקיית הפרויקט ואת main.py / web_server.py
    (אלה החלונות ש-start_all.bat פותח) - נעצר יחד עם כל מה שרץ בתוכו.
  * תהליך python שמריץ web_server.py (שם ייחודי לפרויקט), או main.py עם נתיב שכולל את שם הפרויקט.
תהליכי python של פרויקטים אחרים (למשל src\\main.py של פרויקט אחר) לא נוגעים בהם.
"""

import json
import os
import re
import subprocess
import sys

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_NAME = os.path.basename(PROJECT_DIR).lower()
# חלון הניהול נפתח (start_all.bat) עם פרופיל דפדפן נפרד בתיקייה הזו - כך STOP סוגר רק אותו,
# ולא את Chrome/Edge הרגיל של המשתמש
APP_WINDOW_PROFILE = ".bell_window"
SCRIPT_RE = re.compile(r"(^|[\\/\s\"'])(src[\\/])?(main|web_server)\.py\b")


def decode_output(raw):
    """פלט של פקודות Windows כבתים -> טקסט, בלי לקרוס על תווים לא צפויים.
    (באג 05/10/2026: text=True פענח בקידוד המערכת cp1255, ושורת פקודה של תהליך כלשהו במחשב
    הכילה בית 0x9A שלא קיים בו - הקריאה קרסה ושום עותק ישן לא נעצר.)
    לזיהוי התהליכים שלנו מספיקים החלקים באנגלית (main.py / web_server.py / שם התיקייה)."""
    if not raw:
        return ""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff") or raw.count(b"\x00") > len(raw) // 4:
        return raw.decode("utf-16", errors="replace")      # wmic כותב לפעמים UTF-16
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("mbcs" if os.name == "nt" else "latin-1", errors="replace")


def _list_processes_powershell():
    """PowerShell 3+ (Windows 8 ומעלה, או Windows 7 עם WMF מעודכן). הפלט מבוקש ב-UTF-8."""
    cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
           "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
           "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,CommandLine "
           "| ConvertTo-Json -Compress"]
    out = decode_output(subprocess.run(cmd, capture_output=True, timeout=60).stdout).strip().lstrip("\ufeff")
    if not out:
        return []
    data = json.loads(out)
    return data if isinstance(data, list) else [data]


def parse_wmic_csv(text):
    """פלט של: wmic process get CommandLine,Name,ParentProcessId,ProcessId /format:csv
    העמודות (לפי סדר אלפביתי): Node,CommandLine,Name,ParentProcessId,ProcessId.
    שורת הפקודה עצמה יכולה להכיל פסיקים, ולכן מפרקים מהסוף."""
    procs = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("Node,"):
            continue
        parts = line.split(",")
        if len(parts) < 5:
            continue
        try:
            pid, ppid = int(parts[-1]), int(parts[-2])
        except ValueError:
            continue
        procs.append({"ProcessId": pid, "ParentProcessId": ppid, "Name": parts[-3],
                      "CommandLine": ",".join(parts[1:-3])})
    return procs


def _list_processes_wmic():
    """Windows 7 מגיע עם PowerShell 2, שאין בו Get-CimInstance ו-ConvertTo-Json. wmic קיים בו."""
    cmd = ["wmic", "process", "get", "CommandLine,Name,ParentProcessId,ProcessId", "/format:csv"]
    out = decode_output(subprocess.run(cmd, capture_output=True, timeout=60).stdout)
    return parse_wmic_csv(out)


def list_processes():
    """רשימת תהליכים עם שורת הפקודה המלאה. קודם PowerShell (wmic הוסר ב-Windows 11 החדשים),
    ואם הוא לא זמין או לא החזיר כלום (Windows 7) - wmic. רשימה ריקה אף פעם אינה תקינה (התהליך הזה עצמו רץ)."""
    errors = []
    try:
        procs = _list_processes_powershell()
        if procs:
            return procs
        errors.append("PowerShell: empty")
    except Exception as e:
        errors.append("PowerShell: %s" % e)
    try:
        procs = _list_processes_wmic()
        if procs:
            return procs
        errors.append("wmic: empty")
    except Exception as e:
        errors.append("wmic: %s" % e)
    raise RuntimeError("; ".join(errors))


def find_ours(processes, my_pid):
    """מחזיר את התהליכים של הפרויקט הזה שצריך לעצור (בלי כפילויות: חלון cmd מכסה את ה-python שבתוכו)."""
    targets = {}
    for p in processes:
        pid = p.get("ProcessId")
        name = (p.get("Name") or "").lower()
        cl = (p.get("CommandLine") or "").lower()
        if not pid or pid == my_pid or "stop_all.py" in cl:
            continue
        # חלון הניהול: Chrome/Edge עם הפרופיל הנפרד של המערכת (תהליך הדפדפן הראשי; תהליכי המשנה נסגרים איתו)
        if name in ("chrome.exe", "msedge.exe") and APP_WINDOW_PROFILE in cl and PROJECT_NAME in cl and "--type=" not in cl:
            targets[pid] = p
            continue
        if not SCRIPT_RE.search(cl):
            continue
        if name == "cmd.exe" and PROJECT_NAME in cl:
            targets[pid] = p
        elif name.startswith("python") or name in ("py.exe", "pyw.exe"):
            if "web_server.py" in cl or PROJECT_NAME in cl:
                targets[pid] = p
    # python שההורה שלו כבר ברשימה ייעצר ממילא (taskkill /T)
    return [p for p in targets.values() if p.get("ParentProcessId") not in targets]


def ancestors_of(pid, processes):
    """שרשרת ההורים של התהליך הזה. כשהשרת מפעיל את stop_all (סגירת חלון הניהול ב-X), stop_all רץ
    בתוך העץ של השרת - ולכן את השרת סוגרים אחרון, אחרי המנוע והחלון."""
    parent = {p.get("ProcessId"): p.get("ParentProcessId") for p in processes}
    seen, cur = set(), parent.get(pid)
    while cur and cur not in seen:
        seen.add(cur)
        cur = parent.get(cur)
    return seen


def kill_order(ours, processes, my_pid):
    mine = ancestors_of(my_pid, processes)
    return sorted(ours, key=lambda p: p.get("ProcessId") in mine)


def main():
    if os.name != "nt":
        print("stop_all.py is only needed on Windows.")
        return 0
    try:
        processes = list_processes()
    except Exception as e:
        print("WARNING: could not read the list of running programs (%s)." % e)
        print("         Old copies were NOT stopped. Close the 'Bell Engine' and 'Bell Web Server' windows by hand.")
        return 0
    ours = find_ours(processes, os.getpid())
    if not ours:
        print("No running copies of the system were found.")
        return 0
    for p in kill_order(ours, processes, os.getpid()):
        desc = (p.get("CommandLine") or p.get("Name") or "").strip()
        subprocess.run(["taskkill", "/PID", str(p["ProcessId"]), "/T", "/F"], capture_output=True)
        print("Stopped: PID %s  %s" % (p["ProcessId"], desc[:110].encode("ascii", "replace").decode()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
