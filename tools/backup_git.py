"""
backup_git.py  -  גיבוי הפרויקט: commit ב-Git המקומי + העלאה ל-GitHub

מופעל ע"י backup_git.bat (בתיקיית הפרויקט). מבקש תיאור לגיבוי (אפשר להדביק את התיאור המוכן
ש-Claude נותן אחרי כל עדכון), שומר את כל השינויים ב-commit ומעלה ל-GitHub (origin).

ההודעות בחלון באנגלית: גופן ה-cmd לא מציג עברית (נראית כריבועים). התיאור עצמו נשמר ב-UTF-8 דרך קובץ זמני, כך שעברית נשמרת נכון (העברה דרך cmd הייתה משבשת אותה).
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
        input("\nPress Enter to close...")
    except EOFError:
        pass


def main():
    print("=" * 50)
    print("  Project backup: Git + GitHub")
    print("  " + ROOT)
    print("=" * 50)

    try:
        r = git("rev-parse", "--abbrev-ref", "HEAD")
    except FileNotFoundError:
        print("\nERROR: Git is not installed or not in PATH.")
        print("Install it from https://git-scm.com/download/win and run again.")
        return 1
    if r.returncode != 0:
        print("\nERROR: this folder is not a Git repository.\n" + r.stderr)
        return 1
    branch = r.stdout.strip()
    print("\nBranch: " + branch)

    status = git("status", "--short").stdout.strip()
    if status:
        lines = status.splitlines()
        print("\nChanged files (%d):" % len(lines))
        for line in lines[:40]:
            print("  " + line)
        if len(lines) > 40:
            print("  ... and %d more" % (len(lines) - 40))
    else:
        print("\nNo new changes to save.")

    committed = False
    if status:
        print("\nType or paste a description (Hebrew is fine - it is saved correctly")
        print("even if it looks like boxes here) and press Enter. Empty = date only:")
        try:
            msg = input("> ").strip()
        except EOFError:
            msg = ""
        if not msg:
            msg = "Backup " + datetime.now().strftime("%d/%m/%Y %H:%M")

        r = git("add", "-A")
        if r.returncode != 0:
            print("\nERROR in git add:\n" + r.stderr)
            return 1
        fd, path = tempfile.mkstemp(suffix=".txt")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(msg + "\n")
            r = git("commit", "-F", path)
        finally:
            os.remove(path)
        if r.returncode != 0:
            print("\nERROR in commit:\n" + (r.stdout + r.stderr).strip())
            if "user.email" in r.stderr or "identity" in r.stderr.lower():
                print('\nSet your Git name and email once:\n'
                      '  git config --global user.name "Your Name"\n'
                      '  git config --global user.email "you@example.com"')
            return 1
        committed = True
        print("\nSaved in local Git: commit " + git("log", "-1", "--format=%h").stdout.strip())

    print("\nUploading to GitHub...")
    r = git("push", "origin", "HEAD", capture=False)      # בלי capture - כדי שחלון ההתחברות של GitHub יוכל להופיע
    if r.returncode != 0:
        print("\nUpload to GitHub FAILED." + (" The local backup was saved." if committed else ""))
        print("Common reasons: no internet, GitHub sign-in needed, or GitHub has changes that are not here")
        print("(then run: git pull  and run backup_git.bat again).")
        return 1
    print("\nBACKUP COMPLETE: local Git + GitHub.")
    return 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        pause()
    sys.exit(code)
