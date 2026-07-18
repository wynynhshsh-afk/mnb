-- ============================================================================
-- اسکیمای کامل دیتابیس پروژه (Supabase / PostgreSQL)
-- این کوئری همه‌ی جدول‌هایی که کدِ پروژه (database_supabase.py) استفاده
-- می‌کنه رو می‌سازه. همه‌جا از IF NOT EXISTS استفاده شده، پس اجرای دوباره‌ش
-- روی دیتابیسی که از قبل بعضی جدول‌ها رو داره هم کاملاً بی‌خطره.
--
-- نکته درباره‌ی بازی میویی: نیازی به جدول جدید نداشت — چون از همون جدول
-- عمومیِ amel_settings (کلید/مقدار) استفاده می‌کنه که این‌جا هست.
--
-- نحوه‌ی استفاده: کل این فایل رو کپی کن و توی Supabase → SQL Editor → New
-- query پیست کن و Run بزن.
-- ============================================================================

-- ─── اکانت‌های پنل (لاگین/ثبت‌نام) ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS amel_accounts (
    id SERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    telegram_user_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ─── تنظیمات کلید/مقدار per-owner (شامل تنظیمات بازی میویی) ────────────────
CREATE TABLE IF NOT EXISTS amel_settings (
    owner_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (owner_id, key)
);

-- ─── توکن/سکه‌ها ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS amel_tokens (
    owner_id INTEGER PRIMARY KEY,
    balance INTEGER DEFAULT 0,
    last_daily TEXT,
    total_earned INTEGER DEFAULT 0
);
ALTER TABLE amel_tokens ADD COLUMN IF NOT EXISTS last_daily_ts BIGINT DEFAULT 0;

-- ─── رفرال‌ها ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS amel_referrals (
    id SERIAL PRIMARY KEY,
    referrer_owner_id INTEGER NOT NULL,
    referred_tg_id INTEGER NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ─── پیام‌های ذخیره‌شده (اسلات‌های ۱ تا ۱۰) ─────────────────────────────────
CREATE TABLE IF NOT EXISTS amel_saved_messages (
    owner_id INTEGER NOT NULL,
    slot INTEGER NOT NULL,
    content TEXT,
    media_path TEXT,
    saved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (owner_id, slot)
);

-- ─── پیام‌های زمان‌بندی‌شده ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS amel_scheduled_messages (
    id SERIAL PRIMARY KEY,
    owner_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    message TEXT NOT NULL,
    send_at TIMESTAMP NOT NULL,
    sent INTEGER DEFAULT 0
);

-- ─── پیام‌های حذف/ویرایش‌شده (نگهبان چت) ────────────────────────────────────
CREATE TABLE IF NOT EXISTS amel_deleted_messages (
    id SERIAL PRIMARY KEY,
    owner_id INTEGER NOT NULL,
    chat_id INTEGER,
    sender_id INTEGER,
    sender_name TEXT,
    message TEXT,
    media_type TEXT,
    deleted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ─── کانال‌های جوین اجباری ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS amel_forced_channels (
    id SERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ─── چالش‌های جام جهانی ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS worldcup_challenges (
    id SERIAL PRIMARY KEY,
    match_id TEXT UNIQUE,
    team1 TEXT NOT NULL,
    team2 TEXT NOT NULL,
    match_time TIMESTAMP NOT NULL,
    status TEXT DEFAULT 'pending',
    winner_option TEXT,
    channel_msg_id BIGINT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS challenge_participants (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    user_tg_id BIGINT NOT NULL,
    challenge_id INTEGER NOT NULL,
    selected_option TEXT NOT NULL,
    amount INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(user_id, challenge_id)
);

-- ─── شرط‌بندی‌ها ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS amel_bets (
    id SERIAL PRIMARY KEY,
    creator_id INTEGER NOT NULL,
    creator_tg_id BIGINT NOT NULL,
    opponent_id INTEGER,
    opponent_tg_id BIGINT,
    amount INTEGER NOT NULL,
    status TEXT DEFAULT 'waiting',
    winner_id INTEGER,
    winner_tg_id BIGINT,
    chat_id BIGINT,
    message_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS amel_bet_transactions (
    id SERIAL PRIMARY KEY,
    bet_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    type TEXT NOT NULL,
    amount INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ─── اشتراک/پرداخت/تنظیمات سراسری ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS amel_subscriptions (
    id SERIAL PRIMARY KEY,
    owner_id INTEGER NOT NULL UNIQUE,
    plan TEXT NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS amel_payments (
    id SERIAL PRIMARY KEY,
    owner_id INTEGER NOT NULL,
    tg_id BIGINT NOT NULL,
    type TEXT NOT NULL,
    plan TEXT,
    diamond_amount INTEGER,
    toman_amount INTEGER,
    status TEXT DEFAULT 'pending',
    receipt_file_id TEXT,
    admin_msg_id BIGINT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS amel_global_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- ─── ماموریت‌ها ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS amel_missions (
    id SERIAL PRIMARY KEY,
    channel_username TEXT NOT NULL UNIQUE,
    reward INTEGER NOT NULL DEFAULT 10,
    active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS amel_mission_completions (
    id SERIAL PRIMARY KEY,
    owner_id INTEGER NOT NULL,
    mission_id INTEGER NOT NULL,
    completed_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(owner_id, mission_id)
);

-- ─── ادمین‌های فرعی ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS amel_sub_admins (
    id SERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL UNIQUE,
    name TEXT,
    added_at TIMESTAMP DEFAULT NOW()
);
ALTER TABLE amel_sub_admins ADD COLUMN IF NOT EXISTS permissions TEXT DEFAULT '';

-- ============================================================================
-- پایان — بعد از اجرا، همه‌ی جدول‌های موردنیاز پروژه (از جمله amel_settings
-- که تنظیمات بازی میویی هم توش ذخیره می‌شه) آماده‌ان.
-- ============================================================================
