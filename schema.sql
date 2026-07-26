-- Run this ONCE against your Neon/Postgres DATABASE_URL before starting the bot.
-- Neon SQL editor: paste this whole file and run.
-- psql:  psql "$DATABASE_URL" -f schema.sql
--
-- Reverse-engineered directly from every INSERT/UPDATE/SELECT in database.py.
-- Nothing here was in the original zip — no schema file shipped with it,
-- which is why nothing was being stored: the tables never existed.

-- ========== USERS ==========
CREATE TABLE IF NOT EXISTS users (
    user_id                BIGINT PRIMARY KEY,
    username               TEXT,
    first_name             TEXT,
    created_at             TIMESTAMP NOT NULL DEFAULT NOW(),
    ai_requests_today      INTEGER NOT NULL DEFAULT 0,
    last_ai_request_date   TEXT,                      -- compared as string equality in code; keep as TEXT, not DATE
    is_banned              BOOLEAN NOT NULL DEFAULT FALSE,
    ai_limit               INTEGER,                   -- NULL = falls back to Config.DEFAULT_AI_LIMIT
    restricted_until       TIMESTAMP,
    copyright_warning_ack  BOOLEAN NOT NULL DEFAULT FALSE
);

-- ========== ADMINS ==========
CREATE TABLE IF NOT EXISTS admins (
    user_id     BIGINT PRIMARY KEY,
    added_by    BIGINT,
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);

-- ========== CHANNELS ==========
CREATE TABLE IF NOT EXISTS channels (
    channel_id     BIGINT PRIMARY KEY,
    channel_name   TEXT,
    invite_link    TEXT,
    added_by       BIGINT,
    auto_approve   BOOLEAN NOT NULL DEFAULT FALSE,
    created_at     TIMESTAMP NOT NULL DEFAULT NOW()
);

-- ========== AI USAGE LOG ==========
CREATE TABLE IF NOT EXISTS ai_usage (
    id          SERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL,
    prompt      TEXT,
    response    TEXT,
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ai_usage_user_id ON ai_usage(user_id);

-- ========== SCHEDULED MESSAGES ==========
CREATE TABLE IF NOT EXISTS scheduled_messages (
    id                 SERIAL PRIMARY KEY,
    user_id            BIGINT NOT NULL,
    target_type        TEXT,
    target_id          BIGINT,
    message_text       TEXT,
    media_type         TEXT,
    media_file_id      TEXT,
    media_caption      TEXT,
    reply_markup_json  TEXT,
    schedule_time      TIMESTAMP,
    status             TEXT NOT NULL DEFAULT 'pending',
    created_at         TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_scheduled_messages_status ON scheduled_messages(status);
CREATE INDEX IF NOT EXISTS idx_scheduled_messages_user_id ON scheduled_messages(user_id);

-- ========== JOIN REQUESTS (channel-level, admin-managed channels) ==========
CREATE TABLE IF NOT EXISTS join_requests (
    id          SERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL,
    channel_id  BIGINT NOT NULL,
    status      TEXT,
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_join_requests_user_channel ON join_requests(user_id, channel_id);

-- ========== USER CHANNELS (self-service, per-user auto-approve channels) ==========
CREATE TABLE IF NOT EXISTS user_channels (
    id             SERIAL PRIMARY KEY,
    user_id        BIGINT NOT NULL,
    channel_id     BIGINT NOT NULL UNIQUE,
    channel_name   TEXT,
    auto_approve   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_user_channels_user_id ON user_channels(user_id);

-- ========== BOT UPDATES ==========
CREATE TABLE IF NOT EXISTS bot_updates (
    id          SERIAL PRIMARY KEY,
    title       TEXT,
    message     TEXT,
    added_by    BIGINT,
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);

-- ========== COPYRIGHT: MEDIA LOG ==========
CREATE TABLE IF NOT EXISTS media_log (
    id             SERIAL PRIMARY KEY,
    user_id        BIGINT NOT NULL,
    file_id        TEXT,
    message_id     BIGINT,     -- references scheduled_messages.id (not FK-enforced, see database.py comment)
    media_type     TEXT,
    schedule_time  TIMESTAMP,
    upload_date    TIMESTAMP,
    strike_status  TEXT NOT NULL DEFAULT 'none',  -- 'none' | 'flagged' | 'removed' | 'infringing'
    created_at     TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_media_log_user_id ON media_log(user_id);
CREATE INDEX IF NOT EXISTS idx_media_log_message_id ON media_log(message_id);

-- ========== COPYRIGHT: REPORTS ==========
CREATE TABLE IF NOT EXISTS copyright_reports (
    id                     SERIAL PRIMARY KEY,
    reporter_id            BIGINT NOT NULL,
    reported_user_id       BIGINT NOT NULL,
    scheduled_message_id   BIGINT,
    reason                 TEXT,
    status                 TEXT NOT NULL DEFAULT 'open',
    created_at             TIMESTAMP NOT NULL DEFAULT NOW()
);

-- ========== COPYRIGHT: STRIKES ==========
CREATE TABLE IF NOT EXISTS copyright_strikes (
    id          SERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL,
    reason      TEXT,
    added_by    BIGINT,
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_copyright_strikes_user_id ON copyright_strikes(user_id);

-- ========== AUDIT LOG ==========
CREATE TABLE IF NOT EXISTS audit_log (
    id               SERIAL PRIMARY KEY,
    action_type      TEXT,
    actor_id         BIGINT,
    target_user_id   BIGINT,
    details          TEXT,
    created_at       TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_audit_log_target_user_id ON audit_log(target_user_id);
