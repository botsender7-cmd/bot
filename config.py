import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    OWNER_ID = int(os.getenv("OWNER_ID", "0"))
    GROQ_API_KEY = os.getenv("GROQ_API_KEY")
    # Neon connection string, e.g.
    # postgresql://user:password@ep-xxxx.region.aws.neon.tech/dbname?sslmode=require
    DATABASE_URL = os.getenv("DATABASE_URL")
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
