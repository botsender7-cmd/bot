import logging
import os
from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ChatJoinRequestHandler, filters
from telegram.request import HTTPXRequest
from config import Config
from handlers import (start, side_menu, help_command, callback_handler, message_handler,
chat_join_request, menu_command, handle_join_request, join_request, skip_command,
combined_join_request_handler, report_command)
from scheduler import init_scheduler, reschedule_pending_messages
from telegram.error import BadRequest, NetworkError, TimedOut

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def post_init(application):
    logger.info("SETTING COMMANDS...")

    await application.bot.delete_my_commands()

    await application.bot.set_my_commands([
        BotCommand("start", "Start Bot"),
        BotCommand("help", "Help Menu"),
        BotCommand("menu", "Open Menu"),
        BotCommand("report", "Report Copyright Violation")
    ])

    cmds = await application.bot.get_my_commands()
    logger.info(f"CURRENT COMMANDS: {cmds}")

    # Initialize scheduler HERE (inside async context = PTB's event loop).
    # This ensures APScheduler uses the correct running loop — not a stale
    # or non-existent one from sync main().
    init_scheduler(application.bot)

    # Restore any 'pending' scheduled messages from DB into APScheduler.
    await reschedule_pending_messages()

def main():
    proxy_url = os.getenv("TELEGRAM_PROXY_URL")

    if proxy_url:
        logger.info(f"Using Telegram proxy: {proxy_url}")
    
        request = HTTPXRequest(
            connect_timeout=60,
            read_timeout=60,
            write_timeout=60,
        )
    
        application = (
            Application.builder()
            .token(Config.BOT_TOKEN)
            .base_url(f"{proxy_url}/bot")
            .base_file_url(f"{proxy_url}/file/bot")
            .request(request)
            .build()
        )
    else:
        logger.info("No proxy configured")
        application = Application.builder().token(Config.BOT_TOKEN).build()

    # Global error handler
    async def error_handler(update, context):
        err = context.error
        if isinstance(err, (BadRequest,)) and "query is too old" in str(err).lower():
            return  # stale callback — already handled at query.answer()
        if isinstance(err, (NetworkError, TimedOut)):
            logger.warning(f"Network error: {err}")
            return
        logger.error(f"Update {update} caused error: {err}", exc_info=context.error)

    application.add_error_handler(error_handler)

    # NOTE: init_scheduler is called inside post_init (async context)
    # so APScheduler pins to PTB's event loop correctly.
    # Do NOT call init_scheduler here in sync main().

    application.post_init = post_init

    # Command Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("sidemenu", side_menu))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("menu", menu_command))
    application.add_handler(CommandHandler("skip", skip_command))
    application.add_handler(CommandHandler("report", report_command))

    # Callback & Message Handlers
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.add_handler(MessageHandler(
        filters.ALL & ~filters.COMMAND,
        message_handler
    ))
    # Single ChatJoinRequestHandler — PTB stops at first match, so we
    # use one combined handler that does everything in one pass.
    application.add_handler(ChatJoinRequestHandler(combined_join_request_handler))
    


    logger.info("Bot started! Commands: /start, /sidemenu, /help")

    # Railway sets RAILWAY_PUBLIC_DOMAIN automatically when a public port
    # is exposed (e.g. "yourapp.up.railway.app", no scheme, no trailing slash).
    # Falls back to RAILWAY_STATIC_URL for older Railway environments.
    # Render sets RENDER_EXTERNAL_URL automatically (full URL, has scheme
    # already, e.g. "https://yourapp.onrender.com") - checked separately
    # below since it's already a full URL, not a bare domain.
    # WEBHOOK_URL is the manual override for any other host. If none are
    # set, runs polling instead.
    public_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN") or os.getenv("RAILWAY_STATIC_URL")
    render_url = os.getenv("RENDER_EXTERNAL_URL")  # e.g. https://yourapp.onrender.com
    webhook_url_env = os.getenv("WEBHOOK_URL")  # e.g. https://yourapp.up.railway.app

    port = int(os.getenv("PORT", 8080))

    if public_domain or render_url or webhook_url_env:
        if webhook_url_env:
            base_url = webhook_url_env.rstrip("/")
        elif render_url:
            base_url = render_url.rstrip("/")
        else:
            base_url = f"https://{public_domain}"

        logger.info("Using webhook mode")
        logger.info(f"PORT={port}")
        logger.info(f"Webhook base URL: {base_url}")

        application.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path="telegram",
            webhook_url=f"{base_url}/telegram",
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        )
    else:
        logger.info("No public domain detected - using polling mode")
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        )

if __name__ == "__main__":
    main()
