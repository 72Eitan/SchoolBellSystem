import streamlit as st
import json
import os
import pandas as pd
from datetime import datetime
import uuid

# איתור נתיב הקובץ בשורש הפרויקט
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEDULE_FILE = os.path.join(BASE_DIR, "schedule.json")

st.set_page_config(
    page_title="מערכת ניהול צלצולים שבועית",
    page_icon="🔔",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ----------------- CSS לביטול מראה דפדפן, יישור למרכז ועיצוב -----------------
st.markdown("""
    <style>
    /* 6. הסתרת כל מעטפת הדפדפן והסרגלים של Streamlit */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    .stApp > header {display: none;}
    
    /* ביטול רווחים עליונים לתחושת אפליקציה נקיון */
    .main .block-container {
        padding-top: 1rem !important;
        padding-bottom: 1rem !important;
        max-width: 98% !important;
    }

    body, div, p, h1, h2, h3, label {
        direction: rtl;
        text-align: right;
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    }

    /* 1. יישור כל התאים בטבלה למרכז */
    [data-testid="stDataFrame"] td, [data-testid="stDataFrame"] th {
        text-align: center !important;
    }
    div[aria-colindex] {
        text-align: center !important;
    }

    /* עיצוב כרטיסיית כותרת ושעון מרכזי */
    .header-card {
        background: linear-gradient(135deg, #0f172a, #1e293b);
        color: white;
        padding: 15px 25px;
        border-radius: 12px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        margin-bottom: 15px;
        text-align: center;
    }
    
    /* 3. עיצוב שעון דיגיטלי גדול במרכז */
    .live-clock {
        font-size: 3.2em;
        font-weight: 800;
        color: #38bdf8;
        letter-spacing: 2px;
        font-family: 'Courier New', Courier, monospace;
        margin: 5px 0;
        text-shadow: 0 0 10px rgba(56, 189, 248, 0.3);
    }
    </style>
""", unsafe_allow_html=True)

# ----------------- פונקציות טעינה ושמירה -----------------

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

# ----------------- 3. שעון בלייב + הצלצול הבא (רכיב HTML/JS) -----------------

def get_next_bell_text(schedule_data):
    now = datetime.now()
    current_day_str = now.strftime("%A")
    current_time_str = now.strftime("%H:%M")
    
    if schedule_data.get("system_status") == "disabled":
        disabled_until = schedule_data.get("disabled_until")
        return f"⛔ המערכת מושבתת יזומית {f'עד {disabled_until}' if disabled_until else ''}"

    todays_events = schedule_data.get("weekly_schedule", {}).get(current_day_str, [])
    active_events = [e for e in todays_events if e.get("enabled", True)]
    active_events.sort(key=lambda x: x.get("time", "00:00"))
    
    for ev in active_events:
        if ev.get("time") > current_time_str:
            return f"🔔 הצלצול הבא: {ev.get('time')} - {ev.get('label')} ({'מוזיקה' if ev.get('type')=='music' else 'צלצול'})"
    
    return "😴 אין צלצולים נוספים להיום"

next_bell_str = get_next_bell_text(data)

# רכיב השעון הרץ בזמן אמת בתוך ה-Browser ללא רענון הדף
st.components.v1.html(f"""
    <div style="background: linear-gradient(135deg, #0f172a, #1e293b); color: white; padding: 15px; border-radius: 12px; text-align: center; font-family: 'Segoe UI', sans-serif; direction: rtl;">
        <h2 style="margin: 0; font-size: 1.8em; color: #f8fafc;">🔔 מערכת ניהול צלצולים ומוזיקה</h2>
        <div id="clock" style="font-size: 3.5em; font-weight: bold; color: #38bdf8; font-family: monospace; margin: 5px 0;">--:--:--</div>
        <div style="font-size: 1.2em; color: #cbd5e1; font-weight: 600;">{next_bell_str}</div>
    </div>
    <script>
        function updateClock() {{
            const now = new Date();
            const hours = String(now.getHours()).padStart(2, '0');
            const minutes = String(now.getMinutes()).padStart(2, '0');
            const seconds = String(now.getSeconds()).padStart(2, '0');
            document.getElementById('clock').textContent = hours + ':' + minutes + ':' + seconds;
        }}
        setInterval(updateClock, 1000);
        updateClock();
    </script>
""", height=160)

# ----------------- סרגל שליטה יזום: כיבוי/הפעלה -----------------

with st.expander("⚙️ בקרת הפעלה/כיבוי יזום של המערכת", expanded=False):
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

# ----------------- 4. תצוגת יום בודד מול תצוגת שבוע מלאה -----------------

tab1, tab2 = st.tabs(["📅 ניהול ועריכה לפי יום", "🗓️ תצוגת שבוע מלאה"])

# --- TAB 1: עריכה לפי יום ---
with tab1:
    selected_day_hebrew = st.radio(
        "בחר יום לעריכה:",
        list(days_map.values()),
        index=list(days_map.keys()).index(datetime.now().strftime("%A")),
        horizontal=True
    )
    
    selected_day_english = reverse_days_map[selected_day_hebrew]
    day_events = data.get("weekly_schedule", {}).get(selected_day_english, [])

    # 5. סרגל קל להוספת שורה חדשה בראש הטבלה
    with st.expander("➕ הוספת צלצול/מוזיקה חדש ליום זה", expanded=False):
        with st.form(key=f"add_row_form_{selected_day_english}"):
            fc1, fc2, fc3, fc4, fc5 = st.columns([1, 1, 2, 2, 1])
            new_time = fc1.text_input("שעה (HH:MM)", value="08:00")
            new_type = fc2.selectbox("סוג", ["bell", "music"])
            new_label = fc3.text_input("תיאור", value="צלצול חדש")
            new_audio = fc4.text_input("קובץ שמע", value="bell_standard.mp3" if new_type=="bell" else "playlist/song_1.mp3")
            new_duration = fc5.number_input("משך (שניות)", value=5, min_value=1)
            
            if st.form_submit_button("הוסף לטבלה"):
                new_entry = {
                    "id": str(uuid.uuid4())[:8],
                    "time": new_time,
                    "type": new_type,
                    "label": new_label,
                    "audio": new_audio,
                    "duration_seconds": int(new_duration),
                    "enabled": True
                }
                day_events.append(new_entry)
                day_events.sort(key=lambda x: x.get("time", "00:00")) # 5. מיון לפי שעה עולה
                data["weekly_schedule"][selected_day_english] = day_events
                save_schedule(data)
                st.success("האירוע נוסף בהצלחה!")
                st.rerun()

# הכנת ה-DataFrame
    df = pd.DataFrame(day_events if day_events else [])
    for col in ["id", "enabled", "time", "type", "label", "audio", "duration_seconds"]:
        if col not in df.columns:
            df[col] = []

    df_display = df[["enabled", "time", "type", "label", "audio", "duration_seconds", "id"]].copy()

    # תיקון סוגי הנתונים למניעת שגיאת FLOAT ב-Checkbox
    df_display["enabled"] = df_display["enabled"].fillna(True).astype(bool)
    df_display["duration_seconds"] = df_display["duration_seconds"].fillna(5).astype(int)
    df_display["time"] = df_display["time"].fillna("00:00").astype(str)
    df_display["type"] = df_display["type"].fillna("bell").astype(str)
    df_display["label"] = df_display["label"].fillna("").astype(str)
    df_display["audio"] = df_display["audio"].fillna("bell_standard.mp3").astype(str)
    df_display["id"] = df_display["id"].fillna("").astype(str)

    # 1+2. Data Editor מעודכן עם יישור למרכז ועריכה מהירה
    edited_df = st.data_editor(
        df_display,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "enabled": st.column_config.CheckboxColumn("פעיל", default=True),
            "time": st.column_config.TextColumn("שעה", validate=r"^(0[0-9]|1[0-9]|2[0-3]):[0-5][0-9]$", required=True),
            "type": st.column_config.SelectboxColumn("סוג שמע", options=["bell", "music"], required=True),
            "label": st.column_config.TextColumn("תיאור / תווית", required=True),
            "audio": st.column_config.TextColumn("קובץ שמע", required=True),
            "duration_seconds": st.column_config.NumberColumn("משך (שניות)", min_value=1, max_value=3600, default=5),
            "id": st.column_config.TextColumn("ID", disabled=True)
        },
        hide_index=True,
        key=f"editor_{selected_day_english}"
    )

    if st.button("💾 שמור שינויים לטבלה", type="primary"):
        updated_events = []
        for idx, row in edited_df.iterrows():
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
        
        # 5. מיון אוטומטי לפי סדר השעות העולה
        updated_events.sort(key=lambda x: x["time"])
        data["weekly_schedule"][selected_day_english] = updated_events
        save_schedule(data)
        st.success(f"השינויים עבור {selected_day_hebrew} נשמרו ומוינו בהצלחה!")
        st.rerun()

# --- TAB 2: 4. תצוגת שבוע מלאה ---
with tab2:
    st.subheader("🗓️ לוח זמנים שבועי כולל")
    cols = st.columns(7)
    
    for idx, (eng_day, heb_day) in enumerate(days_map.items()):
        with cols[idx]:
            st.markdown(f"### {heb_day}")
            events = data.get("weekly_schedule", {}).get(eng_day, [])
            if events:
                for ev in sorted(events, key=lambda x: x.get("time", "00:00")):
                    status_icon = "🟢" if ev.get("enabled", True) else "🔴"
                    type_icon = "🎵" if ev.get("type") == "music" else "🔔"
                    st.info(f"{status_icon} **{ev.get('time')}**\n\n{type_icon} {ev.get('label')}")
            else:
                st.caption("אין צלצולים")