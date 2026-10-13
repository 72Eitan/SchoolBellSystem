"""
tunnel.py  -  כתובת ציבורית למרכז הבקרה דרך Tailscale Funnel (לפיילוט, בלי דומיין)

Tailscale Funnel נותן למחשב הזה כתובת HTTPS קבועה, למשל https://et-ratzon.tail1234.ts.net ,
שמפנה למרכז הבקרה (פורט 8600). הכתובת לא משתנה בהפעלה מחדש, ולכן אפשר לרשום אותה פעם אחת
בקובץ cloud_config.json של בית הספר.

  python tunnel.py setup    מפעיל את ה-Funnel (נשאר פעיל גם אחרי הפעלה מחדש של המחשב) ושומר את הכתובת
  python tunnel.py status   מציג את הכתובת ובודק שמרכז הבקרה עונה דרכה
  python tunnel.py off      מכבה את ה-Funnel
"""

import json
import os
import shutil
import subprocess
import sys
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
URL_FILE = os.path.join(BASE_DIR, "data", "public_url.txt")
PORT = 8600


def tailscale_exe():
    for p in (shutil.which("tailscale"), r"C:\Program Files\Tailscale\tailscale.exe",
              r"C:\Program Files (x86)\Tailscale\tailscale.exe"):
        if p and os.path.exists(p):
            return p
    return None


def run(args, timeout=120):
    exe = tailscale_exe()
    r = subprocess.run([exe] + args, capture_output=True, timeout=timeout)
    out = (r.stdout + r.stderr).decode("utf-8", errors="replace")
    return r.returncode, out


def public_url():
    code, out = run(["status", "--json"], timeout=30)
    if code != 0:
        return None, out
    st = json.loads(out[out.index("{"):])
    if st.get("BackendState") != "Running":
        return None, "Tailscale לא מחובר (מצב: %s). פתח את Tailscale והתחבר לחשבון." % st.get("BackendState")
    dns = ((st.get("Self") or {}).get("DNSName") or "").rstrip(".")
    if not dns:
        return None, "לא נמצא שם מחשב ב-Tailscale"
    return "https://" + dns, ""


def check(url):
    try:
        with urllib.request.urlopen(url + "/api/version", timeout=15) as r:
            return r.status == 200, json.loads(r.read().decode("utf-8")).get("version")
    except Exception as e:
        return False, str(e)


def setup():
    url, err = public_url()
    if not url:
        print("שגיאה:", err)
        return 1
    print("מפעיל Funnel לפורט %d..." % PORT)
    print("אם מופיע קישור - פתח אותו בדפדפן ואשר את Funnel בחשבון Tailscale, ואז הפעל שוב.")
    code, out = run(["funnel", "--bg", str(PORT)], timeout=180)
    print(out.strip())
    if code != 0:
        print("הפעלת ה-Funnel נכשלה.")
        return 1
    os.makedirs(os.path.dirname(URL_FILE), exist_ok=True)
    with open(URL_FILE, "w", encoding="ascii") as f:
        f.write(url)
    print()
    print("הכתובת הציבורית:", url)
    print("נשמרה ב-data\\public_url.txt . מעכשיו start_control_center.bat משתמש בה אוטומטית.")
    return 0


def status():
    url = open(URL_FILE).read().strip() if os.path.exists(URL_FILE) else None
    print("כתובת שמורה:", url or "(אין - הרץ setup)")
    code, out = run(["funnel", "status"], timeout=30)
    print(out.strip())
    if url:
        ok, info = check(url)
        print("מרכז הבקרה עונה דרך הכתובת:" if ok else "מרכז הבקרה לא עונה דרך הכתובת:", info)
    return 0


def off():
    code, out = run(["funnel", "reset"], timeout=30)
    print(out.strip() or "ה-Funnel כובה.")
    return code


def main():
    if not tailscale_exe():
        print("Tailscale לא מותקן. מתקינים מ- https://tailscale.com/download/windows , מתחברים, ומריצים שוב.")
        return 1
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    return {"setup": setup, "status": status, "off": off}.get(cmd, status)()


if __name__ == "__main__":
    sys.exit(main())
