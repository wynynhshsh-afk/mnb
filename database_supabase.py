# database_supabase.py
import os
import json
import hashlib
import datetime
import psycopg2
import psycopg2.extras
from typing import Optional, Dict, List, Any
from config import DATABASE_URL
import redis_cache as rc

# ─── اتصال به دیتابیس ──────────────────────────────────────────────────────────
import threading
_conn = None
_conn_lock = threading.Lock()

def get_conn():
    """دریافت اتصال به دیتابیس (thread-safe)"""
    global _conn
    if _conn is None or _conn.closed:
        _conn = psycopg2.connect(DATABASE_URL, sslmode='require', connect_timeout=10)
        _conn.autocommit = True
    return _conn

def execute_query(query: str, params: tuple = None, fetch_one: bool = False, fetch_all: bool = False):
    """
    اجرای کوئری با مدیریت خودکار اتصال.

    ⚠️ این کانکشن بین چند Thread مشترک است (درخواست‌های Flask، حلقه‌ی asyncio که
    همه‌ی سلف‌ها روش اجرا می‌شن، و Timer های پس‌زمینه). psycopg2 thread-safe نیست
    اگه همزمان از چند Thread روی یک کانکشن کوئری زده شود؛ بدون قفل، ممکنه کانکشن
    به‌هم بریزه و باعث خطاهای عجیب/قطعی‌های موقتی برای کاربرهای دیگه بشه.
    به همین خاطر کل عملیات با یک Lock سراسری محافظت می‌شود.
    """
    with _conn_lock:
        global _conn
        conn = get_conn()
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            try:
                cur.execute(query, params)
                if fetch_one:
                    return cur.fetchone()
                elif fetch_all:
                    return cur.fetchall()
                return cur.rowcount
            finally:
                cur.close()
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            # ✅ کانکشن خراب/قطع شده — آن را دور می‌ریزیم تا دفعه‌ی بعد یک
            # کانکشن تازه ساخته شود، به‌جای اینکه برای همیشه روی کانکشن
            # خراب گیر کنیم و همه‌ی کوئری‌های بعدی شکست بخورند
            print(f"❌ Database connection error (در حال بازسازی کانکشن): {e}")
            try:
                conn.close()
            except Exception:
                pass
            _conn = None
            raise
        except Exception as e:
            print(f"❌ Database error: {e}")
            raise

