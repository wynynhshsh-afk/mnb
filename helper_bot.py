# ─── ربات کمکی پنل (Helper Bot) ──────────────────────────────────────────────
# سلف‌بات‌ها (اکانت‌های شخصی تلگرام) نمی‌تونن مستقیم پیام با دکمه‌ی شیشه‌ای
# (inline keyboard / callback) بفرستن و کلیک روش کار کنه — چون callback query
# فقط برای پیام‌هایی که از طرف یک بات ارسال شدن فعال می‌شه.
#
# راه‌حل: یک بات کمکی (مثل @selfnexo_helper_bot) می‌سازیم. وقتی کاربر توی
# سلف خودش می‌نویسه «پنل»، سلف یک inline query به این بات می‌زنه و نتیجه رو
# توی همون چت کلیک می‌کنه؛ پیام به‌صورت «via @selfnexo_helper_bot» ارسال
# می‌شه ولی روی دکمه‌هاش واقعاً کار می‌کنه، چون بات فرستنده‌ی واقعیشه.
#
# قفل مالکیت پنل:
# هر پیام inline که ساخته می‌شه، آیدی تلگرام کسی که inline query رو زده
# (یعنی صاحب پنل) به‌صورت پسوند در callback_data تمام دکمه‌ها ذخیره می‌شه.
# وقتی هرکسی (حتی در گروه) روی یکی از دکمه‌ها کلیک می‌کنه، اول چک می‌شه که
# event.sender_id (کسی که واقعاً کلیک کرده) دقیقاً همون آیدیِ ذخیره‌شده باشه؛
# اگه نبود، با یک alert رد می‌شه و هیچ دستوری اجرا نمی‌شه. یعنی پنلِ هرکس
# فقط برای خودش کار می‌کنه، حتی اگه در یک گروه مشترک ارسال شده باشه.
#
# ساختار پنل چند سطحیه:
#   سطح ۱: منوی اصلی (ساعت، حالت متن، قفل‌ها، منشی، عضویت اجباری، اتوماسیون،
#           دوست و دشمن، ابزار، هوش مصنوعی، ایموجی پرمیوم)
#   سطح ۲: آیتم‌های همون دسته (سوییچ‌های رنگی روشن/خاموش + دکمه‌های اکشن ساده)
#           و/یا دکمه‌هایی به زیرمنوهای دیگه (مثل «فونت ساعت» یا «دوست»/«دشمن»)

import io
import asyncio

from telethon import TelegramClient, events
from telethon.tl.custom import Button
from telethon.sessions import StringSession
import config

_helper_client = None  # سینگلتون - فقط یک بار در کل پروسس بالا میاد
_helper_start_lock = None  # موقع اولین صدا زدنِ start_helper_bot ساخته می‌شه

MAIN_TEXT = "پنل مدیریت سلف\nیک دسته را انتخاب کن"
DENIED_TEXT = "این پنل مخصوص کسی است که آن را باز کرده. دکمه‌ها برای شما فعال نیست."

# ─── بستن خودکار پنل بعد از بیکار موندن ──────────────────────────────────────
PANEL_IDLE_SECONDS = 180  # ۳ دقیقه
IDLE_CLOSED_TEXT = "⏰ این پنل به‌خاطر ۳ دقیقه بیکار موندن بسته شد.\nبرای باز کردن دوباره، بنویس: پنل"
CLOSED_TEXT = "پنل بسته شد.\nبرای باز کردن دوباره،بنویس: پنل"
_panel_timers = {}  # {(chat_id, message_id): asyncio.Task}
_schedule_panel_timeout_impl = None  # موقع start_helper_bot ست میشه


def schedule_panel_timeout(chat_id: int, message_id: int):
    """از بیرون (مثلاً bot.py، وقتی پنل تازه باز میشه) یا از داخل on_callback
    (وقتی پنل باز می‌مونه ولی داره استفاده می‌شه) صدا زده می‌شه تا تایمر
    ۳ دقیقه‌ایِ بستن خودکار reset بشه."""
    if _schedule_panel_timeout_impl is not None:
        _schedule_panel_timeout_impl(chat_id, message_id)


