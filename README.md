---
title: Telegram Multi-Feature Bot
emoji: 🤖
colorFrom: blue
colorTo: purple
sdk: docker
sdk_version: "20.10.0"
pinned: false
---

# 🤖 Telegram Multi-Feature Bot

## Features
- 🤖 **AI Chat** with Groq API (daily limit set by owner)
- ✅ **Auto-Approve** join requests for channels/groups
- 📅 **Message Scheduling** - Text, Photo, Video, Document, Audio, Voice, Video Note, Animation, Sticker, Location, Poll, Contact
- 📊 **QR Code Generator**
- 📢 **Channel Force Join** system
- 👤 **Owner/Admin** management panel
- 📊 **Bot Statistics**
- 📝 **Bot Updates** posting

## Bot Commands
- `/start` - Start bot and show main menu
- `/sidemenu` - Open side menu for quick access
- `/help` - Open help center

## Setup Instructions

### 1. Postgres Database Setup
This bot uses plain Postgres via `DATABASE_URL` (e.g. a free Neon project), not Supabase.
**Tables are created automatically on startup** — `database.py` reads `schema.sql` and runs it
(`CREATE TABLE IF NOT EXISTS ...`) every time the bot boots, so a fresh database will self-provision
on first deploy. If `schema.sql` is missing from the deploy or the auto-create fails for any reason
(e.g. the DB user lacks CREATE privileges), the bot will refuse to start and log the exact error —
it will NOT fall back to silently running against a half-missing schema.

You can still run it by hand if you prefer:
```bash
psql "$DATABASE_URL" -f schema.sql
```

### 2. Environment Variables
Copy `.env.example` to `.env` and fill in your credentials:

```env
BOT_TOKEN=your_telegram_bot_token
OWNER_ID=your_telegram_user_id
GROQ_API_KEY=your_groq_api_key
DATABASE_URL=postgresql://user:password@host/dbname?sslmode=require
```

### 3. Deploy on Render
1. Push this repo to GitHub.
2. On Render: **New +** → **Web Service** → connect the repo. Render auto-detects the `Dockerfile`.
3. Instance type: any (even free tier works, but free tier sleeps on inactivity — see note below).
4. Set the environment variables above in the Render dashboard (**Environment** tab). Do NOT set `PORT`, `RENDER_EXTERNAL_URL`, or `WEBHOOK_URL` — Render injects these automatically and `bot.py` picks them up.
5. Deploy. On boot, `bot.py` detects `RENDER_EXTERNAL_URL` and switches itself into webhook mode automatically (`run_webhook`, path `/telegram`). No manual `setWebhook` call needed.
6. If Render's health check fails on `/`, set the health check path to `/telegram` in the service settings (the app only serves that path, not root).

### 4. Keep Bot Alive
Render's free tier spins down after inactivity, same idea as before. Use UptimeRobot or a similar pinger against your Render URL if you're on the free tier.

## Important Notes
1. Bot ko har channel mein **Admin** banaein with "Approve Users" permission
2. Channel ID format: `-1001234567890`
3. For auto-approve, enable it from Owner Panel after adding channel
4. For media scheduling, bot must be admin in target channel/group
