"""
backup_git.py  -  גיבוי הפרויקט: commit ב-Git המקומי + העלאה ל-GitHub

מופעל ע"י backup_git.bat (בתיקיית הפרויקט). מבקש תיאור לגיבוי (אפשר להדביק את התיאור המוכן
ש-Claude נותן אחרי כל עדכון), שומר את כל השינויים ב-commit ומעלה ל-GitHub (origin).

התיאור נשמר ב-UTF-8 דרך קובץ זמני, כך שעברית נשמרת נכון (העברה דרך cmd הייתה משבשת אותה).
"""

import os
import subprocess
import sys
import tempfile
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def git(*args, capture=True):
    return subprocess.run(["git"] + list(args), cwd=ROOT, capture_output=capture,
                          text=True, encoding="utf-8", errors="replace")


def pause():
    try:
        input("\nלחץ Enter לסגירה...")
    except EOFError:
        pass


def main():
    print("=" * 50)
    print("  גיבוי הפרויקט ל-Git ול-GitHub")
    print("  " + ROOT)
    print("=" * 50)

    try:
        r = git("rev-parse", "--abbrev-ref", "HEAD")
    except FileNotFoundError:
        print("\nשגיאה: Git לא מותקן או לא נמצא ב-PATH.")
        print("מתקינים מ- https://git-scm.com/download/win ומפעילים שוב.")
        return 1
    if r.returncode != 0:
        print("\nשגיאה: התיקייה הזו אינה מאגר Git.\n" + r.stderr)
        return 1
    branch = r.stdout.strip()
    print("\nענף: " + branch)

    status = git("status", "--short").stdout.strip()
    if status:
        lines = status.splitlines()
        print("\nקבצים ששונו (%d):" % len(lines))
        for line in lines[:40]:
            print("  " + line)
        if len(lines) > 40:
            print("  ... ועוד %d" % (len(lines) - 40))
    else:
        print("\nאין שינויים חדשים לשמירה.")

    committed = False
    if status:
        print("\nהקלד או הדבק תיאור לגיבוי ולחץ Enter")
        print("(Enter בלי תיאור = \"גיבוי + תאריך\"):")
        try:
            msg = input("> ").strip()
        except EOFError:
            msg = ""
        if not msg:
            msg = "גיבוי " + datetime.now().strftime("%d/%m/%Y %H:%M")

        r = git("add", "-A")
        if r.returncode != 0:
            print("\nשגיאה ב-git add:\n" + r.stderr)
            return 1
        fd, path = tempfile.mkstemp(suffix=".txt")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(msg + "\n")
            r = git("commit", "-F", path)
        finally:
            os.remove(path)
        if r.returncode != 0:
            print("\nשגיאה ב-commit:\n" + (r.stdout + r.stderr).strip())
            if "user.email" in r.stderr or "identity" in r.stderr.lower():
                print('\nצריך להגדיר פעם אחת שם ומייל ל-Git:\n'
                      '  git config --global user.name "השם שלך"\n'
                      '  git config --global user.email "המייל שלך"')
            return 1
        committed = True
        print("\nנשמר ב-Git המקומי: " + git("log", "-1", "--format=%h  %s").stdout.strip())

    print("\nמעלה ל-GitHub...")
    r = git("push", "origin", "HEAD", capture=False)      # בלי capture - כדי שחלון ההתחברות של GitHub יוכל להופיע
    if r.returncode != 0:
        print("\nההעלאה ל-GitHub נכשלה." + (" הגיבוי המקומי נשמר." if committed else ""))
        print("סיבות נפוצות: אין אינטרנט, צריך להתחבר ל-GitHub, או שב-GitHub יש שינויים שאין כאן")
        print("(אז מריצים: git pull  ואחר כך שוב backup_git.bat).")
        return 1
    print("\nהגיבוי הושלם: Git מקומי + GitHub.")
    return 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        pause()
    sys.exit(code)
