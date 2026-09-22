import streamlit as st
import pandas as pd
import json
import os
from datetime import datetime
import io
import logging

# הגדרת עמוד Streamlit במבנה רחב ועיצוב מודרני
st.set_page_config(
    page_title="מערכת ניהול צלצולים ומוזיקה",
    page_icon="🔔",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# הגדרות נתיבים
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEDULE_FILE = os.path.join(BASE_DIR, "schedule.json")
AUDIO_DIR = os.path.join(BASE_DIR, "audio")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
LOG_FILE = os.path.join(LOGS_DIR, "app.log")

# יצירת תיקיית לוגים במידה ולא קיימת
os.makedirs(LOGS_DIR, exist_ok=True)
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    encoding='utf-8'
)

# עיצוב CSS מותאם אישית
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Rubik:wght@300;400;500;700&display=swap');
    
    html, body, [class*="css"] {
        font-family: 'Rubik', sans-serif;
        direction: rtl;
        text-align: right;
    }
    .main-header {
        background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%);
        color: white;
        padding: 20px 25px;
        border-radius: 12px;
        margin-bottom: 20px;
        box-shadow: 0 4px 15px rgba(0,0,0,0.1);
    }
    .metric-card {
        background: #f8f9fa;
        border-right: 5px solid #2a5298;
        padding: 15px;
        border-radius: 8px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.05);
    }
    .stButton>button {
        border-radius: 8px;
        font-weight: 500;
        transition: all 0.3s;
    }
    .stButton>button:hover {
        transform: translateY(-2px);
    }
