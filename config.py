import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    OWNER_ID = int(os.getenv("OWNER_ID", "0"))
    # MongoDB connection string, e.g.
    # mongodb+srv://user:password@cluster0.xxxxx.mongodb.net/?retryWrites=true&w=majority
    MONGODB_URI = os.getenv("MONGODB_URI")
    MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "telegram_bot")

    # Base URL of the separately-deployed api.py service.
    # e.g. https://your-api.onrender.com
    API_BASE_URL = os.getenv("API_BASE_URL") or f"http://127.0.0.1:{os.getenv('API_PORT', '8000')}"

    # Shared secret with the API service. MUST match API_KEY set there —
    # every /api/* call from this bot sends it as X-API-Key.
    API_KEY = os.getenv("API_KEY")

    # Default limits
    DEFAULT_AI_LIMIT = 10
    
    SET_DEFAULT_AI_LIMIT = 20
    SET_USER_AI_LIMIT = 50

    # Database tables
    TABLE_USERS = "users"
    TABLE_ADMINS = "admins"
    TABLE_CHANNELS = "channels"
    TABLE_AI_USAGE = "ai_usage"
    TABLE_SCHEDULED_MSGS = "scheduled_messages"
    TABLE_JOIN_REQUESTS = "join_requests"
    TABLE_BOT_UPDATES = "bot_updates"

    # ===== Copyright Protection System =====
    TABLE_MEDIA_LOG = "media_log"
    TABLE_COPYRIGHT_STRIKES = "copyright_strikes"
    TABLE_COPYRIGHT_REPORTS = "copyright_reports"
    TABLE_AUDIT_LOG = "audit_log"

    # Media types that require the copyright warning + are logged for moderation.
    # Stickers/location/poll/contact are excluded — they can't carry long-form
    # copyrighted content the way audio/video/document/photo/animation can.
    COPYRIGHT_RELEVANT_MEDIA_TYPES = {
        "audio", "video", "document", "photo", "animation", "voice", "video_note"
    }

    # Strike 2 restriction duration in days. No admin command exists yet to
    # change this per-user — it's a flat constant. If you need per-user
    # configurability later, that's a separate feature (DB field + command).
    STRIKE_RESTRICTION_DAYS = 7
