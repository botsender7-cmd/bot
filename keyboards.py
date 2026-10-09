from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from config import Config

def get_main_menu(user_id):
    buttons = []

    # Common features for all users
    buttons.append([
        InlineKeyboardButton("🤖 AI Chat", callback_data="ai_chat"),
        InlineKeyboardButton("📅 Schedule Message", callback_data="schedule_msg"),
        InlineKeyboardButton("🤖 Auto Approve", callback_data="auto_approve_menu")
    ])
    buttons.append([
        InlineKeyboardButton("📊 QR Code Generator", callback_data="qr_code"),
        InlineKeyboardButton("📢 Bot Updates", callback_data="bot_updates"),
        InlineKeyboardButton("📨 My Scheduled", callback_data="my_scheduled")
    ])

    buttons.append([InlineKeyboardButton("🚪 Leave-Ban Guard", callback_data="leaveban_menu")])
    if user_id != Config.OWNER_ID:
        buttons.append([InlineKeyboardButton("📬 Owner se Contact", callback_data="ct_open")])

    is_admin = user_id in [a["user_id"] for a in __import__('database').db.get_all_admins()]
    is_owner = user_id == Config.OWNER_ID

    if is_admin or is_owner:
        buttons.append([
            InlineKeyboardButton("👤 Manage Users", callback_data="manage_users"),
            InlineKeyboardButton("🛡️ Moderation Panel", callback_data="moderation_panel")
        ])

    if is_owner:
        buttons.append([
            InlineKeyboardButton("⚙️ Owner Panel", callback_data="owner_panel"),
            InlineKeyboardButton("📊 Stats", callback_data="stats")
        ])

    buttons.append([InlineKeyboardButton("❌ Close", callback_data="close")])

    return InlineKeyboardMarkup(buttons)

def get_owner_panel():
    buttons = [
        [InlineKeyboardButton("➕ Add Channel", callback_data="add_channel"),
         InlineKeyboardButton("➕ Add Group", callback_data="add_group")],
        [InlineKeyboardButton("➕ Add Admin", callback_data="add_admin")],
        [InlineKeyboardButton("⚙️ Set AI Limit", callback_data="ai_limit_menu")],
        [InlineKeyboardButton("📋 Channel List", callback_data="channel_list")],
        [InlineKeyboardButton("👥 Admin List", callback_data="admin_list")],
        [InlineKeyboardButton("📝 Post Update", callback_data="post_update")],
        [InlineKeyboardButton("🛡️ Moderation Panel", callback_data="moderation_panel")],
        [InlineKeyboardButton("🎵 Audio Vault", callback_data="audio_menu")],
        [InlineKeyboardButton("🔐 Private Share", callback_data="ps_menu")],
        [InlineKeyboardButton("🤖 Contact Clones", callback_data="cl_list")],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(buttons)

def get_channel_list_keyboard(channels):
    buttons = []
    for ch in channels:
        status = "✅ Auto" if ch.get("auto_approve") else "❌ Manual"
        buttons.append([
            InlineKeyboardButton(f"{ch['channel_name']} ({status})", callback_data=f"ch_detail_{ch['channel_id']}")
        ])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="owner_panel")])
    return InlineKeyboardMarkup(buttons)

def get_channel_detail_keyboard(channel_id, auto_approve):
    toggle_text = "❌ Disable Auto-Approve" if auto_approve else "✅ Enable Auto-Approve"
    buttons = [
        [InlineKeyboardButton(toggle_text, callback_data=f"toggle_approve_{channel_id}")],
        [InlineKeyboardButton("🔗 Update Invite Link", callback_data=f"update_link_{channel_id}")],
        [InlineKeyboardButton("🗑️ Remove Channel", callback_data=f"remove_ch_{channel_id}")],
        [InlineKeyboardButton("🔙 Back", callback_data="channel_list")]
    ]
    return InlineKeyboardMarkup(buttons)

