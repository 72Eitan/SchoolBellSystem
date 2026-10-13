"""
stopped_notice.py  -  הודעה על המסך כשמערכת הצלצולים נעצרה בגלל סגירת חלון הניהול

מופעל ע"י web_server.py רגע לפני העצירה, כתהליך נפרד (כדי שלא ייסגר יחד עם המערכת).
"כן" מפעיל את המערכת מחדש מיד.
"""

import ctypes
import os
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TITLE = "עת רצון - מערכת הצלצולים"
TEXT = ("מערכת הצלצולים הושבתה!\n\n"
        "חלון הניהול נסגר, ולכן הצלצולים לא יישמעו.\n"
        "חלון זה חייב להיות פתוח (אפשר למזער אותו).\n\n"
        "להפעיל את המערכת מחדש עכשיו?")

MB_YESNO, MB_ICONWARNING, MB_SETFOREGROUND, MB_TOPMOST = 0x4, 0x30, 0x10000, 0x40000
MB_RIGHT, MB_RTLREADING = 0x80000, 0x100000
IDYES = 6


def main():
    if os.name != "nt":
        print(TEXT)
        return 0
    flags = MB_YESNO | MB_ICONWARNING | MB_SETFOREGROUND | MB_TOPMOST | MB_RIGHT | MB_RTLREADING
    if ctypes.windll.user32.MessageBoxW(None, TEXT, TITLE, flags) == IDYES:
        vbs = os.path.join(BASE_DIR, "start_hidden.vbs")
        if os.path.exists(vbs):
            subprocess.Popen(["wscript", vbs], cwd=BASE_DIR)
        else:
            os.startfile(os.path.join(BASE_DIR, "start_all.bat"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
