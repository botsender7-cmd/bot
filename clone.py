"""Contact-bot clones.

A user opens  📬 Owner se Contact -> 🤖 Apna Contact Bot Banao, creates a bot in
@BotFather and pastes its token here. From then on that bot works as the user's
own contact bot: anyone who messages it reaches that user, and the user's replies
go back (same behaviour as contact.py, but with the clone's creator as owner).

How it runs
  * Every clone is a separate python-telegram-bot Application running in POLLING
    mode inside this same process / event loop (started from bot.py post_init).
  * Tokens are Fernet-encrypted in MongoDB (`clones`). Needs env ENCRYPTION_KEY;
    without it the feature stays off and nothing else is affected.
  * One clone per user, at most Config.MAX_CLONES in total.
  * The bot owner (Config.OWNER_ID) can list / remove clones: Owner Panel -> 🤖 Contact Clones.

Auto-delete (per clone, set by the clone's owner inside the clone bot)
  * /autodelete          -> buttons (30s ... 48h / Off), or /autodelete 45m, 2h, 90s, off
  * Once ON, every message the clone handles is deleted after that time, in BOTH chats
    (owner's chat and the user's chat) and its reply mapping is dropped from Mongo.
  * The queue lives in Mongo (`clone_autodel`) and one sweeper task works through it, so
    pending deletions survive restarts. Telegram limit: only messages < 48h old can be deleted.
  * Delete for both sides: Telegram does NOT tell bots when a person deletes a message in a
    private chat, so manual deletion cannot be detected. Instead: the "🗑 Dono taraf delete"
    button under "✅ Bhej diya", or /del as a reply to the sent message.

Things worth knowing
  * Clones only receive updates while this process is awake. On a host that
    sleeps when idle (Render free tier) they sleep too.
  * Anyone with the server env AND the database can decrypt every token. The user
    is told this before connecting.
  * Never log exceptions from code that handles tokens: request URLs contain them.

This module deliberately does not import handlers.py (handlers imports it).
"""
import asyncio
import html
import logging
import re
import time
from collections import defaultdict, deque

from cryptography.fernet import Fernet
from pymongo.errors import DuplicateKeyError
from telegram import (Bot, BotCommand, BotCommandScopeChat, InlineKeyboardButton,
                      InlineKeyboardMarkup, Update)
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import contact
from config import Config
from database import db
from keyboards import get_main_menu

logger = logging.getLogger(__name__)

CB_PREFIX = "cl_"
STATE_TOKEN = "clone_token"
TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")

running: dict[int, Application] = {}
_hits: dict[tuple[int, int], deque] = defaultdict(deque)
_fernet_obj = None
_sweeper_task = None
_UNIT = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_PRESETS = [("30s", 30), ("1m", 60), ("5m", 300), ("15m", 900),
            ("1h", 3600), ("6h", 21600), ("24h", 86400), ("48h", 172800)]


def is_owner(user_id):
    return user_id == Config.OWNER_ID


# ---------------------------------------------------------------- encryption

def _fernet():
    global _fernet_obj
    if _fernet_obj is None and Config.ENCRYPTION_KEY:
        try:
            _fernet_obj = Fernet(Config.ENCRYPTION_KEY.encode())
        except Exception:
            logger.error("ENCRYPTION_KEY valid Fernet key nahi hai - clones band rahenge.")
    return _fernet_obj


def enabled():
    return _fernet() is not None


def _encrypt(token):
    return _fernet().encrypt(token.encode()).decode()


def _decrypt(token_enc):
    return _fernet().decrypt(token_enc.encode()).decode()


def _rate_limited(bot_id, user_id):
    now = time.monotonic()
    q = _hits[(bot_id, user_id)]
    while q and now - q[0] > Config.CONTACT_RATE_WINDOW:
        q.popleft()
    if len(q) >= Config.CONTACT_RATE_LIMIT:
        return True
    q.append(now)
    return False


# ---------------------------------------------------------------- keyboards

def _cancel_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="ct_stop")]])


def _back_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Wapas", callback_data="ct_open")]])


def _own_clone_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑️ Clone hatao", callback_data="cl_off")],
        [InlineKeyboardButton("🔙 Wapas", callback_data="ct_open")],
    ])