def get_schedule_type_keyboard():
    buttons = [
        [InlineKeyboardButton("📢 Channel", callback_data="sched_channel")],
        [InlineKeyboardButton("👥 Group", callback_data="sched_group")],
        [InlineKeyboardButton("👤 User", callback_data="sched_user")],
        [InlineKeyboardButton("🔙 Back", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(buttons)

# NEW: Media type selection for scheduling
def get_media_type_keyboard():
    buttons = [
        [InlineKeyboardButton("📝 Text Only ", callback_data="media_text")],
        [InlineKeyboardButton("📷 Photo (FORMAT .jpg)", callback_data="media_photo"),
         InlineKeyboardButton("🎬 Video (FORMAT .Mp4)", callback_data="media_video")],
        [InlineKeyboardButton("📄 Document (FORMAT .file)", callback_data="media_document"),
         InlineKeyboardButton("🎵 Audio (FORMAT .Mp3)", callback_data="media_audio")],
        [InlineKeyboardButton("🎤 Voice (FORMAT .ogg)", callback_data="media_voice"),
         InlineKeyboardButton("🎞️ Video Note (FORMAT .Mp4)", callback_data="media_video_note")],
        [InlineKeyboardButton("🎭 Animation/GIF (FORMAT .gif)", callback_data="media_animation"),
         InlineKeyboardButton("🏷️ Sticker", callback_data="media_sticker")],
        [InlineKeyboardButton("📍 Location", callback_data="media_location"),
         InlineKeyboardButton("📊 Poll", callback_data="media_poll")],
        [InlineKeyboardButton("👤 Contact", callback_data="media_contact")],
        [InlineKeyboardButton("🔙 Cancel", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(buttons)

def get_force_join_keyboard(channels_not_joined):
    buttons = []

    for ch in channels_not_joined:

        invite_link = ch.get("invite_link")

        # Fallback for old channels
        if not invite_link:
            invite_link = ch.get("channel_link")

        if not invite_link:
            channel_name = str(ch.get("channel_name", "")).replace("@", "")
            invite_link = f"https://t.me/{channel_name}"

        buttons.append([
            InlineKeyboardButton(
                f"📢 Join {ch['channel_name']}",
                url=invite_link
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "🔄 Check Again",
            callback_data="check_join"
        )
    ])

    buttons.append([
        InlineKeyboardButton(
            "❌ Close",
            callback_data="close"
        )
    ])

    return InlineKeyboardMarkup(buttons)

def get_admin_list_keyboard(admins):
    buttons = []
    for admin in admins:
        buttons.append([
            InlineKeyboardButton(f"👤 {admin['user_id']}", callback_data=f"admin_detail_{admin['user_id']}")
        ])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="owner_panel")])
    return InlineKeyboardMarkup(buttons)

def get_admin_detail_keyboard(admin_id):
    buttons = [
        [InlineKeyboardButton("🗑️ Remove Admin", callback_data=f"remove_admin_{admin_id}")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_list")]
    ]
    return InlineKeyboardMarkup(buttons)

MEDIA_FORMAT_INFO = {
    "photo":      ("📷", ".jpg"),
    "video":      ("🎬", ".mp4"),
    "document":   ("📄", ".file"),
    "audio":      ("🎵", ".mp3"),
    "voice":      ("🎤", ".ogg"),
    "video_note": ("⭕", ".mp4"),
    "animation":  ("🎭", ".gif"),
    "sticker":    ("🏷️", ".webp"),
    "location":   ("📍", "loc"),
    "poll":       ("📊", "poll"),
    "contact":    ("👤", "vcf"),
}

def get_media_label(media_type):
    """Returns (emoji, format_string) for a given media_type."""
    if not media_type:
        return ("📝", ".txt")
    return MEDIA_FORMAT_INFO.get(media_type, ("📎", f".{media_type}"))

def format_schedule_time(value):
    """schedule_time is stored as naive UTC datetime (Mongo returns datetime,
    not str). Show it in IST, which is what the user typed it in."""
    if not value:
        return "N/A"
    from datetime import datetime, timedelta
    try:
        dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        return (dt + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d %H:%M") + " IST"
    except Exception:
        return str(value)[:16]

def get_scheduled_list_keyboard(messages):
    buttons = []
    for msg in messages:
        status_emoji = "⏳" if msg["status"] == "pending" else "✅" if msg["status"] == "sent" else "❌"
        icon, fmt = get_media_label(msg.get("media_type"))
        time_str = format_schedule_time(msg.get("schedule_time"))
        buttons.append([
            InlineKeyboardButton(
                f"{status_emoji} {icon} {fmt} | ID:{msg['id']} | {time_str}",
                callback_data=f"sched_detail_{msg['id']}"
            )
        ])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="main_menu")])
    return InlineKeyboardMarkup(buttons)

def get_scheduled_detail_keyboard(msg_id):
    buttons = [
        [InlineKeyboardButton("🗑️ Delete Schedule", callback_data=f"del_sched_{msg_id}")],
        [InlineKeyboardButton("🚨 Report Copyright Violation", callback_data=f"report_start_{msg_id}")],
        [InlineKeyboardButton("🔙 Back", callback_data="my_scheduled")]
    ]
    return InlineKeyboardMarkup(buttons)

# NEW: Side Menu Keyboard
def get_side_menu(user_id):
    buttons = [
        [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")],
        [InlineKeyboardButton("🤖 AI Chat", callback_data="ai_chat")],
        [InlineKeyboardButton("📅 Schedule Message", callback_data="schedule_msg")],
        [InlineKeyboardButton("📊 QR Code", callback_data="qr_code")],
        [InlineKeyboardButton("📢 Bot Updates", callback_data="bot_updates")],
        [InlineKeyboardButton("📨 My Scheduled", callback_data="my_scheduled")],
        [InlineKeyboardButton("🚪 Leave-Ban Guard", callback_data="leaveban_menu")],
        [InlineKeyboardButton("📬 Owner se Contact", callback_data="ct_open")],
        [InlineKeyboardButton("❓ Help", callback_data="help")],
        [InlineKeyboardButton("❌ Close", callback_data="close")]
    ]
    return InlineKeyboardMarkup(buttons)

# NEW: Help Menu Keyboard
def get_help_menu():
    buttons = [
        [InlineKeyboardButton("🤖 AI Chat Help", callback_data="help_ai")],
        [InlineKeyboardButton("📅 Schedule Help", callback_data="help_schedule")],
        [InlineKeyboardButton("📊 QR Code Help", callback_data="help_qr")],
        [InlineKeyboardButton("📢 Updates Help", callback_data="help_updates")],
        [InlineKeyboardButton("👤 Admin/Owner Help", callback_data="help_admin")],
        [InlineKeyboardButton("👤 Auto Approve Feature", callback_data="help_auto_approve")],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(buttons)


def get_ai_limit_keybord():
    buttons = [
        [InlineKeyboardButton("👤 User AI Limit",callback_data="set_user_ai_limit")],
        [InlineKeyboardButton("🛡 Admin AI Limit",callback_data="set_admin_ai_limit")],
        [InlineKeyboardButton("🔙 Back", callback_data="owner_panel")]
    ]
    return InlineKeyboardMarkup(buttons)

# ========== AUDIO VAULT (owner only) ==========

def get_audio_menu_keyboard():
    buttons = [
        [InlineKeyboardButton("⬆️ Naya Batch Upload", callback_data="audio_upload")],
        [InlineKeyboardButton("📂 Mere Links", callback_data="audio_list")],
        [InlineKeyboardButton("🔙 Back", callback_data="owner_panel")]
    ]
    return InlineKeyboardMarkup(buttons)


def get_audio_upload_keyboard(file_count):
    """Shown while the owner is sending audio files into a draft batch."""
    buttons = []
    if file_count:
        buttons.append([InlineKeyboardButton(f"✅ Done ({file_count})", callback_data="audio_done")])
    buttons.append([InlineKeyboardButton("🗑️ Cancel Batch", callback_data="audio_cancel")])
    return InlineKeyboardMarkup(buttons)


def get_audio_expiry_keyboard():
    """Preset expiry choices. Free-text durations still work — the owner can
    just type '45m' / '2h30m' / 'never' instead of tapping a button."""
    labels = list(Config.AUDIO_EXPIRY_OPTIONS.keys())
    buttons = [
        [InlineKeyboardButton(label, callback_data=f"audio_exp_{i}")
         for i, label in enumerate(labels[row:row + 2], start=row)]
        for row in range(0, len(labels), 2)
    ]
    buttons.append([InlineKeyboardButton("🗑️ Cancel Batch", callback_data="audio_cancel")])
    return InlineKeyboardMarkup(buttons)


def get_audio_list_keyboard(batches):
    buttons = []
    for b in batches:
        label = f"{len(b.get('files', []))} file(s) • {b['key']}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"audio_detail_{b['key']}")])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="audio_menu")])
    return InlineKeyboardMarkup(buttons)


def get_audio_detail_keyboard(key):
    buttons = [
        [InlineKeyboardButton("🗑️ Delete Link", callback_data=f"audio_del_{key}")],
        [InlineKeyboardButton("🔙 Back", callback_data="audio_list")]
    ]
    return InlineKeyboardMarkup(buttons)


# ========== PRIVATE SHARE (owner + admin) ==========

def get_pshare_menu_keyboard(is_owner=True):
    # owner_panel is owner-only, so admins go back to the moderation panel.
    back = "owner_panel" if is_owner else "moderation_panel"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬆️ Naya Batch Upload", callback_data="ps_new")],
        [InlineKeyboardButton("📂 Mere Batches", callback_data="ps_list")],
        [InlineKeyboardButton("🔙 Back", callback_data=back)]
    ])


def get_pshare_upload_keyboard(file_count):
    buttons = []
    if file_count:
        buttons.append([InlineKeyboardButton(f"✅ Done ({file_count})", callback_data="ps_done")])
    buttons.append([InlineKeyboardButton("🗑️ Cancel Batch", callback_data="ps_cancel")])
    return InlineKeyboardMarkup(buttons)


def get_pshare_addfiles_keyboard(batch_id):
    # "Done" just goes back to the batch; nothing is deleted from here.
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Done", callback_data=f"ps_b_{batch_id}")]
    ])


