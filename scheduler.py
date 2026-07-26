from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from datetime import datetime, timedelta, timezone
from database import db
from telegram import Bot
import asyncio
import logging

logger = logging.getLogger(__name__)

# How often (seconds) the DB-polling safety-net loop wakes up to
# check for pending messages that APScheduler may have missed.
# 30 s is a good balance: low overhead, catches anything missed
# within half a minute of its scheduled time.
DB_POLL_INTERVAL = 30


class MessageScheduler:
    def __init__(self, bot: Bot):
        self.bot = bot
        self._scheduler = None
        # Track which msg_ids are currently "in-flight" so the DB poll
        # loop never double-fires a message that APScheduler already sent.
        self._in_flight: set = set()

    def _get_scheduler(self):
        """Return the scheduler, starting it inside the running loop if needed."""
        if self._scheduler is None:
            self._scheduler = AsyncIOScheduler(timezone="UTC")
            try:
                loop = asyncio.get_running_loop()
                self._scheduler._eventloop = loop
            except RuntimeError:
                pass
            self._scheduler.start()
            logger.info("APScheduler started inside running event loop (timezone=UTC).")
        return self._scheduler

    def schedule_message(self, msg_id, target_type, target_id, message_text, schedule_time,
                         media_type=None, media_file_id=None, media_caption=None,
                         reply_markup=None):
        trigger = DateTrigger(run_date=schedule_time)
        self._get_scheduler().add_job(
            self._send_scheduled_message,
            trigger=trigger,
            args=[msg_id, target_type, target_id, message_text, media_type, media_file_id,
                  media_caption, reply_markup],
            id=str(msg_id),
            replace_existing=True
        )
        # Register in-flight so DB poll loop won't duplicate it
        self._in_flight.add(msg_id)
        logger.info(f"Scheduled msg_id={msg_id} for {schedule_time} → chat_id={target_id}")

    @staticmethod
    def _parse_schedule_time(value):
        """schedule_time now comes back as a native datetime from psycopg2
        (Postgres TIMESTAMP), but keep string support too in case a caller
        passes an ISO string directly (e.g. from an older cached value)."""
        dt = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return dt

    async def _send_scheduled_message(self, msg_id, target_type, target_id, message_text,
                                       media_type, media_file_id, media_caption, reply_markup):
        # Guard: if somehow called twice, bail out early
        if msg_id not in self._in_flight:
            logger.warning(f"[SCHEDULER] msg_id={msg_id} not in _in_flight — skipping duplicate fire")
            return

        # Mark as no longer in-flight BEFORE sending, so a failure
        # doesn't leave it stuck in the set forever.
        self._in_flight.discard(msg_id)

        # Ensure target_id is int (Supabase may return string)
        try:
            target_id = int(target_id)
        except (TypeError, ValueError):
            pass

        try:
            caption = media_caption or message_text

            if media_type and media_file_id:
                if media_type == 'photo':
                    await self.bot.send_photo(chat_id=target_id, photo=media_file_id,
                                              caption=caption, reply_markup=reply_markup)
                elif media_type == 'video':
                    await self.bot.send_video(chat_id=target_id, video=media_file_id,
                                              caption=caption, reply_markup=reply_markup)
                elif media_type == 'document':
                    await self.bot.send_document(chat_id=target_id, document=media_file_id,
                                                  caption=caption, reply_markup=reply_markup)
                elif media_type == 'audio':
                    await self.bot.send_audio(chat_id=target_id, audio=media_file_id,
                                              caption=caption, reply_markup=reply_markup)
                elif media_type == 'voice':
                    await self.bot.send_voice(chat_id=target_id, voice=media_file_id,
                                              caption=caption, reply_markup=reply_markup)
                elif media_type == 'video_note':
                    await self.bot.send_video_note(chat_id=target_id, video_note=media_file_id)
                elif media_type == 'animation':
                    await self.bot.send_animation(chat_id=target_id, animation=media_file_id,
                                                   caption=caption, reply_markup=reply_markup)
                elif media_type == 'sticker':
                    await self.bot.send_sticker(chat_id=target_id, sticker=media_file_id,
                                                reply_markup=reply_markup)
                elif media_type == 'location':
                    parts = media_file_id.split(',')
                    lat, lng = float(parts[0]), float(parts[1])
                    await self.bot.send_location(chat_id=target_id, latitude=lat, longitude=lng,
                                                  reply_markup=reply_markup)
                elif media_type == 'poll':
                    import json
                    poll_data = json.loads(message_text)
                    await self.bot.send_poll(
                        chat_id=target_id,
                        question=poll_data.get('question', 'Poll'),
                        options=poll_data.get('options', []),
                        is_anonymous=poll_data.get('is_anonymous', True),
                        allows_multiple_answers=poll_data.get('allows_multiple_answers', False)
                    )
                elif media_type == 'contact':
                    parts = media_file_id.split('|')
                    phone = parts[0]
                    first_name = parts[1] if len(parts) > 1 else "Contact"
                    last_name = parts[2] if len(parts) > 2 else ""
                    await self.bot.send_contact(chat_id=target_id, phone_number=phone,
                                                 first_name=first_name, last_name=last_name,
                                                 reply_markup=reply_markup)
            else:
                await self.bot.send_message(chat_id=target_id, text=message_text,
                                             reply_markup=reply_markup, parse_mode="HTML")

            db.update_message_status(msg_id, "sent")
            logger.info(f"[SCHEDULER] msg_id={msg_id} sent successfully to {target_id}")

        except Exception as e:
            error_msg = f"failed: {str(e)}"
            db.update_message_status(msg_id, error_msg)
            logger.error(f"[SCHEDULER ERROR] msg_id={msg_id}: {e}", exc_info=True)

    def remove_scheduled_job(self, msg_id):
        self._in_flight.discard(msg_id)
        try:
            if self._scheduler:
                self._scheduler.remove_job(str(msg_id))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # DB-POLLING SAFETY NET
    # ------------------------------------------------------------------
    async def _db_poll_loop(self):
        """
        Runs forever as a background asyncio task.
        Every DB_POLL_INTERVAL seconds it fetches all pending messages
        from Supabase and fires any that are overdue but not in-flight.

        This is the fix for Railway (and any other host) where:
        - The container gets put to sleep and APScheduler loses its jobs, OR
        - A job was never registered (e.g. scheduled after the last reschedule
          call and before the process was killed).
        """
        logger.info(f"[DB POLL] Safety-net loop started (interval={DB_POLL_INTERVAL}s)")
        while True:
            await asyncio.sleep(DB_POLL_INTERVAL)
            try:
                await self._check_and_fire_overdue()
            except Exception as e:
                logger.error(f"[DB POLL] Unexpected error in poll loop: {e}", exc_info=True)

    async def _check_and_fire_overdue(self):
        now = datetime.utcnow()
        pending = db.get_pending_messages()
        fired = 0

        for msg in pending:
            msg_id = msg["id"]

            # Already being handled by APScheduler
            if msg_id in self._in_flight:
                continue

            try:
                schedule_time = self._parse_schedule_time(msg["schedule_time"])
            except Exception as e:
                logger.error(f"[DB POLL] Bad schedule_time for msg_id={msg_id}: {e}")
                continue

            # Not yet due
            if schedule_time > now:
                # Re-register in APScheduler in case the job was lost
                # (e.g. container resumed from sleep)
                if not (self._scheduler and self._scheduler.get_job(str(msg_id))):
                    self._reschedule_single(msg)
                continue

            # Due but not in-flight → fire it now
            logger.info(f"[DB POLL] Overdue msg_id={msg_id} (was {schedule_time}) — firing now")
            reply_markup = _parse_reply_markup(msg.get("reply_markup_json"))
            self._in_flight.add(msg_id)  # claim it before firing
            asyncio.ensure_future(
                self._send_scheduled_message(
                    msg_id,
                    msg["target_type"],
                    msg["target_id"],
                    msg.get("message_text", ""),
                    msg.get("media_type"),
                    msg.get("media_file_id"),
                    msg.get("media_caption"),
                    reply_markup,
                )
            )
            fired += 1

        if fired:
            logger.info(f"[DB POLL] Fired {fired} overdue message(s)")

    def _reschedule_single(self, msg):
        """Re-add a single pending DB message into APScheduler."""
        msg_id = msg["id"]
        try:
            schedule_time = self._parse_schedule_time(msg["schedule_time"])
        except Exception:
            return

        reply_markup = _parse_reply_markup(msg.get("reply_markup_json"))
        self.schedule_message(
            msg_id,
            msg["target_type"],
            msg["target_id"],
            msg.get("message_text", ""),
            schedule_time,
            media_type=msg.get("media_type"),
            media_file_id=msg.get("media_file_id"),
            media_caption=msg.get("media_caption"),
            reply_markup=reply_markup,
        )

    def start_db_poll_loop(self):
        """Call once from async context (post_init) to launch the background task."""
        asyncio.ensure_future(self._db_poll_loop())
        logger.info("[DB POLL] Background polling task scheduled.")

        # Daily cleanup: delete sent messages older than 24h
        from apscheduler.triggers.interval import IntervalTrigger
        self._get_scheduler().add_job(
            self._cleanup_old_sent,
            trigger=IntervalTrigger(hours=24),
            id="daily_cleanup",
            replace_existing=True
        )
        logger.info("[CLEANUP] Daily sent-message cleanup job scheduled (runs every 24h).")

    async def _cleanup_old_sent(self):
        """Delete sent scheduled messages older than 24 hours from DB."""
        try:
            db.delete_old_sent_messages()
            logger.info("[CLEANUP] Old sent messages deleted from DB.")
        except Exception as e:
            logger.error(f"[CLEANUP] Error during cleanup: {e}", exc_info=True)


