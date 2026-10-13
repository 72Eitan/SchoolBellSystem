"""
open_window.py  -  פתיחת חלון הניהול הראשי (כמו בסוף start_all.bat)

Chrome או Edge במצב "אפליקציה" עם פרופיל נפרד (.bell_window), כך ש-STOP סוגר רק את החלון הזה.
אם אין Chrome/Edge - דפדפן ברירת המחדל.
--lang=he: שאלת הסגירה של הדפדפן ("לצאת מהאתר?") בעברית.
"""

import os
import subprocess

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_URL = "http://localhost:8500/?main=1"
PROFILE = os.path.join(BASE_DIR, ".bell_window")


def browser_path():
    env = os.environ.get
    candidates = [
        os.path.join(env("ProgramFiles", ""), r"Google\Chrome\Application\chrome.exe"),
        os.path.join(env("ProgramFiles(x86)", ""), r"Google\Chrome\Application\chrome.exe"),
        os.path.join(env("LocalAppData", ""), r"Google\Chrome\Application\chrome.exe"),
        os.path.join(env("ProgramFiles(x86)", ""), r"Microsoft\Edge\Application\msedge.exe"),
        os.path.join(env("ProgramFiles", ""), r"Microsoft\Edge\Application\msedge.exe"),
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return None


def open_window():
    b = browser_path()
    if b:
        subprocess.Popen([b, "--app=" + APP_URL, "--user-data-dir=" + PROFILE, "--no-first-run",
                          "--no-default-browser-check", "--start-maximized", "--lang=he"], cwd=BASE_DIR)
    elif os.name == "nt":
        os.startfile(APP_URL)


if __name__ == "__main__":
    open_window()