</style>
""", unsafe_allow_html=True)

DAYS_HEBREW_TO_ENGLISH = {
    "ראשון": "Sunday",
    "שני": "Monday",
    "שלישי": "Tuesday",
    "רביעי": "Wednesday",
    "חמישי": "Thursday",
    "שישי": "Friday",
    "שבת": "Saturday"
}

DAYS_ENGLISH_TO_HEBREW = {v: k for k, v in DAYS_HEBREW_TO_ENGLISH.items()}

# טעינת נתוני הלוח מ-schedule.json
def load_schedule_data():
    if not os.path.exists(SCHEDULE_FILE):
        default_data = {
            "system_status": "enabled",
            "disabled_until": None,
            "institution_info": {
                "name": "בית ספר דוגמה",
                "contact": "ישראל ישראלי",
                "phone": "050-0000000",
                "email": "school@example.com",
                "address": "נתיבות, ישראל",
                "license_status": "פעיל בתוקף"
            },
            "weekly_schedule": {day: [] for day in DAYS_HEBREW_TO_ENGLISH.values()}
        }
        save_schedule_data(default_data)
        return default_data

    try:
        with open(SCHEDULE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"שגיאה בטעינת קובץ schedule.json: {e}")
        st.error("שגיאה בטעינת קובץ ההגדרות.")
        return {}

def save_schedule_data(data):
    try:
        with open(SCHEDULE_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        logging.info("קובץ schedule.json עודכן בהצלחה.")
    except Exception as e:
        logging.error(f"שגיאה בשמירת schedule.json: {e}")
        st.error("שגיאה בשמירת הנתונים.")

# רשימת קובצי שמע זמינים
def get_available_audio_files():
    if not os.path.exists(AUDIO_DIR):
        os.makedirs(AUDIO_DIR, exist_ok=True)
    files = [f for f in os.listdir(AUDIO_DIR) if f.endswith(('.mp3', '.wav', '.ogg'))]
    return files if files else ["Tune 4.mp3", "Ring 1.mp3"]

# פונקציית ייבוא מקובץ Excel / CSV במבנה המבוקש
def import_from_excel(uploaded_file):
    try:
        df_import = pd.read_excel(uploaded_file)
        
        # אם יש שורת כותרות משנית (כמו ב-Schedule_Import.xlsx)
        if df_import.iloc[0]['תיאור הארוע'] == 'Text' or 'HH:MM:SS' in str(df_import.iloc[0]['שעת התחלה']):
            df_import = df_import.iloc[1:].reset_index(drop=True)
            
        data = load_schedule_data()
        weekly_schedule = {day: [] for day in DAYS_HEBREW_TO_ENGLISH.values()}
        
        day_cols = [1, 2, 3, 4, 5, 6, 7] # Sun..Sat
        day_keys = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
        
        for idx, row in df_import.iterrows():
            if pd.isna(row.get('תיאור הארוע')) or pd.isna(row.get('שעת התחלה')):
                continue
                
            time_str = str(row['שעת התחלה']).strip()
            if len(time_str.split(':')) == 3:
                time_str = time_str[:5] # המרה ל-HH:MM
                
            event = {
                "id": str(idx + 1),
                "label": str(row['תיאור הארוע']),
                "time": time_str,
                "duration_seconds": int(row.get('משך השמעה בשניות', 30)),
                "audio": str(row.get('קובץ שמע', 'Tune 4.mp3')),
                "type": str(row.get('סוג ארוע', 'Bell')),
                "volume": int(row.get('עוצמת שמע', 85)),
                "enabled": bool(int(row.get('פעיל', 1)))
            }
            
            for d_idx, d_col in enumerate(day_cols):
                if d_col in row and str(row[d_col]).strip() in ['1', '1.0', 'True', 'true']:
                    weekly_schedule[day_keys[d_idx]].append(event.copy())
                    
        data['weekly_schedule'] = weekly_schedule
        save_schedule_data(data)
        logging.info("ייבוא מ-Excel הושלם בהצלחה.")
        st.success("לוח הזמנים יובא בהצלחה מ-Excel!")
        st.rerun()
    except Exception as e:
        logging.error(f"שגיאה בייבוא Excel: {e}")
        st.error(f"שגיאה בייבוא קובץ Excel: {str(e)}")

# פונקציית ייצוא ל-Excel
def export_to_excel(data):
    rows = []
    weekly = data.get("weekly_schedule", {})
    day_keys = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    
    # איסוף כל האירועים הייחודיים
    all_events = {}
    for day in day_keys:
        for ev in weekly.get(day, []):
            key = (ev['time'], ev['label'], ev['audio'])
            if key not in all_events:
                all_events[key] = {
                    "label": ev.get('label'),
                    "time": ev.get('time'),
                    "duration_seconds": ev.get('duration_seconds', 30),
                    "audio": ev.get('audio', 'Tune 4.mp3'),
                    "type": ev.get('type', 'Bell'),
                    "volume": ev.get('volume', 85),
                    "enabled": ev.get('enabled', True),
                    "days": {d: 0 for d in range(1, 8)}
                }
            day_idx = day_keys.index(day) + 1
            all_events[key]["days"][day_idx] = 1 if ev.get('enabled', True) else 0

    for idx, (k, ev) in enumerate(all_events.items(), 1):
        row = {
            "מספר": idx,
            "תיאור הארוע": ev['label'],
            "שעת התחלה": ev['time'],
            "משך השמעה בשניות": ev['duration_seconds'],
            "קובץ שמע": ev['audio'],
            "סוג ארוע": ev['type'],
            "עוצמת שמע": ev['volume'],
            1: ev['days'][1], 2: ev['days'][2], 3: ev['days'][3], 4: ev['days'][4],
            5: ev['days'][5], 6: ev['days'][6], 7: ev['days'][7],
            "פעיל": 1 if ev['enabled'] else 0
        }
        rows.append(row)
        
    df_out = pd.DataFrame(rows)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df_out.to_excel(writer, index=False, sheet_name='גיליון1')
    return output.getvalue()

# --- טעינת הנתונים ---
data = load_schedule_data()

# כותרת ראשית מעוצבת
st.markdown("""
<div class="main-header">
    <h2 style="margin:0; padding:0;">🔔 מערכת ניהול צלצולים ומוזיקה לבית הספר</h2>
    <p style="margin:5px 0 0 0; opacity:0.8;">ממשק ניהול מרכזי, בקרת שמע ולוח זמנים שבועי</p>
