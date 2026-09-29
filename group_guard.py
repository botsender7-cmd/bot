"""Leave-Ban Guard.

Groups only (never channels). Opt-in per group via /leaveban on.

Setup: the person who runs /leaveban on is ASKED which @username to show in
the join warning (reply to the bot's prompt). The feature is switched on only
after a valid username is given.

- User joins  -> short warning (auto-deleted) with that clickable @username.
- User leaves -> auto-ban, but ONLY when they left by themselves. An admin
  kick shows up as LEFT with from_user=admin, so it is ignored.
- Bot owner + bot admins + the person who enabled it get a DM with an Unban
  button; /unban <user_id> also works inside the group.

Requirements: bot must be a group admin with "Ban users" permission.
"""
import asyncio
import html
import logging
import re
import time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ForceReply
from telegram.constants import ChatType, ChatMemberStatus, ParseMode
from telegram.ext import ContextTypes, ApplicationHandlerStop

from config import Config
from database import db
from handlers import is_admin

logger = logging.getLogger(__name__)

GROUP_TYPES = (ChatType.GROUP, ChatType.SUPERGROUP)
CB_PREFIX = "lbunban:"
PENDING_TTL = 300  # seconds the setter has to reply with a username
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{4,32}$")


def _is_member(cmu) -> bool:
    """True if this ChatMember state means 'currently inside the group'."""
    if cmu.status == ChatMemberStatus.MEMBER:
        return True
    # A restricted user can be either still inside or already gone.
    return cmu.status == ChatMemberStatus.RESTRICTED and bool(getattr(cmu, "is_member", False))


async def _can_manage(user_id: int, chat_id: int, bot) -> bool:
    if is_admin(user_id):  # bot owner or bot admin
        return True
    try:
        m = await bot.get_chat_member(chat_id, user_id)
        return m.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except Exception:
        return False


def _parse_username(text: str):
    """'@name', 'name' or 't.me/name' -> 'name' (or None if invalid)."""
    t = re.sub(r"^(https?://)?(www\.)?t\.me/", "", (text or "").strip(), flags=re.I)
    t = t.split("?")[0].strip("/ ").lstrip("@")
    return t if _USERNAME_RE.match(t) else None


def _env_contact_line() -> str:
    """Fallback only: groups enabled before the ask-for-username flow existed."""
    names = Config.LEAVEBAN_CONTACTS or ([Config.OWNER_USERNAME] if Config.OWNER_USERNAME else [])
    links = [f'<a href="https://t.me/{n}">@{n}</a>' for n in names if _USERNAME_RE.match(n)]
    if not links:
        links = [f'<a href="tg://user?id={Config.OWNER_ID}">Owner</a>']
    return " ya ".join(links) + " se contact karo, wo unban kar denge."


def _contact_line(cfg: dict) -> str:
    """Clickable @username that was given during /leaveban setup."""
    u = cfg.get("contact_username")
    if u and _USERNAME_RE.match(u):
        return f'<a href="https://t.me/{u}">@{u}</a> se contact karo, wo unban kar denge.'
    return _env_contact_line()


async def _delete_later(bot, chat_id: int, message_id: int, delay: int):
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        pass


