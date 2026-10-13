"""
window_guard.py  -  שומר על חלון הניהול הראשי מפני סגירה בטעות

הדפדפן לא מאפשר לאתר לעצור את ה-X עם שאלה משלנו. לכן התהליך הקטן הזה (בלי חלון, מופעל ע"י watchdog.py)
"מקשיב" לעכבר ולמקלדת של Windows, ורק כשמנסים לסגור את חלון הניהול הראשי הוא:
  * בולע את הלחיצה - החלון לא נסגר ונשאר על המסך;
  * מקפיץ מעליו את השאלה "סגירת המערכת תשבית את הצלצולים. האם להמשיך?" (close_confirm.ask).
    כן -> המערכת נעצרת (stop_all סוגר גם את החלון).  לא -> לא קורה כלום.

מה נתפס (רק כשזה החלון שלנו - כותרת "עת רצון · מערכת הצלצולים"):
  * לחיצה על X בפינת החלון (Windows עצמו אומר לנו שזה כפתור הסגירה - HTCLOSE)
  * Alt+F4, Ctrl+W, Ctrl+F4, Ctrl+Shift+W
  * תפריט החלון (קליק ימני על שורת הכותרת / על הסמל, Alt+רווח) - כי יש בו "סגור"
מה לא נתפס: "סגור חלון" משורת המשימות. אז החלון נסגר, והשרת מקפיץ את אותה שאלה (וב"לא" החלון נפתח מחדש).

כל שאר הלחיצות עוברות כרגיל, וכל תקלה כאן לא משפיעה על הצלצולים (גרוע ביותר - חוזרים להתנהגות הקודמת).
"""

import os
import socket
import sys
import threading
import time
from datetime import datetime

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SRC_DIR)
LOG_FILE = os.path.join(BASE_DIR, "logs", "window_guard.log")
LOCK_PORT = 8595          # עותק יחיד (השאלה 8596, השומר 8597, הדיווח 8598, המנוע 8599)

sys.path.insert(0, SRC_DIR)
import close_confirm  # noqa: E402

WH_KEYBOARD_LL, WH_MOUSE_LL = 13, 14
WM_KEYDOWN, WM_SYSKEYDOWN = 0x0100, 0x0104
WM_LBUTTONDOWN, WM_LBUTTONUP, WM_RBUTTONDOWN, WM_RBUTTONUP = 0x0201, 0x0202, 0x0204, 0x0205
WM_NCHITTEST = 0x0084
HTCAPTION, HTSYSMENU, HTCLOSE = 2, 3, 20
VK_SPACE, VK_F4, VK_W = 0x20, 0x73, 0x57
VK_CONTROL = 0x11
LLKHF_ALTDOWN = 0x20
SMTO_BLOCK, SMTO_ABORTIFHUNG = 0x1, 0x2
GA_ROOT = 2