def _confirm_off_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Haan, hata do", callback_data="cl_offy")],
        [InlineKeyboardButton("🔙 Nahi", callback_data="cl_menu")],
    ])


def _del_kb(owner_msg_id):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🗑 Dono taraf delete", callback_data=f"cd_{owner_msg_id}")
    ]])


def _autodel_kb(current):
    rows, row = [], []
    for label, secs in _PRESETS:
        row.append(InlineKeyboardButton(("✅ " if secs == current else "") + label,
                                        callback_data=f"ca_{secs}"))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(("✅ " if not current else "") + "🚫 Off",
                                      callback_data="ca_0")])
    return InlineKeyboardMarkup(rows)


def _block_kb(user_id, blocked):
    if blocked:
        btn = InlineKeyboardButton("✅ Unblock user", callback_data=f"cb_ubk_{user_id}")
    else:
        btn = InlineKeyboardButton("🚫 Block user", callback_data=f"cb_blk_{user_id}")
    return InlineKeyboardMarkup([[btn]])


# ---------------------------------------------------------------- user-facing menu (main bot)

_INTRO = (
    "🤖 <b>Apna Contact Bot banao</b>\n\n"
    "1. @BotFather kholo, /newbot chalao aur naya bot banao.\n"
    "2. Wo jo token de (jaise <code>123456:ABC...</code>) use <b>yahan bhejo</b>.\n"
    "3. Apne naye bot ko kholkar ek baar <b>Start</b> dabao, warna wo aapko "
    "message nahi bhej paayega.\n\n"
    "Phir koi bhi us bot ko message kare to aapko wahin milega, aur aap reply karo "
    "to unhe pahunch jaayega.\n\n"
    "⚠️ Token bot ka password hai. Mai use encrypt karke rakhta hoon aur bhejte hi "
    "aapka message delete kar deta hoon, lekin is bot ke owner/hoster ko technically "
    "access ho sakta hai. Bharosa na ho to token mat bhejo. 'Clone hatao' se aap "
    "kabhi bhi saved token delete kar sakte ho."
)


async def _show_menu(query, context, user):
    if not enabled():
        await query.edit_message_text(
            "🤖 Ye feature abhi chalu nahi hai (setup poora nahi hua).",
            reply_markup=_back_kb(),
        )
        return

    row = db.get_clone_by_owner(user.id)
    if row:
        context.user_data["state"] = None
        status = "chal raha hai ✅" if row["bot_id"] in running else "abhi band hai ❌"
        ad = row.get("autodel_seconds") or 0
        ad_line = (f"⏱ Auto-delete: ON ({fmt_duration(ad)})" if ad else "⏱ Auto-delete: OFF")
        await query.edit_message_text(
            f"🤖 <b>Aapka clone</b>\n\n@{html.escape(row['username'] or '?')} - {status}\n"
            f"{ad_line} - clone bot me /autodelete se badlo\n\n"
            "Jab koi us bot ko message karega, wo aapko us bot ki chat mein milega. "
            "Reply karne ke liye us message par reply karo.",
            reply_markup=_own_clone_kb(),
            parse_mode="HTML",
        )
        return

    context.user_data["state"] = STATE_TOKEN
    await query.edit_message_text(_INTRO, reply_markup=_cancel_kb(), parse_mode="HTML")


