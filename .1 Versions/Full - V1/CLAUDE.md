# Instructions for Building School Bell System (Hybrid Architecture with Music & Audio Priority)

## Architecture Overview
This system is built using a Hybrid Architecture:
1. **Deterministic Core (`src/core_engine.py`)**: A rock-solid, zero-AI Python daemon that plays sound files strictly based on `schedule.json`. It MUST run reliably even without internet access.
2. **Agentic Wrapper (`src/schedule_agent.py`)**: An AI agent powered by Claude/Gemini API that reads natural language requests from school managers, evaluates them against `Context.md` business rules, and updates `schedule.json`.
3. **Bot Interface (`src/telegram_bot.py`)**: A Telegram bot interface for receiving text/voice commands and returning system status.

## Audio Concurrency & Priority Rules (CRITICAL)
- **No Overlapping Audio**: Never play two audio sources simultaneously.
- **Priority Hierarchy**: `emergency` > `bell` > `music`.
- **Music Preemption**: Before playing any `bell` or `emergency` event, the core engine MUST check if music is playing. If active, perform an automatic `fadeout(1500)` (1.5 seconds) or instant `stop()`, wait for the channel to clear, and then execute the higher-priority bell.
- **Duration Control**: Music events must respect `duration_seconds` or stop automatically when the next scheduled bell minute arrives.

## Tech Stack
- Python 3.10+
- `pygame` (using `pygame.mixer` with dedicated channels for background music and foreground bells)
- `python-telegram-bot` for Telegram integration
- `google-generativeai` / `anthropic` SDK for AI agent logic

## Execution Guidelines
- Implement thread safety and state locks (`last_played_time`) to prevent duplicate triggering within the same minute.
- Include full error handling, logging, and graceful fallbacks for missing audio files or corrupted JSON files.