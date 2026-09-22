import streamlit as st
import pandas as pd
import json
import os
from datetime import datetime, date, time as dtime
import io
import logging

# ============================================================
# הגדרת עמוד
# ============================================================
st.set_page_config(
    page_title="מערכת ניהול צלצולים ומוזיקה",
    page_icon="🔔",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ============================================================
# נתיבים ולוגים
# ============================================================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEDULE_FILE = os.path.join(BASE_DIR, "schedule.json")
AUDIO_DIR = os.path.join(BASE_DIR, "audio")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
LOG_FILE = os.path.join(LOGS_DIR, "app.log")

os.makedirs(LOGS_DIR, exist_ok=True)
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    encoding='utf-8'
)

# ============================================================
# ימים
# ============================================================
DAYS_HEBREW_TO_ENGLISH = {
    "ראשון": "Sunday", "שני": "Monday", "שלישי": "Tuesday",
    "רביעי": "Wednesday", "חמישי": "Thursday", "שישי": "Friday", "שבת": "Saturday"
}
DAYS_ENGLISH_TO_HEBREW = {v: k for k, v in DAYS_HEBREW_TO_ENGLISH.items()}
DAY_ORDER = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]

TYPE_ICONS = {"Bell": "🔔", "Music": "🎵", "music": "🎵", "Exercise": "🚨"}