async def _remove_clone(bot_id):
    await stop_child(bot_id)
    db.delete_clone(bot_id)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    """cl_menu / cl_off / cl_offy (any user, own clone only);
    cl_list / cl_kill_<bot_id> (bot owner only). Query already answered upstream."""
    query = update.callback_query
    user = query.from_user
    cmd, _, arg = data[len(CB_PREFIX):].partition("_")

    if cmd == "menu":
        await _show_menu(query, context, user)

    elif cmd == "off":
        if db.get_clone_by_owner(user.id):
            await query.edit_message_text(
                "Clone hata dun? Bot band ho jaayega aur saved token + data delete ho jaayega.",
                reply_markup=_confirm_off_kb(),
            )

    elif cmd == "offy":
        row = db.get_clone_by_owner(user.id)
        if not row:
            return
        await _remove_clone(row["bot_id"])
        await query.edit_message_text(
            f"🗑️ @{row['username']} hata diya aur saved token delete kar diya.\n"
            "Pakka karne ke liye BotFather mein /revoke se token bhi badal do.",
            reply_markup=get_main_menu(user.id),
        )

    elif cmd == "list":
        if is_owner(user.id):
            context.user_data["state"] = None
            await _show_list(query)

    elif cmd == "add":
        if not is_owner(user.id):
            return
        if not enabled():
            await query.edit_message_text(
                "🤖 Clone feature chalu nahi hai (ENCRYPTION_KEY set nahi hai).",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🔙 Back", callback_data="cl_list")]]),
            )
            return
        if db.get_clone_by_owner(user.id):
            await query.edit_message_text(
                "ℹ️ Aapka apna clone pehle se bana hua hai (ek user = ek clone). "
                "Naya add karne ke liye pehle use list se hata do.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🔙 Back", callback_data="cl_list")]]),
            )
            return
        if db.count_clones() >= Config.MAX_CLONES:
            await query.edit_message_text(
                f"❌ Limit poori ho gayi ({Config.MAX_CLONES}). MAX_CLONES env badhao.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🔙 Back", callback_data="cl_list")]]),
            )
            return
        context.user_data["state"] = STATE_TOKEN
        await query.edit_message_text(
            "➕ <b>Clone add karo</b>\n\n"
            "@BotFather se naye bot ka token lo aur <b>yahan bhejo</b>. "
            "Bot aapka (owner ka) contact bot ban jaayega. "
            "Phir us bot me ek baar <b>Start</b> dabana mat bhoolna.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("❌ Cancel", callback_data="cl_list")]]),
            parse_mode="HTML",
        )

    elif cmd == "kill":
        if not is_owner(user.id):
            return
        try:
            bot_id = int(arg)
        except ValueError:
            return
        row = db.get_clone(bot_id)
        if row:
            await _remove_clone(bot_id)
            db.add_audit_log("clone_removed", user.id, row["owner_id"], f"@{row['username']}")
            try:
                await context.bot.send_message(
                    row["owner_id"],
                    f"ℹ️ Aapka clone @{row['username']} owner ne hata diya hai.",
                )
            except TelegramError:
                pass
        await _show_list(query)


async def _show_list(query):
    rows = db.list_clones()
    add_btn = [InlineKeyboardButton("➕ Clone Add Karo", callback_data="cl_add")]
    if not rows:
        await query.edit_message_text(
            "🤖 Abhi koi clone nahi hai.",
            reply_markup=InlineKeyboardMarkup(
                [add_btn, [InlineKeyboardButton("🔙 Back", callback_data="owner_panel")]]
            ),
        )
        return
    lines, buttons = [f"🤖 <b>Clones ({len(rows)}/{Config.MAX_CLONES})</b>\n"], [add_btn]
    for r in rows:
        state = "✅" if r["bot_id"] in running else "❌"
        lines.append(f"{state} @{html.escape(r['username'] or '?')} - owner <code>{r['owner_id']}</code>")
        buttons.append([InlineKeyboardButton(
            f"🗑️ @{r['username']} hata do", callback_data=f"cl_kill_{r['bot_id']}")])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="owner_panel")])
    await query.edit_message_text(
        "\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML"
    )


