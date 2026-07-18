# db_cache.py
import sqlite3
import datetime
from config import CACHE_DB_PATH
import redis_cache as rc

# ─── اتصال به دیتابیس کش ──────────────────────────────────────────────────────
_conn = None

def get_conn():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(CACHE_DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
        _conn.execute("PRAGMA cache_size=10000")
        _init_tables()
    return _conn

def _init_tables():
    conn = get_conn()
    c = conn.cursor()
    
    # ─── چنل‌های اجباری ──────────────────────────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS forced_channels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    
    # ─── سایلنت ──────────────────────────────────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS silent_chats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER NOT NULL,
        chat_id INTEGER NOT NULL,
        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (owner_id, chat_id)
    )""")
    
    c.execute("""CREATE TABLE IF NOT EXISTS silent_users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (owner_id, user_id)
    )""")
    
    # ─── 📋 لیست دشمن ──────────────────────────────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS enemies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        username TEXT,
        name TEXT,
        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (owner_id, user_id)
    )""")
    
    # ─── 📋 لیست دوست ──────────────────────────────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS friends (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        username TEXT,
        name TEXT,
        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (owner_id, user_id)
    )""")
    
    # ─── درخواست‌های ورود (تایید ادمین) ────────────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS start_approvals (
        user_id INTEGER PRIMARY KEY,
        status TEXT NOT NULL DEFAULT 'pending',
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

    # ─── شاخص‌ها ──────────────────────────────────────────────────────────────
    c.execute("CREATE INDEX IF NOT EXISTS idx_enemies_owner ON enemies(owner_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_friends_owner ON friends(owner_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_silent_chats_owner ON silent_chats(owner_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_silent_users_owner ON silent_users(owner_id)")
    
    conn.commit()
    print("✅ جداول دیتابیس کش ایجاد شدند!")

# ─── درخواست‌های ورود (تایید ادمین) ────────────────────────────────────────
def get_start_approval_status(user_id: int):
    """وضعیت درخواست ورود کاربر را برمی‌گرداند: 'pending' | 'approved' | 'rejected' | None"""
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT status FROM start_approvals WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    return row["status"] if row else None

def set_start_approval_status(user_id: int, status: str):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """INSERT INTO start_approvals (user_id, status, updated_at)
           VALUES (?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(user_id) DO UPDATE SET status = excluded.status, updated_at = CURRENT_TIMESTAMP""",
        (user_id, status)
    )
    conn.commit()

# ─── چنل‌های اجباری ──────────────────────────────────────────────────────────
def get_forced_channels():
    cached = rc.rget_json(rc.k_forced_channels())
    if cached is not None:
        return cached
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT username FROM forced_channels ORDER BY added_at DESC")
    result = [r["username"] for r in c.fetchall()]
    rc.rset_json(rc.k_forced_channels(), result, rc.TTL_CHANNELS)
    return result

def add_forced_channel(username: str) -> bool:
    if not username.startswith("@"):
        username = "@" + username
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute("INSERT INTO forced_channels (username) VALUES (?)", (username,))
        conn.commit()
        rc.invalidate_forced_channels()
        return True
    except Exception:
        return False

def remove_forced_channel(username: str) -> bool:
    if not username.startswith("@"):
        username = "@" + username
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM forced_channels WHERE username = ?", (username,))
    conn.commit()
    rc.invalidate_forced_channels()
    return c.rowcount > 0

def check_user_membership(bot, user_id: int) -> tuple:
    from database_supabase import get_forced_channels as _supa_get_forced_channels
    channels = _supa_get_forced_channels()
    if not channels:
        return True, []
    missing = []
    for ch in channels:
        try:
            member = bot.get_chat_member(ch, user_id)
            if member.status not in ['member', 'administrator', 'creator']:
                missing.append(ch)
        except Exception:
            missing.append(ch)
    return len(missing) == 0, missing

# ─── سایلنت ───────────────────────────────────────────────────────────────────
def add_silent_chat(owner_id: int, chat_id: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO silent_chats (owner_id, chat_id) VALUES (?, ?)", 
              (owner_id, chat_id))
    conn.commit()

def remove_silent_chat(owner_id: int, chat_id: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM silent_chats WHERE owner_id = ? AND chat_id = ?", 
              (owner_id, chat_id))
    conn.commit()

def is_silent_chat(owner_id: int, chat_id: int) -> bool:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT 1 FROM silent_chats WHERE owner_id = ? AND chat_id = ?", 
              (owner_id, chat_id))
    return c.fetchone() is not None

def add_silent_user(owner_id: int, user_id: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO silent_users (owner_id, user_id) VALUES (?, ?)", 
              (owner_id, user_id))
    conn.commit()

def remove_silent_user(owner_id: int, user_id: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM silent_users WHERE owner_id = ? AND user_id = ?", 
              (owner_id, user_id))
    conn.commit()

def is_silent_user(owner_id: int, user_id: int) -> bool:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT 1 FROM silent_users WHERE owner_id = ? AND user_id = ?", 
              (owner_id, user_id))
    return c.fetchone() is not None

# ─── 📋 لیست دشمن ──────────────────────────────────────────────────────────────
def _invalidate_enemies(owner_id: int):
    rc.invalidate_enemies(owner_id)

def add_enemy(owner_id: int, user_id: int, username=None, name=None):
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("""
            INSERT OR REPLACE INTO enemies (owner_id, user_id, username, name, added_at) 
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (owner_id, user_id, username, name))
        conn.commit()
        _invalidate_enemies(owner_id)
        print(f"✅ دشمن با ID: {user_id} برای کاربر {owner_id} در کش ذخیره شد")
        return True
    except Exception as e:
        print(f"❌ add_enemy error: {e}")
        return False