def get_pshare_users_prompt_keyboard(batch_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭️ Baad mein", callback_data=f"ps_b_{batch_id}")]
    ])


def get_pshare_list_keyboard(batches, show_creator=False):
    buttons = []
    for b in batches:
        label = f"📦 Batch #{b['batch_id']} • {len(b.get('files', []))} file • {b.get('link_count', 0)} user"
        if show_creator:
            label += f" • by {b['owner_id']}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"ps_b_{b['batch_id']}")])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="ps_menu")])
    return InlineKeyboardMarkup(buttons)


def get_pshare_batch_keyboard(batch_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Naye User ke liye Link", callback_data=f"ps_add_{batch_id}")],
        [InlineKeyboardButton("📎 Batch me Nayi File Add", callback_data=f"ps_addf_{batch_id}")],
        [InlineKeyboardButton("🔗 Links / Users dekho", callback_data=f"ps_links_{batch_id}")],
        [InlineKeyboardButton("🗑️ Batch Delete", callback_data=f"ps_delb_{batch_id}")],
        [InlineKeyboardButton("🔙 Back", callback_data="ps_list")]
    ])


def get_pshare_links_keyboard(batch_id, links):
    buttons = []
    for l in links:
        buttons.append([InlineKeyboardButton(
            f"👤 {l['user_id']} • {l.get('opened_count', 0)} open",
            callback_data=f"ps_l_{l['key']}"
        )])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data=f"ps_b_{batch_id}")])
    return InlineKeyboardMarkup(buttons)


