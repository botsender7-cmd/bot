"""Leave-Ban Guard.

Groups only (never channels). Opt-in per group via /leaveban on.

- User joins  -> short warning (auto-deleted) with owner contact.
- User leaves -> auto-ban, but ONLY when they left by themselves. An admin
  kick shows up as LEFT with from_user=admin, so it is ignored.
- Owner + bot admins get a DM with an Unban button; /unban <user_id> also
  works inside the group.

Requirements: bot must be a group admin with "Ban users" permission.
"""
import asyncio
import html
import logging
import re

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatType, ChatMemberStatus, ParseMode
from telegram.ext import ContextTypes, ApplicationHandlerStop

from config import Config
from database import db
from handlers import is_admin

logger = logging.getLogger(__name__)

GROUP_TYPES = (ChatType.GROUP, ChatType.SUPERGROUP)
CB_PREFIX = "lbunban:"


def _is_member(cmu) -> bool:
    """True if this ChatMember state means 'currently inside the group'."""
    if cmu.status == ChatMemberStatus.MEMBER:
        return True
    # A restricted user can be either still inside or already gone.
    return cmu.status == ChatMemberStatus.RESTRICTED and bool(getattr(cmu, "is_member", False))


async def _can_manage(user_id: int, chat_id: int, bot) -> bool:
    if is_admin(user_id):  # owner or bot admin
        return True
    try:
        m = await bot.get_chat_member(chat_id, user_id)
        return m.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except Exception:
        return False


_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{4,32}$")


def _contact_line() -> str:
    """Clickable contact(s). t.me links open the chat directly on tap."""
    names = Config.LEAVEBAN_CONTACTS or ([Config.OWNER_USERNAME] if Config.OWNER_USERNAME else [])
    links = [f'<a href="https://t.me/{n}">@{n}</a>' for n in names if _USERNAME_RE.match(n)]
    if not links:  # no public username configured -> link by numeric ID
        links = [f'<a href="tg://user?id={Config.OWNER_ID}">Owner</a>']
    return " ya ".join(links) + " se contact karo, wo unban kar denge."


async def _delete_later(bot, chat_id: int, message_id: int, delay: int):
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        pass


async def _notify_staff(context, chat, user):
    """DM owner + bot admins with an Unban button. Failures are ignored
    (someone who never /start-ed the bot can't be DM'd)."""
    recipients = {Config.OWNER_ID}
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
    if not db.is_leaveban_enabled(cm.chat.id):
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
                f"Galti se leave ho jaye to {_contact_line()}",
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
        if is_admin(user.id):  # owner / bot admins are exempt
            return
        try:
            await context.bot.ban_chat_member(cm.chat.id, user.id)
        except Exception as e:
            # Most common cause: bot is not admin / lacks "Ban users".
            logger.warning(f"leave-ban FAILED for {user.id} in {cm.chat.id}: {e}")
            return
        db.add_audit_log("leave_ban", context.bot.id, user.id, f"chat_id={cm.chat.id}")
        await _notify_staff(context, cm.chat, user)


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

    if not is_admin(q.from_user.id):
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


async def leaveban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """In group: /leaveban on | off | status"""
    chat, caller = update.effective_chat, update.effective_user
    if not await _can_manage(caller.id, chat.id, context.bot):
        return
    arg = (context.args[0].lower() if context.args else "status")

    if arg in ("on", "off"):
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
        db.set_leaveban(chat.id, arg == "on", caller.id)
        await update.message.reply_text(
            "✅ Leave-ban ON. Ab jo khud leave karega wo ban hoga." if arg == "on"
            else "✅ Leave-ban OFF."
        )
    else:
        state = "ON ✅" if db.is_leaveban_enabled(chat.id) else "OFF ❌"
        await update.message.reply_text(f"Leave-ban: {state}\nUse: /leaveban on | off")
