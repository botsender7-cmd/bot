# Telegram Bot (no AI/QR code here) — deploys to Render (webhook mode)

This service only talks to Telegram + the database directly. AI chat and
QR code generation are NOT implemented here — both are called over HTTP
from the separately-deployed API service (`bot-api`, on Vercel).

## Env vars
- `OWNER_USERNAME` (optional) — public @username shown in the leave-ban join warning
- `BOT_TOKEN`, `OWNER_ID`, `MONGODB_URI`
- `API_BASE_URL` — your Vercel API's URL, e.g. `https://<project>.vercel.app`
- `API_KEY` — must exactly match `API_KEY` set on the Vercel `bot-api` project
- `PORT` — set automatically by Render, don't set manually
- `RENDER_EXTERNAL_URL` — set automatically by Render for Web Services,
  don't set manually. This is what triggers webhook mode (see bot.py).

## Deploy to Render — Web Service (not Background Worker)
Webhook mode means Telegram sends updates to this service over HTTPS, so
Render needs to route public traffic to it — that requires the
**Web Service** type, not Background Worker (which has no public URL).

1. Create a new Web Service on Render, connect this repo/folder, build
   with the included `Dockerfile`.
2. Set the env vars above (`BOT_TOKEN`, `OWNER_ID`, `MONGODB_URI`,
   `API_BASE_URL`, `API_KEY`). Leave `PORT`/`RENDER_EXTERNAL_URL` alone —
   Render injects both automatically.
3. On startup, `bot.py` detects `RENDER_EXTERNAL_URL`, builds
   `{RENDER_EXTERNAL_URL}/telegram` as the webhook URL, and registers it
   with Telegram via `run_webhook()`, binding `0.0.0.0:$PORT`.
4. Check logs for `"Public URL detected - using webhook mode"` and the
   webhook base URL it registered, to confirm it didn't silently fall
   back to polling (which happens if `RENDER_EXTERNAL_URL` is missing —
   shouldn't happen on a real Render Web Service, but worth checking on
   first deploy).

### Note on Render's free tier
Free Web Services on Render spin down after ~15 min of no inbound HTTP
traffic and take a few seconds to spin back up on the next request. In
webhook mode that means Telegram's first update after an idle period may
be delayed until the container wakes up (Telegram will retry, so it isn't
usually lost — just delayed). If you need always-on, that's a paid tier.

## Run locally (polling mode)
```
pip install -r requirements.txt
python bot.py
```
No `RENDER_EXTERNAL_URL`/`WEBHOOK_URL` set locally → falls back to
polling automatically, no webhook registration needed for local dev.

## Leave-Ban Guard (groups only)
Opt-in per group. Bot must be a group admin with **Ban users**.
- `/leaveban on|off|status|contact [@username]` (group; owner/bot-admin/group-admin). `on` asks the setter
  which @username to show in the warning (they reply to the bot's prompt); it turns on only after that.
- Join -> warning (auto-deleted after `LEAVEBAN_WARN_DELETE_SECONDS`) showing that clickable @username (env `OWNER_USERNAME`/`LEAVEBAN_CONTACTS` = fallback only).
- Leave by the user themselves -> banned; owner + bot admins get a DM with an **Unban** button
  (they must have started the bot once). `/unban <user_id>` also works in the group.
- Admin kicks are ignored; owner/bot-admins are exempt; channels are never touched.
- Needs `chat_member` updates: already covered by `allowed_updates=Update.ALL_TYPES` in `bot.py`.
- Main menu / side menu button **🚪 Leave-Ban Guard**: setup help, "Add bot to group" link, and **Mere Groups**
  (per-group status, contact, ON/OFF; bot owner/admins see all groups).