async def handle_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Called from message_handler while user_data['state'] == STATE_TOKEN.
    Returns True if the message was consumed."""
    msg = update.message
    user = update.effective_user
    if update.effective_chat.type != "private":
        return False

    text = (msg.text or "").strip()
    if not TOKEN_RE.match(text):
        await msg.reply_text(
            "❌ Ye BotFather ke token jaisa nahi lag raha (<code>123456:ABC...</code>). "
            "Dobara bhejo ya Cancel dabao.",
            reply_markup=_cancel_kb(), parse_mode="HTML",
        )
        return True

    chat_id = msg.chat_id
    try:  # token chat history mein na rahe
        await msg.delete()
    except TelegramError:
        pass
    context.user_data["state"] = None

    async def say(t, **kw):
        await context.bot.send_message(chat_id, t, **kw)

    if not enabled():
        await say("🤖 Ye feature abhi chalu nahi hai.")
        return True
    if db.get_clone_by_owner(user.id):
        await say("Aapka clone pehle se bana hua hai. Naya banane ke liye pehle use hatao.")
        return True
    if db.count_clones() >= Config.MAX_CLONES:
        await say("❌ Abhi naye clones ki limit poori ho gayi hai.")
        return True
    if text == Config.BOT_TOKEN:
        await say("❌ Ye is bot ka hi token hai. Apna naya bot banao.")
        return True

    try:
        async with Bot(text) as probe:
            info = await probe.get_me()
    except TelegramError:
        await say("❌ Ye token valid nahi hai. BotFather se dobara copy karke bhejo.")
        return True
    if info.id == context.bot.id:
        await say("❌ Ye is bot ka hi token hai. Apna naya bot banao.")
        return True

    try:
        db.add_clone(info.id, user.id, info.username, _encrypt(text))
    except DuplicateKeyError:
        await say("❌ Ye bot pehle se registered hai.")
        return True

    try:
        await start_child(text, user.id)
    except Exception as exc:  # never log exc itself: it can contain the token
        logger.error("Clone %s start nahi hua: %s", info.id, type(exc).__name__)
        db.delete_clone(info.id)
        await say("❌ Bot start nahi ho paya. Token check karke dobara try karo.")
        return True

    await say(
        f"✅ @{info.username} connect ho gaya!\n\n"
        f"Ab zaroori kaam: https://t.me/{info.username} kholo aur ek baar Start dabao. "
        "Uske baad jo bhi us bot ko message karega, wo aapko usi chat mein milega. "
        "Jawab dene ke liye message par reply karo.",
        disable_web_page_preview=True,
    )
    try:
        await context.bot.send_message(
            Config.OWNER_ID, f"🤖 Naya clone: @{info.username} (owner ID {user.id})"
        )
    except TelegramError:
        pass
    return True


# ---------------------------------------------------------------- auto-delete

def parse_duration(text):
    """'30s' / '15m' / '2h' / '1d' (bare number = minutes) -> seconds; 'off'/'0' -> 0;
    anything else -> None."""
    text = (text or "").strip().lower()
    if text in ("off", "0", "band", "no"):
        return 0
    m = re.fullmatch(r"(\d{1,6})\s*([smhd]?)", text)
    if not m:
        return None
    return int(m.group(1)) * _UNIT[m.group(2) or "m"]


def fmt_duration(secs):
    if secs % 86400 == 0:
        return f"{secs // 86400} din"
    if secs % 3600 == 0:
        return f"{secs // 3600} ghanta"
    if secs % 60 == 0:
        return f"{secs // 60} minute"
    return f"{secs} second"


def _valid_secs(secs):
    return secs == 0 or Config.CLONE_AUTODEL_MIN_SECONDS <= secs <= Config.CLONE_AUTODEL_MAX_SECONDS


def _track(context, chat_id, message_id, owner_chat=False):
    """Queue a message for deletion if this clone's auto-delete is ON."""
    secs = context.application.bot_data.get("autodel", 0)
    if secs and message_id:
        db.queue_clone_autodel(context.bot.id, chat_id, message_id, secs, owner_chat)


def _apply_autodel(context, secs):
    bot_id = context.bot.id
    db.set_clone_autodel(bot_id, secs)
    context.application.bot_data["autodel"] = secs
    if not secs:
        db.clear_clone_autodel(bot_id)  # Off = stop pending deletions too


def _autodel_text(secs):
    if secs:
        return (f"⏱ Auto-delete <b>ON</b>: har message {fmt_duration(secs)} baad delete hoga - "
                "aapki chat se bhi aur user ki chat se bhi (user ke bheje + bot ke bheje dono).\n\n"
                "Badalne ke liye neeche chuno ya likho: <code>/autodelete 45m</code> "
                "(s = second, m = minute, h = ghanta, d = din).")
    return ("⏱ Auto-delete <b>OFF</b>.\n\nChalu karne ke liye neeche time chuno ya likho: "
            "<code>/autodelete 45m</code> (s = second, m = minute, h = ghanta; max 48h).")


