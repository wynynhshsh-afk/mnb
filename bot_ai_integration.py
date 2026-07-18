# ═══════════════════════════════════════════════════════════════════════════════
# راهنمای اتصال قابلیت هوش مصنوعی به bot.py
#
# این فایل فقط راهنما است. محتوای آن را در bot.py اصلی وارد کنید.
# ═══════════════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────────────────────
# مرحله ۱: در ابتدای bot.py این import را اضافه کنید
# ─────────────────────────────────────────────────────────────────────────────

from ai_reply import (
    handle_ai_autoreply,
    is_ai_enabled,
    get_ai_context,
    set_ai_context,
    toggle_ai,
    SETTING_AI_ENABLED,
    SETTING_AI_CONTEXT,
)

# ─────────────────────────────────────────────────────────────────────────────
# مرحله ۲: در PANEL_CATEGORIES یک دسته جدید اضافه کنید
# ─────────────────────────────────────────────────────────────────────────────

# این بلوک را داخل دیکشنری PANEL_CATEGORIES اضافه کنید:
PANEL_CATEGORIES_AI_ENTRY = {
    "ai": {
        "title": "هوش مصنوعی",
        "commands": [
            # (key, label, command, style)
            ("ai_toggle",  "پاسخ‌دهی هوش مصنوعی",   "ai:toggle",   None),
            ("ai_context", "تنظیم اطلاعات / زمینه",  "ai:context",  None),
            ("ai_status",  "وضعیت هوش مصنوعی",       "ai:status",   None),
        ],
    },
}

# مثال کامل PANEL_CATEGORIES با دسته AI:
#
# PANEL_CATEGORIES = {
#     "automation": { ... },
#     ...
#     "ai": {
#         "title": "هوش مصنوعی",
#         "commands": [
#             ("ai_toggle",  "پاسخ‌دهی هوش مصنوعی",  "ai:toggle",  None),
#             ("ai_context", "تنظیم اطلاعات / زمینه", "ai:context", None),
#             ("ai_status",  "وضعیت هوش مصنوعی",      "ai:status",  None),
#         ],
#     },
# }


# ─────────────────────────────────────────────────────────────────────────────
# مرحله ۳: در تابع _execute_panel_command این بلوک را اضافه کنید
# ─────────────────────────────────────────────────────────────────────────────

async def _execute_panel_command_ai_block(client, owner_id: int, command_text: str) -> bool:
    """
    این بلوک را داخل _execute_panel_command اصلی اضافه کنید.
    قبل از return False یا بعد از بقیه elif‌ها بگذارید.
    """

    # ─── روشن/خاموش کردن پاسخ خودکار هوش مصنوعی ─────────────────────────
    if command_text == "ai:toggle":
        new_state = toggle_ai(owner_id)
        status = "روشن شد" if new_state else "خاموش شد"
        await client.send_message("me", f"[هوش مصنوعی] پاسخ‌دهی خودکار {status}.")
        return True

    # ─── نمایش وضعیت ──────────────────────────────────────────────────────
    if command_text == "ai:status":
        enabled = is_ai_enabled(owner_id)
        context = get_ai_context(owner_id)
        context_preview = context[:200] + "..." if len(context) > 200 else context
        msg = (
            f"[هوش مصنوعی] وضعیت:\n"
            f"پاسخ‌دهی خودکار: {'روشن' if enabled else 'خاموش'}\n\n"
            f"اطلاعات / زمینه:\n{context_preview if context_preview else '(تنظیم نشده)'}"
        )
        await client.send_message("me", msg)
        return True

    # ─── راهنمای تنظیم زمینه ──────────────────────────────────────────────
    if command_text == "ai:context":
        await client.send_message(
            "me",
            "[هوش مصنوعی] برای تنظیم اطلاعات، پیام زیر را در چت خودت بنویس:\n\n"
            "ai_set_context: [متن اطلاعات شما]\n\n"
            "مثال:\n"
            "ai_set_context: قیمت گوشی Samsung A55 به رنگ مشکی: 18 میلیون تومان. "
            "قیمت iPhone 15: 45 میلیون. فروشگاه ما در تهران، پاسداران است."
        )
        return True

    return False


# ─────────────────────────────────────────────────────────────────────────────
# مرحله ۴: در _register_handlers، در هندلر پیام‌های ورودی این کد را اضافه کنید
# ─────────────────────────────────────────────────────────────────────────────
#
# داخل event handler که پیام‌های private دریافتی رو پردازش می‌کنه:
#
# @client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
# async def on_incoming_message(event):
#     ...
#     # ─── هوش مصنوعی: پاسخ خودکار ─────────────────────────────────────────
#     try:
#         sender = await event.get_sender()
#         sender_name = getattr(sender, "first_name", "") or str(event.sender_id)
#         await handle_ai_autoreply(
#             client=client,
#             owner_id=owner_id,
#             sender_id=event.sender_id,
#             sender_name=sender_name,
#             message_text=event.raw_text,
#         )
#     except Exception as _e:
#         print(f"[AI] خطا در پاسخ خودکار: {_e}")
#
# ─────────────────────────────────────────────────────────────────────────────
# مرحله ۵: دستور ai_set_context را در selfbot هندل کنید
# ─────────────────────────────────────────────────────────────────────────────
#
# داخل event handler که پیام‌های outgoing خودت رو پردازش می‌کنه:
#
# @client.on(events.NewMessage(outgoing=True))
# async def on_outgoing_message(event):
#     text = event.raw_text or ""
#     if text.startswith("ai_set_context:"):
#         context_text = text[len("ai_set_context:"):].strip()
#         if context_text:
#             set_ai_context(owner_id, context_text)
#             await event.delete()
#             await client.send_message("me", "[هوش مصنوعی] اطلاعات ذخیره شد.")
#         return