def get_pshare_link_detail_keyboard(key, batch_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚫 Link Revoke", callback_data=f"ps_rv_{key}")],
        [InlineKeyboardButton("🔙 Back", callback_data=f"ps_links_{batch_id}")]
    ])


def get_pshare_delete_confirm_keyboard(batch_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Haan, delete karo", callback_data=f"ps_delby_{batch_id}")],
        [InlineKeyboardButton("❌ Nahi", callback_data=f"ps_b_{batch_id}")]
    ])


# ========== COPYRIGHT PROTECTION SYSTEM ==========

def get_copyright_warning_keyboard():
    buttons = [
        [InlineKeyboardButton("✅ I Agree / Maine Samjha", callback_data="copyright_ack")],
        [InlineKeyboardButton("❌ Cancel", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(buttons)

def get_report_button_keyboard(scheduled_message_id):
    """Attached to a scheduled-content detail view so any user can report it."""
    buttons = [
        [InlineKeyboardButton("🚨 Report Copyright Violation", callback_data=f"report_start_{scheduled_message_id}")]
    ]
    return InlineKeyboardMarkup(buttons)

def get_moderation_panel_keyboard():
    buttons = [
        [InlineKeyboardButton("📋 View Reports", callback_data="mod_view_reports")],
        [InlineKeyboardButton("⚠️ View Strikes", callback_data="mod_view_strikes")],
        [InlineKeyboardButton("📜 Audit Log", callback_data="mod_audit_log")],
        [InlineKeyboardButton("🚫 Ban User", callback_data="mod_ban_user")],
        [InlineKeyboardButton("✅ Unban User", callback_data="mod_unban_user")],
        [InlineKeyboardButton("♻️ Reset Strikes", callback_data="mod_reset_strikes")],
        [InlineKeyboardButton("🔍 View User History", callback_data="mod_user_history")],
        [InlineKeyboardButton("🔐 Private Share", callback_data="ps_menu")],
        [InlineKeyboardButton("🔙 Back", callback_data="owner_panel")]
    ]
    return InlineKeyboardMarkup(buttons)

def get_report_detail_keyboard(report_id, reported_user_id):
    buttons = [
        [InlineKeyboardButton("🗑️ Remove Content", callback_data=f"mod_remove_content_{report_id}")],
        [InlineKeyboardButton("⚠️ Issue Strike", callback_data=f"mod_strike_{reported_user_id}_{report_id}")],
        [InlineKeyboardButton("✅ Dismiss Report", callback_data=f"mod_dismiss_{report_id}")],
        [InlineKeyboardButton("🔙 Back", callback_data="mod_view_reports")]
    ]
    return InlineKeyboardMarkup(buttons)

# ===== Contact Owner (contact.py) =====
def get_contact_prompt_keyboard(show_clone=False):
    """show_clone adds the "make your own contact bot" entry (clone.py), only on
    the first prompt and only when the feature is configured (ENCRYPTION_KEY)."""
    rows = []
    if show_clone and Config.CLONES_ENABLED:
        rows.append([InlineKeyboardButton("🤖 Apna Contact Bot Banao", callback_data="cl_menu")])
    rows.append([InlineKeyboardButton("❌ Band karo", callback_data="ct_stop")])
    return InlineKeyboardMarkup(rows)

def get_contact_reply_keyboard():
    """Under the owner's reply, so the user can answer back in one tap."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✉️ Jawab do", callback_data="ct_reply")]
    ])

def get_contact_block_keyboard(user_id, blocked):
    """Under the header the owner receives. Flips between Block / Unblock."""
    if blocked:
        btn = InlineKeyboardButton("✅ Unblock user", callback_data=f"ct_ubk_{user_id}")
    else:
        btn = InlineKeyboardButton("🚫 Block user", callback_data=f"ct_blk_{user_id}")
    return InlineKeyboardMarkup([[btn]])

