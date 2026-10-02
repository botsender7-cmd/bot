"""Private Share.

Owner / admin flow (owner sees all batches, an admin only their own)
  1. Private Share -> Naya Batch Upload -> send any media (audio / video / document /
     photo / voice / video note / GIF) -> Done.
  2. Send one or more Telegram user IDs -> bot makes ONE private link
     per user ID.
  3. Same batch can get more users later (Mere Batches -> batch -> Naye User).

User flow
  /start pl_<key>. The link is bound to a single Telegram user ID. Anyone else
  who opens it is refused. Every file is sent with protect_content=True
  (no forward / no save-to-gallery inside Telegram).

Limits worth knowing: protect_content cannot stop screenshots or screen
recording, and a link can only be tied to a Telegram *account*, not a person.

This module deliberately does not import handlers.py (handlers imports it).
"""
import asyncio
import html
import re
import secrets
from datetime import datetime, timedelta

from telegram import Update
from telegram.ext import ContextTypes

from config import Config
from database import db
from keyboards import (
    get_pshare_menu_keyboard, get_pshare_upload_keyboard, get_pshare_addfiles_keyboard,
    get_pshare_users_prompt_keyboard, get_pshare_list_keyboard,
    get_pshare_batch_keyboard, get_pshare_links_keyboard,
    get_pshare_link_detail_keyboard, get_pshare_delete_confirm_keyboard,
)

CB_PREFIX = "ps_"
LINK_PREFIX = "pl_"            # start-payload prefix that marks a pshare link
STATE_UPLOAD = "pshare_upload"
STATE_USERS = "pshare_users"
STATE_ADDFILES = "pshare_addfiles"  # adding items to an already finished batch

_MSG_LIMIT = 3500              # stay under Telegram's 4096-char cap
_HTML = "HTML"                 # file names may contain _ * ` -> Markdown would break


def is_owner(user_id):
    return user_id == Config.OWNER_ID


def can_manage(user_id):
    """Owner or a CURRENT admin. Checked on every action, so removing an admin
    cuts off access immediately, even in the middle of an upload."""
    return is_owner(user_id) or db.get_admin(user_id) is not None


def _can_access(batch, user_id):
    """Owner manages every batch; an admin only the batches they created."""
    return bool(batch) and (is_owner(user_id) or batch.get("owner_id") == user_id)


def is_pshare_state(state):
    return isinstance(state, str) and state.startswith("pshare_")


# ---------------------------------------------------------------- helpers

def _new_key():
    for _ in range(5):
        key = LINK_PREFIX + secrets.token_urlsafe(Config.PSHARE_KEY_BYTES)
        if not db.pshare_key_exists(key):
            return key
    raise RuntimeError("Could not allocate a unique pshare link key")


def _extract_media(message):
    """-> (type, file_id, title) for any supported media, else None.
    Order matters: a GIF also arrives as `document`, so animation goes first."""
    if message.animation:
        m, t = message.animation, "animation"
    elif message.video:
        m, t = message.video, "video"
    elif message.audio:
        m, t = message.audio, "audio"
    elif message.voice:
        m, t = message.voice, "voice"
    elif message.video_note:
        m, t = message.video_note, "video_note"
    elif message.photo:
        m, t = message.photo[-1], "photo"
    elif message.document:
        m, t = message.document, "document"
    else:
        return None
    title = getattr(m, "file_name", None) or getattr(m, "title", None)
    return t, m.file_id, title


_ICON = {"audio": "🎵", "video": "🎬", "document": "📄", "photo": "🖼️",
         "voice": "🎤", "video_note": "🎞️", "animation": "🎭"}


def _parse_user_ids(text):
    """'123 456, 789\\n1011' -> ([ints], [bad tokens]). Order kept, deduped."""
    ids, bad = [], []
    for tok in re.split(r"[\s,;]+", (text or "").strip()):
        if not tok:
            continue
        if tok.isdigit() and 0 < int(tok) < 2 ** 53:
            if int(tok) not in ids:
                ids.append(int(tok))
        else:
            bad.append(tok)
    return ids, bad


def _user_label(user_id):
    u = db.get_user(user_id)
    if not u:
        return f"<code>{user_id}</code> ⚠️ (is user ne bot kabhi start nahi kiya)"
    name = html.escape(u.get("first_name") or "") or (f"@{u['username']}" if u.get("username") else "")
    return f"<code>{user_id}</code>" + (f" ({name})" if name else "")


