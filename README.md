# Telegram Bot (AI/QR code here) — deploys (webhook mode)

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

## Private Share (owner + admin)
Owner Panel or Moderation Panel -> **🔐 Private Share** (not in the main menu).
1. **Naya Batch Upload** -> send any audio / video / document / photo / voice / video note / GIF -> **Done**.
2. Send one or more user Telegram **user IDs** (space / comma / new line separated) ->
   the bot creates one private link **per user ID**.
3. **📎 Batch me Nayi File Add**: finished batch me aur items jodo. Is batch ke sab links me nayi file apne aap aa jati hai (jo user pehle khol chuka hai use alag se notify nahi hota, wo link dobara khole to saari files milti hain).
4. Later: **Mere Batches** -> batch -> **Naye User ke liye Link** (same batch, more users),
   view opens per user, **Revoke** a single user's link, or delete the whole batch.

Access: the owner manages every batch; an admin manages only the batches they created. Admin rights are re-checked on every action, so removing an admin locks them out immediately.

Rules enforced in `pshare.py`:
- Link = `https://t.me/<bot>?start=pl_<random key>`. It is bound to ONE user ID; any other account
  gets "Ye link aapke account ke liye nahi hai" and receives nothing (attempts are counted).
- Every file is sent with `protect_content=True` (no forward / save inside Telegram).
- Same batch + same user ID again returns the existing link (no duplicates).
- Force-join channel check still runs before delivery.
- Config: `PSHARE_AUTODELETE_SECONDS` (0 = files stay in user's chat), `PSHARE_DRAFT_TTL_SECONDS`.
- Mongo collections: `pshare_batches`, `pshare_links`.

Limit: `protect_content` cannot block screenshots / screen recording, and a link is tied to a
Telegram account, not a person.

## Contact Owner (users -> owner, owner replies back)
Main menu / side menu button **📬 Owner se Contact** (hidden for the owner).
- User taps it and sends anything (text / photo / voice / file ...). The owner receives a small
  header (name, @username, ID, **🚫 Block user** button) plus the original message.
- Owner **replies** to the header or to the copied message -> the reply reaches that user as
  "📨 Owner ka jawab" with a **✉️ Jawab do** button, so the user can answer back in one tap.
- **🚫 Block / ✅ Unblock** under every header. This is separate from the global ban.
- Only `OWNER_ID` receives contact messages. The owner must have started the bot once (Telegram rule).
- Force-join channel check still applies before a user can open it.
- Flood guard: `CONTACT_RATE_LIMIT` messages per `CONTACT_RATE_WINDOW` seconds per user (in memory).
- Reply mapping is stored in Mongo (`contact_msgs`, auto-deleted after `CONTACT_MAP_TTL_DAYS` = 30 days, so the
  owner can't reply to older messages); blocks in `contact_blocked`.
- Code: `contact.py`; hooks in `handlers.py` (callback route + top of `message_handler`), `keyboards.py`, `database.py`, `config.py`.
- Limit: the owner's reply is text-only for text (bold/links typed by the owner are sent as plain text).

## Contact-bot clones (users make their own contact bot)
In **📬 Owner se Contact** -> **🤖 Apna Contact Bot Banao**. The user creates a bot in @BotFather, pastes
the token, presses Start on the new bot once, and from then on anyone who messages *that* bot reaches *that user*
(same reply / 🚫 Block flow as above, `/ban` + `/unban` also work). Code: `clone.py`.

New env vars
- `ENCRYPTION_KEY` — **required to enable the feature** (button stays hidden without it). Generate:
  `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
  Lose it and every saved token is unreadable, so back it up somewhere separate from the database.
- `MAX_CLONES` — total cap, default 50. One clone per user.

How it works
- Tokens are Fernet-encrypted in Mongo (`clones`); reply mappings in `clone_msgs` (30-day TTL), blocks in `clone_blocked`.
- Each clone runs as its own polling Application inside this process (started in `post_init`, stopped in `post_shutdown`).
- The user's token message is deleted right after it is read. Users can remove their clone anytime (**🗑️ Clone hatao**),
  which deletes the token and its data. They should also `/revoke` the token in BotFather.
- Bot owner: **Owner Panel -> 🤖 Contact Clones** lists every clone and can remove any of them (logged in `audit_log`).
- `httpx` logging is set to WARNING in `bot.py` because its INFO lines contain bot tokens.

Read before enabling
- **Webhook + sleeping host:** this bot uses a webhook, but clones use polling. On Render's free tier the service
  sleeps after ~15 min without HTTP traffic, and then **all clones stop receiving messages** until it wakes. Use an
  always-on plan (or an uptime pinger hitting the service URL) if clones matter.
- **Custody:** whoever has the server env (`ENCRYPTION_KEY`) and the database can decrypt every token. Users are told this.
- **Abuse:** a clone is a bot you host. People can use one to message strangers. You are the one who can remove it (see above).
- Polling dozens of bots in one process is fine; hundreds is not (needs webhooks or separate workers).
- Clones can't message their owner until the owner presses Start on the clone once (Telegram rule).

## Clone: auto-delete + delete for both sides
Set by the clone's owner **inside the clone bot** (owner only).
- `/autodelete` -> buttons (30s, 1m, 5m, 15m, 1h, 6h, 24h, 48h, Off), or type `/autodelete 45m` (`s`/`m`/`h`/`d`; min 10s, max 48h; `off` to stop).
- When ON, every message that clone handles is deleted after that time **in both chats**: header + copy of the user's message and the owner's own replies/notes (owner chat), the user's original message, the "✅ pahunch gaya" note and the owner's reply copy (user chat). Reply mappings in Mongo are removed with them. Users are told in `/start`.
- Turning it **Off** also cancels deletions that were still pending.
- The queue is in Mongo (`clone_autodel`) and one sweeper task works through it every `CLONE_AUTODEL_SWEEP_SECONDS`, so pending deletions survive restarts (unlike the in-memory timers in Audio Vault / Private Share). If the host is asleep, deletions happen when it wakes.
- Telegram only lets a bot delete messages **younger than 48h**, hence the 48h cap. A message that is already gone is simply skipped.

**Delete for both sides.** Telegram does not tell bots when someone deletes a message in a private chat, so deleting it by hand in your own chat cannot be detected. Use either:
- the **🗑 Dono taraf delete** button under "✅ Bhej diya", or
- `/del` as a reply to your sent message (or to the "✅ Bhej diya" note).

Both remove the copy from the user's chat first; only if that succeeds are your message and the note removed too. If the user's copy is older than 48h or already gone, you get an error and nothing else is touched. Mapping lives in `clone_sent` (47h TTL).
