from groq import Groq
from config import Config
from database import db

class AIHandler:

    def __init__(self):
        print("GROQ KEY EXISTS:", bool(Config.GROQ_API_KEY))
        print("GROQ KEY LENGTH:", len(Config.GROQ_API_KEY) if Config.GROQ_API_KEY else 0)

        self.client = Groq(api_key=Config.GROQ_API_KEY)

    def get_ai_response(self, user_id, prompt):
        user = db.get_user(user_id)
        if not user:
            return None, "User not found. Please start the bot first."

        import datetime
        today = str(datetime.date.today())
        if user.get("last_ai_request_date") != today:
            db.reset_ai_usage(user_id)
        # Always re-fetch after potential reset to get current count
        user = db.get_user(user_id)

        ai_limit = user.get("ai_limit") or Config.DEFAULT_AI_LIMIT
        if user_id != Config.OWNER_ID:
            if user.get("ai_requests_today", 0) >= ai_limit:
                return None, f"❌ Aapka daily AI limit ({ai_limit} requests) khatam ho gaya hai. Kal try karein!"

        try:
            chat_completion = self.client.chat.completions.create(
                messages=[
                    {"role": "system", "content": "You are a helpful AI assistant. Answer in the same language as the user's query."},
                    {"role": "user", "content": prompt}
                ],
                model="llama-3.3-70b-versatile",
                temperature=0.7,
                max_tokens=1024
            )
            response = chat_completion.choices[0].message.content

            # Log and update usage
            db.log_ai_request(user_id, prompt, response)
            if user_id != Config.OWNER_ID:
                db.update_user_ai_usage(user_id)

            remaining = ai_limit - user.get("ai_requests_today", 0) - 1
            return response, f"✅ Response mil gaya! Aapke paas {remaining} requests baki hain aaj ke liye."

        except Exception as e:
            import traceback
            print("GROQ ERROR:", repr(e))
            traceback.print_exc()
            return None, f"❌ AI Error: {str(e)}"

ai_handler = AIHandler()
