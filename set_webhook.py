"""
Manual Telegram webhook setter.

Use this if you need to set/check/delete the webhook by hand
(debugging, or if you're deploying without PTB's run_webhook()).

Note: bot.py's run_webhook() already sets the webhook automatically
on every startup — you normally do NOT need this script for a
Render/Railway deploy. This is for manual verification only.

Usage:
    python set_webhook.py set https://yourapp.onrender.com
    python set_webhook.py info
    python set_webhook.py delete
"""
import sys
import requests
from config import Config

API_BASE = f"https://api.telegram.org/bot{Config.BOT_TOKEN}"


def set_webhook(base_url: str):
    url = base_url.rstrip("/") + "/telegram"
    resp = requests.post(f"{API_BASE}/setWebhook", data={"url": url})
    print(resp.json())


def get_info():
    resp = requests.get(f"{API_BASE}/getWebhookInfo")
    print(resp.json())


def delete_webhook():
    resp = requests.post(f"{API_BASE}/deleteWebhook")
    print(resp.json())


if __name__ == "__main__":
    if not Config.BOT_TOKEN:
        print("BOT_TOKEN not set — check your .env")
        sys.exit(1)

    if len(sys.argv) < 2:
        print("Usage: python set_webhook.py [set <url> | info | delete]")
        sys.exit(1)

    action = sys.argv[1]

    if action == "set":
        if len(sys.argv) < 3:
            print("Usage: python set_webhook.py set https://yourapp.onrender.com")
            sys.exit(1)
        set_webhook(sys.argv[2])
    elif action == "info":
        get_info()
    elif action == "delete":
        delete_webhook()
    else:
        print("Unknown action. Use: set <url> | info | delete")