async def _notify_staff(context, chat, user, cfg):
    """DM bot owner + bot admins + whoever enabled the feature, with an Unban
    button. Failures are ignored (people who never /start-ed the bot can't be DM'd)."""
    recipients = {Config.OWNER_ID}
    if cfg.get("enabled_by"):
        recipients.add(cfg["enabled_by"])
    try:
        recipients |= {a["user_id"] for a in db.get_all_admins()}
    except Exception:
        pass

    kb = InlineKeyboardMarkup([[InlineKeyboardButton(
        "✅ Unban", callback_data=f"{CB_PREFIX}{chat.id}:{user.id}")]])
    text = (
        f"🚫 <b>{html.escape(user.full_name)}</b> (<code>{user.id}</code>) ne "
        f"<b>{html.escape(chat.title or str(chat.id))}</b> leave kiya aur ban ho gaya."
    )
    for rid in recipients:
        try:
            await context.bot.send_message(rid, text, parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception as e:
            logger.info(f"leave-ban notify skipped for {rid}: {e}")


# ---------------------------------------------------------------- handlers
async def on_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cm = update.chat_member
    if not cm or cm.chat.type not in GROUP_TYPES:
        return  # channels etc. are never touched
    cfg = db.get_leaveban(cm.chat.id)
    if not cfg or not cfg.get("enabled"):
        return

    user = cm.new_chat_member.user
    if user.is_bot:
        return

    was_in = _is_member(cm.old_chat_member)
    is_in = _is_member(cm.new_chat_member)

    # ---- JOIN -> warning ----
    if not was_in and is_in:
        try:
            msg = await context.bot.send_message(
                cm.chat.id,
                f"👋 Welcome {user.mention_html()}!\n\n"
                f"⚠️ <b>Warning:</b> Is group ko leave karoge to aap "
                f"<b>automatically ban</b> ho jaoge.\n"
                f"Galti se leave ho jaye to {_contact_line(cfg)}",
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            logger.warning(f"leave-ban warning failed in {cm.chat.id}: {e}")
            return
        delay = Config.LEAVEBAN_WARN_DELETE_SECONDS
        if delay:
            context.application.create_task(
                _delete_later(context.bot, cm.chat.id, msg.message_id, delay)
            )
        return

    # ---- LEAVE (by the user themselves) -> ban ----
    if was_in and cm.new_chat_member.status == ChatMemberStatus.LEFT \
            and cm.from_user.id == user.id:
        if is_admin(user.id) or user.id == cfg.get("enabled_by"):
            return  # bot owner/admins and whoever enabled it are exempt
        try:
            await context.bot.ban_chat_member(cm.chat.id, user.id)
        except Exception as e:
            # Most common cause: bot is not admin / lacks "Ban users".
            logger.warning(f"leave-ban FAILED for {user.id} in {cm.chat.id}: {e}")
            return
        db.add_audit_log("leave_ban", context.bot.id, user.id, f"chat_id={cm.chat.id}")
        await _notify_staff(context, cm.chat, user, cfg)


async def unban_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    # Registered in an earlier handler group than the generic callback_handler,
    # so we must stop propagation or it would also process this update.
    try:
        _, rest = q.data.split(":", 1)
        chat_id, user_id = (int(x) for x in rest.split(":"))
    except Exception:
        await q.answer("Invalid data.", show_alert=True)
        raise ApplicationHandlerStop

    if not await _can_manage(q.from_user.id, chat_id, context.bot):
        await q.answer("Sirf owner/admin unban kar sakta hai.", show_alert=True)
        raise ApplicationHandlerStop

    try:
        await context.bot.unban_chat_member(chat_id, user_id, only_if_banned=True)
    except Exception as e:
        await q.answer(f"Unban fail: {e}", show_alert=True)
        raise ApplicationHandlerStop

    db.add_audit_log("leave_unban", q.from_user.id, user_id, f"chat_id={chat_id} via button")
    await q.answer("Unban ho gaya")
    try:
        await q.edit_message_text(
            (q.message.text_html or q.message.text or "") + "\n\n✅ <b>Unbanned.</b>",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass
    raise ApplicationHandlerStop


async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """In group: /unban <user_id>"""
    chat, caller = update.effective_chat, update.effective_user
    if not await _can_manage(caller.id, chat.id, context.bot):
        return
    if not context.args or not context.args[0].lstrip("-").isdigit():
        await update.message.reply_text("Use: /unban <user_id>")
        return
    target = int(context.args[0])
    try:
        await context.bot.unban_chat_member(chat.id, target, only_if_banned=True)
    except Exception as e:
        await update.message.reply_text(f"❌ Unban fail: {e}")
        return
    db.add_audit_log("leave_unban", caller.id, target, f"chat_id={chat.id} via /unban")
    await update.message.reply_text(
        "✅ Unban ho gaya. User ab invite link se khud wapas join kar sakta hai."
    )


# ------------------------------------------------ /leaveban setup (asks username)
def _pending(context) -> dict:
    return context.application.bot_data.setdefault("leaveban_pending", {})


async def _ask_contact(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str):
    """Ask the setter which @username to show. mode: 'on' | 'contact'."""
    chat, caller = update.effective_chat, update.effective_user
    prompt = await update.message.reply_text(
        "👤 Warning me kaunsa <b>@username</b> dikhana hai? Galti se leave karne wale "
        "isi se contact karenge.\n\n"
        "Is message ko <b>reply</b> karke username bhejo (jaise <code>@your_username</code>). "
        "Apna hi username dikhana ho to <code>me</code> likho.\n"
        f"⏳ {PENDING_TTL // 60} minute me reply karo.",
        parse_mode=ParseMode.HTML,
        reply_markup=ForceReply(selective=True, input_field_placeholder="@username"),
    )
    _pending(context)[(chat.id, caller.id)] = {
        "mode": mode, "expires": time.time() + PENDING_TTL, "prompt_id": prompt.message_id,
    }


async def _save_contact(update, context, mode: str, username: str):
    chat, caller = update.effective_chat, update.effective_user
    if mode == "on":
        db.set_leaveban(chat.id, True, caller, username)
        await update.message.reply_text(
            f"✅ Leave-ban ON. Ab jo khud leave karega wo ban hoga.\n"
            f"Warning me contact: @{username}\n"
            "🔔 Ban alerts ke liye bot ko private me ek baar /start kar do."
        )
    else:
        db.set_leaveban_contact(chat.id, username, caller)
        await update.message.reply_text(f"✅ Contact update ho gaya: @{username}")


async def contact_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Catches the setter's reply to the username prompt. Registered in group -1;
    anything that is not a pending reply falls through untouched."""
    msg, chat, user = update.message, update.effective_chat, update.effective_user
    if not msg or not msg.text or not user or chat.type not in GROUP_TYPES:
        return
    key = (chat.id, user.id)
    pend = _pending(context).get(key)
    if not pend:
        return
    if time.time() > pend["expires"]:
        _pending(context).pop(key, None)
        return
    # Must be a reply to OUR prompt (also what Telegram delivers under privacy mode).
    if not msg.reply_to_message or msg.reply_to_message.message_id != pend["prompt_id"]:
        return

    text = msg.text.strip()
    if text.lower() in ("me", "main", "mera"):
        username = user.username if user.username and _USERNAME_RE.match(user.username) else None
        if not username:
            await msg.reply_text("❌ Aapka public @username nahi hai. Koi aur username bhejo.")
            raise ApplicationHandlerStop
    else:
        username = _parse_username(text)
        if not username:
            await msg.reply_text(
                "❌ Valid @username bhejo (5-32 chars me letters, numbers, _ hi hote hain). "
                "Dobara reply karo."
            )
            raise ApplicationHandlerStop

    _pending(context).pop(key, None)
    await _save_contact(update, context, pend["mode"], username)
    raise ApplicationHandlerStop


async def leaveban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """In group: /leaveban on | off | status | contact [@username]"""
    chat, caller = update.effective_chat, update.effective_user
    if not await _can_manage(caller.id, chat.id, context.bot):
        return
    arg = (context.args[0].lower() if context.args else "status")

    if arg == "on":
        try:
            me = await context.bot.get_chat_member(chat.id, context.bot.id)
            can_ban = me.status == ChatMemberStatus.OWNER or getattr(me, "can_restrict_members", False)
        except Exception:
            can_ban = False
        if not can_ban:
            await update.message.reply_text(
                "❌ Pehle bot ko is group me admin banao aur 'Ban users' permission do, phir /leaveban on karo."
            )
            return
        await _ask_contact(update, context, "on")

    elif arg == "contact":
        if len(context.args) > 1:
            username = _parse_username(context.args[1])
            if not username:
                await update.message.reply_text("❌ Valid @username do. Example: /leaveban contact @name")
                return
            await _save_contact(update, context, "contact", username)
        else:
            await _ask_contact(update, context, "contact")

    elif arg == "off":
        db.set_leaveban(chat.id, False, caller)
        await update.message.reply_text("✅ Leave-ban OFF.")

    else:
        cfg = db.get_leaveban(chat.id) or {}
        state = "ON ✅" if cfg.get("enabled") else "OFF ❌"
        who = f"@{cfg['contact_username']}" if cfg.get("contact_username") else "set nahi hai"
        await update.message.reply_text(
            f"Leave-ban: {state}\nContact: {who}\n\n"
            "Use: /leaveban on | off | contact [@username]"
        )
