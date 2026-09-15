import streamlit as st
import json
import os
from datetime import datetime

SCHEDULE_FILE = "schedule.json"

st.set_page_config(page_title="ניהול מערכת צלצולים", page_icon="🔔", layout="centered")

# עיצוב מותאם לעברית (RTL)
st.markdown("""
    <style>
    body, div, p, h1, h2, h3, label {
        direction: rtl;
        text-align: right;
    }
    .stButton>button {
        width: 100%;
        border-radius: 8px;
        height: 3em;
        background-color: #4CAF50;
        color: white;
        font-weight: bold;
    }
    </style>
""", unsafe_allow_html=True)

st.title("🔔 מערכת ניהול צלצולים ומוזיקה")

def load_schedule():
    if not os.path.exists(SCHEDULE_FILE):
        return {"active_profile": "standard", "profiles": {}, "overrides": []}
    with open(SCHEDULE_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_schedule(data):
    with open(SCHEDULE_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

data = load_schedule()

# --- חלק 1: בחירת פרופיל פעיל ---
st.subheader("📌 פרופיל פעיל")
profiles = list(data.get("profiles", {}).keys())
if not profiles:
    profiles = ["standard"]

current_profile = data.get("active_profile", "standard")
selected_profile = st.selectbox("בחר פרופיל לוח זמנים:", profiles, index=profiles.index(current_profile) if current_profile in profiles else 0)

if selected_profile != current_profile:
    data["active_profile"] = selected_profile
    save_schedule(data)
    st.success(f"הפרופיל הוחלף ל-{selected_profile} בהצלחה!")
    st.rerun()

# --- חלק 2: הצגת הלו"ז היומי ---
st.subheader(f"📋 צלצולים מתוכננים ({selected_profile})")
events = data.get("profiles", {}).get(selected_profile, [])

if events:
    for ev in events:
        col1, col2, col3 = st.columns([1, 2, 2])
        col1.write(f"⏰ **{ev.get('time')}**")
        col2.write(f"🏷️ {ev.get('label', 'צלצול')}")
        col3.write(f"🎵 {ev.get('audio')}")
else:
    st.info("אין צלצולים מוגדרים בפרופיל זה.")

# --- חלק 3: הוספת שינוי חד-פעמי (Override) ---
st.divider()
st.subheader("➕ הוספת שינוי / חריגה להיום")

with st.form("add_override_form"):
    override_time = st.time_input("שעת ההפעלה", value=datetime.now().time())
    override_type = st.selectbox("סוג הפעולה", ["bell", "music"])
    override_label = st.text_input("תיאור / תווית", value="שינוי חד פעמי")
    
    audio_file = "bell_standard.mp3" if override_type == "bell" else "playlist/song_1.mp3"
    
    submitted = st.form_submit_button("שמור שינוי להיום")
    if submitted:
        time_str = override_time.strftime("%H:%M")
        new_override = {
            "time": time_str,
            "type": override_type,
            "audio": audio_file,
            "label": override_label,
            "executed": False
        }
        if "overrides" not in data:
            data["overrides"] = []
        data["overrides"].append(new_override)
        save_schedule(data)
        st.success(f"החריגה לשעה {time_str} נשמרה בהצלחה!")
        st.rerun()

# --- חלק 4: רשימת חריגות קיימות ---
if data.get("overrides"):
    st.subheader("⚠️ חריגות מתוכננות להיום")
    for idx, ov in enumerate(data["overrides"]):
        status = "✅ בוצע" if ov.get("executed") else "⏳ ממתין"
        st.write(f"{ov.get('time')} - {ov.get('label')} ({status})")
        if st.button(f"מחק חריגה {ov.get('time')}", key=f"del_{idx}"):
            data["overrides"].pop(idx)
            save_schedule(data)
            st.rerun()