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
        self.audio_batches = self.db[Config.TABLE_AUDIO_BATCHES]
        self.leaveban_groups = self.db[Config.TABLE_LEAVEBAN_GROUPS]
        self.pshare_batches = self.db[Config.TABLE_PSHARE_BATCHES]
        self.pshare_links = self.db[Config.TABLE_PSHARE_LINKS]
        self.contact_msgs = self.db[Config.TABLE_CONTACT_MSGS]
        self.contact_blocked = self.db[Config.TABLE_CONTACT_BLOCKED]
        self.clones = self.db[Config.TABLE_CLONES]
        self.clone_msgs = self.db[Config.TABLE_CLONE_MSGS]
        self.clone_blocked = self.db[Config.TABLE_CLONE_BLOCKED]
        self.clone_autodel = self.db[Config.TABLE_CLONE_AUTODEL]
        self.clone_sent = self.db[Config.TABLE_CLONE_SENT]

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

        # ----- Audio Vault -----
        self.audio_batches.create_index("key", unique=True)

        # ----- Private Share -----
        self.pshare_batches.create_index("batch_id", unique=True)
        self.pshare_batches.create_index([("owner_id", ASCENDING), ("status", ASCENDING)])
        self.pshare_links.create_index("key", unique=True)
        # One link per (batch, user): asking again returns the same link.
        self.pshare_links.create_index([("batch_id", ASCENDING), ("user_id", ASCENDING)], unique=True)
        try:
            # Only drafts carry draft_expires_at (removed on finalize), so
            # finished batches are never touched by this TTL.
            self.pshare_batches.create_index("draft_expires_at", expireAfterSeconds=0)
        except Exception as e:
            print(f"[WARN] pshare_batches TTL index not created: {e}")

        # ----- Contact Owner -----
        self.contact_msgs.create_index("owner_msg_id", unique=True)
        self.contact_blocked.create_index("user_id", unique=True)
        try:
            self.contact_msgs.create_index(
                "created_at", expireAfterSeconds=Config.CONTACT_MAP_TTL_DAYS * 86400
            )
        except Exception as e:
            print(f"[WARN] contact_msgs TTL index not created: {e}")

        # ----- Contact-bot clones -----
        self.clones.create_index("bot_id", unique=True)
        self.clones.create_index("owner_id", unique=True)  # one clone per user
        self.clone_msgs.create_index(
            [("bot_id", ASCENDING), ("owner_msg_id", ASCENDING)], unique=True
        )
        self.clone_blocked.create_index(
            [("bot_id", ASCENDING), ("user_id", ASCENDING)], unique=True
        )
        try:
            self.clone_msgs.create_index(
                "created_at", expireAfterSeconds=Config.CONTACT_MAP_TTL_DAYS * 86400
            )
        except Exception as e:
            print(f"[WARN] clone_msgs TTL index not created: {e}")

        self.clone_autodel.create_index(
            [("bot_id", ASCENDING), ("chat_id", ASCENDING), ("message_id", ASCENDING)], unique=True
        )
        self.clone_autodel.create_index("delete_at")
        self.clone_sent.create_index(
            [("bot_id", ASCENDING), ("owner_msg_id", ASCENDING)], unique=True
        )
        self.clone_sent.create_index([("bot_id", ASCENDING), ("confirm_msg_id", ASCENDING)])
        try:
            self.clone_sent.create_index(
                "created_at", expireAfterSeconds=Config.CLONE_SENT_TTL_HOURS * 3600
            )
        except Exception as e:
            print(f"[WARN] clone_sent TTL index not created: {e}")

        # ----- Leave-Ban Guard -----
        self.leaveban_groups.create_index("chat_id", unique=True)
        self.audio_batches.create_index([("owner_id", ASCENDING), ("status", ASCENDING)])
        # TTL index: Mongo deletes the document once `expires_at` passes.
        # This is what replaces the old bot's CLEANUP_INTERVAL_SECONDS job.
        # Documents where expires_at is None (or absent) are IGNORED by TTL,
        # which is exactly the "No Expiry" case — no special handling needed.
        # Caveat: the TTL monitor only runs about once a minute, so code
        # that reads a batch must still check expiry itself (see
        # get_audio_batch) rather than trusting the document's existence.
        try:
            self.audio_batches.create_index("expires_at", expireAfterSeconds=0)
        except Exception as e:
            # Shared/free Atlas tiers occasionally refuse index creation;
            # the feature still works, just without automatic purging.
            print(f"[WARN] audio_batches TTL index not created: {e}")

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

    # ========== AUDIO VAULT ==========
    # Document shape (replaces the old store.json entries):
    # {
    #   "key": "<url-safe share key>",
    #   "owner_id": <int>,
    #   "status": "draft" | "ready",
    #   "files": [{"file_id": ..., "title": ...}, ...],
    #   "expires_at": <datetime UTC> or None,
    #   "created_at": <datetime UTC>
    # }
    # All datetimes are naive UTC, matching the rest of this codebase.

    def create_audio_batch(self, key, owner_id, draft_expires_at):
        """Starts a new draft batch. draft_expires_at makes abandoned drafts
        self-clean via the TTL index; it is overwritten on finalize."""
        self.audio_batches.insert_one({
            "key": key,
            "owner_id": owner_id,
            "status": "draft",
            "files": [],
            "expires_at": draft_expires_at,
            "created_at": datetime.utcnow()
        })

    def get_draft_audio_batch(self, owner_id):
        """The owner's in-progress batch. Stored in Mongo rather than
        context.user_data so a restart mid-upload doesn't lose the files."""
        return self._clean(self.audio_batches.find_one(
            {"owner_id": owner_id, "status": "draft"},
            sort=[("created_at", DESCENDING)]
        ))

    def append_audio_file(self, key, file_id, title):
        """Atomic push — avoids the read-modify-write race the old
        save_store() had when several audio files arrived at once."""
        doc = self.audio_batches.find_one_and_update(
            {"key": key},
            {"$push": {"files": {"file_id": file_id, "title": title}}},
            return_document=True
        )
        return self._clean(doc)

    def finalize_audio_batch(self, key, expires_at):
        """Marks a draft ready and sets its real expiry (None = never)."""
        self.audio_batches.update_one(
            {"key": key},
            {"$set": {"status": "ready", "expires_at": expires_at}}
        )

    def get_audio_batch(self, key):
        """Returns the batch, or None if missing/expired. Expiry is checked
        here too because Mongo's TTL sweep lags by up to ~60 seconds."""
        doc = self.audio_batches.find_one({"key": key})
        if not doc:
            return None
        exp = doc.get("expires_at")
        if exp is not None and datetime.utcnow() >= exp:
            self.audio_batches.delete_one({"key": key})
            return None
        return self._clean(doc)

    def delete_audio_batch(self, key):
        return self.audio_batches.delete_one({"key": key}).deleted_count > 0

    def list_audio_batches(self, owner_id, limit=20):
        return self._clean_many(
            self.audio_batches.find({"owner_id": owner_id, "status": "ready"})
            .sort("created_at", DESCENDING).limit(limit)
        )

    # ========== PSHARE VAULT ==========
    # pshare_batches: {batch_id:int, owner_id, status:"draft"|"ready",
    #   files:[{file_id, type, title, caption}], draft_expires_at (drafts only),
    #   created_at}
    # pshare_links:   {key, batch_id, user_id (the ONLY account allowed to
    #   open it), owner_id, opened_count, blocked_count, last_opened_at,
    #   created_at}

    def create_pshare_batch(self, owner_id, draft_expires_at):
        batch_id = self._next_id("pshare_batch")
        self.pshare_batches.insert_one({
            "batch_id": batch_id,
            "owner_id": owner_id,
            "status": "draft",
            "files": [],
            "draft_expires_at": draft_expires_at,
            "created_at": datetime.utcnow()
        })
        return batch_id

    def get_pshare_draft(self, owner_id):
        return self._clean(self.pshare_batches.find_one(
            {"owner_id": owner_id, "status": "draft"},
            sort=[("created_at", DESCENDING)]
        ))

    def append_pshare_file(self, batch_id, file_info, status="draft"):
        """Atomic push. status="draft" while building a new batch,
        status="ready" to add an item to an already finished batch."""
        return self._clean(self.pshare_batches.find_one_and_update(
            {"batch_id": batch_id, "status": status},
            {"$push": {"files": file_info}},
            return_document=True
        ))

    def finalize_pshare_batch(self, batch_id):
        self.pshare_batches.update_one(
            {"batch_id": batch_id},
            {"$set": {"status": "ready"}, "$unset": {"draft_expires_at": ""}}
        )

    def get_pshare_batch(self, batch_id):
        return self._clean(self.pshare_batches.find_one({"batch_id": batch_id}))

    def list_pshare_batches(self, owner_id=None, limit=30):
        """owner_id=None -> every ready batch (used for the bot owner)."""
        q = {"status": "ready"}
        if owner_id is not None:
            q["owner_id"] = owner_id
        batches = self._clean_many(
            self.pshare_batches.find(q).sort("created_at", DESCENDING).limit(limit)
        )
        if batches:
            counts = {
                r["_id"]: r["n"] for r in self.pshare_links.aggregate([
                    {"$match": {"batch_id": {"$in": [b["batch_id"] for b in batches]}}},
                    {"$group": {"_id": "$batch_id", "n": {"$sum": 1}}}
                ])
            }
            for b in batches:
                b["link_count"] = counts.get(b["batch_id"], 0)
        return batches

    def delete_pshare_batch(self, batch_id):
        """Deleting a batch also kills every link that points to it."""
        self.pshare_links.delete_many({"batch_id": batch_id})
        return self.pshare_batches.delete_one({"batch_id": batch_id}).deleted_count > 0

    def get_or_create_pshare_link(self, batch_id, user_id, owner_id, key):
        """Returns (link_doc, created). Idempotent per (batch, user)."""
        existing = self.pshare_links.find_one({"batch_id": batch_id, "user_id": user_id})
        if existing:
            return self._clean(existing), False
        doc = {
            "key": key, "batch_id": batch_id, "user_id": user_id,
            "owner_id": owner_id, "opened_count": 0, "blocked_count": 0,
            "last_opened_at": None, "created_at": datetime.utcnow()
        }
        try:
            self.pshare_links.insert_one(dict(doc))
            return doc, True
        except DuplicateKeyError:
            # Lost a race on (batch_id, user_id) -> return the winner.
            return self._clean(self.pshare_links.find_one(
                {"batch_id": batch_id, "user_id": user_id})), False

    def pshare_key_exists(self, key):
        return self.pshare_links.find_one({"key": key}, {"_id": 1}) is not None

    def get_pshare_link(self, key):
        return self._clean(self.pshare_links.find_one({"key": key}))

    def list_pshare_links(self, batch_id, limit=100):
        return self._clean_many(
            self.pshare_links.find({"batch_id": batch_id}).sort("created_at", ASCENDING).limit(limit)
        )

    def delete_pshare_link(self, key):
        return self.pshare_links.delete_one({"key": key}).deleted_count > 0

    def touch_pshare_link(self, key):
        self.pshare_links.update_one(
            {"key": key},
            {"$inc": {"opened_count": 1}, "$set": {"last_opened_at": datetime.utcnow()}}
        )

    def bump_pshare_link_blocked(self, key):
        self.pshare_links.update_one({"key": key}, {"$inc": {"blocked_count": 1}})

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

    # ========== LEAVE-BAN GUARD ==========
    def set_leaveban(self, chat_id, enabled, user, contact_username=None, title=None):
        """`user` = who ran /leaveban. `contact_username` = the @username (no @)
        the setter typed when asked; shown in the join warning."""
        fields = {"enabled": bool(enabled), "updated_by": user.id,
                  "updated_at": datetime.utcnow()}
        if enabled:
            fields["enabled_by"] = user.id
            if contact_username:
                fields["contact_username"] = contact_username
            if title:
                fields["title"] = title
        self.leaveban_groups.update_one({"chat_id": chat_id}, {"$set": fields}, upsert=True)

    def set_leaveban_contact(self, chat_id, contact_username, user):
        self.leaveban_groups.update_one(
            {"chat_id": chat_id},
            {"$set": {"contact_username": contact_username, "updated_by": user.id,
                      "updated_at": datetime.utcnow()}},
            upsert=True,
        )

    def list_leaveban_groups(self, enabled_by=None, limit=30):
        """Groups set up through the bot. enabled_by=None -> all (bot owner/admins)."""
        q = {"enabled_by": enabled_by} if enabled_by else {"enabled_by": {"$exists": True}}
        return list(self.leaveban_groups.find(q, {"_id": 0}).sort("updated_at", DESCENDING).limit(limit))

    def get_leaveban(self, chat_id):
        return self.leaveban_groups.find_one({"chat_id": chat_id}, {"_id": 0})

    def is_leaveban_enabled(self, chat_id):
        doc = self.get_leaveban(chat_id)
        return bool(doc and doc.get("enabled"))

    # ========== CONTACT OWNER ==========
    def save_contact_map(self, owner_msg_id, user_id):
        """Remember which user a message in the owner's chat came from."""
        self.contact_msgs.update_one(
            {"owner_msg_id": owner_msg_id},
            {"$set": {"user_id": user_id, "created_at": datetime.utcnow()}},
            upsert=True,
        )

    def get_contact_user(self, owner_msg_id):
        doc = self.contact_msgs.find_one({"owner_msg_id": owner_msg_id})
        return doc["user_id"] if doc else None

    def is_contact_blocked(self, user_id):
        return self.contact_blocked.find_one({"user_id": user_id}) is not None

    def set_contact_blocked(self, user_id, blocked=True):
        if blocked:
            self.contact_blocked.update_one(
                {"user_id": user_id},
                {"$set": {"user_id": user_id, "blocked_at": datetime.utcnow()}},
                upsert=True,
            )
        else:
            self.contact_blocked.delete_one({"user_id": user_id})

    # ========== CONTACT-BOT CLONES ==========
    def add_clone(self, bot_id, owner_id, username, token_enc):
        """Raises DuplicateKeyError if the bot or the owner already has a clone."""
        self.clones.insert_one({
            "bot_id": bot_id, "owner_id": owner_id, "username": username,
            "token_enc": token_enc, "created_at": datetime.utcnow(),
        })

    def get_clone(self, bot_id):
        return self._clean(self.clones.find_one({"bot_id": bot_id}))

    def get_clone_by_owner(self, owner_id):
        return self._clean(self.clones.find_one({"owner_id": owner_id}))

    def list_clones(self):
        return self._clean_many(self.clones.find({}).sort("created_at", ASCENDING))

    def count_clones(self):
        return self.clones.count_documents({})

    def delete_clone(self, bot_id):
        """Removes the clone with its token, reply mappings and blocks."""
        self.clones.delete_one({"bot_id": bot_id})
        self.clone_msgs.delete_many({"bot_id": bot_id})
        self.clone_blocked.delete_many({"bot_id": bot_id})
        self.clone_autodel.delete_many({"bot_id": bot_id})
        self.clone_sent.delete_many({"bot_id": bot_id})

    def save_clone_map(self, bot_id, owner_msg_id, user_id):
        self.clone_msgs.update_one(
            {"bot_id": bot_id, "owner_msg_id": owner_msg_id},
            {"$set": {"user_id": user_id, "created_at": datetime.utcnow()}},
            upsert=True,
        )

    def get_clone_user(self, bot_id, owner_msg_id):
        doc = self.clone_msgs.find_one({"bot_id": bot_id, "owner_msg_id": owner_msg_id})
        return doc["user_id"] if doc else None

    def is_clone_blocked(self, bot_id, user_id):
        return self.clone_blocked.find_one({"bot_id": bot_id, "user_id": user_id}) is not None

    def set_clone_blocked(self, bot_id, user_id, blocked=True):
        if blocked:
            self.clone_blocked.update_one(
                {"bot_id": bot_id, "user_id": user_id},
                {"$set": {"blocked_at": datetime.utcnow()}},
                upsert=True,
            )
        else:
            self.clone_blocked.delete_one({"bot_id": bot_id, "user_id": user_id})

    # ----- clone auto-delete queue -----
    def set_clone_autodel(self, bot_id, seconds):
        self.clones.update_one({"bot_id": bot_id}, {"$set": {"autodel_seconds": int(seconds)}})

    def queue_clone_autodel(self, bot_id, chat_id, message_id, seconds, owner_chat=False):
        self.clone_autodel.update_one(
            {"bot_id": bot_id, "chat_id": chat_id, "message_id": message_id},
            {"$set": {"delete_at": datetime.utcnow() + timedelta(seconds=seconds),
                      "owner_chat": owner_chat}},
            upsert=True,
        )

    def due_clone_autodel(self, limit=100):
        """Raw docs (with _id) so the sweeper can drop exactly the ones it handled."""
        return list(self.clone_autodel.find(
            {"delete_at": {"$lte": datetime.utcnow()}}
        ).sort("delete_at", ASCENDING).limit(limit))

    def drop_clone_autodel(self, doc_id):
        self.clone_autodel.delete_one({"_id": doc_id})

    def clear_clone_autodel(self, bot_id):
        self.clone_autodel.delete_many({"bot_id": bot_id})

    def delete_clone_map(self, bot_id, owner_msg_id):
        self.clone_msgs.delete_one({"bot_id": bot_id, "owner_msg_id": owner_msg_id})

    # ----- clone: owner reply -> user's copy (delete for both sides) -----
    def save_clone_sent(self, bot_id, owner_msg_id, confirm_msg_id, user_id, user_msg_id):
        self.clone_sent.update_one(
            {"bot_id": bot_id, "owner_msg_id": owner_msg_id},
            {"$set": {"confirm_msg_id": confirm_msg_id, "user_id": user_id,
                      "user_msg_id": user_msg_id, "created_at": datetime.utcnow()}},
            upsert=True,
        )

    def find_clone_sent(self, bot_id, msg_id):
        """Match by the owner's own message id OR the bot's '✅ Bhej diya' message id."""
        return self._clean(self.clone_sent.find_one({
            "bot_id": bot_id,
            "$or": [{"owner_msg_id": msg_id}, {"confirm_msg_id": msg_id}],
        }))

    def delete_clone_sent(self, bot_id, owner_msg_id):
        self.clone_sent.delete_one({"bot_id": bot_id, "owner_msg_id": owner_msg_id})


db = Database()
