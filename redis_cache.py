# redis_cache.py
# لایه کش Redis — حالا با پشتیبانی از چند اکانت/سرویس Redis همزمان (Sharding)
# تا هم حجم رمِ در دسترس بیشتر شه (هر اکانت رایگان معمولاً چند مگابایت/ده‌ها
# مگابایت محدودیت داره) و هم بار روی چند سرور تقسیم شه (سرعت بیشتر، تک‌نقطه
# شکست کمتر).
#
# تنظیم چند اکانت Redis:
#   یکی از این دو روش رو استفاده کن (هر دو با هم هم کار می‌کنن):
#   ۱) REDIS_URLS="redis://user:pass@host1:port/0,redis://user:pass@host2:port/0,..."
#      (چند URL با کاما جدا شده، توی یک متغیر محیطی)
#   ۲) REDIS_URL_1, REDIS_URL_2, REDIS_URL_3, ... (هر کدوم یک اکانت/سرویس جدا،
#      مثلاً یکی از Upstash، یکی از Redis Cloud، یکی از Aiven و ...)
#   متغیر قدیمی UPSTASH_REDIS_URL هم برای عقب‌گرد (backward-compat) هنوز کار
#   می‌کنه و به‌عنوان یکی از شارد‌ها اضافه می‌شه.
#
# هر کلید بر اساس هش خودش (consistent hashing با ثابت‌ماندنِ نسبیِ توزیع)
# همیشه به یک شارد ثابت می‌ره، پس خوندن/نوشتن یک کلید همیشه سراغ همون
# اتصال می‌ره. اگه یک شارد قطع باشه، فقط کلیدهای همون شارد کش نمی‌شن
# (بدون کش ادامه می‌دن) و بقیه‌ی شاردها بی‌تاثیر می‌مونن.

import os
import json
import time
import hashlib
import redis
from typing import Any, Optional, List

# ─── ساخت لیست URL شاردها از متغیرهای محیطی ───────────────────────────────────
def _collect_redis_urls() -> List[str]:
    urls = []

    combined = os.environ.get("REDIS_URLS", "")
    if combined:
        urls.extend([u.strip() for u in combined.split(",") if u.strip()])

    i = 1
    while True:
        u = os.environ.get(f"REDIS_URL_{i}", "")
        if not u:
            break
        urls.append(u.strip())
        i += 1

    legacy = os.environ.get("UPSTASH_REDIS_URL", "")
    if legacy:
        urls.append(legacy.strip())

    # حذف موارد تکراری با حفظ ترتیب
    seen = set()
    unique_urls = []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            unique_urls.append(u)
    return unique_urls


# ─── اتصال به همه‌ی شاردهای Redis ───────────────────────────────────────────────
_shards: List[Optional[redis.Redis]] = []
_shards_ready = False


def _ensure_shards():
    """اتصال به همه‌ی شاردها رو یک‌بار برقرار می‌کنه (lazy init)."""
    global _shards, _shards_ready
    if _shards_ready:
        return
    _shards_ready = True

    urls = _collect_redis_urls()
    if not urls:
        print("⚠️ هیچ REDIS_URL/REDIS_URLS/UPSTASH_REDIS_URL تنظیم نشده — بدون کش ادامه می‌دهیم")
        return

    for idx, url in enumerate(urls):
        try:
            conn = redis.from_url(url, decode_responses=True, socket_timeout=2)
            conn.ping()
            _shards.append(conn)
            print(f"✅ شارد Redis #{idx + 1} متصل شد")
        except Exception as e:
            print(f"⚠️ اتصال شارد Redis #{idx + 1} ناموفق: {e}")
            _shards.append(None)


def _shard_for(key: str) -> Optional[redis.Redis]:
    """بر اساس هش کلید، همیشه همون شارد ثابت رو برمی‌گردونه (consistent hashing)."""
    _ensure_shards()
    if not _shards:
        return None
    idx = int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16) % len(_shards)
    return _shards[idx]


def get_redis() -> Optional[redis.Redis]:
    """
    برای عقب‌گرد با کدهای قدیمی: اولین شاردِ سالم رو برمی‌گردونه.
    برای عملیات جدید بهتره از rget/rset (که خودشون شاردبندی می‌کنن) استفاده شه.
    """
    _ensure_shards()
    for conn in _shards:
        if conn is not None:
            return conn
    return None


def shards_status() -> dict:
    """برای دیباگ/وضعیت: چند شارد تنظیم شده و چند تاشون سالم وصل هستن."""
    _ensure_shards()
    return {
        "total": len(_shards),
        "connected": sum(1 for c in _shards if c is not None),
    }


# ─── توابع پایه (حالا شاردبندی‌شده) ─────────────────────────────────────────────
def rget(key: str) -> Optional[str]:
    r = _shard_for(key)
    if not r:
        return None
    try:
        return r.get(key)
    except Exception:
        return None