def _hash_pw(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

# ─── ایجاد جداول ──────────────────────────────────────────────────────────────
def init_tables():
    """ساخت جداول مورد نیاز در Supabase"""
    queries = [
        # جدول اکانت‌ها
        """
        CREATE TABLE IF NOT EXISTS amel_accounts (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            telegram_user_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        # جدول تنظیمات
        """
        CREATE TABLE IF NOT EXISTS amel_settings (
            owner_id INTEGER NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY (owner_id, key)
        )
        """,
        # جدول توکن‌ها
        """
        CREATE TABLE IF NOT EXISTS amel_tokens (
            owner_id INTEGER PRIMARY KEY,
            balance INTEGER DEFAULT 0,
            last_daily TEXT,
            total_earned INTEGER DEFAULT 0
        )
        """,
        # جدول رفرال‌ها
        """
        CREATE TABLE IF NOT EXISTS amel_referrals (
            id SERIAL PRIMARY KEY,
            referrer_owner_id INTEGER NOT NULL,
            referred_tg_id INTEGER NOT NULL UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        # جدول پیام‌های ذخیره‌شده
        """
        CREATE TABLE IF NOT EXISTS amel_saved_messages (
            owner_id INTEGER NOT NULL,
            slot INTEGER NOT NULL,
            content TEXT,
            media_path TEXT,
            saved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (owner_id, slot)
        )
        """,
        # جدول پیام‌های زمان‌بندی‌شده
        """
        CREATE TABLE IF NOT EXISTS amel_scheduled_messages (
            id SERIAL PRIMARY KEY,
            owner_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            send_at TIMESTAMP NOT NULL,
            sent INTEGER DEFAULT 0
        )
        """,
        # جدول پیام‌های حذف‌شده
        """
        CREATE TABLE IF NOT EXISTS amel_deleted_messages (
            id SERIAL PRIMARY KEY,
            owner_id INTEGER NOT NULL,
            chat_id INTEGER,
            sender_id INTEGER,
            sender_name TEXT,
            message TEXT,
            media_type TEXT,
            deleted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        # جدول چنل‌های اجباری (دائمی — قبلاً فقط توی SQLite محلی/موقت بود)
        """
        CREATE TABLE IF NOT EXISTS amel_forced_channels (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    ]
    
    for query in queries:
        try:
            execute_query(query)
        except Exception as e:
            print(f"❌ Error creating table: {e}")
    
    print("✅ جداول Supabase ایجاد/تأیید شدند!")

# ─── حساب‌ها ──────────────────────────────────────────────────────────────────
def create_account(username: str, password: str) -> Optional[int]:
    try:
        query = """
            INSERT INTO amel_accounts (username, password_hash, created_at)
            VALUES (%s, %s, %s)
            RETURNING id
        """
        result = execute_query(query, (username.strip(), _hash_pw(password), datetime.datetime.now().isoformat()), fetch_one=True)
        if result:
            print(f"✅ حساب کاربری {username} با ID {result['id']} ایجاد شد")
            return result['id']
        return None
    except psycopg2.IntegrityError:
        print(f"❌ خطا: کاربر با یوزرنیم {username} قبلاً ثبت شده است")
        return None
    except Exception as e:
        print(f"❌ create_account error: {e}")
        return None

def verify_account(username: str, password: str) -> Optional[int]:
    try:
        query = "SELECT id, password_hash FROM amel_accounts WHERE username = %s"
        result = execute_query(query, (username.strip(),), fetch_one=True)
        if result and result['password_hash'] == _hash_pw(password):
            print(f"✅ ورود موفق برای {username}")
            return result['id']
        print(f"❌ ورود ناموفق برای {username}")
        return None
    except Exception as e:
        print(f"❌ verify_account error: {e}")
        return None

def get_account(owner_id: int) -> Optional[Dict]:
    try:
        query = "SELECT id, username, telegram_user_id, created_at FROM amel_accounts WHERE id = %s"
        result = execute_query(query, (owner_id,), fetch_one=True)
        return dict(result) if result else None
    except Exception as e:
        print(f"❌ get_account error: {e}")
        return None

def get_account_by_username(username: str) -> Optional[Dict]:
    try:
        query = "SELECT id, username, telegram_user_id, created_at FROM amel_accounts WHERE username = %s"
        result = execute_query(query, (username.strip(),), fetch_one=True)
        return dict(result) if result else None
    except Exception as e:
        print(f"❌ get_account_by_username error: {e}")
        return None

def get_account_by_tg_id(tg_id: int) -> Optional[Dict]:
    try:
        query = "SELECT id, username, telegram_user_id, created_at FROM amel_accounts WHERE telegram_user_id = %s"
        result = execute_query(query, (tg_id,), fetch_one=True)
        return dict(result) if result else None
    except Exception as e:
        print(f"❌ get_account_by_tg_id error: {e}")
        return None

def get_all_accounts() -> List[Dict]:
    try:
        query = "SELECT id, username, telegram_user_id, created_at FROM amel_accounts ORDER BY created_at"
        result = execute_query(query, fetch_all=True)
        return [dict(r) for r in result] if result else []
    except Exception as e:
        print(f"❌ get_all_accounts error: {e}")
        return []

def account_exists() -> bool:
    try:
        query = "SELECT COUNT(*) as cnt FROM amel_accounts"
        result = execute_query(query, fetch_one=True)
        return result['cnt'] > 0 if result else False
    except Exception as e:
        print(f"❌ account_exists error: {e}")
        return False

def save_telegram_user_id(owner_id: int, tg_user_id: int):
    try:
        query = "UPDATE amel_accounts SET telegram_user_id = %s WHERE id = %s"
        execute_query(query, (tg_user_id, owner_id))
        print(f"✅ آیدی تلگرام {tg_user_id} برای کاربر {owner_id} ذخیره شد")
    except Exception as e:
        print(f"❌ save_telegram_user_id error: {e}")

def get_telegram_id_by_owner(owner_id: int) -> Optional[int]:
    try:
        query = "SELECT telegram_user_id FROM amel_accounts WHERE id = %s"
        result = execute_query(query, (owner_id,), fetch_one=True)
        return result['telegram_user_id'] if result else None
    except Exception as e:
        print(f"❌ get_telegram_id_by_owner error: {e}")
        return None

# ─── تنظیمات ──────────────────────────────────────────────────────────────────
SETTING_DEFAULTS = {
    "self_bot_active": "0",
    "secretary_active": "0",
    "anti_delete_active": "0",
    "anti_link_active": "0",
    "auto_seen_active": "0",
    "auto_reaction_active": "0",
    "private_lock_active": "0",
    "enemy_reply_active": "0",
    "auto_save_media": "0",
    "clock_name_active": "0",
    "clock_bio_active": "0",
    "selected_font": "0",
    "secretary_message": "در حال حاضر در دسترس نیستم.",
    "auto_reaction_emoji": "❤️",
    "spam_active": "0",
    "channel_save_active": "0",
    "spam_delay": "2",
    "session_data": "",
    "logged_in": "0",
    "session_started_at": "",
    # ─── بازی میویی (@MeowieeeQBot) ───
    "meowie_game_active": "0",
    "meowie_game_group_id": "",
    "meowie_next_meow_ts": "0",
    "meowie_next_fish_ts": "0",
    "meowie_last_meow_msg_id": "",
    "meowie_last_fish_msg_id": "",
}

# کش تنظیمات (RAM — fallback سریع)
_settings_cache = {}

def get_setting(owner_id: int, key: str, default=None) -> str:
    # ۱. کش RAM
    ram_key = f"{owner_id}:{key}"
    if ram_key in _settings_cache:
        return _settings_cache[ram_key]

    # ۲. کش Redis
    cached = rc.rget(rc.k_setting(owner_id, key))
    if cached is not None:
        _settings_cache[ram_key] = cached
        return cached

    # ۳. Supabase
    try:
        query = "SELECT value FROM amel_settings WHERE owner_id = %s AND key = %s"
        result = execute_query(query, (owner_id, key), fetch_one=True)
        if result:
            val = result['value']
        else:
            # ✅ ردیف واقعاً در دیتابیس وجود ندارد — این یک نتیجه‌ی قطعی است،
            # پس مقدار پیش‌فرض را با خاطر جمعی کش می‌کنیم
            val = str(SETTING_DEFAULTS.get(key, default) or "")
        _settings_cache[ram_key] = val
        rc.rset(rc.k_setting(owner_id, key), val, rc.TTL_SETTING)
        return val
    except Exception as e:
        # ⚠️ اینجا یک خطای موقتی دیتابیس/شبکه است، نه «نبود داده».
        # قبلاً این حالت هم با مقدار پیش‌فرض (خالی) کش می‌شد و چون کش RAM
        # هیچ‌وقت expire نمی‌شد، یک قطعی موقتی دیتابیس باعث می‌شد session_data
        # کاربر برای همیشه «خالی» در نظر گرفته شود و سلف تا لاگین مجدد دستی
        # دیگر هیچ‌وقت وصل نشود. به همین خاطر در خطا چیزی کش نمی‌کنیم تا
        # درخواست بعدی دوباره از دیتابیس بخواند.
        print(f"⚠️ get_setting خطای موقتی ({owner_id}, {key}): {e} — کش نشد")
        return str(default or "")

def set_setting(owner_id: int, key: str, value):
    try:
        check_query = "SELECT 1 FROM amel_settings WHERE owner_id = %s AND key = %s"
        exists = execute_query(check_query, (owner_id, key), fetch_one=True)

        if exists:
            query = "UPDATE amel_settings SET value = %s WHERE owner_id = %s AND key = %s"
            execute_query(query, (str(value), owner_id, key))
        else:
            query = "INSERT INTO amel_settings (owner_id, key, value) VALUES (%s, %s, %s)"
            execute_query(query, (owner_id, key, str(value)))

        str_val = str(value)
        _settings_cache[f"{owner_id}:{key}"] = str_val
        rc.rset(rc.k_setting(owner_id, key), str_val, rc.TTL_SETTING)
    except Exception as e:
        print(f"❌ set_setting error: {e}")

# ─── چنل‌های اجباری (دائمی — Supabase) ────────────────────────────────────────
_FORCED_CHANNELS_RKEY = "forced_channels:list"


def get_forced_channels() -> list:
    cached = rc.rget_json(_FORCED_CHANNELS_RKEY)
    if cached is not None:
        return cached
    try:
        rows = execute_query(
            "SELECT username FROM amel_forced_channels ORDER BY added_at DESC",
            fetch_all=True,
        ) or []
        result = [r["username"] for r in rows]
        rc.rset_json(_FORCED_CHANNELS_RKEY, result, rc.TTL_CHANNELS)
        return result
    except Exception as e:
        print(f"⚠️ get_forced_channels خطا: {e}")
        return []


def add_forced_channel(username: str) -> bool:
    if not username.startswith("@"):
        username = "@" + username
    try:
        execute_query(
            "INSERT INTO amel_forced_channels (username) VALUES (%s) ON CONFLICT (username) DO NOTHING",
            (username,),
        )
        rc.rdel(_FORCED_CHANNELS_RKEY)
        return True
    except Exception as e:
        print(f"❌ add_forced_channel خطا: {e}")
        return False


def remove_forced_channel(username: str) -> bool:
    if not username.startswith("@"):
        username = "@" + username
    try:
        execute_query(
            "DELETE FROM amel_forced_channels WHERE username = %s",
            (username,),
        )
        rc.rdel(_FORCED_CHANNELS_RKEY)
        return True
    except Exception as e:
        print(f"❌ remove_forced_channel خطا: {e}")
        return False


def toggle_setting(owner_id: int, key: str) -> bool:
    current = get_setting(owner_id, key, "0")
    new_val = "0" if current == "1" else "1"
    set_setting(owner_id, key, new_val)
    return new_val == "1"

def get_all_logged_in_users() -> List[int]:
    try:
        query = "SELECT owner_id FROM amel_settings WHERE key = 'logged_in' AND value = '1'"
        result = execute_query(query, fetch_all=True)
        return [r['owner_id'] for r in result] if result else []
    except Exception as e:
        print(f"❌ get_all_logged_in_users error: {e}")
        return []

def init_user_settings(owner_id: int):
    for key, value in SETTING_DEFAULTS.items():
        set_setting(owner_id, key, value)
    print(f"✅ تنظیمات کاربر {owner_id} مقداردهی شد")

# ─── توکن‌ها ──────────────────────────────────────────────────────────────────
def _init_tokens(owner_id: int):
    try:
        query = "INSERT INTO amel_tokens (owner_id, balance, total_earned) VALUES (%s, 0, 0) ON CONFLICT (owner_id) DO NOTHING"
        execute_query(query, (owner_id,))
    except Exception as e:
        print(f"❌ _init_tokens error: {e}")

def get_token_balance(owner_id: int) -> int:
    # کش Redis
    cached = rc.rget(rc.k_token(owner_id))
    if cached is not None:
        try:
            return int(cached)
        except Exception:
            pass
    try:
        query = "SELECT balance FROM amel_tokens WHERE owner_id = %s"
        result = execute_query(query, (owner_id,), fetch_one=True)
        if result:
            bal = result['balance']
            rc.rset(rc.k_token(owner_id), str(bal), rc.TTL_TOKEN)
            return bal
        _init_tokens(owner_id)
        return 0
    except Exception as e:
        print(f"❌ get_token_balance error: {e}")
        return 0

def add_tokens(owner_id: int, amount: int):
    try:
        _init_tokens(owner_id)
        query = "UPDATE amel_tokens SET balance = balance + %s, total_earned = total_earned + %s WHERE owner_id = %s"
        execute_query(query, (amount, amount, owner_id))
        rc.invalidate_token(owner_id)  # کش رو expire کن
    except Exception as e:
        print(f"❌ add_tokens error: {e}")

def deduct_tokens(owner_id: int, amount: int) -> bool:
    try:
        _init_tokens(owner_id)
        query = "SELECT balance FROM amel_tokens WHERE owner_id = %s"
        result = execute_query(query, (owner_id,), fetch_one=True)
        if not result or result['balance'] < amount:
            return False
        query = "UPDATE amel_tokens SET balance = balance - %s WHERE owner_id = %s"
        execute_query(query, (amount, owner_id))
        rc.invalidate_token(owner_id)  # کش رو expire کن
        return True
    except Exception as e:
        print(f"❌ deduct_tokens error: {e}")
        return False

def transfer_diamonds(from_owner_id: int, to_owner_id: int, amount: int) -> tuple:
    """انتقال الماس بین دو حساب. خروجی: (success: bool, message: str)"""
    try:
        if amount < 1:
            return False, "❌ مقدار باید بیشتر از 0 باشد."
        if from_owner_id == to_owner_id:
            return False, "❌ نمی‌توانید به خودتان الماس انتقال دهید."
        balance = get_token_balance(from_owner_id)
        if balance < amount:
            return False, f"❌ موجودی کافی ندارید! موجودی فعلی: {balance} الماس"
        if not deduct_tokens(from_owner_id, amount):
            return False, "❌ خطا در کسر الماس!"
        add_tokens(to_owner_id, amount)
        return True, f"✅ {amount} الماس با موفقیت منتقل شد."
    except Exception as e:
        print(f"❌ transfer_diamonds error: {e}")
        return False, f"❌ خطا: {e}"

def claim_daily_token(owner_id: int):
    import time as _time
    import config as _cfg
    COOLDOWN = 86400  # 24 ساعت
    DAILY_AMOUNT = 5  # روزانه ۵ الماس
    try:
        _init_tokens(owner_id)
        now_ts = int(_time.time())

        try:
            execute_query("ALTER TABLE amel_tokens ADD COLUMN IF NOT EXISTS last_daily_ts BIGINT DEFAULT 0")
        except Exception:
            pass

        result = execute_query("SELECT last_daily_ts FROM amel_tokens WHERE owner_id = %s", (owner_id,), fetch_one=True)
        last_ts = int(result["last_daily_ts"] or 0) if result else 0

        elapsed = now_ts - last_ts
        if elapsed < COOLDOWN:
            remaining = COOLDOWN - elapsed
            h = remaining // 3600
            m = (remaining % 3600) // 60
            return False, f"⏰ هدیه روزانه دریافت شد!\n\n🕐 تا هدیه بعدی: <b>{h} ساعت و {m} دقیقه</b> مانده."

        execute_query(
            "UPDATE amel_tokens SET balance = balance + %s, total_earned = total_earned + %s, last_daily_ts = %s WHERE owner_id = %s",
            (DAILY_AMOUNT, DAILY_AMOUNT, now_ts, owner_id)
        )
        return True, f"🎁 <b>{DAILY_AMOUNT} الماس</b> دریافت کردید!\n💎 فردا دوباره بیا!"
    except Exception as e:
        print(f"❌ claim_daily_token error: {e}")
        return False, "❌ خطا در دریافت هدیه"

def get_token_stats(owner_id: int) -> dict:
    try:
        _init_tokens(owner_id)
        query = "SELECT balance, last_daily, total_earned FROM amel_tokens WHERE owner_id = %s"
        result = execute_query(query, (owner_id,), fetch_one=True)
        if result:
            today = datetime.date.today().isoformat()
            return {
                "balance": result['balance'],
                "last_daily": result['last_daily'],
                "total_earned": result['total_earned'],
                "can_claim_daily": result['last_daily'] != today,
            }
    except Exception as e:
        print(f"❌ get_token_stats error: {e}")
    return {"balance": 0, "last_daily": None, "total_earned": 0, "can_claim_daily": True}

# ─── رفرال ──────────────────────────────────────────────────────────────────
def process_referral(referrer_owner_id: int, referred_tg_id: int) -> bool:
    from config import REFERRAL_TOKENS
    try:
        query = "SELECT 1 FROM amel_referrals WHERE referred_tg_id = %s"
        if execute_query(query, (referred_tg_id,), fetch_one=True):
            return False
        
        if not get_account(referrer_owner_id):
            return False
        
        query = "INSERT INTO amel_referrals (referrer_owner_id, referred_tg_id, created_at) VALUES (%s, %s, %s)"
        execute_query(query, (referrer_owner_id, referred_tg_id, datetime.datetime.now().isoformat()))
        add_tokens(referrer_owner_id, REFERRAL_TOKENS)
        return True
    except Exception as e:
        print(f"❌ process_referral error: {e}")
        return False

def get_referral_count(owner_id: int) -> int:
    try:
        query = "SELECT COUNT(*) as cnt FROM amel_referrals WHERE referrer_owner_id = %s"
        result = execute_query(query, (owner_id,), fetch_one=True)
        return result['cnt'] if result else 0
    except Exception as e:
        print(f"❌ get_referral_count error: {e}")
        return 0

# ─── ⚠️ توابع دشمن و دوست به دیتابیس کش منتقل شدند ⚠️ ──────────────────────
# این توابع دیگر در Supabase ذخیره نمی‌شوند و به db_cache منتقل شده‌اند
# برای استفاده از آنها، از فایل database.py استفاده کنید که به db_cache متصل است

# ─── پیام‌های ذخیره‌شده ──────────────────────────────────────────────────
def save_message_slot(owner_id: int, slot: int, content, media_path=None):
    try:
        query = """
            INSERT INTO amel_saved_messages (owner_id, slot, content, media_path, saved_at) 
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (owner_id, slot) DO UPDATE SET content = EXCLUDED.content, media_path = EXCLUDED.media_path, saved_at = EXCLUDED.saved_at
        """
        execute_query(query, (owner_id, slot, content, media_path, datetime.datetime.now().isoformat()))
    except Exception as e:
        print(f"❌ save_message_slot error: {e}")

def get_message_slot(owner_id: int, slot: int):
    try:
        query = "SELECT * FROM amel_saved_messages WHERE owner_id = %s AND slot = %s"
        result = execute_query(query, (owner_id, slot), fetch_one=True)
        return dict(result) if result else None
    except Exception as e:
        print(f"❌ get_message_slot error: {e}")
        return None

# ─── پیام‌های زمان‌بندی‌شده ──────────────────────────────────────────────
def add_scheduled_message(owner_id: int, chat_id, message, send_at):
    try:
        query = """
            INSERT INTO amel_scheduled_messages (owner_id, chat_id, message, send_at, sent) 
            VALUES (%s, %s, %s, %s, 0)
            RETURNING id
        """
        result = execute_query(query, (owner_id, chat_id, message, send_at), fetch_one=True)
        return result['id'] if result else None
    except Exception as e:
        print(f"❌ add_scheduled_message error: {e}")
        return None

def get_pending_scheduled(owner_id: int):
    try:
        query = """
            SELECT * FROM amel_scheduled_messages 
            WHERE owner_id = %s AND sent = 0 AND send_at <= %s 
            ORDER BY send_at
        """
        now = datetime.datetime.now().isoformat()
        result = execute_query(query, (owner_id, now), fetch_all=True)
        return [dict(r) for r in result] if result else []
    except Exception as e:
        print(f"❌ get_pending_scheduled error: {e}")
        return []

def mark_scheduled_sent(msg_id: int):
    try:
        query = "UPDATE amel_scheduled_messages SET sent = 1 WHERE id = %s"
        execute_query(query, (msg_id,))
    except Exception as e:
        print(f"❌ mark_scheduled_sent error: {e}")

# ─── پیام‌های حذف‌شده ────────────────────────────────────────────────────
def log_deleted_message(owner_id: int, chat_id, sender_id, sender_name, message, media_type=None):
    try:
        query = """
            INSERT INTO amel_deleted_messages (owner_id, chat_id, sender_id, sender_name, message, media_type, deleted_at) 
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        execute_query(query, (owner_id, chat_id, sender_id, sender_name, message, media_type, datetime.datetime.now().isoformat()))
    except Exception as e:
        print(f"❌ log_deleted_message error: {e}")

def get_deleted_messages(owner_id: int, limit=50):
    try:
        query = """
            SELECT * FROM amel_deleted_messages 
            WHERE owner_id = %s 
            ORDER BY deleted_at DESC 
            LIMIT %s
        """
        result = execute_query(query, (owner_id, limit), fetch_all=True)
        return [dict(r) for r in result] if result else []
    except Exception as e:
        print(f"❌ get_deleted_messages error: {e}")
        return []

# ─── سیستم چالش جام جهانی (ارتقا‌یافته با football-data.org) ──────────────────

def init_world_cup_tables():
    queries = [
        """
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
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS challenge_participants (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            user_tg_id BIGINT NOT NULL,
            challenge_id INTEGER NOT NULL,
            selected_option TEXT NOT NULL,
            amount INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(user_id, challenge_id)
        )
        """
    ]
    for q in queries:
        try:
            execute_query(q)
        except Exception as e:
            print(f"❌ init_world_cup_tables error: {e}")
    print("✅ جداول جام جهانی ایجاد/تأیید شدند!")


def wc_challenge_exists(match_id: str) -> bool:
    try:
        r = execute_query("SELECT id FROM worldcup_challenges WHERE match_id=%s", (match_id,), fetch_one=True)
        return r is not None
    except Exception:
        return False


def create_wc_challenge(match_id: str, team1: str, team2: str, match_time) -> Optional[int]:
    try:
        r = execute_query(
            """INSERT INTO worldcup_challenges (match_id, team1, team2, match_time, status)
               VALUES (%s, %s, %s, %s, 'pending') RETURNING id""",
            (match_id, team1, team2, match_time), fetch_one=True
        )
        return r["id"] if r else None
    except Exception as e:
        print(f"❌ create_wc_challenge error: {e}")
        return None


def set_wc_channel_msg(challenge_id: int, msg_id: int):
    try:
        execute_query("UPDATE worldcup_challenges SET channel_msg_id=%s WHERE id=%s", (msg_id, challenge_id))
    except Exception as e:
        print(f"❌ set_wc_channel_msg error: {e}")


def get_wc_challenge(challenge_id: int) -> Optional[Dict]:
    try:
        r = execute_query("SELECT * FROM worldcup_challenges WHERE id=%s", (challenge_id,), fetch_one=True)
        return dict(r) if r else None
    except Exception as e:
        print(f"❌ get_wc_challenge error: {e}")
        return None


def get_pending_wc_challenges() -> list:
    try:
        rows = execute_query("SELECT * FROM worldcup_challenges WHERE status='pending'", fetch_all=True)
        return [dict(r) for r in rows] if rows else []
    except Exception as e:
        print(f"❌ get_pending_wc_challenges error: {e}")
        return []


def join_wc_challenge(challenge_id: int, user_id: int, user_tg_id: int,
                      selected_option: str, amount: int) -> tuple:
    try:
        ch = get_wc_challenge(challenge_id)
        if not ch:
            return False, "❌ چالش یافت نشد."
        if ch["status"] != "pending":
            return False, "❌ این چالش دیگر فعال نیست."
        dup = execute_query(
            "SELECT id FROM challenge_participants WHERE user_id=%s AND challenge_id=%s",
            (user_id, challenge_id), fetch_one=True
        )
        if dup:
            return False, "❌ شما قبلاً در این چالش شرکت کرده‌اید."
        balance = get_token_balance(user_id)
        if balance < amount:
            return False, f"❌ موجودی کافی ندارید!\nنیاز: {amount} — موجودی: {balance} الماس"
        if not deduct_tokens(user_id, amount):
            return False, "❌ خطا در کسر موجودی."
        execute_query(
            """INSERT INTO challenge_participants
               (user_id, user_tg_id, challenge_id, selected_option, amount)
               VALUES (%s, %s, %s, %s, %s)""",
            (user_id, user_tg_id, challenge_id, selected_option, amount)
        )
        return True, "✅ شرط شما ثبت شد."
    except Exception as e:
        print(f"❌ join_wc_challenge error: {e}")
        return False, f"❌ خطا: {e}"


def finish_wc_challenge(challenge_id: int, winner_option: str) -> list:
    paid = []
    try:
        execute_query(
            "UPDATE worldcup_challenges SET status='finished', winner_option=%s WHERE id=%s",
            (winner_option, challenge_id)
        )
        winners = execute_query(
            "SELECT * FROM challenge_participants WHERE challenge_id=%s AND selected_option=%s",
            (challenge_id, winner_option), fetch_all=True
        )
        if winners:
            for w in winners:
                payout = w["amount"] * 2
                add_tokens(w["user_id"], payout)
                paid.append({"user_tg_id": w["user_tg_id"], "payout": payout})
    except Exception as e:
        print(f"❌ finish_wc_challenge error: {e}")
    return paid


# ── سازگاری با کد قدیمی ─────────────────────────────────────────────────────
def create_world_cup_challenge(team1: str, team2: str, match_time: str, bet_amount: int) -> Optional[int]:
    import hashlib
    fake_id = hashlib.md5(f"{team1}{team2}{match_time}".encode()).hexdigest()[:12]
    return create_wc_challenge(fake_id, team1, team2, match_time)

def update_challenge_message(challenge_id: int, message_id: int, chat_id: int):
    set_wc_channel_msg(challenge_id, message_id)

def join_world_cup_challenge(challenge_id: int, user_id: int, user_tg_id: int, chosen_team: str, amount: int):
    return join_wc_challenge(challenge_id, user_id, user_tg_id, chosen_team, amount)

def finish_world_cup_challenge(challenge_id: int, winner_team: str):
    finish_wc_challenge(challenge_id, winner_team)


# ─── سیستم شرط‌بندی ───────────────────────────────────────────────────────────

def init_bet_tables():
    """ساخت جداول سیستم شرط‌بندی"""
    queries = [
        """
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
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS amel_bet_transactions (
            id SERIAL PRIMARY KEY,
            bet_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            type TEXT NOT NULL,
            amount INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    ]
    for query in queries:
        try:
            execute_query(query)
        except Exception as e:
            print(f"❌ Error creating bet table: {e}")
    print("✅ جداول شرط‌بندی ایجاد/تأیید شدند!")


def create_bet(creator_id: int, creator_tg_id: int, amount: int, chat_id: int) -> Optional[int]:
    """ساخت شرط‌بندی جدید — موجودی سازنده کسر می‌شود"""
    try:
        query = """
            INSERT INTO amel_bets (creator_id, creator_tg_id, amount, status, chat_id)
            VALUES (%s, %s, %s, 'waiting', %s)
            RETURNING id
        """
        result = execute_query(query, (creator_id, creator_tg_id, amount, chat_id), fetch_one=True)
        if not result:
            return None
        bet_id = result["id"]
        # کسر موجودی از سازنده
        deduct_tokens(creator_id, amount)
        # ثبت تراکنش
        execute_query(
            "INSERT INTO amel_bet_transactions (bet_id, user_id, type, amount) VALUES (%s, %s, 'entry', %s)",
            (bet_id, creator_id, amount)
        )
        return bet_id
    except Exception as e:
        print(f"❌ create_bet error: {e}")
        return None


def get_bet(bet_id: int) -> Optional[Dict]:
    """دریافت اطلاعات یک شرط"""
    try:
        result = execute_query(
            "SELECT * FROM amel_bets WHERE id = %s",
            (bet_id,), fetch_one=True
        )
        return dict(result) if result else None
    except Exception as e:
        print(f"❌ get_bet error: {e}")
        return None


def update_bet_message(bet_id: int, message_id: int):
    """ذخیره message_id پیام شرط"""
    try:
        execute_query(
            "UPDATE amel_bets SET message_id = %s WHERE id = %s",
            (message_id, bet_id)
        )
    except Exception as e:
        print(f"❌ update_bet_message error: {e}")


def join_bet(bet_id: int, opponent_id: int, opponent_tg_id: int) -> tuple:
    """ورود نفر دوم به شرط‌بندی"""
    try:
        bet = get_bet(bet_id)
        if not bet:
            return False, "❌ شرط‌بندی یافت نشد."
        if bet["status"] != "waiting":
            return False, "❌ این شرط‌بندی دیگر فعال نیست."
        if bet["creator_tg_id"] == opponent_tg_id:
            return False, "❌ شما سازنده این شرط هستید!"
        if bet["opponent_tg_id"] is not None:
            return False, "❌ این شرط قبلاً تکمیل شده است."

        # بررسی موجودی نفر دوم
        balance = get_token_balance(opponent_id)
        if balance < bet["amount"]:
            return False, f"❌ موجودی کافی ندارید!\nنیاز: {bet['amount']} الماس — موجودی: {balance}"

        # کسر موجودی از نفر دوم
        if not deduct_tokens(opponent_id, bet["amount"]):
            return False, "❌ خطا در کسر موجودی."

        # آپدیت وضعیت شرط
        execute_query(
            """UPDATE amel_bets
               SET opponent_id=%s, opponent_tg_id=%s, status='active'
               WHERE id=%s""",
            (opponent_id, opponent_tg_id, bet_id)
        )
        # ثبت تراکنش ورود نفر دوم
        execute_query(
            "INSERT INTO amel_bet_transactions (bet_id, user_id, type, amount) VALUES (%s, %s, 'entry', %s)",
            (bet_id, opponent_id, bet["amount"])
        )
        return True, "✅ ورود موفق"
    except Exception as e:
        print(f"❌ join_bet error: {e}")
        return False, f"❌ خطا: {e}"


def finish_bet(bet_id: int) -> tuple:
    """اجرای شرط‌بندی، انتخاب برنده، واریز جایزه"""
    import random as _random
    TAX_RATE = 0.17
    try:
        bet = get_bet(bet_id)
        if not bet or bet["status"] != "active":
            return False, None, 0

        total = bet["amount"] * 2
        tax = round(total * TAX_RATE)
        payout = total - tax

        # انتخاب تصادفی برنده
        candidates = [
            {"owner_id": bet["creator_id"],  "tg_id": bet["creator_tg_id"]},
            {"owner_id": bet["opponent_id"], "tg_id": bet["opponent_tg_id"]},
        ]
        winner = _random.choice(candidates)
        loser  = [c for c in candidates if c["tg_id"] != winner["tg_id"]][0]

        # واریز به برنده
        add_tokens(winner["owner_id"], payout)

        # آپدیت جدول
        execute_query(
            """UPDATE amel_bets
               SET status='finished', winner_id=%s, winner_tg_id=%s, finished_at=CURRENT_TIMESTAMP
               WHERE id=%s""",
            (winner["owner_id"], winner["tg_id"], bet_id)
        )

        # ثبت تراکنش برنده
        execute_query(
            "INSERT INTO amel_bet_transactions (bet_id, user_id, type, amount) VALUES (%s, %s, 'win', %s)",
            (bet_id, winner["owner_id"], payout)
        )
        # ثبت تراکنش بازنده
        execute_query(
            "INSERT INTO amel_bet_transactions (bet_id, user_id, type, amount) VALUES (%s, %s, 'loss', %s)",
            (bet_id, loser["owner_id"], bet["amount"])
        )

        return True, winner, payout
    except Exception as e:
        print(f"❌ finish_bet error: {e}")
        return False, None, 0


def cancel_bet(bet_id: int):
    """لغو شرط و بازگشت موجودی سازنده"""
    try:
        bet = get_bet(bet_id)
        if not bet or bet["status"] != "waiting":
            return
        add_tokens(bet["creator_id"], bet["amount"])
        execute_query(
            "UPDATE amel_bets SET status='cancelled' WHERE id=%s",
            (bet_id,)
        )
    except Exception as e:
        print(f"❌ cancel_bet error: {e}")


# ─── سیستم خرید و اشتراک ─────────────────────────────────────────────────────

def init_purchase_tables():
    queries = [
        """
        CREATE TABLE IF NOT EXISTS amel_subscriptions (
            id SERIAL PRIMARY KEY,
            owner_id INTEGER NOT NULL UNIQUE,
            plan TEXT NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        )
        """,
        """
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
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS amel_global_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    ]
    for q in queries:
        try:
            execute_query(q)
        except Exception as e:
            print(f"❌ init_purchase_tables error: {e}")
    print("✅ جداول خرید و اشتراک ایجاد/تأیید شدند!")


def get_global_setting(key: str, default: str = "") -> str:
    try:
        r = execute_query("SELECT value FROM amel_global_settings WHERE key=%s", (key,), fetch_one=True)
        return r["value"] if r else default
    except Exception:
        return default


def set_global_setting(key: str, value: str):
    try:
        execute_query(
            "INSERT INTO amel_global_settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value=%s",
            (key, value, value)
        )
    except Exception as e:
        print(f"❌ set_global_setting error: {e}")


def _tehran_now():
    """زمان فعلی به وقت تهران"""
    IRAN_TZ = datetime.timezone(datetime.timedelta(hours=3, minutes=30))
    return datetime.datetime.now(IRAN_TZ).replace(tzinfo=None)


def get_subscription(owner_id: int) -> Optional[Dict]:
    # کش Redis
    cached = rc.rget_json(rc.k_subscribe(owner_id))
    if cached is not None:
        return cached
    try:
        r = execute_query("SELECT * FROM amel_subscriptions WHERE owner_id=%s", (owner_id,), fetch_one=True)
        result = dict(r) if r else None
        if result:
            # تبدیل datetime به string برای JSON
            if isinstance(result.get("expires_at"), datetime.datetime):
                result["expires_at"] = result["expires_at"].isoformat()
            if isinstance(result.get("created_at"), datetime.datetime):
                result["created_at"] = result["created_at"].isoformat()
        rc.rset_json(rc.k_subscribe(owner_id), result, rc.TTL_SUBSCRIBE)
        return result
    except Exception as e:
        print(f"❌ get_subscription error: {e}")
        return None


def set_subscription(owner_id: int, plan: str, days: int):
    try:
        now_teh = _tehran_now()
        existing = get_subscription(owner_id)
        if existing and existing["expires_at"]:
            try:
                exp = existing["expires_at"]
                if isinstance(exp, str):
                    exp = datetime.datetime.fromisoformat(exp)
                if hasattr(exp, 'tzinfo') and exp.tzinfo:
                    exp = exp.replace(tzinfo=None)
                base = max(exp, now_teh)
            except Exception:
                base = now_teh
        else:
            base = now_teh
        expires = base + datetime.timedelta(days=days)
        execute_query(
            """INSERT INTO amel_subscriptions (owner_id, plan, expires_at)
               VALUES (%s, %s, %s)
               ON CONFLICT (owner_id) DO UPDATE SET plan=%s, expires_at=%s""",
            (owner_id, plan, expires, plan, expires)
        )
        rc.invalidate_subscribe(owner_id)  # کش اشتراک رو پاک کن
        return expires
    except Exception as e:
        print(f"❌ set_subscription error: {e}")
        return None


def is_subscribed(owner_id: int) -> bool:
    sub = get_subscription(owner_id)
    if not sub:
        return False
    try:
        exp = sub["expires_at"]
        if isinstance(exp, str):
            exp = datetime.datetime.fromisoformat(exp)
        if hasattr(exp, 'tzinfo') and exp.tzinfo:
            exp = exp.replace(tzinfo=None)
        return exp > _tehran_now()
    except Exception:
        return False


def transfer_subscription(from_owner_id: int, to_owner_id: int) -> tuple:
    """انتقال باقی‌مانده‌ی اشتراک از یک حساب به حساب دیگر. خروجی: (success: bool, message: str)"""
    try:
        if from_owner_id == to_owner_id:
            return False, "❌ نمی‌توانید اشتراک را به خودتان انتقال دهید."

        sub = get_subscription(from_owner_id)
        if not sub:
            return False, "❌ شما اشتراک فعالی برای انتقال ندارید."

        now_teh = _tehran_now()
        exp = sub["expires_at"]
        if isinstance(exp, str):
            exp = datetime.datetime.fromisoformat(exp)
        if hasattr(exp, 'tzinfo') and exp.tzinfo:
            exp = exp.replace(tzinfo=None)

        if exp <= now_teh:
            return False, "❌ اشتراک شما منقضی شده و قابل انتقال نیست."

        remaining_seconds = (exp - now_teh).total_seconds()
        remaining_days = int(remaining_seconds // 86400)
        if remaining_seconds % 86400 > 0:
            remaining_days += 1
        remaining_days = max(1, remaining_days)

        # اشتراک رو از حساب فرستنده حذف می‌کنیم
        execute_query("DELETE FROM amel_subscriptions WHERE owner_id=%s", (from_owner_id,))
        rc.invalidate_subscribe(from_owner_id)

        # همون باقی‌مانده رو به حساب گیرنده اضافه می‌کنیم (اگه گیرنده هم
        # اشتراک فعال داشته باشه، set_subscription خودش روزها رو بهش اضافه می‌کنه)
        set_subscription(to_owner_id, sub["plan"], remaining_days)

        return True, f"✅ اشتراک با {remaining_days} روز باقی‌مانده با موفقیت منتقل شد."
    except Exception as e:
        print(f"❌ transfer_subscription error: {e}")
        return False, f"❌ خطا: {e}"


def give_free_trial(owner_id: int) -> bool:
    """یک روز سلف رایگان برای کاربر تازه‌وارد"""
    try:
        existing = get_subscription(owner_id)
        if existing:
            return False  # قبلاً اشتراک داشته
        set_subscription(owner_id, "trial", 1)
        return True
    except Exception as e:
        print(f"❌ give_free_trial error: {e}")
        return False


def get_expiring_soon_subscriptions(hours: int = 2) -> list:
    """اشتراک‌هایی که در X ساعت آینده منقضی می‌شوند (برای اطلاع‌رسانی)"""
    try:
        now = _tehran_now()
        limit = now + datetime.timedelta(hours=hours)
        rows = execute_query(
            """SELECT s.*, a.id as acc_id
               FROM amel_subscriptions s
               JOIN amel_accounts a ON a.id = s.owner_id
               WHERE s.expires_at BETWEEN %s AND %s
               AND s.status_notified IS DISTINCT FROM 'expiring'""",
            (now, limit), fetch_all=True
        )
        return [dict(r) for r in rows] if rows else []
    except Exception:
        return []


def get_expired_subscriptions() -> list:
    """اشتراک‌هایی که تازه منقضی شده‌اند"""
    try:
        now = _tehran_now()
        past = now - datetime.timedelta(hours=1)
        rows = execute_query(
            """SELECT * FROM amel_subscriptions
               WHERE expires_at BETWEEN %s AND %s
               AND status_notified IS DISTINCT FROM 'expired'""",
            (past, now), fetch_all=True
        )
        return [dict(r) for r in rows] if rows else []
    except Exception:
        return []


def mark_subscription_notified(owner_id: int, status: str):
    try:
        execute_query(
            "UPDATE amel_subscriptions SET status_notified=%s WHERE owner_id=%s",
            (status, owner_id)
        )
    except Exception as e:
        print(f"❌ mark_subscription_notified error: {e}")


def create_payment(owner_id: int, tg_id: int, ptype: str,
                   plan: str = None, diamond_amount: int = None,
                   toman_amount: int = None) -> Optional[int]:
    try:
        r = execute_query(
            """INSERT INTO amel_payments
               (owner_id, tg_id, type, plan, diamond_amount, toman_amount, status)
               VALUES (%s, %s, %s, %s, %s, %s, 'pending') RETURNING id""",
            (owner_id, tg_id, ptype, plan, diamond_amount, toman_amount), fetch_one=True
        )
        return r["id"] if r else None
    except Exception as e:
        print(f"❌ create_payment error: {e}")
        return None


def update_payment(payment_id: int, **kwargs):
    try:
        sets = ", ".join(f"{k}=%s" for k in kwargs)
        vals = list(kwargs.values()) + [payment_id]
        execute_query(f"UPDATE amel_payments SET {sets} WHERE id=%s", vals)
    except Exception as e:
        print(f"❌ update_payment error: {e}")


def get_payment(payment_id: int) -> Optional[Dict]:
    try:
        r = execute_query("SELECT * FROM amel_payments WHERE id=%s", (payment_id,), fetch_one=True)
        return dict(r) if r else None
    except Exception as e:
        print(f"❌ get_payment error: {e}")
        return None


def get_pending_payments() -> list:
    try:
        rows = execute_query(
            "SELECT * FROM amel_payments WHERE status='pending' ORDER BY created_at DESC",
            fetch_all=True
        )
        return [dict(r) for r in rows] if rows else []
    except Exception as e:
        print(f"❌ get_pending_payments error: {e}")
        return []


# ─── سیستم ماموریت‌ها ────────────────────────────────────────────────────────

def init_mission_tables():
    queries = [
        """
        CREATE TABLE IF NOT EXISTS amel_missions (
            id SERIAL PRIMARY KEY,
            channel_username TEXT NOT NULL UNIQUE,
            reward INTEGER NOT NULL DEFAULT 10,
            active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS amel_mission_completions (
            id SERIAL PRIMARY KEY,
            owner_id INTEGER NOT NULL,
            mission_id INTEGER NOT NULL,
            completed_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(owner_id, mission_id)
        )
        """,
    ]
    for q in queries:
        try:
            execute_query(q)
        except Exception as e:
            print(f"❌ init_mission_tables error: {e}")


def get_active_missions() -> list:
    try:
        rows = execute_query(
            "SELECT * FROM amel_missions WHERE active = TRUE ORDER BY created_at",
            fetch_all=True
        )
        return [dict(r) for r in rows] if rows else []
    except Exception as e:
        print(f"❌ get_active_missions error: {e}")
        return []


def add_mission(channel_username: str, reward: int) -> bool:
    if not channel_username.startswith("@"):
        channel_username = "@" + channel_username
    try:
        execute_query(
            "INSERT INTO amel_missions (channel_username, reward) VALUES (%s, %s) ON CONFLICT (channel_username) DO UPDATE SET active=TRUE, reward=%s",
            (channel_username, reward, reward)
        )
        return True
    except Exception as e:
        print(f"❌ add_mission error: {e}")
        return False


def remove_mission(mission_id: int) -> bool:
    try:
        execute_query("UPDATE amel_missions SET active=FALSE WHERE id=%s", (mission_id,))
        return True
    except Exception as e:
        print(f"❌ remove_mission error: {e}")
        return False


def get_completed_mission_ids(owner_id: int) -> list:
    try:
        rows = execute_query(
            "SELECT mission_id FROM amel_mission_completions WHERE owner_id=%s",
            (owner_id,), fetch_all=True
        )
        return [r["mission_id"] for r in rows] if rows else []
    except Exception as e:
        print(f"❌ get_completed_mission_ids error: {e}")
        return []


def complete_mission(owner_id: int, mission_id: int, reward: int) -> bool:
    """ثبت انجام ماموریت و اضافه کردن جایزه"""
    try:
        r = execute_query(
            "INSERT INTO amel_mission_completions (owner_id, mission_id) VALUES (%s, %s) ON CONFLICT DO NOTHING RETURNING id",
            (owner_id, mission_id), fetch_one=True
        )
        if r:
            add_tokens(owner_id, reward)
            return True
        return False
    except Exception as e:
        print(f"❌ complete_mission error: {e}")
        return False


def get_all_telegram_ids() -> list:
    """دریافت آیدی تلگرام همه کاربران ثبت‌شده"""
    try:
        rows = execute_query(
            "SELECT telegram_user_id FROM amel_accounts WHERE telegram_user_id IS NOT NULL",
            fetch_all=True
        )
        return [r["telegram_user_id"] for r in rows] if rows else []
    except Exception as e:
        print(f"❌ get_all_telegram_ids error: {e}")
        return []


def get_wc_participants() -> list:
    """دریافت لیست کاربران شرکت‌کننده در جام جهانی"""
    try:
        rows = execute_query(
            """SELECT DISTINCT a.username, a.telegram_user_id, a.created_at,
                      COUNT(cp.id) as bet_count, SUM(cp.amount) as total_bet
               FROM challenge_participants cp
               JOIN amel_accounts a ON a.id = cp.user_id
               GROUP BY a.id, a.username, a.telegram_user_id, a.created_at
               ORDER BY bet_count DESC""",
            fetch_all=True
        )
        return [dict(r) for r in rows] if rows else []
    except Exception as e:
        print(f"❌ get_wc_participants error: {e}")
        return []


# ─── سیستم ادمین‌های فرعی ────────────────────────────────────────────────────

def init_admin_tables():
    q = """
        CREATE TABLE IF NOT EXISTS amel_sub_admins (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT NOT NULL UNIQUE,
            name TEXT,
            added_at TIMESTAMP DEFAULT NOW()
        )
    """
    try:
        execute_query(q)
    except Exception as e:
        print(f"❌ init_admin_tables error: {e}")


def add_sub_admin(telegram_id: int, name: str = "") -> bool:
    try:
        execute_query(
            "INSERT INTO amel_sub_admins (telegram_id, name) VALUES (%s, %s) ON CONFLICT (telegram_id) DO NOTHING",
            (telegram_id, name)
        )
        return True
    except Exception as e:
        print(f"❌ add_sub_admin error: {e}")
        return False


def remove_sub_admin(telegram_id: int) -> bool:
    try:
        execute_query("DELETE FROM amel_sub_admins WHERE telegram_id=%s", (telegram_id,))
        return True
    except Exception as e:
        print(f"❌ remove_sub_admin error: {e}")
        return False


def get_sub_admins() -> list:
    try:
        rows = execute_query("SELECT * FROM amel_sub_admins ORDER BY added_at DESC", fetch_all=True)
        return [dict(r) for r in rows] if rows else []
    except Exception as e:
        print(f"❌ get_sub_admins error: {e}")
        return []


def is_sub_admin(telegram_id: int) -> bool:
    try:
        r = execute_query("SELECT id FROM amel_sub_admins WHERE telegram_id=%s", (telegram_id,), fetch_one=True)
        return r is not None
    except Exception:
        return False


# ─── مقداردهی اولیه (کامل) ───────────────────────────────────────────────────
try:
    init_tables()
    init_world_cup_tables()
    init_bet_tables()
    init_purchase_tables()
    init_mission_tables()
    init_admin_tables()
except Exception as e:
    print(f"❌ خطا در ایجاد جداول: {e}")

print("✅ database_supabase.py بارگذاری شد!")


# ─── توابع جدید دسترسی ادمین‌های فرعی ────────────────────────────────────────

ADMIN_PERMISSIONS = [
    ("channels", "📢 چنل‌های اجباری"),
    ("users", "👥 کاربران"),
    ("wc", "🏆 جام جهانی"),
    ("today_games", "📅 بازی‌های امروز"),
    ("transfer", "💎 انتقال الماس"),
    ("give", "💰 دادن الماس"),
    ("set_card", "💳 تنظیم شماره کارت"),
    ("payments", "🧾 پرداخت‌های معلق"),
    ("broadcast", "📣 پیام عمومی"),
    ("missions", "🎯 ماموریت‌ها"),
    ("wc_participants", "👥 شرکت‌کنندگان جام"),
    ("gift", "🎁 هدیه"),
    ("guide_manage", "📚 مدیریت راهنما"),
    ("welcome_settings", "✏️ تنظیمات خوش‌آمد"),
]

# اضافه کردن ستون permissions در صورت نیاز
try:
    execute_query("ALTER TABLE amel_sub_admins ADD COLUMN IF NOT EXISTS permissions TEXT DEFAULT ''")
except Exception:
    pass


def get_sub_admin(telegram_id: int) -> dict:
    try:
        r = execute_query("SELECT * FROM amel_sub_admins WHERE telegram_id=%s", (telegram_id,), fetch_one=True)
        return dict(r) if r else {}
    except Exception:
        return {}


def update_sub_admin_permissions(telegram_id: int, permissions: str) -> bool:
    try:
        execute_query(
            "UPDATE amel_sub_admins SET permissions=%s WHERE telegram_id=%s",
            (permissions, telegram_id)
        )
        return True
    except Exception as e:
        print(f"❌ update_sub_admin_permissions error: {e}")
        return False


def sub_admin_has_permission(telegram_id: int, perm: str) -> bool:
    try:
        r = execute_query("SELECT permissions FROM amel_sub_admins WHERE telegram_id=%s", (telegram_id,), fetch_one=True)
        if not r:
            return False
        perms = (r["permissions"] or "").split(",")
        return perm in perms
    except Exception:
        return False