# ------------------------------------------------------------------ #
# Module-level helpers                                                 #
# ------------------------------------------------------------------ #

def _parse_reply_markup(raw_markup):
    """Parse reply_markup_json from DB into an InlineKeyboardMarkup, or None."""
    if not raw_markup:
        return None
    try:
        import json as _json
        from telegram import InlineKeyboardMarkup, InlineKeyboardButton
        markup_data = _json.loads(raw_markup)
        keyboard = [
            [InlineKeyboardButton(btn["text"],
                                  callback_data=btn.get("callback_data"),
                                  url=btn.get("url"))
             for btn in row]
            for row in markup_data.get("inline_keyboard", [])
        ]
        return InlineKeyboardMarkup(keyboard)
    except Exception as e:
        logger.warning(f"Could not parse reply_markup_json: {e}")
        return None


scheduler = None


def init_scheduler(bot):
    global scheduler
    scheduler = MessageScheduler(bot)
    return scheduler


async def reschedule_pending_messages():
    """
    Called from post_init (async context, inside PTB's running loop).
    Re-registers any 'pending' DB messages into APScheduler,
    then starts the DB-polling safety-net loop.
    """
    if not scheduler:
        logger.warning("reschedule_pending_messages called before init_scheduler")
        return

    pending = db.get_pending_messages()
    now = datetime.utcnow()
    restored, missed = 0, 0

    for msg in pending:
        try:
            schedule_time = MessageScheduler._parse_schedule_time(msg["schedule_time"])
        except Exception as e:
            logger.error(f"Bad schedule_time for msg_id={msg.get('id')}: {e}")
            continue

        if schedule_time <= now:
            schedule_time = now + timedelta(seconds=5)
            missed += 1

        reply_markup = _parse_reply_markup(msg.get("reply_markup_json"))

        scheduler.schedule_message(
            msg["id"],
            msg["target_type"],
            msg["target_id"],
            msg.get("message_text", ""),
            schedule_time,
            media_type=msg.get("media_type"),
            media_file_id=msg.get("media_file_id"),
            media_caption=msg.get("media_caption"),
            reply_markup=reply_markup,
        )
        restored += 1

    logger.info(
        f"Rescheduled {restored} pending message(s) on startup "
        f"({missed} were past due and will fire shortly)."
    )

    # Start the DB-polling safety-net AFTER restoring existing jobs
    scheduler.start_db_poll_loop()
