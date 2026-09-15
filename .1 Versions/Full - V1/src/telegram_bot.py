import os
import json
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from schedule_agent import process_manager_request

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("שלום! אני סוכן ניהול מערכת הצלצולים והמוזיקה. שלח לי פקודה בקול/טקסט.")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if os.path.exists("schedule.json"):
        with open("schedule.json", 'r', encoding='utf-8') as f:
            data = json.load(f)
        profile = data.get("active_profile", "standard")
        await update.message.reply_text(f"פרופיל פעיל כרגע: {profile}\nחריגות מתוכננות: {len(data.get('overrides', []))}")
    else:
        await update.message.reply_text("קובץ schedule.json לא נמצא.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    await update.message.reply_text("מעבד את בקשתך מול חוקי המערכת...")
    reply = process_manager_request(user_text)
    await update.message.reply_text(reply)

def main():
    if not TELEGRAM_TOKEN:
        print("שגיאה: חסר TELEGRAM_BOT_TOKEN במשתני הסביבה.")
        return

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("בוט ה-Telegram הופעל...")
    app.run_polling()

if __name__ == "__main__":
    main()