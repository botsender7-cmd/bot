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


ANON_ADMIN_ID = 1087968824  # Telegram's "GroupAnonymousBot" (admins posting anonymously)


async def _caller_is_manager(update: Update, bot) -> bool:
    """Bot owner/admin or group admin. Anonymous admins (message sent 'as the
    group') are accepted: Telegram only allows that for admins of this chat."""
    msg, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if msg is not None and msg.sender_chat is not None and msg.sender_chat.id == chat.id:
        return True
    return await _can_manage(user.id, chat.id, bot)


async def _bot_rights(bot, chat_id: int):
    """(is_admin, can_ban) for the bot itself in this chat. Works even when the
    bot is not an admin (looking up itself is always allowed)."""
    try:
        me = await bot.get_chat_member(chat_id, bot.id)
    except Exception:
        return False, False
    if me.status == ChatMemberStatus.OWNER:
        return True, True
    if me.status == ChatMemberStatus.ADMINISTRATOR:
        return True, bool(getattr(me, "can_restrict_members", False))
    return False, False


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
    if not await _caller_is_manager(update, context.bot):
        await update.message.reply_text("❌ Ye command sirf group ke admin/owner chala sakte hain.")
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
        db.set_leaveban(chat.id, True, caller, username, chat.title)
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
        username = (user.username if user.id != ANON_ADMIN_ID and user.username
                    and _USERNAME_RE.match(user.username) else None)
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
    arg = (context.args[0].lower() if context.args else "status")
    logger.info(f"/leaveban {arg} from {caller.id} in {chat.id}")

    # 1) Bot itself must be an admin, otherwise nothing here can work.
    bot_admin, can_ban = await _bot_rights(context.bot, chat.id)
    if not bot_admin:
        await update.message.reply_text(
            "❌ Pehle mujhe is group me <b>admin</b> banao (<b>Ban users</b> permission ke saath), "
            "phir /leaveban on karo.", parse_mode=ParseMode.HTML)
        return

    # 2) Caller must be an admin (anonymous admins included).
    if not await _caller_is_manager(update, context.bot):
        await update.message.reply_text("❌ Ye command sirf group ke admin/owner chala sakte hain.")
        return

    if arg == "on":
        if not can_ban:
            await update.message.reply_text(
                "❌ Mere paas <b>Ban users</b> permission nahi hai. Group ki admin settings me "
                "mujhe ye permission do, phir /leaveban on karo.", parse_mode=ParseMode.HTML)
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


# ------------------------------------------------------------ main-menu (private chat)
# Reached from handlers.callback_handler (so the bot's ban / force-join checks
# still apply). Callback data: leaveban_menu, lb_help, lb_fix, lb_list, lb_g:<chat>, lb_t:<chat>.
def _menu_text() -> str:
    return (
        "🚪 <b>Leave-Ban Guard</b>\n\n"
        "Group se jo koi <b>khud leave</b> karega wo <b>automatically ban</b> ho jayega. "
        "Naye member ko join par warning milti hai, aur galti se leave karne wala aapke "
        "diye <b>@username</b> se contact karke unban ho sakta hai.\n\n"
        "📌 <b>Kaise use karein</b>\n"
        "1️⃣ Neeche <b>➕ Bot ko group me add karo</b> dabao aur bot ko <b>admin</b> banao "
        "(<b>Ban users</b> permission ke saath).\n"
        "2️⃣ <u>Group ke andar</u> likho: <code>/leaveban on</code>\n"
        "3️⃣ Bot poochega kaunsa @username warning me dikhana hai. Us message ko "
        "<b>reply</b> karke username bhejo (ya apna hi dikhana ho to <code>me</code> likho).\n"
        "4️⃣ Ho gaya! Ab jo khud leave karega wo ban hoga.\n\n"
        "⌨️ <b>Group me commands</b>\n"
        "<code>/leaveban on</code> — chalu karo\n"
        "<code>/leaveban off</code> — band karo\n"
        "<code>/leaveban status</code> — status dekho\n"
        "<code>/leaveban contact @username</code> — warning wala contact badlo\n"
        "<code>/unban user_id</code> — kisi ko unban karo\n\n"
        "🔔 Ban alerts yahan is bot ke private chat me aate hain, uske liye bot ko ek baar "
        "<b>/start</b> kiya hona chahiye. Alert me <b>Unban</b> button hota hai.\n\n"
        "⚠️ Ye commands sirf <b>group ke andar</b> chalte hain, bot ke private chat me nahi."
    )


def _fix_text() -> str:
    return (
        "🛠 <b>Kuch kaam nahi kar raha?</b>\n\n"
        "✔️ Bot group me <b>admin</b> hai aur uske paas <b>Ban users</b> permission hai?\n"
        "✔️ <code>/leaveban on</code> <u>group me</u> likha, bot ke private chat me nahi?\n"
        "✔️ Bot ne username poochha to us message ko <b>reply</b> kiya (seedha likhne se "
        "bot ko nahi dikhta)? 5 minute ke andar?\n"
        "✔️ <code>/leaveban status</code> me <b>ON ✅</b> dikh raha hai?\n"
        "✔️ Aap group ke <b>admin</b> ho? Anonymous admin ho to bhi chalega, par 'Mere Groups' "
        "me group tab nahi dikhega.\n"
        "✔️ Ban alert nahi aa raha? Bot ko private me ek baar <b>/start</b> karo.\n\n"
        "🧪 <b>Test</b>: kisi dusre account se group join karke leave karo. Join par warning "
        "aani chahiye aur leave par ban ho jana chahiye. Aap khud (jisne on kiya) aur bot "
        "admins test me ban nahi hote, isliye dusra account use karo."
    )


