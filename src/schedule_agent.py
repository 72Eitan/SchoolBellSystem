import json
import os
import google.generativeai as genai

genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))

def load_file(path):
    if not os.path.exists(path):
        return ""
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()

def process_manager_request(user_prompt):
    context_rules = load_file("Context.md")
    current_schedule = load_file("schedule.json")

    system_instruction = f"""
    אתה סוכן AI המנהל את מערכת הצלצולים והמוזיקה של בית הספר.
    חוקי הברזל והמגבלות שלך מופיעים ב-Context.md:
    {context_rules}

    קובץ הלו"ז הנוכחי schedule.json:
    {current_schedule}

    תפקידך: לקבל פקודה בשפה חופשית מהמנהל, וליצור פלט JSON בלבד המעדכן את schedule.json.
    אם הבקשה מפרה חוק (למשל צלצול ב-20:00), החזר JSON עם שגיאה.
    אם זו בקשה לשינוי חד-פעמי, הוסף רשומה ל-overrides.

    פורמט הפלט (JSON בלבד):
    {{
      "status": "success" | "error",
      "message": "הסבר בעברית למנהל",
      "updated_json": {{ ... המבנה המעודכן כולו ... }}
    }}
    """

    model = genai.GenerativeModel("gemini-2.5-flash")
    response = model.generate_content(
        f"{system_instruction}\n\nבקשת המנהל: {user_prompt}",
        generation_config={"response_mime_type": "application/json"}
    )
    
    try:
        res_data = json.loads(response.text)
        if res_data.get("status") == "success" and "updated_json" in res_data:
            with open("schedule.json", 'w', encoding='utf-8') as f:
                json.dump(res_data["updated_json"], f, indent=2, ensure_ascii=False)
        return res_data.get("message", "העדכון בוצע בהצלחה")
    except Exception as e:
        return f"שגיאה בעיבוד תגובת ה-AI: {e}"