async def _sweep_once():
    for d in db.due_clone_autodel():
        app = running.get(d["bot_id"])
        if app is None:
            if db.get_clone(d["bot_id"]) is None:  # clone removed -> nothing to do
                db.drop_clone_autodel(d["_id"])
            continue  # clone exists but is down: retry when it is back
        try:
            await app.bot.delete_message(d["chat_id"], d["message_id"])
        except RetryAfter as exc:
            await asyncio.sleep(min(float(exc.retry_after), 30))
            return
        except (BadRequest, Forbidden):
            pass  # already deleted / too old / user blocked: retrying can never succeed
            # (BadRequest is a NetworkError subclass in PTB, so it must be caught BEFORE it)
        except NetworkError:
            continue  # timeout / connection problem: retried next sweep
        except TelegramError:
            pass
        if d.get("owner_chat"):
            db.delete_clone_map(d["bot_id"], d["message_id"])
        db.drop_clone_autodel(d["_id"])


async def _sweeper():
    while True:
        try:
            await _sweep_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # never log exc itself: it can contain the token
            logger.warning("Auto-delete sweep error: %s", type(exc).__name__)
        await asyncio.sleep(Config.CLONE_AUTODEL_SWEEP_SECONDS)


async def c_autodelete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if context.args:
        secs = parse_duration(" ".join(context.args))
        if secs is None or not _valid_secs(secs):
            await msg.reply_text(
                f"❌ Time {Config.CLONE_AUTODEL_MIN_SECONDS} second se 48 ghante ke beech chahiye. "
                "Jaise: /autodelete 30s, /autodelete 15m, /autodelete 2h, /autodelete off"
            )
            return
        _apply_autodel(context, secs)
    cur = context.application.bot_data.get("autodel", 0)
    await msg.reply_text(_autodel_text(cur), reply_markup=_autodel_kb(cur), parse_mode="HTML")


async def c_autodel_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if query.from_user.id != context.application.bot_data["owner_id"]:
        return
    try:
        secs = int(query.data.split("_")[1])
    except (IndexError, ValueError):
        return
    if not _valid_secs(secs):
        return
    _apply_autodel(context, secs)
    try:
        await query.edit_message_text(_autodel_text(secs), reply_markup=_autodel_kb(secs),
                                      parse_mode="HTML")
    except TelegramError:
        pass  # same content as before (user tapped the active option)


# ---------------------------------------------------------------- delete for both sides

async def _delete_sent(context, doc, extra_ids=()) -> bool:
    """Delete the owner's reply from the USER's chat; only if that worked, also remove it
    (and the '✅ Bhej diya' note) from the owner's chat. Returns False if the user's copy
    could not be deleted (older than 48h, or already gone)."""
    bot = context.bot
    owner_id = context.application.bot_data["owner_id"]
    try:
        await bot.delete_message(doc["user_id"], doc["user_msg_id"])
    except TelegramError:
        return False
    for mid in (doc["owner_msg_id"], doc["confirm_msg_id"], *extra_ids):
        try:
            await bot.delete_message(owner_id, mid)
        except TelegramError:
            pass
    db.delete_clone_sent(bot.id, doc["owner_msg_id"])
    return True


async def c_del_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query.from_user.id != context.application.bot_data["owner_id"]:
        await query.answer()
        return
    try:
        owner_msg_id = int(query.data.split("_")[1])
    except (IndexError, ValueError):
        await query.answer()
        return
    doc = db.find_clone_sent(context.bot.id, owner_msg_id)
    if not doc:
        await query.answer("Ye ab delete nahi ho sakta (purana ho gaya).", show_alert=True)
        return
    if await _delete_sent(context, doc):
        await query.answer("🗑 Dono taraf se delete ho gaya.")
    else:
        await query.answer(
            "❌ User ki chat se delete nahi ho paya (48 ghante se purana ya pehle hi delete).",
            show_alert=True,
        )