# ============================================================
# CSS - כותרת קבועה, RTL, כרטיסים, גלילה אנכית בלבד
# ============================================================
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Rubik:wght@300;400;500;700;900&display=swap');

    html, body, [class*="css"] {
        font-family: 'Rubik', sans-serif;
        direction: rtl;
        text-align: right;
    }

    /* מרווח עליון כדי לפנות מקום לכותרת הקבועה */
    .block-container { padding-top: 190px !important; }

    .sticky-header {
        position: fixed;
        top: 0; left: 0; right: 0;
        z-index: 999;
        background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%);
        color: white;
        padding: 14px 30px;
        box-shadow: 0 4px 18px rgba(0,0,0,0.25);
        display: flex;
        align-items: center;
        justify-content: space-between;
        direction: rtl;
    }
    .clock-block { text-align: center; }
    .clock-main {
        font-size: 3rem;
        font-weight: 900;
        line-height: 1;
        letter-spacing: 2px;
        font-variant-numeric: tabular-nums;
    }
    .clock-sub { font-size: 0.95rem; opacity: 0.85; margin-top: 2px; }
    .next-event-block {
        text-align: center;
        background: rgba(255,255,255,0.12);
        border-radius: 10px;
        padding: 8px 18px;
        min-width: 160px;
    }
    .next-event-label { font-size: 0.8rem; opacity: 0.8; }
    .next-event-time { font-size: 1.6rem; font-weight: 700; font-variant-numeric: tabular-nums; }
    .status-block { text-align: center; min-width: 130px; }

    .metric-pill {
        background: rgba(255,255,255,0.10);
        border-radius: 10px;
        padding: 6px 14px;
        font-size: 0.85rem;
    }

    /* גלילה אנכית בלבד ללוח הזמנים */
    .schedule-scroll {
        max-height: 62vh;
        overflow-y: auto;
        overflow-x: hidden;
        padding-left: 6px;
    }
    .schedule-scroll::-webkit-scrollbar { width: 8px; }
    .schedule-scroll::-webkit-scrollbar-thumb { background: #c8d3e0; border-radius: 6px; }

    .day-col-header {
        text-align: center;
        font-weight: 700;
        padding: 8px;
        border-radius: 8px 8px 0 0;
        margin-bottom: 6px;
    }
    .day-col-today { background: #e3edff; border: 1px solid #a9c3f5; }
    .day-col-normal { background: #f4f6f8; }

    .event-card {
        border-radius: 10px;
        padding: 10px 12px;
        margin-bottom: 8px;
        background: #ffffff;
        border: 1px solid #e2e6ea;
        box-shadow: 0 1px 4px rgba(0,0,0,0.05);
        position: relative;
    }
    .event-card-next {
        border: 2px solid #ff8c00;
        background: #fff7ec;
        box-shadow: 0 3px 10px rgba(255,140,0,0.25);
    }
    .event-card-disabled { opacity: 0.45; }
    .event-time { font-size: 1.35rem; font-weight: 800; color: #1e3c72; }
    .event-label { font-size: 0.95rem; color: #333; margin-top: 2px; }
    .event-icons { position: absolute; top: 8px; left: 8px; font-size: 1.05rem; }

    .row-next {
        background-color: #fff3e0 !important;
    }

    .stButton>button { border-radius: 8px; font-weight: 500; transition: all 0.2s; }
    .stButton>button:hover { transform: translateY(-1px); }
</style>
""", unsafe_allow_html=True)


# ============================================================
# טעינה / שמירה
# ============================================================
def _migrate_time(t):
    """מנרמל כל ערך שעה לתבנית HH:MM:SS"""
    t = str(t).strip()
    parts = t.split(":")
    if len(parts) == 2:
        return f"{parts[0].zfill(2)}:{parts[1].zfill(2)}:00"
    if len(parts) == 3:
        return f"{parts[0].zfill(2)}:{parts[1].zfill(2)}:{parts[2].zfill(2)}"
    return "00:00:00"


def load_schedule_data():
    if not os.path.exists(SCHEDULE_FILE):
        default_data = {
            "system_status": "enabled",
            "disabled_scope": None,      # "day" | "indefinite" | None
            "disabled_date": None,       # תאריך ISO כאשר scope == "day"
            "institution_info": {
                "name": "בית ספר דוגמה", "contact": "ישראל ישראלי", "phone": "050-0000000",
                "email": "school@example.com", "address": "נתיבות, ישראל", "license_status": "פעיל בתוקף"
            },
            "weekly_schedule": {day: [] for day in DAY_ORDER}
        }
        save_schedule_data(default_data)
        return default_data

    try:
        with open(SCHEDULE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        logging.error(f"שגיאה בטעינת קובץ schedule.json: {e}")
        st.error("שגיאה בטעינת קובץ ההגדרות.")
        return {}

    # מיגרציה: נרמול שעות ל-HH:MM:SS, מיון, ותוספת שדות חדשים אם חסרים
    weekly = data.get("weekly_schedule", {})
    for day in DAY_ORDER:
        events = weekly.get(day, [])
        for ev in events:
            ev["time"] = _migrate_time(ev.get("time", "00:00:00"))
            ev.setdefault("enabled", True)
            ev.setdefault("type", "Bell")
            ev.setdefault("volume", 85)
            ev.setdefault("duration_seconds", 30)
        events.sort(key=lambda x: x.get("time", "00:00:00"))
        weekly[day] = events
    data["weekly_schedule"] = weekly
    data.setdefault("system_status", "enabled")
    data.setdefault("disabled_scope", None)
    data.setdefault("disabled_date", None)
    return data


def save_schedule_data(data):
    try:
        with open(SCHEDULE_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        logging.info("קובץ schedule.json עודכן בהצלחה.")
    except Exception as e:
        logging.error(f"שגיאה בשמירת schedule.json: {e}")
        st.error("שגיאה בשמירת הנתונים.")


def get_available_audio_files():
    if not os.path.exists(AUDIO_DIR):
        os.makedirs(AUDIO_DIR, exist_ok=True)
    files = [f for f in os.listdir(AUDIO_DIR) if f.endswith(('.mp3', '.wav', '.ogg'))]
    return files if files else ["Tune 4.mp3", "Ring 1.mp3"]


# ============================================================
# עזר: שעה כטקסט <-> אובייקט time, ואירוע הבא
# ============================================================
def time_str_to_obj(t):
    try:
        h, m, s = [int(x) for x in t.split(":")]
        return dtime(hour=h, minute=m, second=s)
    except Exception:
        return dtime(0, 0, 0)


def time_obj_to_str(t):
    return t.strftime("%H:%M:%S")


def get_next_event(data, now_dt):
    """מוצא את האירוע הפעיל הקרוב ביותר, החל מהיום הנוכחי וממשיך קדימה בשבוע."""
    weekly = data.get("weekly_schedule", {})
    today_idx = DAY_ORDER.index(now_dt.strftime("%A")) if now_dt.strftime("%A") in DAY_ORDER else 0
    now_str = now_dt.strftime("%H:%M:%S")

    for offset in range(8):  # עד שבוע קדימה, כולל היום
        day_idx = (today_idx + offset) % 7
        day_key = DAY_ORDER[day_idx]
        events = [e for e in weekly.get(day_key, []) if e.get("enabled", True)]
        if offset == 0:
            events = [e for e in events if e.get("time", "00:00:00") > now_str]
        events.sort(key=lambda x: x.get("time", "00:00:00"))
        if events:
            return day_key, events[0]
    return None, None


# ============================================================
# ייבוא / ייצוא Excel (ללא שינוי מהותי, מותאם לתבנית HH:MM:SS)
# ============================================================
def import_from_excel(uploaded_file):
    try:
        df_import = pd.read_excel(uploaded_file)
        if df_import.iloc[0]['תיאור הארוע'] == 'Text' or 'HH:MM:SS' in str(df_import.iloc[0]['שעת התחלה']):
            df_import = df_import.iloc[1:].reset_index(drop=True)

        data = load_schedule_data()
        weekly_schedule = {day: [] for day in DAY_ORDER}
        day_cols = [1, 2, 3, 4, 5, 6, 7]

        for idx, row in df_import.iterrows():
            if pd.isna(row.get('תיאור הארוע')) or pd.isna(row.get('שעת התחלה')):
                continue
            event = {
                "label": str(row['תיאור הארוע']),
                "time": _migrate_time(row['שעת התחלה']),
                "duration_seconds": int(row.get('משך השמעה בשניות', 30)),
                "audio": str(row.get('קובץ שמע', 'Tune 4.mp3')),
                "type": str(row.get('סוג ארוע', 'Bell')),
                "volume": int(row.get('עוצמת שמע', 85)),
                "enabled": bool(int(row.get('פעיל', 1)))
            }
            for d_idx, d_col in enumerate(day_cols):
                if d_col in row and str(row[d_col]).strip() in ['1', '1.0', 'True', 'true']:
                    weekly_schedule[DAY_ORDER[d_idx]].append(event.copy())

        for day in DAY_ORDER:
            weekly_schedule[day].sort(key=lambda x: x["time"])

        data['weekly_schedule'] = weekly_schedule
        save_schedule_data(data)
        logging.info("ייבוא מ-Excel הושלם בהצלחה.")
        st.success("לוח הזמנים יובא בהצלחה מ-Excel!")
        st.rerun()
    except Exception as e:
        logging.error(f"שגיאה בייבוא Excel: {e}")
        st.error(f"שגיאה בייבוא קובץ Excel: {str(e)}")


def export_to_excel(data):
    rows = []
    weekly = data.get("weekly_schedule", {})
    all_events = {}
    for day in DAY_ORDER:
        for ev in weekly.get(day, []):
            key = (ev['time'], ev['label'], ev['audio'])
            if key not in all_events:
                all_events[key] = {
                    "label": ev.get('label'), "time": ev.get('time'),
                    "duration_seconds": ev.get('duration_seconds', 30),
                    "audio": ev.get('audio', 'Tune 4.mp3'), "type": ev.get('type', 'Bell'),
                    "volume": ev.get('volume', 85), "enabled": ev.get('enabled', True),
                    "days": {d: 0 for d in range(1, 8)}
                }
            day_idx = DAY_ORDER.index(day) + 1
            all_events[key]["days"][day_idx] = 1 if ev.get('enabled', True) else 0

    for idx, (k, ev) in enumerate(all_events.items(), 1):
        row = {
            "מספר": idx, "תיאור הארוע": ev['label'], "שעת התחלה": ev['time'],
            "משך השמעה בשניות": ev['duration_seconds'], "קובץ שמע": ev['audio'],
            "סוג ארוע": ev['type'], "עוצמת שמע": ev['volume'],
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


# ============================================================
# טעינת נתונים
# ============================================================
data = load_schedule_data()

# ============================================================
# כותרת קבועה: שעון חי + אירוע הבא + סטטוס (מתעדכן כל שנייה בלי לאבד state)
# ============================================================
@st.fragment(run_every=1)
def render_sticky_header():
    now = datetime.now()
    day_heb = DAYS_ENGLISH_TO_HEBREW.get(now.strftime("%A"), now.strftime("%A"))
    date_str = now.strftime("%d/%m/%Y")

    next_day_key, next_event = get_next_event(data, now)
    if next_event:
        next_day_heb = DAYS_ENGLISH_TO_HEBREW.get(next_day_key, next_day_key)
        same_day = (next_day_key == now.strftime("%A"))
        next_label = f"{next_event['label']} ({next_day_heb})" if not same_day else next_event['label']
        next_time_str = next_event['time']
    else:
        next_label = "אין אירועים קרובים"
        next_time_str = "--:--:--"

    status_mode = data.get("system_status", "enabled")
    if status_mode == "enabled":
        status_display = "🟢 פעיל"
    elif data.get("disabled_scope") == "day":
        status_display = "🟡 מושבת להיום"
    else:
        status_display = "🔴 מושבת"

    todays_events = data.get("weekly_schedule", {}).get(now.strftime("%A"), [])
    active_count = sum(1 for e in todays_events if e.get("enabled", True))

    st.markdown(f"""
    <div class="sticky-header">
        <div class="status-block">
            <div class="metric-pill">⚡ {status_display}</div>
            <div class="metric-pill" style="margin-top:6px;">🔔 {active_count} אירועים היום</div>
        </div>
        <div class="clock-block">
            <div class="clock-main">{now.strftime('%H:%M:%S')}</div>
            <div class="clock-sub">יום {day_heb} | {date_str}</div>
        </div>
        <div class="next-event-block">
            <div class="next-event-label">⏭ האירוע הבא</div>
            <div class="next-event-time">{next_time_str}</div>
            <div class="clock-sub">{next_label}</div>
        </div>
    </div>
    """, unsafe_allow_html=True)


render_sticky_header()

# ============================================================
# בקרת הפעלה / השבתה
# ============================================================
with st.container():
    c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
    with c1:
        st.markdown("##### ⚙️ בקרת מערכת")
    with c2:
        if st.button("🟢 הפעל מערכת", use_container_width=True):
            data["system_status"] = "enabled"
            data["disabled_scope"] = None
            data["disabled_date"] = None
            save_schedule_data(data)
            st.rerun()
    with c3:
        if st.button("🟡 השבת להיום בלבד", use_container_width=True):
            data["system_status"] = "disabled"
            data["disabled_scope"] = "day"
            data["disabled_date"] = date.today().isoformat()
            save_schedule_data(data)
            st.rerun()
    with c4:
        if st.button("🔴 השבת עד הפעלה ידנית", use_container_width=True):
            data["system_status"] = "disabled"
            data["disabled_scope"] = "indefinite"
            data["disabled_date"] = None
            save_schedule_data(data)
            st.rerun()

st.write("")

# ============================================================
# טאבים ראשיים
# ============================================================
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📅 לוח צלצולים שבועי", "📥 ייבוא / ייצוא", "⚙️ הגדרות מערכת ורישוי",
    "📜 לוגים ודיווחים", "🤖 בוט טלגרם"
])

# ----------------- TAB 1: לוח צלצולים -----------------
with tab1:
    view_mode = st.radio("תצוגה:", ["📋 טבלה", "🗂️ קוביות שבועי"], horizontal=True, label_visibility="collapsed")

    now = datetime.now()
    current_day_eng = now.strftime("%A")
    now_str = now.strftime("%H:%M:%S")

    # ---------- תצוגת טבלה (יום בודד, עריכה + שמירה אוטומטית) ----------
    if view_mode == "📋 טבלה":
        selected_day_heb = st.selectbox(
            "בחר יום להצגה ועריכה:", list(DAYS_HEBREW_TO_ENGLISH.keys()),
            index=list(DAYS_HEBREW_TO_ENGLISH.values()).index(current_day_eng)
        )
        selected_day_eng = DAYS_HEBREW_TO_ENGLISH[selected_day_heb]
        events_list = data.get("weekly_schedule", {}).get(selected_day_eng, [])

        df_display = pd.DataFrame(events_list if events_list else [])
        required_cols = ["enabled", "time", "label", "type", "audio", "volume", "duration_seconds"]
        for col in required_cols:
            if col not in df_display.columns:
                df_display[col] = None

        df_display["enabled"] = df_display["enabled"].fillna(True).astype(bool)
        df_display["time"] = df_display["time"].fillna("00:00:00").apply(_migrate_time)
        df_display["time"] = df_display["time"].apply(time_str_to_obj)
        df_display["label"] = df_display["label"].fillna("").astype(str)
        df_display["type"] = df_display["type"].fillna("Bell").astype(str)
        df_display["audio"] = df_display["audio"].fillna("Tune 4.mp3").astype(str)
        df_display["volume"] = pd.to_numeric(df_display["volume"], errors='coerce').fillna(85).astype(int)
        df_display["duration_seconds"] = pd.to_numeric(df_display["duration_seconds"], errors='coerce').fillna(30).astype(int)

        df_editor = df_display[["enabled", "time", "label", "type", "audio", "volume", "duration_seconds"]]

        st.subheader(f"עריכת לוח זמנים ליום {selected_day_heb}")
        st.caption("עריכה בלחיצה כפולה על תא — השמירה מתבצעת אוטומטית, ללא צורך בכפתור שמירה.")

        edited_df = st.data_editor(
            df_editor,
            num_rows="dynamic",
            column_config={
                "enabled": st.column_config.CheckboxColumn("פעיל", default=True),
                "time": st.column_config.TimeColumn("שעת התחלה", format="HH:mm:ss", step=1, required=True),
                "label": st.column_config.TextColumn("תיאור האירוע", required=True),
                "type": st.column_config.SelectboxColumn("סוג אירוע", options=["Bell", "Music", "Exercise"]),
                "audio": st.column_config.SelectboxColumn("קובץ שמע", options=get_available_audio_files()),
                "volume": st.column_config.NumberColumn("עוצמת שמע (%)", min_value=0, max_value=100, step=5, default=85),
                "duration_seconds": st.column_config.NumberColumn("משך (שניות)", min_value=1, max_value=3600, step=5, default=30),
            },
            key=f"editor_{selected_day_eng}",
            use_container_width=True
        )

        # שמירה אוטומטית: השוואה בין הנתונים שנטענו לנתונים שנערכו
        edited_records = edited_df.to_dict(orient="records")
        for r in edited_records:
            r["time"] = time_obj_to_str(r["time"]) if isinstance(r["time"], dtime) else _migrate_time(r["time"])
        edited_records.sort(key=lambda x: x["time"])

        original_records = events_list
        if edited_records != original_records:
            data["weekly_schedule"][selected_day_eng] = edited_records
            save_schedule_data(data)
            st.toast("✅ השינויים נשמרו אוטומטית", icon="💾")
            st.rerun()

    # ---------- תצוגת קוביות (כל השבוע, טור לכל יום) ----------
    else:
        st.caption("לחיצה על ✎ בכרטיס פותחת עריכה מהירה עם שמירה אוטומטית. האירוע הקרוב מובלט בכתום.")
        next_day_key, next_event_obj = get_next_event(data, now)

        st.markdown('<div class="schedule-scroll">', unsafe_allow_html=True)
        cols = st.columns(7)
        for i, day_key in enumerate(DAY_ORDER):
            day_heb = DAYS_ENGLISH_TO_HEBREW[day_key]
            is_today = (day_key == current_day_eng)
            with cols[i]:
                header_class = "day-col-today" if is_today else "day-col-normal"
                st.markdown(f'<div class="day-col-header {header_class}">{day_heb}</div>', unsafe_allow_html=True)

                events = sorted(data.get("weekly_schedule", {}).get(day_key, []), key=lambda x: x.get("time", "00:00:00"))
                for ev_idx, ev in enumerate(events):
                    is_next = (day_key == next_day_key and next_event_obj is not None and ev is next_event_obj)
                    card_class = "event-card"
                    if is_next:
                        card_class += " event-card-next"
                    if not ev.get("enabled", True):
                        card_class += " event-card-disabled"
                    icon = TYPE_ICONS.get(ev.get("type", "Bell"), "🔔")

                    st.markdown(f"""
                    <div class="{card_class}">
                        <div class="event-icons">{icon}</div>
                        <div class="event-time">{ev.get('time', '')[:5]}</div>
                        <div class="event-label">{ev.get('label', '')}</div>
                    </div>
                    """, unsafe_allow_html=True)

                    with st.popover("✎ עריכה", use_container_width=True):
                        with st.form(key=f"form_{day_key}_{ev_idx}"):
                            f_enabled = st.checkbox("פעיל", value=ev.get("enabled", True))
                            f_time = st.time_input("שעת התחלה", value=time_str_to_obj(ev.get("time", "00:00:00")), step=60)
                            f_label = st.text_input("תיאור האירוע", value=ev.get("label", ""))
                            f_type = st.selectbox("סוג אירוע", ["Bell", "Music", "Exercise"],
                                                   index=["Bell", "Music", "Exercise"].index(ev.get("type", "Bell")) if ev.get("type", "Bell") in ["Bell", "Music", "Exercise"] else 0)
                            f_audio = st.selectbox("קובץ שמע", get_available_audio_files())
                            vol_col1, vol_col2 = st.columns([3, 1])
                            with vol_col1:
                                f_volume_slider = st.slider("עוצמת שמע (%)", 0, 100, int(ev.get("volume", 85)))
                            with vol_col2:
                                f_volume_num = st.number_input("‎", 0, 100, f_volume_slider, label_visibility="collapsed")
                            f_duration = st.number_input("משך (שניות)", 1, 3600, int(ev.get("duration_seconds", 30)))

                            bcol1, bcol2 = st.columns(2)
                            save_clicked = bcol1.form_submit_button("💾 שמור", use_container_width=True)
                            delete_clicked = bcol2.form_submit_button("🗑 מחק", use_container_width=True)

                            if save_clicked:
                                new_ev = {
                                    "enabled": f_enabled, "time": time_obj_to_str(f_time),
                                    "label": f_label, "type": f_type, "audio": f_audio,
                                    "volume": f_volume_num, "duration_seconds": f_duration
                                }
                                day_events = data["weekly_schedule"][day_key]
                                day_events[ev_idx] = new_ev
                                day_events.sort(key=lambda x: x["time"])
                                save_schedule_data(data)
                                st.rerun()

                            if delete_clicked:
                                del data["weekly_schedule"][day_key][ev_idx]
                                save_schedule_data(data)
                                st.rerun()

                # הוספת אירוע חדש ליום זה
                with st.popover("➕ הוסף אירוע", use_container_width=True):
                    with st.form(key=f"add_form_{day_key}"):
                        n_time = st.time_input("שעת התחלה", value=dtime(8, 0, 0), step=60)
                        n_label = st.text_input("תיאור האירוע")
                        n_type = st.selectbox("סוג אירוע", ["Bell", "Music", "Exercise"], key=f"add_type_{day_key}")
                        n_audio = st.selectbox("קובץ שמע", get_available_audio_files(), key=f"add_audio_{day_key}")
                        n_volume = st.slider("עוצמת שמע (%)", 0, 100, 85, key=f"add_vol_{day_key}")
                        n_duration = st.number_input("משך (שניות)", 1, 3600, 30, key=f"add_dur_{day_key}")
                        if st.form_submit_button("➕ הוסף", use_container_width=True):
                            new_ev = {
                                "enabled": True, "time": time_obj_to_str(n_time), "label": n_label or "אירוע חדש",
                                "type": n_type, "audio": n_audio, "volume": n_volume, "duration_seconds": n_duration
                            }
                            data["weekly_schedule"][day_key].append(new_ev)
                            data["weekly_schedule"][day_key].sort(key=lambda x: x["time"])
                            save_schedule_data(data)
                            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

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
            label="📊 הורד לוח שבועי (Excel)", data=excel_bytes,
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
                "name": inst_name, "contact": contact_name, "phone": phone,
                "email": email, "address": address, "license_status": license_status
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

# ----------------- TAB 5: בוט טלגרם -----------------
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
