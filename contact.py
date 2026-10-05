"""Contact Owner.

User flow
  Main menu -> "📬 Owner se Contact" -> user sends any message (text / photo /
  voice / file ...). It reaches the owner as a small header (name, @username, ID,
  🚫 Block button) followed by the original message.

Owner flow
  Reply to the header or to the copied message -> the reply is delivered to that
  user as "📨 Owner ka jawab" with a "✉️ Jawab do" button, so the user can answer
  back without opening the menu. 🚫 Block / ✅ Unblock sits under every header.

Notes
  * Only the owner receives contact messages (Config.OWNER_ID).
  * The reply mapping (owner message id -> user id) lives in MongoDB, so replies
    still work after a restart. It expires after Config.CONTACT_MAP_TTL_DAYS.
  * Contact block is separate from the global ban in database.is_user_banned.
  * Per-user rate limit is in memory (resets on restart), by design.

This module deliberately does not import handlers.py (handlers imports it).
"""
import html
import logging
import time
from collections import defaultdict, deque

from telegram import Update
from telegram.error import Forbidden, TelegramError
from telegram.ext import ContextTypes

from config import Config
from database import db
from keyboards import (
    get_main_menu, get_contact_prompt_keyboard,
    get_contact_reply_keyboard, get_contact_block_keyboard,
)

logger = logging.getLogger(__name__)

CB_PREFIX = "ct_"
STATE_MSG = "contact_msg"

_REPLY_HEADER = "📨 Owner ka jawab:"
_PROMPT = (
    "📬 **Owner se Contact**\n\n"
    "Apna message yahan bhejein (text, photo, voice, file - kuch bhi). "
    "Mai use owner tak pahuncha dunga, jawab yahin milega.\n\n"
    "Khatam karne ke liye neeche wala button dabayein ya /menu."
)
# Types where copy_message may replace the caption.
_CAPTION_MEDIA = ("photo", "video", "audio", "document", "animation", "voice")

_hits: dict[int, deque] = defaultdict(deque)


def is_owner(user_id):
    return user_id == Config.OWNER_ID


def _rate_limited(user_id):
    now = time.monotonic()
    q = _hits[user_id]
    while q and now - q[0] > Config.CONTACT_RATE_WINDOW:
        q.popleft()
    if len(q) >= Config.CONTACT_RATE_LIMIT:
        return True
    q.append(now)
    return False


def _header(user):
    name = html.escape(" ".join(filter(None, [user.first_name, user.last_name])) or "User")
    uname = f"@{html.escape(user.username)}" if user.username else "(username nahi hai)"
    return (
        f"📩 <a href=\"tg://user?id={user.id}\">{name}</a> {uname}\n"
        f"ID: <code>{user.id}</code>\n"
        "Jawab dene ke liye is message ya neeche wale message par reply karo."
    )


# ------------------------------------------------------------ callbacks

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    """ct_open / ct_reply / ct_stop (any user), ct_blk_<id> / ct_ubk_<id> (owner).
    The query was already answered upstream in handlers.callback_handler."""
    query = update.callback_query
    user = query.from_user
    cmd, _, arg = data[len(CB_PREFIX):].partition("_")

    if cmd in ("open", "reply"):
        if is_owner(user.id):
            await query.message.reply_text(
                "Tum owner ho. Users ke message par reply karke unhe jawab do.",
                reply_markup=get_main_menu(user.id),
            )
            return
        if db.is_contact_blocked(user.id):
            await query.message.reply_text("🚫 Aap is feature se block hain.")
            return
        context.user_data["state"] = STATE_MSG
        if cmd == "open":
            await query.edit_message_text(
                _PROMPT, reply_markup=get_contact_prompt_keyboard(show_clone=True),
                parse_mode="Markdown",
            )
        else:
            # Button sits under the owner's reply, so send a NEW message
            # instead of editing (editing would overwrite the reply itself).
            await query.message.reply_text(
                _PROMPT, reply_markup=get_contact_prompt_keyboard(), parse_mode="Markdown"
            )

    elif cmd == "stop":
        # also ends the clone-token prompt ("clone_token", see clone.py)
        if str(context.user_data.get("state")).startswith(("contact_", "clone_")):
            context.user_data["state"] = None
        await query.edit_message_text(
            "**Main Menu**\n\nOption choose karein:",
            reply_markup=get_main_menu(user.id),
            parse_mode="Markdown",
        )

    elif cmd in ("blk", "ubk"):
        if not is_owner(user.id):
            return
        try:
            target = int(arg)
        except ValueError:
            return
        blocked = cmd == "blk"
        db.set_contact_blocked(target, blocked)
        try:
            await query.edit_message_reply_markup(
                reply_markup=get_contact_block_keyboard(target, blocked)
            )
        except TelegramError:
            pass  # markup unchanged / message too old