def remove_enemy(owner_id: int, user_id: int) -> bool:
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM enemies WHERE owner_id = ? AND user_id = ?", (owner_id, user_id))
    conn.commit()
    _invalidate_enemies(owner_id)
    return c.rowcount > 0

def get_enemies(owner_id: int):
    cached = rc.rget_json(rc.k_enemies(owner_id))
    if cached is not None:
        return cached
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM enemies WHERE owner_id = ? ORDER BY added_at DESC", (owner_id,))
    result = [dict(r) for r in c.fetchall()]
    rc.rset_json(rc.k_enemies(owner_id), result, rc.TTL_ENEMIES)
    return result

def is_enemy(owner_id: int, user_id: int) -> bool:
    # چک سریع از لیست کش‌شده
    enemies = get_enemies(owner_id)
    return any(e["user_id"] == user_id for e in enemies)

def clear_enemies(owner_id: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM enemies WHERE owner_id = ?", (owner_id,))
    conn.commit()
    _invalidate_enemies(owner_id)

def get_enemy_count(owner_id: int) -> int:
    return len(get_enemies(owner_id))

# ─── 📋 لیست دوست ──────────────────────────────────────────────────────────────
def _invalidate_friends(owner_id: int):
    rc.invalidate_friends(owner_id)

def add_friend(owner_id: int, user_id: int, username=None, name=None):
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("""
            INSERT OR REPLACE INTO friends (owner_id, user_id, username, name, added_at) 
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (owner_id, user_id, username, name))
        conn.commit()
        _invalidate_friends(owner_id)
        print(f"✅ دوست با ID: {user_id} برای کاربر {owner_id} در کش ذخیره شد")
        return True
    except Exception as e:
        print(f"❌ add_friend error: {e}")
        return False

def remove_friend(owner_id: int, user_id: int) -> bool:
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM friends WHERE owner_id = ? AND user_id = ?", (owner_id, user_id))
    conn.commit()
    _invalidate_friends(owner_id)
    return c.rowcount > 0

def get_friends(owner_id: int):
    cached = rc.rget_json(rc.k_friends(owner_id))
    if cached is not None:
        return cached
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM friends WHERE owner_id = ? ORDER BY added_at DESC", (owner_id,))
    result = [dict(r) for r in c.fetchall()]
    rc.rset_json(rc.k_friends(owner_id), result, rc.TTL_FRIENDS)
    return result

def is_friend(owner_id: int, user_id: int) -> bool:
    friends = get_friends(owner_id)
    return any(f["user_id"] == user_id for f in friends)

def clear_friends(owner_id: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM friends WHERE owner_id = ?", (owner_id,))
    conn.commit()
    _invalidate_friends(owner_id)

def get_friend_count(owner_id: int) -> int:
    return len(get_friends(owner_id))