async def c_del(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg.reply_to_message:
        await msg.reply_text("Jo jawab bheja tha us par reply karke /del likho.")
        return
    doc = db.find_clone_sent(context.bot.id, msg.reply_to_message.message_id)
    if not doc:
        await msg.reply_text(
            "Ye mere bheje hue jawab se match nahi hua (ya bahut purana hai). "
            "Apne jawab par ya uske neeche wale '✅ Bhej diya' par reply karo."
        )
        return
    if not await _delete_sent(context, doc, extra_ids=(msg.message_id,)):
        await msg.reply_text(
            "❌ User ki chat se delete nahi ho paya (48 ghante se purana ya pehle hi delete)."
        )


# ---------------------------------------------------------------- clone bot handlers

async def c_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    owner_id = context.application.bot_data["owner_id"]
    if update.effective_user.id == owner_id:
        await update.effective_message.reply_text(
            "👋 Owner mode ON. Jab koi is bot ko message karega, wo yahin aayega. "
            "Jawab dene ke liye us message par reply karo. "
            "/ban aur /unban bhi user ke message par reply karke chalte hain.\n\n"
            "⏱ /autodelete - messages ka auto-delete timer\n"
            "🗑 /del - bheje hue jawab par reply karo, user ki chat se bhi delete ho jaayega"
        )
        return
    secs = context.application.bot_data.get("autodel", 0)
    note = (f"\n\n⏱ Is chat ke messages {fmt_duration(secs)} baad apne aap delete ho jaate hain."
            if secs else "")
    await update.effective_message.reply_text(
        "Namaste! Apna message yahan likhiye (text, photo, voice, file - kuch bhi). "
        "Mai use seedha owner tak pahuncha dunga, aur jawab yahin milega." + note
    )


async def c_from_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    user = update.effective_user
    bot_id = context.bot.id
    owner_id = context.application.bot_data["owner_id"]

    if db.is_clone_blocked(bot_id, user.id):
        return
    if _rate_limited(bot_id, user.id):
        await msg.reply_text("⏳ Thoda dheere, bahut jaldi jaldi message aa rahe hain.")
        return

    try:
        header = await context.bot.send_message(
            owner_id, contact._header(user), parse_mode="HTML",
            reply_markup=_block_kb(user.id, False),
        )
        copied = await context.bot.copy_message(
            chat_id=owner_id, from_chat_id=msg.chat_id, message_id=msg.message_id,
            reply_to_message_id=header.message_id,
        )
    except TelegramError as exc:
        logger.error("Clone %s: owner ko message nahi gaya: %s", bot_id, type(exc).__name__)
        await msg.reply_text("Abhi message nahi pahunch paya, thodi der baad try karein.")
        return

    db.save_clone_map(bot_id, header.message_id, user.id)
    db.save_clone_map(bot_id, copied.message_id, user.id)
    ack = await msg.reply_text("✅ Aapka message owner tak pahunch gaya hai.")
    # auto-delete (no-op when OFF): owner side (+ map cleanup) and user side
    _track(context, owner_id, header.message_id, owner_chat=True)
    _track(context, owner_id, copied.message_id, owner_chat=True)
    _track(context, msg.chat_id, msg.message_id)
    _track(context, msg.chat_id, ack.message_id)


async def c_from_owner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    bot_id = context.bot.id
    user_id = db.get_clone_user(bot_id, msg.reply_to_message.message_id)
    if user_id is None:
        await msg.reply_text(
            "Is message se user nahi mila. User ke bheje hue message par reply karo."
        )
        return
    try:
        sent = await context.bot.copy_message(
            chat_id=user_id, from_chat_id=msg.chat_id, message_id=msg.message_id
        )
    except Forbidden:
        await msg.reply_text("❌ User ne bot block kar diya hai.")
        return
    except TelegramError as exc:
        logger.error("Clone %s: user ko reply nahi gaya: %s", bot_id, type(exc).__name__)
        await msg.reply_text("❌ Bhejne mein error aaya.")
        return
    confirm = await msg.reply_text("✅ Bhej diya.", reply_markup=_del_kb(msg.message_id))
    db.save_clone_sent(bot_id, msg.message_id, confirm.message_id, user_id, sent.message_id)
    _track(context, user_id, sent.message_id)
    _track(context, msg.chat_id, msg.message_id)
    _track(context, msg.chat_id, confirm.message_id)


async def c_owner_hint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "Kisi user ko jawab dene ke liye uske message par reply karo."
    )


async def _ban_toggle(update, context, banned):
    msg = update.effective_message
    bot_id = context.bot.id
    if not msg.reply_to_message:
        await msg.reply_text("User ke message par reply karke command chalao.")
        return
    user_id = db.get_clone_user(bot_id, msg.reply_to_message.message_id)
    if user_id is None:
        await msg.reply_text("Is message se user nahi mila.")
        return
    db.set_clone_blocked(bot_id, user_id, banned)
    await msg.reply_text(f"{'🚫 Ban' if banned else '✅ Unban'} kar diya: {user_id}")


