# ------------------------------------------------------------------
# קובץ הפעלה למרכז הבקרה ב-PythonAnywhere.
# את כל התוכן מעתיקים לקובץ ה-WSGI של האתר (לשונית Web -> WSGI configuration file),
# במקום מה שיש בו, ומחליפים את USERNAME בשם המשתמש שלך ב-PythonAnywhere (2 מקומות).
# ------------------------------------------------------------------
import sys

CC_DIR = "/home/USERNAME/control_center"
PUBLIC_URL = "https://USERNAME.pythonanywhere.com"

if CC_DIR not in sys.path:
    sys.path.insert(0, CC_DIR)

import server  # noqa: E402

# באתר ציבורי חשבון היוצר נוצר מהמסוף (Bash console):
#   cd ~/control_center && python3 server.py --create-owner admin
application = server.configure(PUBLIC_URL, web_setup=False, backup_thread=False)
