import os
import json
import time
import logging
import pygame
from datetime import datetime

# הגדרת נתיבים
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEDULE_FILE = os.path.join(BASE_DIR, "schedule.json")
AUDIO_DIR = os.path.join(BASE_DIR, "audio")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
LOG_FILE = os.path.join(LOGS_DIR, "app.log")

# הגדרת יומן רישום (Logs)
os.makedirs(LOGS_DIR, exist_ok=True)
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    encoding='utf-8'
)

DAYS_MAP = {
    "Sunday": "Sunday",
    "Monday": "Monday",
    "Tuesday": "Tuesday",
    "Wednesday": "Wednesday",
    "Thursday": "Thursday",
    "Friday": "Friday",
    "Saturday": "Saturday"
}

def init_audio():
    try:
        pygame.mixer.init()
        logging.info("מנוע השמע (Pygame) הופעל בהצלחה.")
    except Exception as e:
        logging.error(f"שגיאה באתחול מנוע השמע: {e}")

def play_sound(file_name, volume=85, duration=30):
    file_path = os.path.join(AUDIO_DIR, file_name)
    if not os.path.exists(file_path):
        logging.error(f"קובץ השמע לא נמצא: {file_path}")
        print(f"❌ שגיאה: קובץ השמע '{file_name}' לא נמצא בתיקיית audio!")
        return

    try:
        print(f"🔔 מפעיל: {file_name} (בעוצמה {volume}%, למשך {duration} שניות)")
        logging.info(f"השמעת קובץ: {file_name}, עוצמה: {volume}%, משך: {duration} שניות")
        
        pygame.mixer.music.load(file_path)
        pygame.mixer.music.set_volume(volume / 100.0)
        pygame.mixer.music.play()
        
        # המתנה למשך הזמן שהוגדר או עד סיום השיר
        start_time = time.time()
        while pygame.mixer.music.get_busy() and (time.time() - start_time) < duration:
            time.sleep(0.5)
            
        pygame.mixer.music.stop()
    except Exception as e:
        logging.error(f"שגיאה במהלך השמעת הקובץ {file_name}: {e}")

def check_and_run_schedule():
    if not os.path.exists(SCHEDULE_FILE):
        return

    try:
        with open(SCHEDULE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        logging.error(f"שגיאה בקריאת schedule.json: {e}")
        return

    # בדיקת סטטוס מערכת
    if data.get("system_status") != "enabled":
        return

    now = datetime.now()
    current_day = now.strftime("%A")
    current_time = now.strftime("%H:%M")

    weekly_schedule = data.get("weekly_schedule", {})
    todays_events = weekly_schedule.get(current_day, [])

    for event in todays_events:
        if not event.get("enabled", True):
            continue
            
        event_time = str(event.get("time", "")).strip()
        
        # בדיקה אם הזמן הנוכחי תואם לשעת האירוע
        if event_time == current_time:
            audio_file = event.get("audio", "Tune 4.mp3")
            volume = int(event.get("volume", 85))
            duration = int(event.get("duration_seconds", 30))
            
            play_sound(audio_file, volume, duration)
            time.sleep(60) # המתנה של דקה למניעת השמעה כפולה באותה הדקה

def main():
    print("🚀 מנוע ההשמעה ברקע הופעל בהצלחה!")
    print("המערכת מנטרת את השעון וסורקת את schedule.json...")
    init_audio()
    
    while True:
        check_and_run_schedule()
        time.sleep(1)

if __name__ == "__main__":
    main()