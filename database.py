from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError
from config import Config
from datetime import datetime, date, timedelta


class Database:
    def __init__(self):
        self.uri = Config.MONGODB_URI.strip() if Config.MONGODB_URI else ""

        if not self.uri:
            raise ValueError("MONGODB_URI is missing! Check your .env or Hugging Face secrets.")

        print(f"[DEBUG] Connecting to MongoDB: {self.uri.split('@')[-1] if '@' in self.uri else '(hidden)'}")

        self.client = MongoClient(self.uri, serverSelectionTimeoutMS=10000)
        self.db = self.client[Config.MONGODB_DB_NAME]

        # Collections
        self.users = self.db["users"]
        self.admins = self.db["admins"]
        self.channels = self.db["channels"]
        self.ai_usage = self.db["ai_usage"]
        self.scheduled_messages = self.db["scheduled_messages"]
        self.join_requests = self.db["join_requests"]
        self.user_channels = self.db["user_channels"]
        self.bot_updates = self.db["bot_updates"]
        self.media_log = self.db["media_log"]
        self.copyright_reports = self.db["copyright_reports"]
        self.copyright_strikes = self.db["copyright_strikes"]
        self.audit_log = self.db["audit_log"]
        self.counters = self.db["counters"]

        # Fail fast if the connection string / cluster is unreachable, same
        # spirit as the old pool creation failing loudly on bad DSNs.
        self.client.admin.command("ping")

        self._ensure_indexes()

    def _ensure_indexes(self):
        self.users.create_index("user_id", unique=True)
        self.admins.create_index("user_id", unique=True)
        self.channels.create_index("channel_id", unique=True)
        self.scheduled_messages.create_index("id", unique=True)
        self.scheduled_messages.create_index("status")
        self.scheduled_messages.create_index("user_id")
        self.join_requests.create_index([("channel_id", ASCENDING), ("user_id", ASCENDING)])
        self.user_channels.create_index("channel_id", unique=True)
        self.user_channels.create_index("user_id")
        self.bot_updates.create_index("id", unique=True)
        self.media_log.create_index("id", unique=True)
        self.media_log.create_index("message_id")
        self.media_log.create_index("user_id")
        self.copyright_reports.create_index("id", unique=True)
        self.copyright_strikes.create_index("user_id")
        self.audit_log.create_index("target_user_id")

    # ========== INTERNAL HELPERS ==========
    def _next_id(self, name):
        """Mongo has no SERIAL/auto-increment column, so integer ids (needed
        because Telegram callback_data parses them back with int()) are
        handed out from a counters collection instead of relying on _id."""
        doc = self.counters.find_one_and_update(
            {"_id": name},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=True
        )
        return doc["seq"]

    @staticmethod
    def _clean(doc):
        """Strip Mongo's internal _id (ObjectId isn't used anywhere downstream
        and isn't JSON-serializable) so callers get plain dicts like before."""
        if doc is None:
            return None
        doc.pop("_id", None)
        return doc

    @classmethod
    def _clean_many(cls, docs):
        return [cls._clean(d) for d in docs]

    # ========== USERS ==========
    def get_user(self, user_id):
        return self._clean(self.users.find_one({"user_id": user_id}))

    def create_user(self, user_id, username, first_name):
        data = {
            "user_id": user_id,
            "username": username,
            "first_name": first_name,
            "created_at": datetime.utcnow(),
            "ai_requests_today": 0,
            "last_ai_request_date": str(date.today()),
            "is_banned": False
        }
        try:
            self.users.insert_one(dict(data))
        except DuplicateKeyError:
            pass
        return data

    def update_user_ai_usage(self, user_id):
        today = str(date.today())
        user = self.get_user(user_id)
        if user and user.get("last_ai_request_date") == today:
            self.users.update_one(
                {"user_id": user_id},
                {"$set": {"ai_requests_today": user["ai_requests_today"] + 1}}
            )
        else:
            self.users.update_one(
                {"user_id": user_id},
                {"$set": {"ai_requests_today": 1, "last_ai_request_date": today}}
            )

    def reset_ai_usage(self, user_id):
        self.users.update_one(
            {"user_id": user_id},
            {"$set": {"ai_requests_today": 0, "last_ai_request_date": str(date.today())}}
        )

    def get_all_users(self):
        return self._clean_many(self.users.find())

    def set_default_ai_limit(self, limit):
        """Updates ai_limit for all non-owner users who still have the default limit."""
        self.users.update_many(
            {"$or": [{"ai_limit": Config.DEFAULT_AI_LIMIT}, {"ai_limit": None}, {"ai_limit": {"$exists": False}}]},
            {"$set": {"ai_limit": limit}}
        )
        Config.DEFAULT_AI_LIMIT = limit

    def set_user_ai_limit(self, user_id, limit):
        """Sets a single user's ai_limit. Replaces the old dead `db._patch(...)`
        call (leftover from a pre-Postgres Supabase/PostgREST implementation)
        that the owner-panel 'set user AI limit' flow was silently failing on."""
        self.users.update_one({"user_id": user_id}, {"$set": {"ai_limit": limit}})

    # ========== ADMINS ==========
    def get_admin(self, user_id):
        return self._clean(self.admins.find_one({"user_id": user_id}))

    def add_admin(self, user_id, added_by):
        data = {"user_id": user_id, "added_by": added_by, "created_at": datetime.utcnow()}
        try:
            self.admins.insert_one(dict(data))
        except DuplicateKeyError:
            pass
        return data

    def remove_admin(self, user_id):
        self.admins.delete_one({"user_id": user_id})
        return True

    def get_all_admins(self):
        return self._clean_many(self.admins.find())

    # ========== CHANNELS ==========
    def get_channel(self, channel_id):
        return self._clean(self.channels.find_one({"channel_id": channel_id}))

    def add_channel(self, channel_id, channel_name, invite_link, added_by, auto_approve=False):
        data = {
            "channel_id": channel_id,
            "channel_name": channel_name,
            "invite_link": invite_link,
            "added_by": added_by,
            "auto_approve": auto_approve,
            "created_at": datetime.utcnow()
        }
        self.channels.update_one(
            {"channel_id": channel_id},
            {
                "$set": {
                    "channel_name": channel_name,
                    "invite_link": invite_link,
                    "auto_approve": auto_approve
                },
                "$setOnInsert": {
                    "channel_id": channel_id,
                    "added_by": added_by,
                    "created_at": data["created_at"]
                }
            },
            upsert=True
        )
        return data

    def update_channel_invite_link(self, channel_id, invite_link):
        self.channels.update_one({"channel_id": channel_id}, {"$set": {"invite_link": invite_link}})

    def remove_channel(self, channel_id):
        self.channels.delete_one({"channel_id": channel_id})
        return True

    def get_all_channels(self):
        return self._clean_many(self.channels.find())

    def update_channel_auto_approve(self, channel_id, auto_approve):
        self.channels.update_one({"channel_id": channel_id}, {"$set": {"auto_approve": auto_approve}})

    # ========== AI USAGE LOG ==========
    def log_ai_request(self, user_id, prompt, response):
        self.ai_usage.insert_one({
            "user_id": user_id,
            "prompt": prompt,
            "response": response,
            "created_at": datetime.utcnow()
        })

    # ========== SCHEDULED MESSAGES ==========
    def add_scheduled_message(self, user_id, target_type, target_id, message_text, schedule_time,
                               media_type=None, media_file_id=None, media_caption=None,
                               reply_markup_json=None):
        data = {
            "id": self._next_id("scheduled_messages"),
            "user_id": user_id,
            "target_type": target_type,
            "target_id": target_id,
            "message_text": message_text,
            "media_type": media_type,
            "media_file_id": media_file_id,
            "media_caption": media_caption,
            "reply_markup_json": reply_markup_json,
            "schedule_time": schedule_time,
            "status": "pending",
            "created_at": datetime.utcnow()
        }
        self.scheduled_messages.insert_one(dict(data))
        return data

    def get_pending_messages(self):
        return self._clean_many(self.scheduled_messages.find({"status": "pending"}))

    def update_message_status(self, msg_id, status):
        self.scheduled_messages.update_one({"id": msg_id}, {"$set": {"status": status}})

    def get_user_scheduled_messages(self, user_id):
        return self._clean_many(self.scheduled_messages.find({"user_id": user_id}))

    def get_scheduled_message_by_id(self, msg_id):
        """Looks up a scheduled message by its own id, regardless of owner.
        Needed for copyright reports filed against someone else's schedule."""
        return self._clean(self.scheduled_messages.find_one({"id": msg_id}))

    def delete_scheduled_message(self, msg_id):
        self.scheduled_messages.delete_one({"id": msg_id})
        return True

    def delete_old_sent_messages(self, older_than_hours=24):
        """Delete sent scheduled messages older than `older_than_hours`.
        Was called from scheduler.py's daily cleanup job but never existed
        on the old Database class, so the job silently failed every run."""
        cutoff = datetime.utcnow() - timedelta(hours=older_than_hours)
        self.scheduled_messages.delete_many({"status": "sent", "schedule_time": {"$lt": cutoff}})

    # ========== JOIN REQUESTS ==========
    def log_join_request(self, channel_id, user_id, status):
        self.join_requests.insert_one({
            "channel_id": channel_id,
            "user_id": user_id,
            "status": status,
            "created_at": datetime.utcnow()
        })

    def get_join_request(self, channel_id, user_id):
        return self._clean(self.join_requests.find_one({"channel_id": channel_id, "user_id": user_id}))

    def save_join_request(self, user_id, channel_id):
        if self.has_join_request(user_id, channel_id):
            return

        data = {
            "user_id": user_id,
            "channel_id": channel_id,
            "status": "verified",
            "created_at": datetime.utcnow()
        }
        self.join_requests.insert_one(dict(data))
        return data

    def has_join_request(self, user_id, channel_id):
        return self.join_requests.count_documents({"user_id": user_id, "channel_id": channel_id}, limit=1) > 0

    def add_user_channel(self, user_id, channel_id, channel_name):
        data = {
            "user_id": user_id,
            "channel_id": channel_id,
            "channel_name": channel_name,
            "auto_approve": True,
            "created_at": datetime.utcnow()
        }
        self.user_channels.insert_one(dict(data))
        return data

    def get_user_channel(self, channel_id):
        return self._clean(self.user_channels.find_one({"channel_id": channel_id}))

    def get_user_channels(self, user_id):
        return self._clean_many(self.user_channels.find({"user_id": user_id}))

    def remove_user_channel(self, channel_id):
        self.user_channels.delete_one({"channel_id": channel_id})
        return True

    def toggle_auto_approve(self, channel_id, state):
        self.user_channels.update_one({"channel_id": channel_id}, {"$set": {"auto_approve": state}})

    # ========== BOT UPDATES ==========
    def add_bot_update(self, title, message, added_by):
        data = {
            "id": self._next_id("bot_updates"),
            "title": title,
            "message": message,
            "added_by": added_by,
            "created_at": datetime.utcnow()
        }
        self.bot_updates.insert_one(dict(data))
        return data

    def get_all_updates(self):
        return self._clean_many(self.bot_updates.find().sort("created_at", DESCENDING))

    def delete_update(self, update_id):
        self.bot_updates.delete_one({"id": update_id})
        return True

    # ========== COPYRIGHT: BAN / RESTRICTION CHECKS ==========
    def is_user_banned(self, user_id):
        user = self.get_user(user_id)
        return bool(user and user.get("is_banned"))

    def is_user_restricted(self, user_id):
        """Returns the restriction expiry datetime if user is currently
        restricted from scheduling, else None. Restriction is time-bound
        (strike 2); ban (strike 3) is permanent and checked separately."""
        user = self.get_user(user_id)
        if not user:
            return None
        until = user.get("restricted_until")
        if not until:
            return None
        if isinstance(until, str):
            try:
                until = datetime.fromisoformat(until)
            except Exception:
                return None
        if until > datetime.utcnow():
            return until
        return None

    def set_user_banned(self, user_id, banned=True):
        self.users.update_one({"user_id": user_id}, {"$set": {"is_banned": banned}})

    def set_user_restricted_until(self, user_id, until_dt):
        """Pass None to clear the restriction."""
        self.users.update_one({"user_id": user_id}, {"$set": {"restricted_until": until_dt}})

    def has_acknowledged_copyright_warning(self, user_id):
        user = self.get_user(user_id)
        return bool(user and user.get("copyright_warning_ack"))

    def set_copyright_warning_acknowledged(self, user_id):
        self.users.update_one({"user_id": user_id}, {"$set": {"copyright_warning_ack": True}})

    # ========== COPYRIGHT: MEDIA LOG ==========
    def log_scheduled_media(self, user_id, file_id, message_id, media_type, schedule_time, upload_date=None):
        """Records every scheduled copyright-relevant media item for moderation lookup.
        message_id here is the scheduled_messages.id (the schedule's own DB id),
        since the original Telegram message_id isn't retained anywhere upstream."""
        data = {
            "id": self._next_id("media_log"),
            "user_id": user_id,
            "file_id": file_id,
            "message_id": message_id,
            "media_type": media_type,
            "schedule_time": schedule_time,
            "upload_date": upload_date or datetime.utcnow(),
            "strike_status": "none",
            "created_at": datetime.utcnow()
        }
        self.media_log.insert_one(dict(data))
        return data

    def get_media_log_entry(self, log_id):
        return self._clean(self.media_log.find_one({"id": log_id}))

    def get_media_log_by_schedule_id(self, schedule_message_id):
        return self._clean(self.media_log.find_one({"message_id": schedule_message_id}))

    def get_user_media_log(self, user_id):
        return self._clean_many(self.media_log.find({"user_id": user_id}).sort("created_at", DESCENDING))

    def mark_media_log_status(self, log_id, status):
        """status: 'none' | 'flagged' | 'removed' | 'infringing'"""
        self.media_log.update_one({"id": log_id}, {"$set": {"strike_status": status}})

    # ========== COPYRIGHT: REPORTS ==========
    def create_copyright_report(self, reporter_id, reported_user_id, scheduled_message_id, reason):
        data = {
            "id": self._next_id("copyright_reports"),
            "reporter_id": reporter_id,
            "reported_user_id": reported_user_id,
            "scheduled_message_id": scheduled_message_id,
            "reason": reason,
            "status": "open",
            "created_at": datetime.utcnow()
        }
        self.copyright_reports.insert_one(dict(data))
        return data

    def get_all_reports(self):
        return self._clean_many(self.copyright_reports.find().sort("created_at", DESCENDING))

    def get_report(self, report_id):
        return self._clean(self.copyright_reports.find_one({"id": report_id}))

    def update_report_status(self, report_id, status):
        self.copyright_reports.update_one({"id": report_id}, {"$set": {"status": status}})

    # ========== COPYRIGHT: STRIKES ==========
    def get_user_strike_count(self, user_id):
        return self.copyright_strikes.count_documents({"user_id": user_id})

    def add_strike(self, user_id, reason, added_by):
        self.copyright_strikes.insert_one({
            "user_id": user_id,
            "reason": reason,
            "added_by": added_by,
            "created_at": datetime.utcnow()
        })
        return self.get_user_strike_count(user_id)

    def get_user_strikes(self, user_id):
        return self._clean_many(self.copyright_strikes.find({"user_id": user_id}).sort("created_at", DESCENDING))

    def reset_strikes(self, user_id):
        self.copyright_strikes.delete_many({"user_id": user_id})

    # ========== COPYRIGHT: AUDIT LOG ==========
    def add_audit_log(self, action_type, actor_id, target_user_id, details=""):
        self.audit_log.insert_one({
            "action_type": action_type,
            "actor_id": actor_id,
            "target_user_id": target_user_id,
            "details": details,
            "created_at": datetime.utcnow()
        })

    def get_audit_log(self, limit=50):
        return self._clean_many(self.audit_log.find().sort("created_at", DESCENDING).limit(limit))

    def get_user_audit_log(self, user_id):
        return self._clean_many(self.audit_log.find({"target_user_id": user_id}).sort("created_at", DESCENDING))


db = Database()
