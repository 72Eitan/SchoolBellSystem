import json
import time
import os
import random
from datetime import datetime
import threading
import pygame

SCHEDULE_FILE = "schedule.json"
CONTEXT_FILE = "Context.md"
AUDIO_DIR = "audio"

last_played_time = None
current_music_thread = None
is_music_playing = False

def init_audio():
    pygame.mixer.init()
    # הקצאת ערוץ 0 למוזיקה, ערוץ 1 לצלצולים/חירום
    pygame.mixer.set_num_channels(4)

def stop_background_music():
    global is_music_playing
    music_channel = pygame.mixer.Channel(0)
    if music_channel.get_busy():
        print(f"[{datetime.now().strftime('%H:%M:%S')}] עוצר מוזיקת ברקע (Fade-out)...")
        music_channel.fadeout(1500)  # Fade out 1.5 שניות
        time.sleep(1.5)
        music_channel.stop()
    is_music_playing = False

def play_bell(file_name, is_emergency=False):
    stop_background_music()
    bell_channel = pygame.mixer.Channel(1)
    
    path = os.path.join(AUDIO_DIR, file_name)
    if not os.path.exists(path):
        print(f"שגיאה: קובץ הצלצול {path} לא נמצא!")
        return

    print(f"[{datetime.now().strftime('%H:%M:%S')}] מפעיל צלצול: {file_name}")
    sound = pygame.mixer.Sound(path)
    bell_channel.play(sound)
    
    while bell_channel.get_busy():
        time.sleep(0.2)

def play_music_track(file_path, duration_seconds=1200):
    global is_music_playing
    music_channel = pygame.mixer.Channel(0)
    
    if not os.path.exists(file_path):
        print(f"שגיאה: קובץ המוזיקה {file_path} לא נמצא!")
        return

    print(f"[{datetime.now().strftime('%H:%M:%S')}] מפעיל מוזיקת הפסקה: {file_path}")
    sound = pygame.mixer.Sound(file_path)
    music_channel.play(sound)
    is_music_playing = True
    
    start_time = time.time()
    while music_channel.get_busy() and is_music_playing:
        if time.time() - start_time >= duration_seconds:
            music_channel.fadeout(1500)
            break
        time.sleep(0.5)
    is_music_playing = False

def trigger_music(audio_path, duration_seconds=1200):
    global current_music_thread
    stop_background_music()
    
    # טיפול בבחירת שיר רנדומלי מהפלייליסט
    if "playlist" in audio_path and not os.path.exists(os.path.join(AUDIO_DIR, audio_path)):
        playlist_dir = os.path.join(AUDIO_DIR, "playlist")
        if os.path.exists(playlist_dir):
            files = [f for f in os.listdir(playlist_dir) if f.endswith(('.mp3', '.wav'))]
            if files:
                audio_path = os.path.join("playlist", random.choice(files))
    
    full_path = os.path.join(AUDIO_DIR, audio_path)
    current_music_thread = threading.Thread(target=play_music_track, args=(full_path, duration_seconds))
    current_music_thread.daemon = True
    current_music_thread.start()

def check_and_run():
    global last_played_time
    if not os.path.exists(SCHEDULE_FILE):
        return

    now_str = datetime.now().strftime("%H:%M")
    if last_played_time == now_str:
        return # מניעת הפעלה כפולה באותה דקה

    try:
        with open(SCHEDULE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"שגיאה בקריאת {SCHEDULE_FILE}: {e}")
        return

    current_profile = data.get("active_profile", "standard")
    events = data.get("profiles", {}).get(current_profile, [])
    overrides = data.get("overrides", [])

    # 1. בדיקת חריגות (Overrides)
    for item in overrides:
        if item.get("time") == now_str and not item.get("executed", False):
            event_type = item.get("type", "bell")
            if event_type == "bell":
                play_bell(item["audio"], is_emergency=(item.get("label") == "emergency"))
            elif event_type == "music":
                trigger_music(item["audio"], item.get("duration_seconds", 1200))
            
            item["executed"] = True
            last_played_time = now_str
            with open(SCHEDULE_FILE, 'w', encoding='utf-8') as f_out:
                json.dump(data, f_out, indent=2, ensure_ascii=False)
            return

    # 2. בדיקת לוח זמנים רגיל
    for item in events:
        if item.get("time") == now_str:
            event_type = item.get("type", "bell")
            if event_type == "bell":
                play_bell(item["audio"])
            elif event_type == "music":
                trigger_music(item["audio"], item.get("duration_seconds", 1200))
            
            last_played_time = now_str
            break

def main():
    init_audio()
    print("מנוע הצלצולים והמוזיקה הקשיח הופעל בהצלחה...")
    while True:
        check_and_run()
        time.sleep(3)

if __name__ == "__main__":
    main()