# ─── پاسخ‌دهی خودکار با هوش مصنوعی DeepSeek ─────────────────────────────────
#
# وقتی کاربر این قابلیت رو روشن می‌کنه و آفلاین باشه،
# هر کسی که بهش پیام بده، سلف به‌صورت خودکار با DeepSeek جوابشو میده.
# کاربر می‌تونه یک متن زمینه (context) تعریف کنه — مثلاً لیست قیمت‌ها یا
# هر اطلاعاتی که می‌خواد هوش مصنوعی از طرفش استفاده کنه.

import asyncio
import time
import httpx
from typing import Optional

import config
import database as db

# ─── ثابت‌ها ─────────────────────────────────────────────────────────────────
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL   = "deepseek-chat"

# کلید تنظیمات دیتابیس
SETTING_AI_ENABLED  = "ai_autoreply"       # 0 یا 1
SETTING_AI_CONTEXT  = "ai_context"         # متن زمینه که کاربر تعریف کرده

# جلوگیری از اسپم: برای هر فرستنده چقدر صبر کنیم قبل از جواب بعدی (ثانیه)
AI_REPLY_COOLDOWN = 120   # 2 دقیقه

# حداکثر طول پیام ورودی که به DeepSeek می‌فرستیم
MAX_INPUT_CHARS = 800

# کش زمان آخرین پاسخ به هر فرستنده: {(owner_id, sender_id): timestamp}
_reply_cooldown_cache: dict = {}


# ─── بررسی وضعیت آفلاین بودن کاربر ──────────────────────────────────────────
async def is_user_offline(client) -> bool:
    """
    True اگه صاحب سلف الان آفلاین باشه.
    از وضعیت تلگرام خودِ کاربر چک می‌کنیم.
    """
    try:
        from telethon.tl.types import (
            UserStatusOnline,
            UserStatusOffline,
            UserStatusRecently,
        )
        me = await client.get_me()
        status = getattr(me, "status", None)
        if status is None:
            return True
        if isinstance(status, UserStatusOnline):
            return False
        # آفلاین یا "اخیراً آنلاین" → پاسخ خودکار فعال
        return True
    except Exception:
        return True


# ─── بررسی کولداون (جلوگیری از اسپم) ────────────────────────────────────────
def _is_on_cooldown(owner_id: int, sender_id: int) -> bool:
    key = (owner_id, sender_id)
    last = _reply_cooldown_cache.get(key, 0)
    return (time.time() - last) < AI_REPLY_COOLDOWN


def _set_cooldown(owner_id: int, sender_id: int):
    _reply_cooldown_cache[(owner_id, sender_id)] = time.time()


# ─── دریافت تنظیمات ──────────────────────────────────────────────────────────
def is_ai_enabled(owner_id: int) -> bool:
    """True اگه پاسخ‌دهی خودکار هوش مصنوعی برای این کاربر روشن باشه."""
    return db.get_setting(owner_id, SETTING_AI_ENABLED, "0") == "1"


def get_ai_context(owner_id: int) -> str:
    """متن زمینه‌ای که کاربر برای هوش مصنوعی تعریف کرده."""
    return db.get_setting(owner_id, SETTING_AI_CONTEXT, "") or ""


def set_ai_context(owner_id: int, context: str):
    """ذخیره متن زمینه."""
    db.set_setting(owner_id, SETTING_AI_CONTEXT, context.strip())


def toggle_ai(owner_id: int) -> bool:
    """تغییر حالت روشن/خاموش. True=روشن، False=خاموش برمی‌گردونه."""
    return db.toggle_setting(owner_id, SETTING_AI_ENABLED)


# ─── ارسال پیام به DeepSeek و دریافت جواب ───────────────────────────────────
async def _call_deepseek(system_prompt: str, user_message: str) -> Optional[str]:
    """
    یک پیام به DeepSeek می‌فرسته و متن جواب رو برمی‌گردونه.
    اگه خطا بود None برمی‌گردونه.
    """
    api_key = getattr(config, "DEEPSEEK_API_KEY", "")
    if not api_key:
        return None

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_message[:MAX_INPUT_CHARS]},
        ],
        "temperature": 0.7,
        "max_tokens": 400,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(DEEPSEEK_API_URL, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[AI] خطا در ارسال به DeepSeek: {e}")
        return None


# ─── ساخت prompt سیستم ───────────────────────────────────────────────────────
def _build_system_prompt(context: str) -> str:
    base = (
        "تو دستیار هوشمند یک کاربر تلگرام هستی. "
        "کاربر الان آفلاین است و تو باید به پیام‌های ورودی از طرف او پاسخ بدهی. "
        "پاسخ‌ها باید کوتاه، مودبانه و مفید باشند. "
        "هیچ ایموجی استفاده نکن. "
        "فقط فارسی جواب بده مگر اینکه طرف مقابل به زبان دیگری نوشته باشد."
    )
    if context:
        base += f"\n\nاطلاعاتی که کاربر برای پاسخ دادن به تو داده:\n{context}"
    return base


# ─── تابع اصلی: پاسخ خودکار ─────────────────────────────────────────────────
async def handle_ai_autoreply(
    client,
    owner_id: int,
    sender_id: int,
    sender_name: str,
    message_text: str,
) -> bool:
    """
    چک می‌کنه که آیا باید جواب بده و اگه بله، جواب می‌فرسته.

    Args:
        client: TelegramClient سلف
        owner_id: آیدی عددی پنل
        sender_id: آیدی تلگرام فرستنده
        sender_name: نام فرستنده (برای لاگ)
        message_text: متن پیام دریافت‌شده

    Returns:
        True اگه جواب فرستاده شد
    """
    # ─── شرط ۱: قابلیت روشن باشه ────────────────────────────────────────────
    if not is_ai_enabled(owner_id):
        return False

    # ─── شرط ۲: کاربر آفلاین باشه ──────────────────────────────────────────
    if not await is_user_offline(client):
        return False

    # ─── شرط ۳: پیام خالی نباشه ─────────────────────────────────────────────
    if not message_text or not message_text.strip():
        return False

    # ─── شرط ۴: کولداون (جلوگیری از اسپم) ──────────────────────────────────
    if _is_on_cooldown(owner_id, sender_id):
        return False

    # ─── دریافت زمینه کاربر ──────────────────────────────────────────────────
    context = get_ai_context(owner_id)
    system_prompt = _build_system_prompt(context)

    # ─── ارسال به DeepSeek ───────────────────────────────────────────────────
    reply_text = await _call_deepseek(system_prompt, message_text)
    if not reply_text:
        return False

    # ─── ارسال جواب در همون چت ───────────────────────────────────────────────
    try:
        await client.send_message(sender_id, reply_text)
        _set_cooldown(owner_id, sender_id)
        print(f"[AI] جواب به {sender_name} ({sender_id}) فرستاده شد")
        return True
    except Exception as e:
        print(f"[AI] خطا در ارسال جواب: {e}")
        return False