def log(msg):
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 512 * 1024:
            os.replace(LOG_FILE, LOG_FILE + ".1")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write("%s  %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg))
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


def main():
    if os.name != "nt":
        print("window_guard.py is only needed on Windows.")
        return 0
    import ctypes
    from ctypes import wintypes

    lock = acquire_lock()
    if lock is None:
        return 0

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    # קואורדינטות אמיתיות של המסך גם כשיש הגדלת תצוגה (125%/150%)
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))      # Windows 10+
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)              # Windows 8.1
        except Exception:
            try:
                user32.SetProcessDPIAware()                             # Windows 7
            except Exception:
                pass

    LRESULT = ctypes.c_ssize_t
    ULONG_PTR = ctypes.c_size_t

    class MSLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
    user32.SetWindowsHookExW.restype = wintypes.HHOOK
    user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
    user32.CallNextHookEx.restype = LRESULT
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                                           wintypes.UINT, wintypes.UINT, ctypes.POINTER(ULONG_PTR)]
    user32.SendMessageTimeoutW.restype = LRESULT
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE

    buf = ctypes.create_unicode_buffer(256)

    def is_main_window(hwnd):
        if not hwnd:
            return False
        user32.GetClassNameW(hwnd, buf, 256)
        if not buf.value.startswith("Chrome_WidgetWin"):          # Chrome / Edge
            return False
        user32.GetWindowTextW(hwnd, buf, 256)
        if close_confirm.is_main_title(buf.value):
            return True
        if "רצון" in buf.value and buf.value not in seen_titles:    # לאבחון: כותרת דומה שלא זוהתה
            seen_titles.add(buf.value)
            log("חלון Chrome לא זוהה כחלון הניהול, כותרת: %r" % buf.value)
        return False

    seen_titles = set()

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    try:
        dwm_get = ctypes.windll.dwmapi.DwmGetWindowAttribute
        dwm_get.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    except Exception:
        dwm_get = None
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
    try:
        get_dpi = user32.GetDpiForWindow                        # Windows 10+
        get_dpi.argtypes = [wintypes.HWND]
        get_dpi.restype = wintypes.UINT
    except AttributeError:
        get_dpi = None

    def in_close_corner(hwnd, x, y):
        """גיבוי ל-hit-test: האם הנקודה בפינה העליונה של החלון, איפה שכפתור X.
        בודקים את שתי הפינות - בממשק בעברית (--lang=he) Chrome מעביר את הכפתורים לשמאל.
        (בפינה השנייה יש את סמל החלון, שלחיצה כפולה עליו ממילא סוגרת.)"""
        r = RECT()
        if not (dwm_get and dwm_get(hwnd, 9, ctypes.byref(r), ctypes.sizeof(r)) == 0):   # הגבולות הנראים
            if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
                return False
        scale = (get_dpi(hwnd) if get_dpi else 96) / 96.0
        w, h = 46 * scale, 32 * scale
        if not (r.top <= y < r.top + h):
            return False
        return r.right - w <= x < r.right or r.left <= x < r.left + w

    def hit_test(hwnd, x, y):
        res = ULONG_PTR(0)
        lparam = ((y & 0xFFFF) << 16) | (x & 0xFFFF)
        ok = user32.SendMessageTimeoutW(hwnd, WM_NCHITTEST, 0, lparam, SMTO_BLOCK | SMTO_ABORTIFHUNG, 150,
                                        ctypes.byref(res))
        if not ok:
            return None
        v = res.value & 0xFFFFFFFF
        return v - (1 << 32) if v & 0x80000000 else v

    state = {"swallow_up": None, "asking": False}

    def ask_async(reason):
        if state["asking"]:
            return
        state["asking"] = True
        log("ניסיון לסגור את חלון הניהול (%s) - הסגירה נחסמה, מוצגת שאלה" % reason)

        def run():
            try:
                close_confirm.ask(reopen_if_missing=False)
            except Exception as e:
                log("שגיאה בשאלה: %s" % e)
            finally:
                state["asking"] = False
        threading.Thread(target=run, daemon=True).start()

    @HOOKPROC
    def mouse_proc(n_code, w_param, l_param):
        try:
            if n_code == 0 and w_param in (WM_LBUTTONDOWN, WM_LBUTTONUP, WM_RBUTTONDOWN, WM_RBUTTONUP):
                if w_param in (WM_LBUTTONUP, WM_RBUTTONUP) and state["swallow_up"] == w_param:
                    state["swallow_up"] = None
                    return 1
                if w_param in (WM_LBUTTONDOWN, WM_RBUTTONDOWN):
                    info = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                    pt = wintypes.POINT(info.pt.x, info.pt.y)
                    root = user32.GetAncestor(user32.WindowFromPoint(pt), GA_ROOT)
                    if is_main_window(root):
                        ht = hit_test(root, pt.x, pt.y)
                        if ht not in (HTCLOSE, 8, 9) and in_close_corner(root, pt.x, pt.y):   # 8/9 = מזעור/הגדלה
                            ht = HTCLOSE
                        if ht not in (1, None) and ht != HTCAPTION:      # 1 = תוך הדף (לא מעניין)
                            log("לחיצה על מסגרת החלון: hit-test=%s" % ht)   # לאבחון: 20 = X
                        block = (ht == HTCLOSE or ht == HTSYSMENU) if w_param == WM_LBUTTONDOWN \
                            else ht in (HTCAPTION, HTSYSMENU, HTCLOSE)
                        if block:
                            state["swallow_up"] = WM_LBUTTONUP if w_param == WM_LBUTTONDOWN else WM_RBUTTONUP
                            if ht == HTCLOSE:
                                ask_async("כפתור X")
                            return 1
        except Exception as e:
            log("שגיאה (עכבר): %s" % e)
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    @HOOKPROC
    def keyboard_proc(n_code, w_param, l_param):
        try:
            if n_code == 0 and w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
                info = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                vk = info.vkCode
                if vk in (VK_F4, VK_W, VK_SPACE):
                    alt = bool(info.flags & LLKHF_ALTDOWN)
                    ctrl = bool(user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)
                    closing = (vk == VK_F4 and (alt or ctrl)) or (vk == VK_W and ctrl)
                    sysmenu = vk == VK_SPACE and alt and not ctrl
                    if (closing or sysmenu) and is_main_window(user32.GetForegroundWindow()):
                        if closing:
                            ask_async("מקלדת")
                        return 1
        except Exception as e:
            log("שגיאה (מקלדת): %s" % e)
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    hmod = kernel32.GetModuleHandleW(None)
    h_mouse = user32.SetWindowsHookExW(WH_MOUSE_LL, mouse_proc, hmod, 0)
    h_kbd = user32.SetWindowsHookExW(WH_KEYBOARD_LL, keyboard_proc, hmod, 0)
    if not h_mouse or not h_kbd:
        log("לא ניתן היה להתקין את השמירה על החלון (שגיאה %d) - הסגירה תעבוד כמו קודם" % ctypes.get_last_error())
        time.sleep(3600)       # לא ליפול שוב ושוב (השומר היה מנסה להפעיל מחדש)
        return 1
    log("השמירה על חלון הניהול פעילה")

    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