async def _link_for(context, key):
    me = await context.bot.get_me()
    return f"https://t.me/{me.username}?start={key}"


async def _reply_chunks(target, lines, last_markup):
    """Sends lines as one or more messages; keyboard goes on the last one."""
    chunks, cur = [], ""
    for line in lines:
        if cur and len(cur) + len(line) + 2 > _MSG_LIMIT:
            chunks.append(cur)
            cur = ""
        cur += line + "\n\n"
    if cur:
        chunks.append(cur)
    for i, chunk in enumerate(chunks):
        await target.reply_text(
            chunk.strip(), parse_mode=_HTML, disable_web_page_preview=True,
            reply_markup=last_markup if i == len(chunks) - 1 else None
        )


def _upload_text(count, last_title=None):
    text = (
        "🔐 <b>Private Share — batch upload</b>\n\n"
        "Audio / video / document / photo / voice sab bhej sakte hain. "
        "Sab ek hi batch mein jayega.\n"
        f"Abhi is batch mein: <b>{count}</b> file(s)."
    )
    if last_title:
        text += f"\n\n📎 Last saved: {html.escape(last_title)}"
    return text


def _addfiles_text(batch_id, total, added, last_title=None):
    text = (
        f"📎 <b>Batch #{batch_id}</b> — nayi file(s) add karein\n\n"
        "Audio / video / document / photo / voice bhejte jayein, sab isi batch me judenge.\n"
        f"Abhi batch me: <b>{total}</b> file(s)  •  Is baar add hui: <b>{added}</b>\n"
        "ℹ️ Is batch ke sab links me nayi file apne aap aa jayegi."
    )
    if last_title:
        text += f"\n\n📎 Last saved: {html.escape(last_title)}"
    return text


def _clear_flow(context):
    for k in ("state", "pshare_batch_id", "pshare_status_chat_id", "pshare_status_msg_id", "pshare_added"):
        context.user_data.pop(k, None)


# ------------------------------------------------- user side (redeem)

async def _send_one(bot, chat_id, f):
    """One file, always with protect_content=True."""
    kind, fid = f.get("type", "document"), f["file_id"]
    caption = f.get("title")
    common = dict(chat_id=chat_id, protect_content=True)
    if kind == "audio":
        return await bot.send_audio(audio=fid, caption=caption, **common)
    if kind == "video":
        return await bot.send_video(video=fid, caption=caption, **common)
    if kind == "voice":
        return await bot.send_voice(voice=fid, **common)
    if kind == "video_note":
        return await bot.send_video_note(video_note=fid, **common)
    if kind == "photo":
        return await bot.send_photo(photo=fid, caption=caption, **common)
    if kind == "animation":
        return await bot.send_animation(animation=fid, caption=caption, **common)
    return await bot.send_document(document=fid, caption=caption, **common)


async def handle_pshare_deep_link(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str) -> bool:
    """/start pl_<key>. Returns True if the payload was a pshare link (handled,
    including refusals) and False if it isn't one, so the caller can fall back
    to other deep-link types."""
    if not key.startswith(LINK_PREFIX):
        return False
    link = db.get_pshare_link(key)
    if link is None:
        # Not a known pshare key. Let the audio-vault handler have a go
        # (its random keys could, in theory, start with "pl_").
        return False

    user = update.effective_user
    # The whole point of this feature: link works ONLY for the bound account.
    if user.id != link["user_id"]:
        db.bump_pshare_link_blocked(key)
        await update.message.reply_text("🚫 Ye link aapke account ke liye nahi hai.")
        return True

    batch = db.get_pshare_batch(link["batch_id"])
    if not batch or batch.get("status") != "ready" or not batch.get("files"):
        await update.message.reply_text("❌ Ye link ab valid nahi hai.")
        return True

    files = batch["files"]
    db.touch_pshare_link(key)
    await update.message.reply_text(
        f"📚 {len(files)} file(s) bhej raha hoon.\n🔒 Content protected hai — forward / save nahi hoga."
    )

    async def delayed_delete(msg, delay):
        await asyncio.sleep(delay)
        try:
            await msg.delete()
        except Exception:
            pass

    chat_id = update.effective_chat.id
    failed = 0
    for f in files:
        try:
            msg = await _send_one(context.bot, chat_id, f)
            if Config.PSHARE_AUTODELETE_SECONDS:
                context.application.create_task(delayed_delete(msg, Config.PSHARE_AUTODELETE_SECONDS))
            await asyncio.sleep(0.5)  # avoid Telegram flood limits on bursts
        except Exception as e:
            failed += 1
            print(f"[pshare] send failed batch={link['batch_id']} user={user.id}: {e}")
    if failed:
        await update.message.reply_text(f"⚠️ {failed} file bhej nahi paya. Private Share se contact karein.")
    return True


