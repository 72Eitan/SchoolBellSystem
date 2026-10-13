"""
close_confirm.py  -  השאלה שמוצגת כשחלון הניהול הראשי נסגר ב-X

מוצגת מעל חלון הניהול, שנשאר על המסך. מופעלת מ:
  * window_guard.py - כשלוחצים על X / Alt+F4 (הסגירה נחסמת, החלון נשאר)
  * stop_all.bat (קיצור הדרך "School Bell System - STOP")
  * web_server.py - אם החלון בכל זאת נסגר (למשל "סגור חלון" משורת המשימות)
עד שעונים (ואם אף אחד לא עונה) - המערכת ממשיכה לפעול.
  כן  -> המערכת נעצרת (כמו STOP), כולל חלון הניהול.
  לא  -> לא קורה כלום. אם החלון כבר נסגר - הוא נפתח מחדש. (ברירת המחדל - כדי ש-Enter בטעות לא ישבית)
שאלה אחת בכל פעם.
"""

import ctypes
import os
import socket
import subprocess
import sys
import threading
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import open_window  # noqa: E402

TITLE = "עת רצון - מערכת הצלצולים"
MAIN_WINDOW_TITLE = "עת רצון · מערכת הצלצולים"     # הכותרת שהדף נותן לחלון הראשי (index.html)
# Chrome עוטף כותרת בעברית בתווי כיווניות נסתרים (U+202B...U+202C) - מסירים אותם לפני ההשוואה
_BIDI_MARKS = dict.fromkeys(map(ord, "\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"))


def is_main_title(title):
    t = (title or "").translate(_BIDI_MARKS).strip()
    return t.startswith(MAIN_WINDOW_TITLE)
TEXT = "סגירת המערכת תשבית את הצלצולים.\n\nהאם לסגור?"

MB_YESNO, MB_ICONWARNING, MB_DEFBUTTON2 = 0x4, 0x30, 0x100
MB_SETFOREGROUND, MB_TOPMOST, MB_RIGHT, MB_RTLREADING = 0x10000, 0x40000, 0x80000, 0x100000
IDYES = 6
LOCK_PORT = 8596          # שאלה אחת בכל פעם (השומר 8597, הדיווח 8598, המנוע 8599)


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


def stop_system():
    try:
        req = urllib.request.Request("http://127.0.0.1:8500/api/window/stop-confirmed", data=b"", method="POST")
        urllib.request.urlopen(req, timeout=10).read()
    except Exception:
        # השרת לא עונה - עוצרים ישירות
        subprocess.Popen([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "stop_all.py")],
                         creationflags=0x08000000)


def main_window_exists():
    """האם חלון הניהול הראשי פתוח על המסך."""
    if os.name != "nt":
        return False
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    found = []
    buf = ctypes.create_unicode_buffer(256)

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            user32.GetClassNameW(hwnd, buf, 256)
            if buf.value.startswith("Chrome_WidgetWin"):
                user32.GetWindowTextW(hwnd, buf, 256)
                if is_main_title(buf.value):
                    found.append(hwnd)
                    return False
        return True
    user32.EnumWindows(cb, 0)
    return bool(found)


def server_running():
    try:
        socket.create_connection(("127.0.0.1", 8500), timeout=2).close()
        return True
    except OSError:
        return False


def _keep_dialog_on_top(stop_event):
    """MessageBox רגיל לא תמיד מקבל קדימות (Windows לא נותן לתהליך ברקע "לקפוץ" קדימה).
    מוצאים את חלון השאלה שלנו, מעבירים אליו את הפוקוס ומשאירים אותו מעל כל החלונות עד שעונים."""
    from ctypes import wintypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    user32.GetForegroundWindow.restype = wintypes.HWND
    # טיפוסים מדויקים (ב-64 ביט HWND_TOPMOST=-1 חייב לעבור כמצביע מלא)
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, wintypes.UINT]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    for name in ("IsWindow", "IsWindowVisible", "BringWindowToTop", "SetForegroundWindow"):
        getattr(user32, name).argtypes = [wintypes.HWND]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    my_pid = os.getpid()
    buf = ctypes.create_unicode_buffer(64)
    HWND_TOPMOST, SWP_NOSIZE, SWP_NOMOVE, SWP_SHOWWINDOW = -1, 0x1, 0x2, 0x40

    def find_dialog():
        found = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def cb(hwnd, _):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == my_pid and user32.IsWindowVisible(hwnd):
                user32.GetClassNameW(hwnd, buf, 64)
                if buf.value == "#32770":
                    found.append(hwnd)
                    return False
            return True
        user32.EnumWindows(cb, 0)
        return found[0] if found else None

    def bring_to_front(hwnd):
        fg = user32.GetForegroundWindow()
        if fg == hwnd:
            return
        fg_thread = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        me = kernel32.GetCurrentThreadId()
        attached = bool(fg_thread and fg_thread != me and user32.AttachThreadInput(me, fg_thread, True))
        try:
            user32.ShowWindow(hwnd, 5)                      # SW_SHOW
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(me, fg_thread, False)
        if user32.GetForegroundWindow() != hwnd:
            user32.keybd_event(0, 0, 0, 0)                  # "קלט" ריק - מתיר ל-Windows להעביר אלינו את הפוקוס
            user32.SetForegroundWindow(hwnd)

    dlg, tries = None, 0
    while not stop_event.is_set():
        try:
            dlg = dlg if dlg and user32.IsWindow(dlg) else find_dialog()
            if dlg:
                user32.SetWindowPos(dlg, wintypes.HWND(HWND_TOPMOST), 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
                if tries < 10:                              # בשניות הראשונות - גם פוקוס
                    bring_to_front(dlg)
                    tries += 1
        except Exception:
            pass
        stop_event.wait(0.2 if tries < 10 else 1.0)


def ask(reopen_if_missing=True):
    """מציג את השאלה (אחת בכל פעם), מעל כל החלונות. מחזיר True אם המערכת נעצרה.
    reopen_if_missing: ב"לא" - לפתוח את חלון הניהול רק אם הוא באמת נסגר (שורת המשימות)."""
    if os.name != "nt":
        print(TEXT)
        return False
    lock = acquire_lock()
    if lock is None:
        return False                   # השאלה כבר מוצגת
    stop_event = threading.Event()
    threading.Thread(target=_keep_dialog_on_top, args=(stop_event,), daemon=True).start()
    try:
        flags = MB_YESNO | MB_ICONWARNING | MB_DEFBUTTON2 | MB_SETFOREGROUND | MB_TOPMOST | MB_RIGHT | MB_RTLREADING
        answer = ctypes.windll.user32.MessageBoxW(None, TEXT, TITLE, flags)
        stop_event.set()
        if answer == IDYES:
            stop_system()
            return True
        if reopen_if_missing and server_running() and not main_window_exists():
            open_window.open_window()
        return False
    finally:
        stop_event.set()
        lock.close()


def main():
    # stop_all.bat מפעיל עם --keep-window: החלון לא נסגר, ולכן ב"לא" אף פעם לא פותחים עוד אחד
    ask(reopen_if_missing="--keep-window" not in sys.argv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
