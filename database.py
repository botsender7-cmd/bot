import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool
from contextlib import contextmanager
from config import Config
from datetime import datetime, date


class Database:
    def __init__(self):
        self.dsn = Config.DATABASE_URL.strip() if Config.DATABASE_URL else ""

        if not self.dsn:
            raise ValueError("DATABASE_URL is missing! Check your .env or Hugging Face secrets.")

        print(f"[DEBUG] Connecting to Neon Postgres: {self.dsn.split('@')[-1] if '@' in self.dsn else '(hidden)'}")

        # Neon free tier: keep the pool small. minconn=1 avoids holding
        # connections open when the bot is idle; maxconn=5 is plenty for a
        # single-worker Telegram bot handling requests one at a time.
        self.pool = ThreadedConnectionPool(1, 5, dsn=self.dsn, sslmode="require")

    @contextmanager
    def _conn(self):
        conn = self.pool.getconn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self.pool.putconn(conn)

    def _query(self, sql, params=None, fetch="all"):
        """fetch: 'all' | 'one' | 'none'. Returns list[dict] / dict|None / None."""
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                try:
                    cur.execute(sql, params or ())
                except Exception as e:
                    print(f"[DB ERROR] {sql[:100]} -> {e}")
                    if fetch == "all":
                        return []
                    return None
                if fetch == "none":
                    return None
                if fetch == "one":
                    row = cur.fetchone()
                    return dict(row) if row else None
                rows = cur.fetchall()
                return [dict(r) for r in rows]

    # ========== USERS ==========
    def get_user(self, user_id):
        return self._query("SELECT * FROM users WHERE user_id = %s", (user_id,), fetch="one")

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
        self._query(
            """INSERT INTO users (user_id, username, first_name, created_at, ai_requests_today, last_ai_request_date, is_banned)
               VALUES (%(user_id)s, %(username)s, %(first_name)s, %(created_at)s, %(ai_requests_today)s, %(last_ai_request_date)s, %(is_banned)s)
               ON CONFLICT (user_id) DO NOTHING""",
            data, fetch="none"
        )
        return data

    def update_user_ai_usage(self, user_id):
        today = str(date.today())
        user = self.get_user(user_id)
        if user and user.get("last_ai_request_date") == today:
            self._query(
                "UPDATE users SET ai_requests_today = %s WHERE user_id = %s",
                (user["ai_requests_today"] + 1, user_id), fetch="none"
            )
        else:
            self._query(
                "UPDATE users SET ai_requests_today = 1, last_ai_request_date = %s WHERE user_id = %s",
                (today, user_id), fetch="none"
            )

    def reset_ai_usage(self, user_id):
        self._query(
            "UPDATE users SET ai_requests_today = 0, last_ai_request_date = %s WHERE user_id = %s",
            (str(date.today()), user_id), fetch="none"
        )

    def get_all_users(self):
        return self._query("SELECT * FROM users")

    def set_default_ai_limit(self, limit):
        """Updates ai_limit for all non-owner users who still have the default limit."""
        self._query(
            "UPDATE users SET ai_limit = %s WHERE ai_limit = %s OR ai_limit IS NULL",
            (limit, Config.DEFAULT_AI_LIMIT), fetch="none"
        )
        Config.DEFAULT_AI_LIMIT = limit

    # ========== ADMINS ==========
    def get_admin(self, user_id):
        return self._query("SELECT * FROM admins WHERE user_id = %s", (user_id,), fetch="one")

    def add_admin(self, user_id, added_by):
        data = {"user_id": user_id, "added_by": added_by, "created_at": datetime.utcnow()}
        self._query(
            """INSERT INTO admins (user_id, added_by, created_at) VALUES (%(user_id)s, %(added_by)s, %(created_at)s)
               ON CONFLICT (user_id) DO NOTHING""",
            data, fetch="none"
        )
        return data

    def remove_admin(self, user_id):
        self._query("DELETE FROM admins WHERE user_id = %s", (user_id,), fetch="none")
        return True

    def get_all_admins(self):
        return self._query("SELECT * FROM admins")

    # ========== CHANNELS ==========
    def get_channel(self, channel_id):
        return self._query("SELECT * FROM channels WHERE channel_id = %s", (channel_id,), fetch="one")

    def add_channel(self, channel_id, channel_name, invite_link, added_by, auto_approve=False):
        data = {
            "channel_id": channel_id,
            "channel_name": channel_name,
            "invite_link": invite_link,
            "added_by": added_by,
            "auto_approve": auto_approve,
            "created_at": datetime.utcnow()
        }
        self._query(
            """INSERT INTO channels (channel_id, channel_name, invite_link, added_by, auto_approve, created_at)
               VALUES (%(channel_id)s, %(channel_name)s, %(invite_link)s, %(added_by)s, %(auto_approve)s, %(created_at)s)
               ON CONFLICT (channel_id) DO UPDATE SET channel_name = EXCLUDED.channel_name,
                   invite_link = EXCLUDED.invite_link, auto_approve = EXCLUDED.auto_approve""",
            data, fetch="none"
        )
        return data

    def update_channel_invite_link(self, channel_id, invite_link):
        self._query("UPDATE channels SET invite_link = %s WHERE channel_id = %s", (invite_link, channel_id), fetch="none")

    def remove_channel(self, channel_id):
        self._query("DELETE FROM channels WHERE channel_id = %s", (channel_id,), fetch="none")
        return True

    def get_all_channels(self):
        return self._query("SELECT * FROM channels")

    def update_channel_auto_approve(self, channel_id, auto_approve):
        self._query("UPDATE channels SET auto_approve = %s WHERE channel_id = %s", (auto_approve, channel_id), fetch="none")

    # ========== AI USAGE LOG ==========
    def log_ai_request(self, user_id, prompt, response):
        self._query(
            "INSERT INTO ai_usage (user_id, prompt, response, created_at) VALUES (%s, %s, %s, %s)",
            (user_id, prompt, response, datetime.utcnow()), fetch="none"
        )

    # ========== SCHEDULED MESSAGES ==========
    def add_scheduled_message(self, user_id, target_type, target_id, message_text, schedule_time,
                               media_type=None, media_file_id=None, media_caption=None,
                               reply_markup_json=None):
        row = self._query(
            """INSERT INTO scheduled_messages
               (user_id, target_type, target_id, message_text, media_type, media_file_id,
                media_caption, reply_markup_json, schedule_time, status, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s)
               RETURNING *""",
            (user_id, target_type, target_id, message_text, media_type, media_file_id,
             media_caption, reply_markup_json, schedule_time, datetime.utcnow()),
            fetch="one"
        )
        return row

    def get_pending_messages(self):
        return self._query("SELECT * FROM scheduled_messages WHERE status = 'pending'")

    def update_message_status(self, msg_id, status):
        self._query("UPDATE scheduled_messages SET status = %s WHERE id = %s", (status, msg_id), fetch="none")

    def get_user_scheduled_messages(self, user_id):
        return self._query("SELECT * FROM scheduled_messages WHERE user_id = %s", (user_id,))

    def get_scheduled_message_by_id(self, msg_id):
        """Looks up a scheduled message by its own id, regardless of owner.
        Needed for copyright reports filed against someone else's schedule."""
        return self._query("SELECT * FROM scheduled_messages WHERE id = %s", (msg_id,), fetch="one")

    def delete_scheduled_message(self, msg_id):
        self._query("DELETE FROM scheduled_messages WHERE id = %s", (msg_id,), fetch="none")
        return True

    # ========== JOIN REQUESTS ==========
    def log_join_request(self, channel_id, user_id, status):
        self._query(
            "INSERT INTO join_requests (channel_id, user_id, status, created_at) VALUES (%s, %s, %s, %s)",
            (channel_id, user_id, status, datetime.utcnow()), fetch="none"
        )

    def get_join_request(self, channel_id, user_id):
        return self._query(
            "SELECT * FROM join_requests WHERE channel_id = %s AND user_id = %s",
            (channel_id, user_id), fetch="one"
        )

    def save_join_request(self, user_id, channel_id):
        if self.has_join_request(user_id, channel_id):
            return

        data = {
            "user_id": user_id,
            "channel_id": channel_id,
            "status": "verified",
            "created_at": datetime.utcnow()
        }
        self._query(
            "INSERT INTO join_requests (user_id, channel_id, status, created_at) VALUES (%(user_id)s, %(channel_id)s, %(status)s, %(created_at)s)",
            data, fetch="none"
        )
        return data

    def has_join_request(self, user_id, channel_id):
        rows = self._query(
            "SELECT 1 FROM join_requests WHERE user_id = %s AND channel_id = %s",
            (user_id, channel_id)
        )
        return len(rows) > 0

    def add_user_channel(self, user_id, channel_id, channel_name):
        data = {
            "user_id": user_id,
            "channel_id": channel_id,
            "channel_name": channel_name,
            "auto_approve": True,
            "created_at": datetime.utcnow()
        }
        return self._query(
            """INSERT INTO user_channels (user_id, channel_id, channel_name, auto_approve, created_at)
               VALUES (%(user_id)s, %(channel_id)s, %(channel_name)s, %(auto_approve)s, %(created_at)s)
               RETURNING *""",
            data, fetch="one"
        )

    def get_user_channel(self, channel_id):
        return self._query("SELECT * FROM user_channels WHERE channel_id = %s", (channel_id,), fetch="one")

    def get_user_channels(self, user_id):
        return self._query("SELECT * FROM user_channels WHERE user_id = %s", (user_id,))

    def remove_user_channel(self, channel_id):
        self._query("DELETE FROM user_channels WHERE channel_id = %s", (channel_id,), fetch="none")
        return True

    def toggle_auto_approve(self, channel_id, state):
        self._query("UPDATE user_channels SET auto_approve = %s WHERE channel_id = %s", (state, channel_id), fetch="none")

    # ========== BOT UPDATES ==========
    def add_bot_update(self, title, message, added_by):
        return self._query(
            """INSERT INTO bot_updates (title, message, added_by, created_at)
               VALUES (%s, %s, %s, %s) RETURNING *""",
            (title, message, added_by, datetime.utcnow()), fetch="one"
        )

    def get_all_updates(self):
        return self._query("SELECT * FROM bot_updates ORDER BY created_at DESC")

    def delete_update(self, update_id):
        self._query("DELETE FROM bot_updates WHERE id = %s", (update_id,), fetch="none")
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
        self._query("UPDATE users SET is_banned = %s WHERE user_id = %s", (banned, user_id), fetch="none")

    def set_user_restricted_until(self, user_id, until_dt):
        """Pass None to clear the restriction."""
        self._query("UPDATE users SET restricted_until = %s WHERE user_id = %s", (until_dt, user_id), fetch="none")

    def has_acknowledged_copyright_warning(self, user_id):
        user = self.get_user(user_id)
        return bool(user and user.get("copyright_warning_ack"))

    def set_copyright_warning_acknowledged(self, user_id):
        self._query("UPDATE users SET copyright_warning_ack = TRUE WHERE user_id = %s", (user_id,), fetch="none")

    # ========== COPYRIGHT: MEDIA LOG ==========
    def log_scheduled_media(self, user_id, file_id, message_id, media_type, schedule_time, upload_date=None):
        """Records every scheduled copyright-relevant media item for moderation lookup.
        message_id here is the scheduled_messages.id (the schedule's own DB id),
        since the original Telegram message_id isn't retained anywhere upstream."""
        row = self._query(
            """INSERT INTO media_log (user_id, file_id, message_id, media_type, schedule_time, upload_date, strike_status, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, 'none', %s) RETURNING *""",
            (user_id, file_id, message_id, media_type, schedule_time, upload_date or datetime.utcnow(), datetime.utcnow()),
            fetch="one"
        )
        return row

    def get_media_log_entry(self, log_id):
        return self._query("SELECT * FROM media_log WHERE id = %s", (log_id,), fetch="one")

    def get_media_log_by_schedule_id(self, schedule_message_id):
        return self._query("SELECT * FROM media_log WHERE message_id = %s", (schedule_message_id,), fetch="one")

    def get_user_media_log(self, user_id):
        return self._query("SELECT * FROM media_log WHERE user_id = %s ORDER BY created_at DESC", (user_id,))

    def mark_media_log_status(self, log_id, status):
        """status: 'none' | 'flagged' | 'removed' | 'infringing'"""
        self._query("UPDATE media_log SET strike_status = %s WHERE id = %s", (status, log_id), fetch="none")

    # ========== COPYRIGHT: REPORTS ==========
    def create_copyright_report(self, reporter_id, reported_user_id, scheduled_message_id, reason):
        return self._query(
            """INSERT INTO copyright_reports (reporter_id, reported_user_id, scheduled_message_id, reason, status, created_at)
               VALUES (%s, %s, %s, %s, 'open', %s) RETURNING *""",
            (reporter_id, reported_user_id, scheduled_message_id, reason, datetime.utcnow()), fetch="one"
        )

    def get_all_reports(self):
        return self._query("SELECT * FROM copyright_reports ORDER BY created_at DESC")

    def get_report(self, report_id):
        return self._query("SELECT * FROM copyright_reports WHERE id = %s", (report_id,), fetch="one")

    def update_report_status(self, report_id, status):
        self._query("UPDATE copyright_reports SET status = %s WHERE id = %s", (status, report_id), fetch="none")

    # ========== COPYRIGHT: STRIKES ==========
    def get_user_strike_count(self, user_id):
        rows = self._query("SELECT 1 FROM copyright_strikes WHERE user_id = %s", (user_id,))
        return len(rows) if rows else 0

    def add_strike(self, user_id, reason, added_by):
        self._query(
            "INSERT INTO copyright_strikes (user_id, reason, added_by, created_at) VALUES (%s, %s, %s, %s)",
            (user_id, reason, added_by, datetime.utcnow()), fetch="none"
        )
        return self.get_user_strike_count(user_id)

    def get_user_strikes(self, user_id):
        return self._query("SELECT * FROM copyright_strikes WHERE user_id = %s ORDER BY created_at DESC", (user_id,))

    def reset_strikes(self, user_id):
        self._query("DELETE FROM copyright_strikes WHERE user_id = %s", (user_id,), fetch="none")

    # ========== COPYRIGHT: AUDIT LOG ==========
    def add_audit_log(self, action_type, actor_id, target_user_id, details=""):
        self._query(
            "INSERT INTO audit_log (action_type, actor_id, target_user_id, details, created_at) VALUES (%s, %s, %s, %s, %s)",
            (action_type, actor_id, target_user_id, details, datetime.utcnow()), fetch="none"
        )

    def get_audit_log(self, limit=50):
        return self._query("SELECT * FROM audit_log ORDER BY created_at DESC LIMIT %s", (limit,))

    def get_user_audit_log(self, user_id):
        return self._query("SELECT * FROM audit_log WHERE target_user_id = %s ORDER BY created_at DESC", (user_id,))


db = Database()