# ------------------------------------------------------- owner: callbacks

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    query = update.callback_query
    user_id = query.from_user.id
    if not can_manage(user_id):
        return  # callback was already answered upstream; stay silent
    owner = is_owner(user_id)

    cmd, _, arg = data[len(CB_PREFIX):].partition("_")

    async def show(text, markup):
        await query.edit_message_text(text, reply_markup=markup, parse_mode=_HTML,
                                      disable_web_page_preview=True)

    def batch_or_none():
        try:
            b = db.get_pshare_batch(int(arg))
        except ValueError:
            return None
        return b if _can_access(b, user_id) else None

    if cmd == "menu":
        _clear_flow(context)
        draft = db.get_pshare_draft(user_id)
        note = ""
        if draft and draft.get("files"):
            note = f"\n\n⚠️ Ek adhoora batch hai ({len(draft['files'])} file). Upload dabane par wahin se resume hoga."
        await show("🔐 <b>Private Share</b>\n\nBatch banao → user ID do → har user ka alag private link." + note,
                   get_pshare_menu_keyboard(owner))

    elif cmd == "new":
        draft = db.get_pshare_draft(user_id)
        if draft:
            batch_id, count = draft["batch_id"], len(draft.get("files", []))
        else:
            batch_id = db.create_pshare_batch(
                user_id, datetime.utcnow() + timedelta(seconds=Config.PSHARE_DRAFT_TTL_SECONDS))
            count = 0
        context.user_data["state"] = STATE_UPLOAD
        context.user_data["pshare_batch_id"] = batch_id
        await show(_upload_text(count), get_pshare_upload_keyboard(count))
        # Later uploads edit this message instead of posting one bubble per file.
        context.user_data["pshare_status_chat_id"] = query.message.chat_id
        context.user_data["pshare_status_msg_id"] = query.message.message_id

    elif cmd == "done":
        batch_id = context.user_data.get("pshare_batch_id")
        batch = db.get_pshare_batch(batch_id) if batch_id else None
        if not batch or not batch.get("files"):
            _clear_flow(context)
            await show("❌ Abhi tak koi file save nahi hui.", get_pshare_menu_keyboard(owner))
            return
        db.finalize_pshare_batch(batch_id)
        context.user_data["state"] = STATE_USERS
        context.user_data["pshare_batch_id"] = batch_id
        context.user_data.pop("pshare_status_chat_id", None)
        context.user_data.pop("pshare_status_msg_id", None)
        await show(
            f"✅ <b>Batch #{batch_id}</b> ban gaya ({len(batch['files'])} file).\n\n"
            "Ab <b>Telegram user ID</b> bhejein.\n"
            "Ek se zyada ho to space / comma / nayi line se alag karke bhejein, "
            "har user ka alag link banega.\n\n"
            "Example:\n<code>123456789 987654321</code>",
            get_pshare_users_prompt_keyboard(batch_id)
        )

    elif cmd == "cancel":
        batch_id = context.user_data.get("pshare_batch_id")
        if batch_id:
            b = db.get_pshare_batch(batch_id)
            if b and b.get("status") == "draft":
                db.delete_pshare_batch(batch_id)
        _clear_flow(context)
        await show("🗑️ Batch cancel ho gaya.", get_pshare_menu_keyboard(owner))

    elif cmd == "list":
        _clear_flow(context)
        batches = db.list_pshare_batches(None if owner else user_id)
        if not batches:
            await show("📂 Abhi koi batch nahi hai.", get_pshare_menu_keyboard(owner))
        else:
            await show("📂 <b>Mere Batches</b>", get_pshare_list_keyboard(batches, show_creator=owner))

    elif cmd == "b":  # batch detail
        _clear_flow(context)
        b = batch_or_none()
        if not b or b.get("status") != "ready":
            await show("❌ Batch nahi mila.", get_pshare_menu_keyboard(owner))
            return
        files = b.get("files", [])
        lines = "\n".join(
            f"{_ICON.get(f.get('type'), '📎')} {html.escape(f.get('title') or f.get('type', 'file'))}"
            for f in files[:10]
        )
        if len(files) > 10:
            lines += f"\n… +{len(files) - 10} aur"
        n_links = len(db.list_pshare_links(b["batch_id"]))
        await show(
            f"📦 <b>Batch #{b['batch_id']}</b>\n"
            f"Files: {len(files)}  •  Users with link: {n_links}\n\n{lines}",
            get_pshare_batch_keyboard(b["batch_id"])
        )

    elif cmd == "add":  # more users for an existing batch
        b = batch_or_none()
        if not b or b.get("status") != "ready":
            await show("❌ Batch nahi mila.", get_pshare_menu_keyboard(owner))
            return
        context.user_data["state"] = STATE_USERS
        context.user_data["pshare_batch_id"] = b["batch_id"]
        await show(
            f"👤 <b>Batch #{b['batch_id']}</b> — user ID(s) bhejein "
            "(space / comma / nayi line se alag).",
            get_pshare_users_prompt_keyboard(b["batch_id"])
        )

    elif cmd == "addf":  # add items to an existing, finished batch
        b = batch_or_none()
        if not b or b.get("status") != "ready":
            await show("❌ Batch nahi mila.", get_pshare_menu_keyboard(owner))
            return
        context.user_data["state"] = STATE_ADDFILES
        context.user_data["pshare_batch_id"] = b["batch_id"]
        context.user_data["pshare_added"] = 0
        await show(_addfiles_text(b["batch_id"], len(b.get("files", [])), 0),
                   get_pshare_addfiles_keyboard(b["batch_id"]))
        context.user_data["pshare_status_chat_id"] = query.message.chat_id
        context.user_data["pshare_status_msg_id"] = query.message.message_id

    elif cmd == "links":
        _clear_flow(context)
        b = batch_or_none()
        if not b:
            await show("❌ Batch nahi mila.", get_pshare_menu_keyboard(owner))
            return
        links = db.list_pshare_links(b["batch_id"])
        if not links:
            await show(f"📦 Batch #{b['batch_id']} ke liye abhi koi link nahi bana.",
                       get_pshare_batch_keyboard(b["batch_id"]))
            return
        await show(f"🔗 <b>Batch #{b['batch_id']}</b> — user chuno (link dekhne / revoke karne ke liye):",
                   get_pshare_links_keyboard(b["batch_id"], links[:90]))

    elif cmd == "l":  # one link
        link = db.get_pshare_link(arg)
        if not link or not _can_access(db.get_pshare_batch(link["batch_id"]), user_id):
            await show("❌ Link nahi mila (revoke ho chuka hoga).", get_pshare_menu_keyboard(owner))
            return
        last = link.get("last_opened_at")
        blocked = link.get("blocked_count", 0)
        await show(
            f"👤 {_user_label(link['user_id'])}\n"
            f"📦 Batch #{link['batch_id']}\n\n"
            f"<code>{await _link_for(context, link['key'])}</code>\n\n"
            f"Opened: {link.get('opened_count', 0)}"
            + (f" (last {last.strftime('%d-%m-%Y %H:%M')} UTC)" if last else "")
            + (f"\n🚫 Galat account ne {blocked} baar try kiya" if blocked else ""),
            get_pshare_link_detail_keyboard(link["key"], link["batch_id"])
        )

    elif cmd == "rv":
        link = db.get_pshare_link(arg)
        if link and _can_access(db.get_pshare_batch(link["batch_id"]), user_id):
            db.delete_pshare_link(arg)
            await show(f"🚫 User <code>{link['user_id']}</code> ka link revoke ho gaya. Ab ye kaam nahi karega.",
                       get_pshare_batch_keyboard(link["batch_id"]))
        else:
            await show("❌ Link pehle hi nahi hai.", get_pshare_menu_keyboard(owner))

    elif cmd == "delb":  # ask first
        b = batch_or_none()
        if not b:
            await show("❌ Batch nahi mila.", get_pshare_menu_keyboard(owner))
            return
        n = len(db.list_pshare_links(b["batch_id"]))
        await show(f"⚠️ Batch #{b['batch_id']} delete karein?\nIske {n} link bhi band ho jayenge.",
                   get_pshare_delete_confirm_keyboard(b["batch_id"]))

    elif cmd == "delby":
        b = batch_or_none()
        if not b:
            await show("❌ Batch nahi mila.", get_pshare_menu_keyboard(owner))
            return
        db.delete_pshare_batch(b["batch_id"])
        await show("🗑️ Batch aur uske sab links delete ho gaye.", get_pshare_menu_keyboard(owner))