async def c_ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _ban_toggle(update, context, True)


async def c_unban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _ban_toggle(update, context, False)


async def c_block_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    owner_id = context.application.bot_data["owner_id"]
    if query.from_user.id != owner_id:
        return
    _, action, uid = query.data.split("_")  # cb_blk_<id> / cb_ubk_<id>
    blocked = action == "blk"
    db.set_clone_blocked(context.bot.id, int(uid), blocked)
    try:
        await query.edit_message_reply_markup(reply_markup=_block_kb(int(uid), blocked))
    except TelegramError:
        pass


async def c_error(update, context) -> None:
    logger.warning("Clone handler error: %s", type(context.error).__name__)


# ---------------------------------------------------------------- lifecycle

def build_child_app(token, owner_id):
    app = Application.builder().token(token).build()
    app.bot_data["owner_id"] = owner_id
    row = db.get_clone(int(token.split(":")[0]))
    app.bot_data["autodel"] = (row or {}).get("autodel_seconds", 0)

    owner = filters.User(user_id=owner_id)
    private = filters.ChatType.PRIVATE

    app.add_handler(CommandHandler("start", c_start, filters=private))
    app.add_handler(CommandHandler("ban", c_ban, filters=owner))
    app.add_handler(CommandHandler("unban", c_unban, filters=owner))
    app.add_handler(CommandHandler("autodelete", c_autodelete, filters=owner & private))
    app.add_handler(CommandHandler("del", c_del, filters=owner & private))
    app.add_handler(CallbackQueryHandler(c_block_button, pattern=r"^cb_(blk|ubk)_\d+$"))
    app.add_handler(CallbackQueryHandler(c_autodel_button, pattern=r"^ca_\d+$"))
    app.add_handler(CallbackQueryHandler(c_del_button, pattern=r"^cd_\d+$"))
    app.add_handler(
        MessageHandler(owner & private & filters.REPLY & ~filters.COMMAND, c_from_owner)
    )
    app.add_handler(
        MessageHandler(owner & private & ~filters.REPLY & ~filters.COMMAND, c_owner_hint)
    )
    app.add_handler(MessageHandler(private & ~owner & ~filters.COMMAND, c_from_user))
    app.add_error_handler(c_error)
    return app


async def _teardown(app):
    """Best-effort stop; each step may legitimately fail if it never started."""
    for step in (
        lambda: app.updater.stop(),
        lambda: app.stop(),
        lambda: app.shutdown(),
    ):
        try:
            await step()
        except Exception:
            pass


async def start_child(token, owner_id):
    app = build_child_app(token, owner_id)
    await app.initialize()
    try:
        await app.start()
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    except Exception:
        await _teardown(app)
        raise
    running[app.bot.id] = app
    try:  # show the new commands in the owner's "/" menu (needs owner to have pressed Start)
        await app.bot.set_my_commands(
            [BotCommand("autodelete", "Auto-delete timer"),
             BotCommand("del", "Reply to a sent message: delete for user too")],
            scope=BotCommandScopeChat(owner_id),
        )
    except TelegramError:
        pass
    return app


async def stop_child(bot_id):
    app = running.pop(bot_id, None)
    if app is not None:
        await _teardown(app)


async def start_all():
    """Called from bot.py post_init: bring every saved clone back up."""
    global _sweeper_task
    if not enabled():
        return
    if _sweeper_task is None or _sweeper_task.done():
        _sweeper_task = asyncio.create_task(_sweeper())
    for row in db.list_clones():
        try:
            await start_child(_decrypt(row["token_enc"]), row["owner_id"])
        except Exception as exc:  # never log exc itself: it can contain the token
            logger.error("Clone %s start nahi hua: %s", row["bot_id"], type(exc).__name__)
    logger.info("Clones active: %d", len(running))


async def stop_all():
    global _sweeper_task
    if _sweeper_task is not None:
        _sweeper_task.cancel()
        _sweeper_task = None
    for bot_id in list(running):
        await stop_child(bot_id)
