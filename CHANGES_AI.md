# تغییرات اضافه‌شده برای قابلیت هوش مصنوعی

## فایل‌های جدید

### `ai_reply.py`
ماژول اصلی. همه کار رو خودش می‌کنه — وصل شدن به DeepSeek، چک کردن آفلاین بودن، نگه داشتن کولداون.

### `bot_ai_integration.py`
راهنمای تغییرات لازم در `bot.py` (فقط راهنماست، لازم نیست اجرا بشه).

---

## فایل‌های تغییریافته

### `config.py`
یک خط اضافه شد:
```python
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
```

### `requirements.txt`
یک پکیج اضافه شد:
```
httpx>=0.27.0
```

---

## متغیر محیطی لازم

در فایل `.env` یا متغیرهای محیطی سرور اضافه کن:
```
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxx
```

کلید API از: https://platform.deepseek.com/api_keys

---

## مراحل اتصال به bot.py

### ۱. import اضافه کن
```python
from ai_reply import (
    handle_ai_autoreply,
    is_ai_enabled,
    get_ai_context,
    set_ai_context,
    toggle_ai,
)
```

### ۲. دکمه در PANEL_CATEGORIES
```python
"ai": {
    "title": "هوش مصنوعی",
    "commands": [
        ("ai_toggle",  "پاسخ‌دهی هوش مصنوعی",  "ai:toggle",  None),
        ("ai_context", "تنظیم اطلاعات / زمینه", "ai:context", None),
        ("ai_status",  "وضعیت هوش مصنوعی",      "ai:status",  None),
    ],
},
```

### ۳. دستورات در _execute_panel_command
```python
if command_text == "ai:toggle":
    new_state = toggle_ai(owner_id)
    status = "روشن شد" if new_state else "خاموش شد"
    await client.send_message("me", f"[هوش مصنوعی] پاسخ‌دهی خودکار {status}.")
    return True

if command_text == "ai:status":
    enabled = is_ai_enabled(owner_id)
    context = get_ai_context(owner_id)
    preview = context[:200] + "..." if len(context) > 200 else context
    await client.send_message("me",
        f"[هوش مصنوعی]\nپاسخ‌دهی: {'روشن' if enabled else 'خاموش'}\n\n"
        f"اطلاعات:\n{preview or '(تنظیم نشده)'}"
    )
    return True

if command_text == "ai:context":
    await client.send_message("me",
        "[هوش مصنوعی] برای تنظیم اطلاعات بنویس:\n\n"
        "ai_set_context: [اطلاعات شما]"
    )
    return True
```

### ۴. هندلر پیام ورودی
در هندلر `incoming=True` پیام‌های private:
```python
try:
    sender = await event.get_sender()
    sender_name = getattr(sender, "first_name", "") or str(event.sender_id)
    await handle_ai_autoreply(
        client=client,
        owner_id=owner_id,
        sender_id=event.sender_id,
        sender_name=sender_name,
        message_text=event.raw_text,
    )
except Exception as _e:
    print(f"[AI] خطا: {_e}")
```

### ۵. دستور ذخیره اطلاعات
در هندلر `outgoing=True`:
```python
text = event.raw_text or ""
if text.startswith("ai_set_context:"):
    context_text = text[len("ai_set_context:"):].strip()
    if context_text:
        set_ai_context(owner_id, context_text)
        await event.delete()
        await client.send_message("me", "[هوش مصنوعی] اطلاعات ذخیره شد.")
    return
```

---

## نحوه استفاده (کاربر)

۱. کلید DeepSeek رو در `.env` تنظیم کن
۲. از پنل روی "هوش مصنوعی" بزن
۳. "تنظیم اطلاعات" رو بزن
۴. در چت خودت بنویس: `ai_set_context: [اطلاعات خودت]`
   مثال: `ai_set_context: قیمت iPhone 15: 45 میلیون. فروشگاه ما در تهران است.`
۵. "پاسخ‌دهی هوش مصنوعی" رو روشن کن

از این به بعد وقتی آفلاینی، سلف به‌صورت خودکار با DeepSeek جواب میده.

**نکات:**
- بین هر جواب به یک نفر، ۲ دقیقه صبر می‌کنه (anti-spam)
- بدون ایموجی
- اگه کاربر آنلاین باشه، جوابی داده نمیشه
- جواب‌ها فارسی هستن (مگه پیام به زبان دیگه‌ای باشه)