def _category_text(title):
    return f"{title}\nیکی از دکمه‌ها رو بزن تا روشن/خاموش بشه یا اجرا شه"


def get_helper_client():
    return _helper_client


def _split_owner_tag(data: str):
    """
    آیدی تلگرامِ صاحبِ پنل رو که به‌صورت "..._{tg_id}" ته callback_data چسبیده
    جدا می‌کنه. اگه فرمت نامعتبر بود (مثل panel_noop) None برمی‌گردونه.
    """
    body, _, tail = data.rpartition("_")
    if tail.isdigit():
        return body, int(tail)
    return data, None


async def start_helper_bot():
    """
    بات کمکی رو راه‌اندازی می‌کنه. ایمن برای صدا زدنِ چندباره از چند جا
    (مثلاً موقع بالا اومدن سرور، بعد از هر ثبت‌نامِ تازه، و توسط واچ‌داگِ
    دوره‌ای) — اگه از قبل سالم و وصل باشه فوراً همون رو برمی‌گردونه، وگرنه
    یک اتصالِ تازه می‌سازه. با قفل جلوگیری می‌کنه که دو تا فراخوانیِ هم‌زمان
    دو تا کلاینتِ جدا با یک توکن بسازن.
    """
    global _helper_client, _helper_start_lock

    if not config.HELPER_BOT_TOKEN:
        print("⚠️ HELPER_BOT_TOKEN تنظیم نشده — پنل دکمه‌ای سلف غیرفعال است.")
        return None

    if _helper_client is not None and _helper_client.is_connected():
        return _helper_client

    if _helper_start_lock is None:
        _helper_start_lock = asyncio.Lock()

    async with _helper_start_lock:
        # دوباره چک کن، شاید تا رسیدن نوبتِ ما یه فراخوانیِ دیگه همین الان
        # وصلش کرده باشه
        if _helper_client is not None and _helper_client.is_connected():
            return _helper_client

        if _helper_client is not None:
            # کلاینتِ قبلی قطع شده (مثلاً به‌خاطر یک خطای شبکه‌ای موقت) — قبل از
            # ساختِ کلاینتِ تازه، مطمئن می‌شیم درست قطع شده تا دو تا کانکشن با
            # یک توکن هم‌زمان بالا نمونن.
            try:
                await _helper_client.disconnect()
            except Exception:
                pass
            _helper_client = None

        # import داخل تابع تا از circular import با bot.py جلوگیری بشه
        from bot import (
            bot_manager,
            PANEL_CATEGORIES,
            build_category_menu,
            build_category_commands,
            _execute_panel_command,
            _get_force_join_channels,
        )
        from telegram_bot import get_all_commands_buttons
        import database as db

        # ⏱️ نکته‌ی مهم: قبلاً اینجا `await cl.start(...)` بدون هیچ timeout ای
        # صدا زده می‌شد. اگه این اتصال (مثلاً به‌خاطر یک فلاکِ شبکه‌ایِ موقتِ
        # هاست) گیر می‌کرد و هیچ‌وقت برنمی‌گشت، چون این خط داخلِ
        # `async with _helper_start_lock:` هست، قفل برای همیشه گرفته می‌موند
        # و همه‌ی تلاش‌های بعدی (واچ‌داگِ هر ۲۰ ثانیه، لاگینِ کاربرِ تازه و...)
        # پشتِ همون قفل تا ابد صف می‌کشیدن — دقیقاً همون «تا ریستارتِ سرور
        # درست نمی‌شه» که مشاهده شده. با اضافه‌کردنِ timeout و disconnect
        # کردنِ صریح در صورتِ شکست، قفل زود آزاد می‌شه و واچ‌داگِ بعدی
        # می‌تونه دوباره تلاش کنه.
        cl = TelegramClient(
            StringSession(),
            config.API_ID,
            config.API_HASH,
            connection_retries=3,
            retry_delay=2,
            timeout=10,
        )
        try:
            await asyncio.wait_for(cl.start(bot_token=config.HELPER_BOT_TOKEN), timeout=25)
        except asyncio.TimeoutError:
            print("⏱️ تایم‌اوت در اتصال ربات کمکی — تلاش لغو شد، دفعه‌ی بعد دوباره امتحان می‌شه.")
            try:
                await cl.disconnect()
            except Exception:
                pass
            return None
        except Exception as e:
            print(f"❌ خطا در اتصال ربات کمکی: {e} — دفعه‌ی بعد دوباره امتحان می‌شه.")
            try:
                await cl.disconnect()
            except Exception:
                pass
            return None

        _helper_client = cl
        me = await cl.get_me()
        print(f"✅ ربات کمکی پنل راه‌اندازی شد — @{me.username}")

    async def _close_panel_after_idle(chat_id, message_id):
        try:
            await asyncio.sleep(PANEL_IDLE_SECONDS)
            try:
                await cl.edit_message(chat_id, message_id, IDLE_CLOSED_TEXT, buttons=None)
            except Exception:
                try:
                    await cl.delete_messages(chat_id, message_id)
                except Exception:
                    pass
        finally:
            _panel_timers.pop((chat_id, message_id), None)

    def _do_schedule(chat_id, message_id):
        key = (chat_id, message_id)
        old = _panel_timers.get(key)
        if old and not old.done():
            old.cancel()
        _panel_timers[key] = asyncio.ensure_future(_close_panel_after_idle(chat_id, message_id))

    global _schedule_panel_timeout_impl
    _schedule_panel_timeout_impl = _do_schedule

    def _menu_buttons(owner_tg_id, page=0):
        """
        دکمه‌های سطح ۱ به‌صورت شبکه‌ای (۳ ستونه)، رنگی از طریق style واقعی.
        فقط دکمه‌های «آبی» (primary) بین ۲ صفحه تقسیم می‌شن؛ دکمه‌های سبز
        (success) و قرمز (danger — شامل موارد غیرفعال مثل «ایموجی پرمیوم»)
        همیشه توی هر دو صفحه ثابت نشون داده می‌شن، چون تعدادشون کمه و نیازی
        به صفحه‌بندی ندارن. دکمه‌ی «بستن» هم همیشه ثابته و پیجینیت نمی‌شه.
        """
        categories = build_category_menu()
        primary_cats = [c for c in categories if (c[2] or "primary") == "primary"]
        fixed_cats = [c for c in categories if (c[2] or "primary") != "primary"]

        page_size = -(-len(primary_cats) // 2) if primary_cats else 0  # سقف تقسیم بر ۲
        total_pages = 2 if page_size and len(primary_cats) > page_size else 1
        page = max(0, min(page, total_pages - 1))
        start = page * page_size
        page_primary = primary_cats[start:start + page_size] if page_size else primary_cats

        def _grid(items):
            rows, row = [], []
            for key, title, style in items:
                row.append(Button.inline(title, data=f"panel_cat_{key}_{owner_tg_id}", style=style or "primary"))
                if len(row) == 3:
                    rows.append(row)
                    row = []
            if row:
                rows.append(row)
            return rows

        rows = _grid(page_primary)
        rows += _grid(fixed_cats)

        if total_pages > 1:
            nav = []
            if page > 0:
                nav.append(Button.inline("‹ صفحه قبل", data=f"panel_menu_page_{page - 1}_{owner_tg_id}", style="primary"))
            if page < total_pages - 1:
                nav.append(Button.inline("صفحه بعد ›", data=f"panel_menu_page_{page + 1}_{owner_tg_id}", style="primary"))
            if nav:
                rows.append(nav)

        rows.append([Button.inline("بستن", data=f"panel_close_{owner_tg_id}", style="danger")])
        return rows

    def _back_target(category_key, owner_tg_id):
        """دکمه‌ی بازگشتِ یک دسته: اگه دسته یک parent داشته باشه به همون
        دسته‌ی والد برمی‌گرده، وگرنه به منوی اصلی."""
        cat = PANEL_CATEGORIES.get(category_key, {})
        parent = cat.get("parent")
        if parent:
            return f"panel_cat_{parent}_{owner_tg_id}"
        return f"panel_menu_{owner_tg_id}"

    def _category_buttons(owner_id, owner_tg_id, category_key, page=0):
        """دکمه‌های سطح ۲ (آیتم‌های داخل یک دسته) + دکمه‌های زیرمنو (children)
        + بازگشت، همه با پسوند مالک."""
        cat = PANEL_CATEGORIES.get(category_key, {})
        items = build_category_commands(owner_id, category_key)
        buttons = get_all_commands_buttons(
            items,
            page=page,
            prefix=f"panel_item_{category_key}_",
            page_prefix=f"panel_item_page_{category_key}_",
            owner_suffix=f"_{owner_tg_id}",
        )
        for label, child_key in cat.get("children", []):
            buttons.append([Button.inline(label, data=f"panel_cat_{child_key}_{owner_tg_id}", style="primary")])
        buttons.append([Button.inline("بازگشت", data=_back_target(category_key, owner_tg_id), style="primary")])
        return buttons

    # ─── پاسخ به inline query (وقتی سلف داره نتیجه رو می‌گیره تا کلیک کنه) ───
    @cl.on(events.InlineQuery())
    async def on_inline(event):
        owner_id, entry = bot_manager.get_owner_by_tg_id(event.query.user_id)
        if owner_id is None:
            await event.answer(
                [event.builder.article(
                    title="غیرمجاز",
                    description="این اکانت به هیچ سلف فعالی متصل نیست.",
                    text="این پنل فقط برای سلف‌های فعال نکسو سلف در دسترسه.",
                )],
                cache_time=0,
            )
            return

        owner_tg_id = event.query.user_id  # همون کسی که inline query زده = صاحب پنل

        # ─── جوین اجباری: پیام هشدار با دکمه‌های واقعیِ لینک کانال‌ها ──────────
        query_text = (getattr(event.query, "query", "") or "").strip()
        if query_text == "جوین":
            join_msg = db.get_setting(owner_id, "force_join_message",
                "⛔ برای ارسال پیام ابتدا باید در کانال‌های زیر عضو شوید.")
            fj_channels = _get_force_join_channels(owner_id)
            rows = []
            for ch in fj_channels:
                link = ch.get("link")
                title = ch.get("title") or "عضویت در کانال"
                if link:
                    rows.append([Button.url(f"📢 عضویت در {title} ✅", link)])
            result = event.builder.article(
                title="هشدار جوین اجباری",
                description="پیام هشدار عضویت اجباری",
                text=join_msg,
                buttons=rows or None,
            )
            await event.answer([result], cache_time=0)
            return

        buttons = _menu_buttons(owner_tg_id)

        # ─── ساخت متن مشخصات (اسم + آیدی عددی + یوزرنیم) از روی خودِ سلف ───
        self_client = entry.get("client") if entry else None
        display_name = "کاربر"
        username_line = ""
        photo_bytes = None

        if self_client is not None:
            try:
                me = await self_client.get_me()
                full_name = " ".join(p for p in [me.first_name, me.last_name] if p)
                display_name = full_name or "بدون نام"
                if me.username:
                    username_line = f"یوزرنیم: @{me.username}\n"
                try:
                    raw_buf = io.BytesIO()
                    photo = await self_client.download_profile_photo(me, file=raw_buf)
                    if photo:
                        raw_buf.seek(0)
                        from banner import generate_banner
                        banner_bytes = generate_banner(raw_buf.read(), bottom_text="self panel", bottom_sub=f"@{me.username}" if me.username else "")
                        buf = io.BytesIO(banner_bytes)
                        buf.name = "panel.png"
                        photo_bytes = buf
                except Exception:
                    photo_bytes = None
            except Exception:
                pass

        caption = (
            f"نام: {display_name}\n"
            f"آیدی عددی: {owner_tg_id}\n"
            f"{username_line}"
            f"\n{MAIN_TEXT}"
        )

        if photo_bytes is not None:
            result = await event.builder.photo(
                file=photo_bytes,
                text=caption,
                buttons=buttons,
            )
        else:
            result = event.builder.article(
                title="پنل مدیریت سلف",
                description="برای نمایش پنل دکمه‌ای لمس کن",
                text=caption,
                buttons=buttons,
            )
        # cache_time=0 تا این پنل هیچ‌وقت به‌جای کاربر دیگه از کش تلگرام serve نشه
        await event.answer([result], cache_time=0)

    async def _answer_info(event, text: str):
        """
        نمایشِ متنِ راهنما/اطلاع‌رسانی به کاربر. تلگرام برای پاپ‌آپِ alert
        توی answerCallbackQuery سقفِ ۲۰۰ کاراکتری داره — اگه از این سقف رد
        بشیم، خودِ فراخوانی با خطا مواجه می‌شه (و چون قبلاً این خطا جایی
        catch نمی‌شد، دکمه از دیدِ کاربر «کار نمی‌کنه»: لودینگِ روی دکمه
        برای همیشه می‌مونه و هیچ پیامی نشون داده نمی‌شه). برای متن‌های کوتاه
        همون پاپ‌آپِ قبلی حفظ می‌شه، برای متن‌های بلند (مثل راهنمای تبچی یا
        راهنمای بازی میویی) به‌جاش یک پیامِ معمولی توی همون چت فرستاده
        می‌شه که محدودیتِ طول نداره.
        """
        if len(text) <= 200:
            try:
                await event.answer(text, alert=True)
                return
            except Exception:
                pass  # اگه به هر دلیلی پاپ‌آپ هم شکست خورد، به فالبکِ پیام معمولی برو
        try:
            await event.answer()
        except Exception:
            pass
        try:
            await cl.send_message(event.chat_id, text)
        except Exception:
            pass

    # ─── کلیک روی دکمه‌های پنل ────────────────────────────────────────────────
    @cl.on(events.CallbackQuery())
    async def on_callback(event):
        try:
            await _handle_panel_callback(event)
        except Exception as e:
            # هر خطای پیش‌بینی‌نشده‌ای که این‌جا بگیریم رو باید حتماً جواب
            # بدیم؛ وگرنه از دیدِ کاربر دکمه برای همیشه توی حالتِ لودینگ
            # می‌مونه و به نظر می‌رسه «کار نمی‌کنه»، بدون اینکه هیچ خطایی
            # جایی نشون داده بشه.
            import traceback
            traceback.print_exc()
            try:
                await event.answer("⚠️ خطایی رخ داد، دوباره امتحان کن.", alert=True)
            except Exception:
                pass

    async def _handle_panel_callback(event):
        data = event.data.decode("utf-8")

        if data == "panel_noop":
            await event.answer()
            return

        body, owner_tg_id = _split_owner_tag(data)
        if owner_tg_id is None:
            await event.answer("دکمه نامعتبر است.", alert=True)
            return

        # 🔒 قفل مالکیت: فقط همون کسی که پنل رو باز کرده اجازه‌ی کلیک داره
        if event.sender_id != owner_tg_id:
            await event.answer(DENIED_TEXT, alert=True)
            return

        owner_id, entry = bot_manager.get_owner_by_tg_id(event.sender_id)
        if owner_id is None or not entry or not entry.get("client"):
            await event.answer("سلف فعالی برای این اکانت پیدا نشد.", alert=True)
            return

        self_client = entry["client"]

        # هر تعاملی با پنل (باز شدن دسته، صفحه‌بندی، اجرای آیتم) تایمر ۳ دقیقه‌ای
        # بستن خودکار رو ریست می‌کنه
        schedule_panel_timeout(event.chat_id, event.message_id)

        # ─── بازگشت به منوی اصلی (لیست دسته‌ها) ────────────────────────────
        if body == "panel_menu":
            await event.edit(MAIN_TEXT, buttons=_menu_buttons(owner_tg_id))
            return

        # ─── ورق‌زدن صفحه‌های منوی اصلی (فقط دکمه‌های آبی) ──────────────────
        if body.startswith("panel_menu_page_"):
            try:
                page = int(body.replace("panel_menu_page_", ""))
            except ValueError:
                page = 0
            await event.edit(MAIN_TEXT, buttons=_menu_buttons(owner_tg_id, page=page))
            return

        # ─── بستن پنل ───────────────────────────────────────────────────────
        if body == "panel_close":
            old = _panel_timers.pop((event.chat_id, event.message_id), None)
            if old and not old.done():
                old.cancel()
            try:
                await event.edit(CLOSED_TEXT, buttons=None)
            except Exception:
                try:
                    await cl.delete_messages(event.chat_id, event.message_id)
                except Exception:
                    pass
            return

        # ─── انتخاب یک دسته از منوی اصلی ───────────────────────────────────
        if body.startswith("panel_cat_"):
            category_key = body.replace("panel_cat_", "")
            cat = PANEL_CATEGORIES.get(category_key)
            if not cat:
                await event.answer("دسته نامعتبر است.", alert=True)
                return
            if cat.get("stub_message"):
                await event.answer(cat["stub_message"], alert=True)
                return
            # دکمه‌های تک‌عملی که مستقیم اجرا می‌شن (زیرمنو ندارن)
            direct = cat.get("direct_command")
            if direct is not None:
                if direct.startswith("INFO::"):
                    await _answer_info(event, direct[len("INFO::"):])
                else:
                    await event.answer(f"در حال اجرا: {cat['title']}")
                    await _execute_panel_command(self_client, owner_id, direct)
                return
            await event.edit(
                _category_text(cat["title"]),
                buttons=_category_buttons(owner_id, owner_tg_id, category_key, page=0),
            )
            return

        # ─── ورق‌زدن صفحه‌های داخل یک دسته ─────────────────────────────────
        if body.startswith("panel_item_page_"):
            # فرمت بدنه: panel_item_page_{category_key}_{page}
            rest = body.replace("panel_item_page_", "")
            category_key, _, page_str = rest.rpartition("_")
            cat = PANEL_CATEGORIES.get(category_key)
            if not cat:
                await event.answer("دسته نامعتبر است.", alert=True)
                return
            await event.edit(
                _category_text(cat["title"]),
                buttons=_category_buttons(owner_id, owner_tg_id, category_key, page=int(page_str)),
            )
            return

        # ─── کلیک روی یک آیتم داخل دسته (toggle یا action) ─────────────────
        if body.startswith("panel_item_"):
            # فرمت بدنه: panel_item_{category_key}_{idx}
            rest = body.replace("panel_item_", "")
            category_key, _, idx_str = rest.rpartition("_")
            cat = PANEL_CATEGORIES.get(category_key)
            if not cat:
                await event.answer("دسته نامعتبر است.", alert=True)
                return

            items = build_category_commands(owner_id, category_key)
            idx = int(idx_str)
            if not (0 <= idx < len(items)):
                await event.answer("دستور نامعتبر است.", alert=True)
                return

            _, label, command_text, _style = items[idx]

            # ─── دکمه‌های فقط-اطلاع‌رسانی (مثل ماشین‌حساب/ترجمه) ────────────
            # این‌ها نیاز به ورودی متنی دارن، پس به‌جای اجرا روی سلف، فقط یک
            # توضیح کوتاه (toast) نشون داده می‌شه.
            if command_text.startswith("INFO::"):
                await _answer_info(event, command_text[len("INFO::"):])
                return

            await event.answer(f"در حال اجرا: {label}")
            await _execute_panel_command(self_client, owner_id, command_text)

            # بعد از اجرا، همون دسته رو با وضعیت/رنگ تازه دوباره رسم می‌کنیم
            page = idx // 8  # باید هم‌راستا با PANEL_PAGE_SIZE در telegram_bot.py باشه
            try:
                await event.edit(
                    _category_text(cat["title"]),
                    buttons=_category_buttons(owner_id, owner_tg_id, category_key, page=page),
                )
            except Exception:
                pass
            return

        await event.answer("دکمه نامعتبر است.", alert=True)

    return cl
