"""
test_sound.py - plays one bell immediately, without the schedule.
Separates "the computer cannot play sound" from "the schedule did not trigger".
Output is in English on purpose (Hebrew does not display in the Windows 7 console).
"""
import json
import os
import sys
import time
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDIO_DIR = os.path.join(BASE_DIR, "audio")
SCHEDULE_FILE = os.path.join(BASE_DIR, "schedule.json")
LOG_FILE = os.path.join(BASE_DIR, "logs", "app.log")


def main():
    print("Computer clock now:", datetime.now().strftime("%A %d/%m/%Y %H:%M:%S"))
    print("Folder:", BASE_DIR)
    print()

    # 1. schedule.json
    if not os.path.exists(SCHEDULE_FILE):
        print("[X] schedule.json NOT FOUND - copy it from the development computer (guide, step 3).")
        data = {}
    else:
        with open(SCHEDULE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        weekly = data.get("weekly_schedule", {})
        today = datetime.now().strftime("%A")
        n_today = len([e for e in weekly.get(today, []) if e.get("enabled", True)])
        print("[OK] schedule.json: %d events in total, %d enabled today (%s)"
              % (sum(len(v) for v in weekly.values()), n_today, today))
        info = data.get("institution_info", {})
        if data.get("system_status") == "disabled":
            scope, ddate = data.get("disabled_scope"), data.get("disabled_date")
            if scope == "day" and ddate != datetime.now().date().isoformat():
                print("[OK] An old 'today only' disable from %s - expired, not active." % ddate)
            else:
                print("[X] System is DISABLED (%s) -> nothing rings. Press 'enable system' in the management screen." % scope)
        if info.get("license_status") != u"פעיל בתוקף":
            print("[X] License status is not valid -> the engine plays nothing. Fix it in Settings.")
        print("    Audio device chosen in Settings:", repr(info.get("audio_device") or "(default)"))
    print()

    # 2. audio folder
    files = [f for f in os.listdir(AUDIO_DIR) if f.lower().endswith((".mp3", ".wav", ".ogg"))] if os.path.isdir(AUDIO_DIR) else []
    if not files:
        print("[X] No sound files in", AUDIO_DIR, "- copy the audio folder (guide, step 3).")
        return 1
    print("[OK] Sound files in audio folder:", ", ".join(files))
    test_file = "bell_standard.mp3" if "bell_standard.mp3" in files else files[0]
    print()

    # 3. engine running? (it holds port 8599 while it runs)
    import socket
    s = socket.socket()
    running = s.connect_ex(("127.0.0.1", 8599)) == 0
    s.close()
    print("[OK] The playback engine (main.py) is running." if running else
          "[X] The playback engine (main.py) is NOT running! Start it with start_all.bat. Without it nothing rings.")
    print()

    # 4. play
    import pygame
    pygame.mixer.init()
    try:
        from pygame._sdl2 import audio as sdl2_audio
        devices = list(sdl2_audio.get_audio_device_names(False))
    except Exception as e:
        devices = ["(could not list: %s)" % e]
    print("Sound output devices on this computer:")
    for d in devices:
        print("   -", d)
    print()
    print("Playing '%s' at full volume for up to 10 seconds - LISTEN NOW..." % test_file)
    snd = pygame.mixer.Sound(os.path.join(AUDIO_DIR, test_file))
    snd.set_volume(1.0)
    ch = snd.play()
    t0 = time.time()
    while ch is not None and ch.get_busy() and time.time() - t0 < 10:
        time.sleep(0.2)
    ch and ch.stop()
    print("Done playing.")
    print()
    print("Heard it?  YES -> sound works; the problem is the schedule/engine (send logs\\app.log).")
    print("           NO  -> Windows sound: speakers, volume, mute, or Volume Mixer (python).")

    # 5. last engine messages
    if os.path.exists(LOG_FILE):
        print()
        print("Log file to send:", LOG_FILE)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("ERROR:", e)
        sys.exit(1)