# ------------------------------------------------------------ messages

async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Called from message_handler while user_data['state'] == STATE_MSG.
    Returns True if the message was consumed."""
    msg = update.message
    user = update.effective_user

    if update.effective_chat.type != "private":
        return False
    if is_owner(user.id):
        context.user_data["state"] = None
        return False
    if db.is_contact_blocked(user.id):
        context.user_data["state"] = None
        await msg.reply_text("🚫 Aap is feature se block hain.")
        return True
    if _rate_limited(user.id):
        await msg.reply_text("⏳ Thoda dheere, bahut jaldi jaldi message aa rahe hain.")
        return True

    try:
        header = await context.bot.send_message(
            Config.OWNER_ID,
            _header(user),
            parse_mode="HTML",
            reply_markup=get_contact_block_keyboard(user.id, False),
        )
        copied = await context.bot.copy_message(
            chat_id=Config.OWNER_ID,
            from_chat_id=msg.chat_id,
            message_id=msg.message_id,
            reply_to_message_id=header.message_id,
        )
    except TelegramError:
        logger.exception("Owner ko contact message nahi gaya (user %s)", user.id)
        await msg.reply_text("❌ Abhi owner tak message nahi pahunch paya, thodi der baad try karein.")
        return True

    db.save_contact_map(header.message_id, user.id)
    db.save_contact_map(copied.message_id, user.id)
    await msg.reply_text(
        "✅ Message owner tak pahunch gaya. Aur kuch bhejna ho to bhejte rahein.",
        reply_markup=get_contact_prompt_keyboard(),
    )
    return True


async def handle_owner_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Called at the top of message_handler. Returns True only if this message is
    the owner replying to a forwarded contact message; otherwise False, so every
    other flow (including the owner's own states) runs untouched."""
    msg = update.message
    user = update.effective_user
    if (not msg or not msg.reply_to_message or not is_owner(user.id)
            or update.effective_chat.type != "private"):
        return False

    user_id = db.get_contact_user(msg.reply_to_message.message_id)
    if user_id is None:
        return False

    kb = get_contact_reply_keyboard()
    try:
        if msg.text:
            await context.bot.send_message(user_id, f"{_REPLY_HEADER}\n\n{msg.text}", reply_markup=kb)
        elif any(getattr(msg, a) for a in _CAPTION_MEDIA):
            caption = f"{_REPLY_HEADER}\n\n{msg.caption}" if msg.caption else _REPLY_HEADER
            await context.bot.copy_message(
                chat_id=user_id, from_chat_id=msg.chat_id, message_id=msg.message_id,
                caption=caption[:1024], reply_markup=kb,
            )
        else:  # sticker, video note, location, contact ... cannot carry a caption
            await context.bot.send_message(user_id, _REPLY_HEADER)
            await context.bot.copy_message(
                chat_id=user_id, from_chat_id=msg.chat_id, message_id=msg.message_id,
                reply_markup=kb,
            )
    except Forbidden:
        await msg.reply_text("❌ User ne bot block kar diya hai.")
    except TelegramError as exc:
        logger.exception("User %s ko reply nahi gaya", user_id)
        await msg.reply_text(f"❌ Bhejne mein error: {exc}")
    else:
        await msg.reply_text("✅ Bhej diya.")
    return True