</div>
""", unsafe_allow_html=True)

# סרגל מדדים העליון
col1, col2, col3, col4 = st.columns(4)

with col1:
    now_str = datetime.now().strftime("%H:%M:%S")
    st.markdown(f"""
    <div class="metric-card">
        <small style="color:#6c757d;">⏰ שעה במערכת</small>
        <h3 style="margin:5px 0 0 0; color:#1e3c72;">{now_str}</h3>
    </div>
    """, unsafe_allow_html=True)

with col2:
    current_day_eng = datetime.now().strftime("%A")
    current_day_heb = DAYS_ENGLISH_TO_HEBREW.get(current_day_eng, current_day_eng)
    st.markdown(f"""
    <div class="metric-card">
        <small style="color:#6c757d;">📅 היום בשבוע</small>
        <h3 style="margin:5px 0 0 0; color:#1e3c72;">יום {current_day_heb}</h3>
    </div>
    """, unsafe_allow_html=True)

with col3:
    status_mode = data.get("system_status", "enabled")
    status_display = "🟢 פעיל" if status_mode == "enabled" else "🔴 מושבת"
    st.markdown(f"""
    <div class="metric-card">
        <small style="color:#6c757d;">⚡ סטטוס מערכת</small>
        <h3 style="margin:5px 0 0 0;">{status_display}</h3>
    </div>
    """, unsafe_allow_html=True)

with col4:
    todays_events = data.get("weekly_schedule", {}).get(current_day_eng, [])
    active_count = sum(1 for e in todays_events if e.get("enabled", True))
    st.markdown(f"""
    <div class="metric-card">
        <small style="color:#6c757d;">🔔 אירועים פעילים להיום</small>
        <h3 style="margin:5px 0 0 0; color:#1e3c72;">{active_count}</h3>
    </div>
    """, unsafe_allow_html=True)

st.write("")

# טאבים ראשיים
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📅 לוח צלצולים שבועי", 
    "📥 ייבוא / ייצוא", 
    "⚙️ הגדרות מערכת ורישוי", 
    "📜 לוגים ודיווחים", 
    "🤖 בוט טלגרם"
])

# ----------------- TAB 1: עריכת לוח צלצולים -----------------

with tab1:
    col_day, col_btn1, col_btn2 = st.columns([2, 1, 1])
    
    with col_day:
        selected_day_heb = st.selectbox("בחר יום להצגה ועריכה:", list(DAYS_HEBREW_TO_ENGLISH.keys()))
        selected_day_eng = DAYS_HEBREW_TO_ENGLISH[selected_day_heb]
        
    events_list = data.get("weekly_schedule", {}).get(selected_day_eng, [])
    
    # יצירת DataFrame מהאירועים ביום שנבחר
    df_display = pd.DataFrame(events_list if events_list else [])
    
    # רשימת העמודות הנדרשות
    required_cols = ["id", "enabled", "time", "label", "type", "audio", "volume", "duration_seconds"]
    
    # יצירת עמודות חסרות במידה ואינן קיימות בצורה בטוחה
    for col in required_cols:
        if col not in df_display.columns:
            df_display[col] = None

    # המרת ערכים חסרים לערכי ברירת מחדל תקינים
    df_display["enabled"] = df_display["enabled"].fillna(True).astype(bool)
    df_display["time"] = df_display["time"].fillna("00:00").astype(str)
    df_display["label"] = df_display["label"].fillna("").astype(str)
    df_display["type"] = df_display["type"].fillna("Bell").astype(str)
    df_display["audio"] = df_display["audio"].fillna("Tune 4.mp3").astype(str)
    df_display["volume"] = pd.to_numeric(df_display["volume"], errors='coerce').fillna(85).astype(int)
    df_display["duration_seconds"] = pd.to_numeric(df_display["duration_seconds"], errors='coerce').fillna(30).astype(int)
    df_display["id"] = df_display["id"].fillna("").astype(str)

    # סידור העמודות לתצוגה בטבלה בלבד
    cols_order = ["enabled", "time", "label", "type", "audio", "volume", "duration_seconds"]
    df_editor = df_display[cols_order]

    st.subheader(f"עריכת לוח זמנים ליום {selected_day_heb}")
    
    edited_df = st.data_editor(
        df_editor,
        num_rows="dynamic",
        column_config={
            "enabled": st.column_config.CheckboxColumn("פעיל", default=True),
            "time": st.column_config.TextColumn("שעת התחלה (HH:MM)", required=True),
            "label": st.column_config.TextColumn("תיאור האירוע", required=True),
            "type": st.column_config.SelectboxColumn("סוג אירוע", options=["Bell", "Music", "Exercise"]),
            "audio": st.column_config.SelectboxColumn("קובץ שמע", options=get_available_audio_files()),
            "volume": st.column_config.NumberColumn("עוצמת שמע (%)", min_value=0, max_value=100, step=5, default=85),
            "duration_seconds": st.column_config.NumberColumn("משך (שניות)", min_value=1, max_value=3600, step=5, default=30),
        },
        key=f"editor_{selected_day_eng}",
        use_container_width=True
    )

    if st.button("💾 שמור שינויים ליום זה", type="primary"):
        updated_events = edited_df.to_dict(orient="records")
        # מיון לפי שעה עולה
        updated_events.sort(key=lambda x: str(x.get("time", "00:00")))
        data["weekly_schedule"][selected_day_eng] = updated_events
        save_schedule_data(data)
        st.success(f"השינויים ליום {selected_day_heb} שנשמרו בהצלחה!")
        st.rerun()

# ----------------- TAB 2: ייבוא / ייצוא -----------------
with tab2:
    st.subheader("📥 ייבוא וייצוא לוח צלצולים")
    
    col_imp, col_exp = st.columns(2)
    
    with col_imp:
        st.markdown("#### ייבוא מ-Excel / CSV")
        st.write("טען קובץ במבנה התואם ל-`Schedule_Import.xlsx` להחלפת לוח הזמנים השבועי:")
        uploaded_excel = st.file_uploader("בחר קובץ Excel", type=["xlsx", "xls", "csv"])
        if uploaded_excel is not None:
            if st.button("בצע ייבוא עכשיו"):
                import_from_excel(uploaded_excel)
                
    with col_exp:
        st.markdown("#### ייצוא ל-Excel")
        st.write("הורד את לוח הזמנים השבועי הקיים במערכת כקובץ Excel לעריכה חיצונית:")
        excel_bytes = export_to_excel(data)
        st.download_button(
            label="📊 הורד לוח שבועי (Excel)",
            data=excel_bytes,
            file_name=f"Schedule_Export_{datetime.now().strftime('%Y%m%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

# ----------------- TAB 3: הגדרות מערכת ורישוי -----------------
with tab3:
    st.subheader("⚙️ הגדרות מוסד, חומרה ורישוי")
    
    inst_info = data.get("institution_info", {})
    
    with st.form("settings_form"):
        col_a, col_b = st.columns(2)
        
        with col_a:
            st.markdown("##### 🏫 פרטי מוסד ואיש קשר")
            inst_name = st.text_input("שם המוסד / בית הספר", value=inst_info.get("name", ""))
            contact_name = st.text_input("שם איש קשר", value=inst_info.get("contact", ""))
            phone = st.text_input("טלפון ליצירת קשר", value=inst_info.get("phone", ""))
            email = st.text_input("דואר אלקטרוני", value=inst_info.get("email", ""))
            address = st.text_input("כתובת המוסד", value=inst_info.get("address", ""))
            
        with col_b:
            st.markdown("##### 🔊 הגדרות שמע וחומרה")
            audio_device = st.selectbox("התקן שמע ליציאה", ["Default Audio Output", "Speakers (Realtek High Definition)", "HDMI Output"])
            language = st.selectbox("שפת ממשק", ["עברית", "English"])
            
            st.markdown("##### 🔑 ניהול רישיון ותשלום")
            license_status = inst_info.get("license_status", "פעיל בתוקף")
            st.info(f"סטטוס רישיון נוכחי: **{license_status}**")
            license_key = st.text_input("מפתח רישיון (License Key)", value="XXXX-YYYY-ZZZZ-1234", type="password")
            
        if st.form_submit_button("שמור הגדרות מערכת"):
            data["institution_info"] = {
                "name": inst_name,
                "contact": contact_name,
                "phone": phone,
                "email": email,
                "address": address,
                "license_status": license_status
            }
            save_schedule_data(data)
            st.success("הגדרות המערכת שנשמרו בהצלחה!")

# ----------------- TAB 4: לוגים ודיווחים -----------------
with tab4:
    st.subheader("📜 לוגים ודיווחים בזמן אמת")
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            logs_content = f.readlines()
        st.text_area("יומן אירועים ותקלות (Logs):", value="".join(logs_content[-50:]), height=350)
    else:
        st.info("טרם נוצרו לוגים במערכת.")

# ----------------- TAB 5: יציאה לבוט טלגרם -----------------
with tab5:
    st.subheader("🤖 אינטגרציה מול בוט טלגרם")
    st.write("המערכת מחוברת לבוט Telegram לבקרה, עדכונים ושליטה מרחוק.")
    
    st.markdown("""
    * **פקודות נתמכות בבוט:**
      * `/start` - הפעלת התקשורת מול הבוט.
      * `/status` - קבלת דיווח חי על מצב המערכת והצלצול הבא.
      * **טקסט חופשי:** שליחת הודעות טקסט לעדכון לוח הזמנים באמצעות AI.
    """)
    
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "לא מוגדר")
    st.code(f"TELEGRAM_BOT_TOKEN: {bot_token[:5]}***" if bot_token != "לא מוגדר" else "TELEGRAM_BOT_TOKEN חסר במשתני הסביבה", language="bash")