def rset(key: str, value: str, ttl: int = 300):
    """ذخیره در Redis با TTL ثانیه (پیش‌فرض ۵ دقیقه) — روی شارد متناظر با خودِ کلید"""
    r = _shard_for(key)
    if not r:
        return
    try:
        r.setex(key, ttl, value)
    except Exception:
        pass

def rdel(key: str):
    r = _shard_for(key)
    if not r:
        return
    try:
        r.delete(key)
    except Exception:
        pass

def rdel_pattern(pattern: str):
    """حذف همه کلیدهایی که با pattern مطابقت دارن — روی همه‌ی شاردها (چون pattern به یک شارد خاص مقید نیست)"""
    _ensure_shards()
    for r in _shards:
        if not r:
            continue
        try:
            keys = r.keys(pattern)
            if keys:
                r.delete(*keys)
        except Exception:
            pass

def rget_json(key: str) -> Optional[Any]:
    raw = rget(key)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None

def rset_json(key: str, value: Any, ttl: int = 300):
    try:
        rset(key, json.dumps(value, ensure_ascii=False, default=str), ttl)
    except Exception:
        pass

# ─── TTL های استاندارد (ثانیه) ────────────────────────────────────────────────
TTL_SETTING   = 600    # تنظیمات کاربر — ۱۰ دقیقه
TTL_SUBSCRIBE = 120    # وضعیت اشتراک — ۲ دقیقه (چون حساسه)
TTL_TOKEN     = 60     # موجودی توکن — ۱ دقیقه
TTL_ENEMIES   = 300    # لیست دشمن — ۵ دقیقه
TTL_FRIENDS   = 300    # لیست دوست — ۵ دقیقه
TTL_SILENT    = 300    # سایلنت — ۵ دقیقه
TTL_CHANNELS  = 600    # چنل‌های اجباری — ۱۰ دقیقه
TTL_ACCOUNT   = 300    # اطلاعات اکانت — ۵ دقیقه

# ─── کلیدهای Redis ────────────────────────────────────────────────────────────
def k_setting(owner_id: int, key: str) -> str:
    return f"stg:{owner_id}:{key}"

def k_all_settings(owner_id: int) -> str:
    return f"stg:{owner_id}:*"

def k_subscribe(owner_id: int) -> str:
    return f"sub:{owner_id}"

def k_token(owner_id: int) -> str:
    return f"tok:{owner_id}"

def k_enemies(owner_id: int) -> str:
    return f"enm:{owner_id}"

def k_friends(owner_id: int) -> str:
    return f"frn:{owner_id}"

def k_silent_chats(owner_id: int) -> str:
    return f"sltc:{owner_id}"

def k_silent_users(owner_id: int) -> str:
    return f"sltu:{owner_id}"

def k_forced_channels() -> str:
    return "fc:list"

def k_account(owner_id: int) -> str:
    return f"acc:{owner_id}"

# ─── توابع invalidation ────────────────────────────────────────────────────────
def invalidate_setting(owner_id: int, key: str):
    rdel(k_setting(owner_id, key))

def invalidate_all_settings(owner_id: int):
    rdel_pattern(k_all_settings(owner_id))

def invalidate_subscribe(owner_id: int):
    rdel(k_subscribe(owner_id))

def invalidate_token(owner_id: int):
    rdel(k_token(owner_id))

def invalidate_enemies(owner_id: int):
    rdel(k_enemies(owner_id))

def invalidate_friends(owner_id: int):
    rdel(k_friends(owner_id))

def invalidate_silent(owner_id: int):
    rdel(k_silent_chats(owner_id))
    rdel(k_silent_users(owner_id))

def invalidate_forced_channels():
    rdel(k_forced_channels())
# ─── اضافات جدید برای سیستم Queue و Heartbeat ─────────────────────────────────

# TTL‌های جدید
TTL_HEARTBEAT = 60   # ۶۰ ثانیه برای Heartbeat
TTL_QUEUE = 3600     # ۱ ساعت برای تسک‌های Queue

def k_queue(owner_id: int) -> str:
    return f"queue:{owner_id}"

def k_heartbeat(owner_id: int) -> str:
    return f"hb:{owner_id}"

def k_active_bots() -> str:
    return "active_bots:set"

# توابع جدید برای مدیریت Queue
def push_task(owner_id: int, task_type: str, data: dict) -> bool:
    """افزودن تسک به Queue"""
    r = get_redis()
    if not r:
        return False
    try:
        import json
        task = {
            "type": task_type,
            "data": data,
            "timestamp": time.time()
        }
        r.rpush(k_queue(owner_id), json.dumps(task))
        return True
    except Exception:
        return False

def pop_task(owner_id: int) -> Optional[dict]:
    """دریافت تسک از Queue"""
    r = get_redis()
    if not r:
        return None
    try:
        import json
        raw = r.lpop(k_queue(owner_id))
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    return None

def get_queue_length(owner_id: int) -> int:
    """تعداد تسک‌های در صف"""
    r = get_redis()
    if not r:
        return 0
    try:
        return r.llen(k_queue(owner_id))
    except Exception:
        return 0