def _menu_kb(bot_username: str) -> InlineKeyboardMarkup:
    add_url = f"https://t.me/{bot_username}?startgroup=true&admin=restrict_members"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Bot ko group me add karo", url=add_url)],
        [InlineKeyboardButton("📋 Mere Groups", callback_data="lb_list"),
         InlineKeyboardButton("🛠 Kuch kaam nahi kar raha?", callback_data="lb_fix")],
        [InlineKeyboardButton("🔙 Back", callback_data="main_menu")],
    ])


def _back_kb(target="leaveban_menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=target)]])


async def _show(query, text, kb):
    try:
        await query.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except Exception as e:
        if "not modified" in str(e).lower():
            return
        logger.warning(f"leaveban menu edit failed: {e}")
        try:  # fall back to a fresh message so the user never sees "nothing"
            await query.message.reply_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        except Exception as e2:
            logger.warning(f"leaveban menu fallback failed: {e2}")


async def _group_detail(query, context, chat_id: int, note: str = ""):
    cfg = db.get_leaveban(chat_id)
    if not cfg:
        await _show(query, "❌ Group nahi mila.", _back_kb("lb_list"))
        return
    on = bool(cfg.get("enabled"))
    contact = f"@{cfg['contact_username']}" if cfg.get("contact_username") else "set nahi hai"
    text = (
        f"🚪 <b>{html.escape(cfg.get('title') or str(chat_id))}</b>\n\n"
        f"Status: {'ON ✅' if on else 'OFF ❌'}\n"
        f"Warning contact: {html.escape(contact)}\n\n"
        "Contact badalne ke liye group me: <code>/leaveban contact @username</code>"
        + (f"\n\n{note}" if note else "")
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔴 OFF karo" if on else "🟢 ON karo", callback_data=f"lb_t:{chat_id}")],
        [InlineKeyboardButton("🔙 Back", callback_data="lb_list")],
    ])
    await _show(query, text, kb)


async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    query, user = update.callback_query, update.callback_query.from_user

    if data in ("leaveban_menu", "lb_help"):
        await _show(query, _menu_text(), _menu_kb(context.bot.username))

    elif data == "lb_fix":
        await _show(query, _fix_text(), _back_kb())

    elif data == "lb_list":
        groups = db.list_leaveban_groups(None if is_admin(user.id) else user.id)
        if not groups:
            await _show(query,
                "📋 <b>Mere Groups</b>\n\nAbhi koi group nahi hai. Kisi group me "
                "<code>/leaveban on</code> chalao, wo yahan dikhega.", _back_kb())
            return
        rows = [[InlineKeyboardButton(
            f"{'✅' if g.get('enabled') else '❌'} {(g.get('title') or str(g['chat_id']))[:32]}",
            callback_data=f"lb_g:{g['chat_id']}")] for g in groups]
        rows.append([InlineKeyboardButton("🔙 Back", callback_data="leaveban_menu")])
        await _show(query, "📋 <b>Mere Groups</b>\n\nGroup chuno:", InlineKeyboardMarkup(rows))

    elif data.startswith(("lb_g:", "lb_t:")):
        try:
            chat_id = int(data.split(":", 1)[1])
        except ValueError:
            return
        if not await _can_manage(user.id, chat_id, context.bot):
            await _show(query, "❌ Aap is group ke admin nahi ho.", _back_kb("lb_list"))
            return

        if data.startswith("lb_t:"):
            cfg = db.get_leaveban(chat_id) or {}
            note = ""
            if cfg.get("enabled"):
                db.set_leaveban(chat_id, False, user)
                note = "✅ Leave-ban OFF ho gaya."
            elif not cfg.get("contact_username"):
                note = "⚠️ Pehle group me <code>/leaveban on</code> chalao (username set karna padega)."
            else:
                bot_admin, can_ban = await _bot_rights(context.bot, chat_id)
                if not can_ban:
                    note = "⚠️ Bot ko us group me admin banao (<b>Ban users</b> permission)."
                else:
                    db.set_leaveban(chat_id, True, user, cfg["contact_username"], cfg.get("title"))
                    note = "✅ Leave-ban ON ho gaya."
            await _group_detail(query, context, chat_id, note)
        else:
            await _group_detail(query, context, chat_id)


async def private_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/leaveban or /unban typed in the bot's PRIVATE chat: they only work inside
    a group, so show the guide instead of staying silent."""
    if db.is_user_banned(update.effective_user.id):
        await update.message.reply_text("You are permanently banned from using this bot.")
        return
    await update.message.reply_text(
        "ℹ️ Ye command <b>group ke andar</b> chalta hai, yahan nahi.\n\n" + _menu_text(),
        reply_markup=_menu_kb(context.bot.username),
        parse_mode=ParseMode.HTML,
    )
