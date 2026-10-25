"""
build_update.py  -  בניית חבילת עדכון חתומה לכל מחשבי הצלצולים (רץ במחשב של היוצר בלבד)

  python tools/build_update.py keygen               פעם אחת: יוצר זוג מפתחות
  python tools/build_update.py build "מה חדש"       בונה חבילה מהקבצים הנוכחיים וחותם עליה

המפתח הפרטי נשמר מחוץ לפרויקט (בתיקיית המשתמש, ‎.etratzon_keys) כדי שלא ייכנס בטעות ל-Git/GitHub.
המפתח הציבורי נכתב ל-src/update_public_key.txt ונשלח עם התוכנה - איתו מחשבי הצלצולים בודקים
שעדכון באמת נחתם כאן. בלי המפתח הפרטי אי אפשר לשלוח עדכון, גם למי שפרץ לדשבורד.

הגרסה של החבילה = SERVER_VERSION שב-src/web_server.py. לפני בנייה משנים אותו לגרסה חדשה.
"""

import hashlib
import json
import os
import re
import sys
import zipfile
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import ed25519_pure as ed  # noqa: E402

KEY_DIR = os.path.join(os.path.expanduser("~"), ".etratzon_keys")
PRIVATE_KEY_FILE = os.path.join(KEY_DIR, "update_signing.key")
PUBLIC_KEY_FILE = os.path.join(ROOT, "src", "update_public_key.txt")
OUT_DIR = os.path.join(ROOT, "updates_out")

# מה נכנס לחבילה: כל src (קוד, ממשק, תעודות, המפתח הציבורי) + קבצי ההפעלה וההתקנה
ROOT_FILES = ["start_all.bat", "stop_all.bat", "start_hidden.vbs", "find_python.bat", "install.bat", "test_sound.bat",
              "start_cloud_reporter.bat", "requirements.txt", "requirements-agents.txt",
              "Install-Guide.html", "app_icon.ico"]
INCLUDE_DIRS = ["src"]
SKIP_DIR_NAMES = {"__pycache__"}
SKIP_EXT = (".pyc", ".pyo", ".log", ".tmp")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def current_version():
    with open(os.path.join(ROOT, "src", "web_server.py"), encoding="utf-8") as f:
        m = re.search(r'^SERVER_VERSION\s*=\s*"([^"]+)"', f.read(), re.M)
    if not m:
        raise SystemExit("לא נמצא SERVER_VERSION ב-src/web_server.py")
    return m.group(1)


def keygen():
    if os.path.exists(PRIVATE_KEY_FILE):
        print("כבר קיים מפתח:", PRIVATE_KEY_FILE)
        print("לא יוצר חדש - מפתח חדש יחייב התקנה ידנית של המפתח הציבורי בכל בתי הספר.")
        return 1
    os.makedirs(KEY_DIR, exist_ok=True)
    secret = os.urandom(32)
    with open(PRIVATE_KEY_FILE, "w") as f:
        f.write(secret.hex())
    with open(PUBLIC_KEY_FILE, "w") as f:
        f.write(ed.secret_to_public(secret).hex() + "\n")
    print("נוצר מפתח פרטי:  ", PRIVATE_KEY_FILE)
    print("נוצר מפתח ציבורי:", PUBLIC_KEY_FILE)
    print()
    print("חשוב: שמור עותק של המפתח הפרטי במקום בטוח (למשל דיסק-און-קי). לא להעלות אותו לשום מקום.")
    print("אם הוא אובד - אי אפשר לשלוח עוד עדכונים עד שמתקינים ידנית מפתח חדש בכל בית ספר.")
    return 0


def load_secret():
    if not os.path.exists(PRIVATE_KEY_FILE):
        raise SystemExit("אין מפתח פרטי. הרץ קודם: build_update.bat keygen")
    with open(PRIVATE_KEY_FILE) as f:
        secret = bytes.fromhex(f.read().strip())
    if not os.path.exists(PUBLIC_KEY_FILE):
        raise SystemExit("חסר src/update_public_key.txt")
    with open(PUBLIC_KEY_FILE) as f:
        public = bytes.fromhex(f.read().strip())
    if ed.secret_to_public(secret) != public:
        raise SystemExit("המפתח הציבורי ב-src לא מתאים למפתח הפרטי - בתי הספר לא יוכלו לאמת את העדכון.")
    return secret, public


def collect_files():
    files = []
    for name in ROOT_FILES:
        if os.path.isfile(os.path.join(ROOT, name)):
            files.append(name)
    for d in INCLUDE_DIRS:
        for base, dirs, names in os.walk(os.path.join(ROOT, d)):
            dirs[:] = sorted(x for x in dirs if x not in SKIP_DIR_NAMES)
            for n in sorted(names):
                if n.lower().endswith(SKIP_EXT):
                    continue
                rel = os.path.relpath(os.path.join(base, n), ROOT).replace("\\", "/")
                files.append(rel)
    return files


def build(notes):
    secret, public = load_secret()
    version = current_version()
    files = collect_files()
    if "src/update_public_key.txt" not in files:
        raise SystemExit("המפתח הציבורי לא נכלל בחבילה")
    if "src/license_public_key.txt" not in files:
        print("שים לב: src/license_public_key.txt (מפתח האימות של רישיונות) לא קיים ולכן לא בחבילה.")
        print("        הוא נוצר לבד אחרי דיווח אחד של המחשב הזה למרכז הבקרה. בלעדיו בית ספר בלי אינטרנט")
        print("        לא יוכל להפעיל קוד הפעלה ידני.")
    os.makedirs(OUT_DIR, exist_ok=True)
    zip_path = os.path.join(OUT_DIR, "etratzon-update-%s.zip" % version)
    if os.path.exists(zip_path):
        print("שים לב: חבילה לגרסה %s כבר קיימת ותוחלף. אם כבר שלחת אותה ללקוחות - שנה קודם את SERVER_VERSION." % version)
    manifest = {"format": 1, "version": version, "created": datetime.now().isoformat(timespec="seconds"),
                "notes": notes, "files": [{"path": f, "sha256": sha256_file(os.path.join(ROOT, f)),
                                            "size": os.path.getsize(os.path.join(ROOT, f))} for f in files]}
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
        for f in files:
            z.write(os.path.join(ROOT, f), "files/" + f)
    digest = sha256_file(zip_path)
    sig = ed.sign(secret, ed.update_message(version, digest))
    sig_path = zip_path[:-4] + ".sig.json"
    with open(sig_path, "w", encoding="utf-8") as f:
        json.dump({"format": 1, "version": version, "sha256": digest, "size": os.path.getsize(zip_path),
                   "signature": sig.hex(), "public_key": public.hex(), "notes": notes,
                   "created": manifest["created"]}, f, ensure_ascii=False, indent=1)
    if not ed.verify(public, ed.update_message(version, digest), sig):
        raise SystemExit("בדיקת החתימה נכשלה - החבילה לא תקינה")
    print("גרסה:   ", version)
    print("קבצים:  ", len(files))
    print("חבילה:  ", zip_path)
    print("חתימה:  ", sig_path)
    print()
    print("עכשיו: מרכז הבקרה -> עדכונים -> העלאת שני הקבצים, ובחירת בתי הספר.")
    return 0


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "keygen":
        return keygen()
    if cmd == "build":
        notes = " ".join(sys.argv[2:]).strip()
        if not notes:
            try:
                notes = input("מה חדש בגרסה הזו? (אפשר להשאיר ריק ולכתוב בדשבורד): ").strip()
            except EOFError:
                notes = ""
        return build(notes)
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
