import streamlit as st
import json
import os
import pandas as pd
from datetime import datetime, timedelta
import uuid

# איתור נתיב הקובץ בשורש הפרויקט
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEDULE_FILE = os.path.join(BASE_DIR, "schedule.json")

# הגדרת דף ברוחב מלא (Wide Layout)
st.set_page_config(
    page_title="מערכת ניהול צלצולים שבועית",
    page_icon="🔔",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# עיצוב מותאם: הסתרת תפריטי דפדפן, מראה אפליקציה עצמאית, RTL ועיצוב טבלה
st.markdown("""
    <style>
    /* הסתרת הממשק הסטנדרטי של Streamlit למראה אפליקציה עצמאית */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}

    body, div, p, h1, h2, h3, label {
        direction: rtl;
        text-align: right;
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    }

    /* כרטיסיית שעון והודעת הצלצול הבא */
    .top-header-card {
        background: linear-gradient(135deg, #1e293b, #0f172a);
        color: white;
        padding: 20px;
        border-radius: 12px;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
        margin-bottom: 20px;
    }
    
    .status-badge-active {
        background-color: #22c55e;
        color: white;
        padding: 4px 12px;
        border-radius: 9999px;
        font-weight: bold;
    }
    
    .status-badge-disabled {
        background-color: #ef4444;
        color: white;
        padding: 4px 12px;
        border-radius: 9999px;
        font-weight: bold;
    }

    /* התאמת אלמנטים */
    .stButton>button {
        width: 100%;
        border-radius: 6px;
        font-weight: bold;
    }
    </style>
""", unsafe_allow_html=True)

# ----------------- פונקציות עזר -----------------

def load_schedule():
    if not os.path.exists(SCHEDULE_FILE):
        default_data = {
            "system_status": "enabled",
            "disabled_until": None,
            "weekly_schedule": {day: [] for day in ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]},
            "overrides": []
        }
        with open(SCHEDULE_FILE, 'w', encoding='utf-8') as f:
            json.dump(default_data, f, indent=2, ensure_ascii=False)
        return default_data
    
    with open(SCHEDULE_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_schedule(data):
    with open(SCHEDULE_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

data = load_schedule()

days_map = {
    "Sunday": "יום ראשון",
    "Monday": "יום שני",
    "Tuesday": "יום שלישי",
    "Wednesday": "יום רביעי",
    "Thursday": "יום חמישי",
    "Friday": "יום שישי",
    "Saturday": "יום שבת"
}

reverse_days_map = {v: k for k, v in days_map.items()}

# ----------------- חישוב הצלצול הבא -----------------

def get_next_bell(schedule_data):
    now = datetime.now()
    current_day_str = now.strftime("%A")
    current_time_str = now.strftime("%H:%M")
    
    # בדיקת סטטוס מערכת
    if schedule_data.get("system_status") == "disabled":
        disabled_until = schedule_data.get("disabled_until")
        if disabled_until:
            return f"⛔ המערכת מושבתת עד {disabled_until}"
        return "⛔ המערכת מושבתת כרגע באופן יזום"

    todays_events = schedule_data.get("weekly_schedule", {}).get(current_day_str, [])
    active_events = [e for e in todays_events if e.get("enabled", True)]
    
    # מיון לפי שעה
    active_events.sort(key=lambda x: x.get("time", "00:00"))
    
    for ev in active_events:
        if ev.get("time") > current_time_str:
            return f"🔔 הצלצול הבא: {ev.get('time')} - {ev.get('label')} ({'מוזיקה' if ev.get('type')=='music' else 'צלצול'})"
    
    return "😴 אין צלצולים נוספים המתוכננים להיום"

# ----------------- חלק עליון: שעון, סטטוס ובקרת הפעלה -----------------

now_datetime = datetime.now()
current_time_display = now_datetime.strftime("%H:%M:%S")
current_date_display = now_datetime.strftime("%d/%m/%Y")

next_bell_info = get_next_bell(data)

st.markdown(f"""
<div class="top-header-card">
    <div style="display: flex; justify-content: space-between; align-items: center;">
        <div>
            <h1 style="margin:0; font-size: 2.2em;">🔔 מערכת ניהול צלצולים ומוזיקה</h1>
            <h3 style="margin:5px 0 0 0; color: #94a3b8;">{next_bell_info}</h3>
        </div>
        <div style="text-align: left;">
            <div style="font-size: 2.5em; font-weight: bold; color: #38bdf8;">{current_time_display}</div>
            <div style="font-size: 1.1em; color: #cbd5e1;">{days_map.get(now_datetime.strftime('%A'), '')}, {current_date_display}</div>
        </div>
    </div>
</div>
""", unsafe_allow_html=True)

# ----------------- סרגל שליטה ראשי: כיבוי/הפעלה יזום -----------------

with st.expander("⚙️ בקרת הפעלה וכיבוי של המערכת", expanded=False):
    col_stat1, col_stat2 = st.columns([2, 3])
    
    is_enabled = data.get("system_status") == "enabled"
    
    with col_stat1:
        st.write(f"סטטוס מערכת נוכחי: **{'🟢 פעילה' if is_enabled else '🔴 מושבתת'}**")
        if not is_enabled and data.get("disabled_until"):
            st.info(f"מושבתת עד: {data.get('disabled_until')}")

    with col_stat2:
        c1, c2, c3 = st.columns(3)
        if c1.button("🟢 הפעל מערכת"):
            data["system_status"] = "enabled"
            data["disabled_until"] = None
            save_schedule(data)
            st.success("המערכת הופעלה!")
            st.rerun()
            
        if c2.button("🔴 השבת עד סוף היום"):
            end_of_day = datetime.now().replace(hour=23, minute=59, second=59).strftime("%Y-%m-%d %H:%M")
            data["system_status"] = "disabled"
            data["disabled_until"] = end_of_day
            save_schedule(data)
            st.warning("המערכת הושבתה עד סוף היום.")
            st.rerun()

        if c3.button("⛔ השבתה מלאה (עד להפעלה)"):
            data["system_status"] = "disabled"
            data["disabled_until"] = None
            save_schedule(data)
            st.error("המערכת הושבתה ללא הגבלת זמן.")
            st.rerun()

st.divider()

# ----------------- בחירת יום / תצוגת לוח שבועי -----------------

st.subheader("📅 ניהול לוח זמנים שבועי")

selected_day_hebrew = st.radio(
    "בחר יום לצפייה ועריכה:",
    list(days_map.values()),
    index=list(days_map.keys()).index(now_datetime.strftime("%A")),
    horizontal=True
)

selected_day_english = reverse_days_map[selected_day_hebrew]
day_events = data.get("weekly_schedule", {}).get(selected_day_english, [])

# המרת הנתונים ל-DataFrame עבור ה-Data Editor הדינמי
if day_events:
    df = pd.DataFrame(day_events)
else:
    df = pd.DataFrame(columns=["id", "time", "type", "label", "audio", "duration_seconds", "enabled"])

# ודא קיומן של כל העמודות
for col in ["id", "time", "type", "label", "audio", "duration_seconds", "enabled"]:
    if col not in df.columns:
        df[col] = []

# סידור העמודות לתצוגה נוחה
df_display = df[["enabled", "time", "type", "label", "audio", "duration_seconds", "id"]].copy()

st.write("✏️ **לחץ פעמיים על כל תא בטבלה כדי לערוך אותו ישירות.** תוכל לשנות שעות, סוגי שמע, השתקה ומשך זמן.")

# ----------------- טבלה אינטראקטיבית דינמית (Editable Table) -----------------

edited_df = st.data_editor(
    df_display,
    num_rows="dynamic",
    use_container_width=True,
    column_config={
        "enabled": st.column_config.CheckboxColumn(
            "פעיל ביום זה",
            help="סמן/בטל לסיום או השתקת הצלצול ליום זה",
            default=True
        ),
        "time": st.column_config.TextColumn(
            "שעה (HH:MM)",
            help="פורמט 24 שעות, למשל 08:30",
            validate=r"^(0[0-9]|1[0-9]|2[0-3]):[0-5][0-9]$",
            required=True
        ),
        "type": st.column_config.SelectboxColumn(
            "סוג שמע",
            options=["bell", "music"],
            required=True,
            help="bell = צלצול קצר, music = מוזיקת הפסקה"
        ),
        "label": st.column_config.TextColumn(
            "תיאור / תווית",
            help="למשל: צלצול כניסה / הפסקת עשר",
            required=True
        ),
        "audio": st.column_config.TextColumn(
            "קובץ שמע",
            help="שם הקובץ בתיקיית audio (למשל bell_standard.mp3 או playlist/song_1.mp3)",
            required=True
        ),
        "duration_seconds": st.column_config.NumberColumn(
            "משך בשניות",
            help="משך הצלצול/השיר בשניות (למשל 5 לצלצול, 900 ל-15 דקות מוזיקה)",
            min_value=1,
            max_value=3600,
            step=5,
            default=5
        ),
        "id": st.column_config.TextColumn(
            "מזהה",
            disabled=True
        )
    },
    hide_index=True,
    key=f"editor_{selected_day_english}"
)

# ----------------- עיבוד ושמירת השינויים בטבלה -----------------

if st.button("💾 שמור שינויים ללוח הזמנים", type="primary"):
    updated_events = []
    
    for idx, row in edited_df.iterrows():
        # יצירת ID במידה וזו שורה חדשה שהוספה ע"י המשתמש
        row_id = str(row["id"]) if pd.notna(row["id"]) and row["id"] != "" else str(uuid.uuid4())[:8]
        
        event_dict = {
            "id": row_id,
            "time": str(row["time"]) if pd.notna(row["time"]) else "00:00",
            "type": str(row["type"]) if pd.notna(row["type"]) else "bell",
            "label": str(row["label"]) if pd.notna(row["label"]) else "צלצול",
            "audio": str(row["audio"]) if pd.notna(row["audio"]) else "bell_standard.mp3",
            "duration_seconds": int(row["duration_seconds"]) if pd.notna(row["duration_seconds"]) else 5,
            "enabled": bool(row["enabled"])
        }
        updated_events.append(event_dict)
    
    # מיון אוטומטי של השורות לפי סדר השעה העולה (HH:MM)
    updated_events.sort(key=lambda x: x["time"])
    
    # שמירה ב-JSON
    data["weekly_schedule"][selected_day_english] = updated_events
    save_schedule(data)
    
    st.success(f"השינויים עבור {selected_day_hebrew} נשמרו בהצלחה מוינו לפי סדר השעה!")
    st.rerun()