"""
حلقه‌ی asyncio مشترک برای کل پروژه.

قبلاً این حلقه داخل app.py (که خود یک وب‌سرور Flask بود) ساخته می‌شد. حالا که
سایت/پنل وب کامل از پروژه حذف شده، این ماژول کوچیک همون نقش رو ادامه می‌ده:
یک event loop پس‌زمینه که همه‌جای پروژه (bot_manager، telegram_bot و ...) برای
اجرای کارهای async (مثل استارت کردن سلف‌ها) ازش استفاده می‌کنن.
"""

import asyncio
import threading

_loop = None
_lock = threading.Lock()


def get_loop():
    """حلقه‌ی asyncio مشترک رو برمی‌گردونه؛ اگه هنوز ساخته نشده، می‌سازدش."""
    global _loop
    with _lock:
        if _loop is None or _loop.is_closed():
            _loop = asyncio.new_event_loop()
            t = threading.Thread(target=_loop.run_forever, daemon=True)
            t.start()
        return _loop


def run_async(coro, timeout: int = 30):
    """یک کوروتین رو روی حلقه‌ی مشترک اجرا می‌کنه و منتظر نتیجه می‌مونه."""
    return asyncio.run_coroutine_threadsafe(coro, get_loop()).result(timeout=timeout)