# ------------------------------------------------------- owner: messages

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE, state: str) -> bool:
    """Called from message_handler when user_data['state'] is a pshare state.
    Returns True if the message was consumed."""
    user = update.effective_user
    if not can_manage(user.id):
        _clear_flow(context)
        return False

    batch_id = context.user_data.get("pshare_batch_id")

    # ----- upload -----
    if state in (STATE_UPLOAD, STATE_ADDFILES):
        adding = state == STATE_ADDFILES
        media = _extract_media(update.message)
        if not media:
            await update.message.reply_text("❌ Koi audio / video / document / photo bhejein.")
            return True
        batch = db.get_pshare_batch(batch_id) if batch_id else None
        if not _can_access(batch, user.id) or batch.get("status") != ("ready" if adding else "draft"):
            _clear_flow(context)
            await update.message.reply_text("❌ Batch nahi mila, Private Share menu se dobara shuru karein.",
                                            reply_markup=get_pshare_menu_keyboard(is_owner(user.id)))
            return True
        kind, file_id, title = media
        title = title or update.message.caption or f"{kind}_{len(batch.get('files', [])) + 1}"
        updated = db.append_pshare_file(batch_id, {"file_id": file_id, "type": kind, "title": title},
                                        status="ready" if adding else "draft")
        if not updated:  # batch was deleted / changed while uploading
            _clear_flow(context)
            await update.message.reply_text("❌ Batch ab available nahi hai.",
                                            reply_markup=get_pshare_menu_keyboard(is_owner(user.id)))
            return True
        count = len(updated.get("files", []))
        if adding:
            context.user_data["pshare_added"] = context.user_data.get("pshare_added", 0) + 1

        try:  # keep the chat tidy: only the status message should move
            await update.message.delete()
        except Exception:
            pass

        if adding:
            text = _addfiles_text(batch_id, count, context.user_data.get("pshare_added", 0), title)
            markup = get_pshare_addfiles_keyboard(batch_id)
        else:
            text = _upload_text(count, title)
            markup = get_pshare_upload_keyboard(count)
        chat_id = context.user_data.get("pshare_status_chat_id")
        msg_id = context.user_data.get("pshare_status_msg_id")
        edited = False
        if chat_id and msg_id:
            try:
                await context.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text,
                                                    reply_markup=markup, parse_mode=_HTML)
                edited = True
            except Exception:
                pass
        if not edited:
            sent = await update.message.reply_text(text, reply_markup=markup, parse_mode=_HTML)
            context.user_data["pshare_status_chat_id"] = sent.chat_id
            context.user_data["pshare_status_msg_id"] = sent.message_id
        return True

    # ----- user IDs -> links -----
    if state == STATE_USERS:
        batch = db.get_pshare_batch(batch_id) if batch_id else None
        if not _can_access(batch, user.id) or batch.get("status") != "ready":
            _clear_flow(context)
            await update.message.reply_text("❌ Batch nahi mila.", reply_markup=get_pshare_menu_keyboard(is_owner(user.id)))
            return True

        ids, bad = _parse_user_ids(update.message.text)
        if not ids:
            await update.message.reply_text(
                "❌ Valid numeric user ID bhejein (jaise <code>123456789</code>).",
                parse_mode=_HTML, reply_markup=get_pshare_users_prompt_keyboard(batch_id))
            return True

        lines = [f"🔗 <b>Batch #{batch_id}</b> — {len(ids)} user ke links"]
        for uid in ids:
            link, created = db.get_or_create_pshare_link(batch_id, uid, user.id, _new_key())
            tag = "" if created else " <i>(pehle se bana tha)</i>"
            lines.append(f"👤 {_user_label(uid)}{tag}\n<code>{await _link_for(context, link['key'])}</code>")
        if bad:
            lines.append("⚠️ Ye ID galat the, skip kiye: " + html.escape(", ".join(bad[:10])))
        lines.append("ℹ️ Har link sirf apne user ID par khulega. Content protected hai.")

        _clear_flow(context)
        await _reply_chunks(update.message, lines, get_pshare_batch_keyboard(batch_id))
        return True

    return False
