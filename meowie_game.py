# meowie_game.py
# ─────────────────────────────────────────────────────────────────────────────
# ماژول مدیریت خودکار بازی Meowie (@MeowieeeQBot) برای سلف‌بات.
#
# این فایل عمداً مستقل و ماژولار نوشته شده تا با کمترین تغییر توی bot.py
# قابل قلاب‌شدن (wire) باشه. سه چیز به بیرون می‌ده:
#
#   1) SETTING_DEFAULTS_EXTRA   → باید با SETTING_DEFAULTS توی
#      database_supabase.py مرج بشه (کلیدهای پیش‌فرض تنظیمات این ماژول).
#
#   2) register_handlers(cl, owner_id)  → داخل _register_handlers() در
#      bot.py صدا زده می‌شه؛ دو هندلر روی کلاینت سلف ثبت می‌کنه:
#        - رصد پیام دستیِ «میو» خودِ کاربر برای بایند کردن گروه بازی
#        - رصد پیام‌های ورودی از @MeowieeeQBot برای پارس امتیاز/کول‌داون
#          و کلیک خودکار دکمه‌ی «بده پیشی بخوره»
#
#   3) meowie_loop(cl, owner_id)  → یک تسک پس‌زمینه (asyncio) که باید کنار
#      بقیه‌ی حلقه‌ها (_clock_loop, _scheduler_loop, ...) با
#      asyncio.ensure_future(...) استارت و در پایان cancel بشه. این حلقه
#      طبق next_meow_ts/next_fish_ts ذخیره‌شده، دوباره «میو»/«ماهی» می‌فرسته.
#
#   4) handle_panel_command(text, owner_id, ss, edit) -> bool  → برای قلاب
#      شدن به دیسپچرِ متنیِ _handle_command؛ اگه True برگردوند یعنی دستور
#      مربوط به این ماژول بود و پردازش شد.
#
#   5) PANEL_CATEGORY  → دیکشنری آماده برای اضافه‌کردن به PANEL_CATEGORIES
#      (پنل دکمه‌ای) با کلید "meowie_game".
#
# نکته‌ی مهم درباره‌ی محدودیت‌های اخلاقی: این ماژول فقط پیام‌های متنیِ
# ساده (میو/ماهی) رو طبق تایمر اعلام‌شده توسط خودِ ربات بازی ارسال می‌کنه؛
# هیچ تلاشی برای اسپم بیشتر از چیزی که ربات بازی اجازه می‌ده، دور زدن
# کول‌داون، یا حمله/سوءاستفاده از حساب‌های دیگه انجام نمی‌ده.
# ─────────────────────────────────────────────────────────────────────────────

import re
import time
import asyncio

from telethon import events

# ─── تنظیمات ثابت ───────────────────────────────────────────────────────────
MEOWIE_BOT_USERNAME = "MeowieeeQBot"   # بدون @ (برای نمایش/لاگ)

# چون ربات بازی گاهی زیر دو یوزرنیم مختلف دیده شده (MeowieeeQBot و
# MeowieQBot)، هر دو رو قبول می‌کنیم تا پیام‌ها بسته به اینکه از کدوم
# یوزرنیم بیان، نادیده گرفته نشن.
MEOWIE_BOT_USERNAMES = {"meowieeeqbot", "meowieqbot"}

# فاصله‌ی امن پیش‌فرض بین دو ارسال، وقتی هنوز پاسخی از ربات نرسیده
# (صرفاً یک شبکه‌ی ایمنی در برابر لوپ سریع، نه دور زدن کول‌داون واقعی)
_FALLBACK_RETRY_SECONDS = 20

# کلیدهای تنظیمات این ماژول + مقدار پیش‌فرض؛ توی database_supabase.py
# باید با SETTING_DEFAULTS مرج بشه (init_user_settings خودکار این‌ها رو
# هم برای کاربر تازه مقداردهی می‌کنه).
SETTING_DEFAULTS_EXTRA = {
    "meowie_game_active": "0",       # وضعیت روشن/خاموش کلی قابلیت
    "meowie_game_group_id": "",      # آیدی عددی گروه بازی (بعد از بایند شدن)
    "meowie_next_meow_ts": "0",      # یونیکس‌تایمِ زمان مجاز بعدی برای «میو»
    "meowie_next_fish_ts": "0",      # یونیکس‌تایمِ زمان مجاز بعدی برای «ماهی»
    "meowie_last_meow_msg_id": "",   # آیدیِ آخرین پیام «میو»یی که خودِ همین کاربر فرستاده
    "meowie_last_fish_msg_id": "",   # آیدیِ آخرین پیام «ماهی»یی که خودِ همین کاربر فرستاده
    "meowie_pishi_started": "0",     # ۱ یعنی کاربر حداقل یک‌بار دستی «پیشی» فرستاده (شروعِ خودکارسازی)
    "meowie_next_pishi_ts": "0",     # یونیکس‌تایمِ زمانی که ظرفیتِ پیشی پر می‌شه (برای ارسال دوباره‌ی «پیشی»)
    "meowie_last_pishi_msg_id": "",  # آیدیِ آخرین پیام «پیشی»یی که خودِ همین کاربر فرستاده
    "meowie_cat_belly_cur": "0",     # آخرین مقدارِ شکمِ گربه (خونده‌شده از پیامِ «پیشی»)
    "meowie_cat_belly_max": "0",     # آخرین سقفِ شکمِ گربه (خونده‌شده از پیامِ «پیشی»)
    "meowie_upgrade_cost": "0",      # آخرین «هزینه ارتقا سطح» (خونده‌شده از پیامِ «پیشی»)
    "meowie_want_upgrade": "0",      # ۱ یعنی موجودی کافیه و باید دفعه‌ی بعد که پیامِ «پیشی» با دکمه اومد، «ارتقا سطح» کلیک بشه
    "meowie_last_myohaam_msg_id": "",  # آیدیِ آخرین پیام «میوهام»یی که خودِ همین کاربر فرستاده
    "meowie_next_myohaam_ts": "0",     # یونیکس‌تایمِ زمانِ ارسال بعدیِ «میوهام»
    "meowie_myohaam_interval_seconds": "21600",  # فاصله‌ی پیش‌فرض بین دو «میوهام» (۶ ساعت)

    # ─── تنظیمات ریز قابلیت‌های خودکار (زیرمنوی «⚙️ تنظیمات») ─────────────────
    # همه‌ی این‌ها پیش‌فرضشون روشنه (همون رفتار قبلی که این تنظیمات نبودن)؛
    # کاربر می‌تونه هرکدوم رو جدا از بقیه از توی پنل خاموش کنه، بدون اینکه
    # کل «بازی میویی» رو خاموش کنه.
    "meowie_auto_meow_active": "1",     # ارسال خودکار «میو» توسط حلقه‌ی پس‌زمینه
    "meowie_auto_fish_active": "1",     # ارسال خودکار «ماهی» توسط حلقه‌ی پس‌زمینه
    "meowie_auto_sell_fish_active": "1",  # وقتی گربه سیره، فروش/انداختن‌توی‌یخچالِ خودکارِ ماهی
    "meowie_auto_pishi_active": "1",    # ارسال خودکار «پیشی» توسط حلقه‌ی پس‌زمینه (برای پر شدن ظرفیت)
    "meowie_auto_withdraw_active": "1",  # کلیک خودکار دکمه‌ی «برداشت میو پوینت ها»
    "meowie_auto_upgrade_active": "1",  # ارسال «پیشی» + کلیک خودکار «ارتقا سطح» وقتی موجودی کافیه
    "meowie_auto_myohaam_active": "1",  # ارسال خودکار «میوهام» (چک دوره‌ای موجودی/ارتقا)
}

# ارزشِ ریالیِ ماهی که اگه بیشتر یا مساویش باشه، حتی وقتی گربه سیره هم
# ماهی فروخته می‌شه (به‌جای انداختنش تو یخچال).
_FISH_SELL_THRESHOLD = 1000

_PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")

# کاراکترهای نامرئیِ جهت‌دهی (LRM/RLM/LRE/RLE/PDF/isolates/ZWSP) که تلگرام یا
# کیبورد گوشی گاهی بین رقم‌های انگلیسیِ زمان (مثل «5:00») و متن فارسی اطراف‌شون
# درج می‌کنه. اگه حذف نشن، رجکس (\d+):(\d+) به‌خاطر همین کاراکترهای نامرئی
# بین رقم و «:» شکست می‌خوره و زمان اصلاً پارس نمی‌شه — این شایع‌ترین دلیلِ
# «تشخیص داد ولی دوباره خودکار انجام نشد» هست.
_INVISIBLE_RE = re.compile("[\u200b\u200e\u200f\u202a-\u202e\u2066-\u2069]")


def _clean(s: str) -> str:
    s = (s or "").translate(_PERSIAN_DIGITS)
    s = _INVISIBLE_RE.sub("", s)
    return s


def _normalize_digits(s: str) -> str:
    return _clean(s)


# ─── تاگل‌های ریزِ زیرمنوی «⚙️ تنظیمات» ────────────────────────────────────
# فرمت هر آیتم: (کلیدِ تنظیم توی دیتابیس، برچسبِ فارسی، دستورِ متنیِ روشن‌کردن،
# دستورِ متنیِ خاموش‌کردن). این لیست هم برای ساختِ دکمه‌های پنل (پایین) و هم
# برای پردازشِ خودِ دستورها توی handle_panel_command استفاده می‌شه، تا لازم
# نباشه هر تاگل رو دوبار (یک‌بار برای دکمه، یک‌بار برای پردازش دستور) بنویسیم.
SETTINGS_TOGGLES = [
    ("meowie_auto_meow_active", "میو خودکار", "میو خودکار روشن", "میو خودکار خاموش"),
    ("meowie_auto_fish_active", "ماهی خودکار", "ماهی خودکار روشن", "ماهی خودکار خاموش"),
    ("meowie_auto_sell_fish_active", "فروش ماهی خودکار", "فروش ماهی خودکار روشن", "فروش ماهی خودکار خاموش"),
    ("meowie_auto_pishi_active", "پیشی خودکار", "پیشی خودکار روشن", "پیشی خودکار خاموش"),
    ("meowie_auto_withdraw_active", "برداشت خودکار امتیاز", "برداشت خودکار امتیاز روشن", "برداشت خودکار امتیاز خاموش"),
    ("meowie_auto_upgrade_active", "ارتقا خودکار پیشی", "ارتقا خودکار پیشی روشن", "ارتقا خودکار پیشی خاموش"),
    ("meowie_auto_myohaam_active", "چک خودکار موجودی", "چک خودکار موجودی روشن", "چک خودکار موجودی خاموش"),
]


# ─── پنل دکمه‌ای ─────────────────────────────────────────────────────────────
PANEL_CATEGORY = {
    "title": "مدیریت بازی میویی",
    "menu_style": "primary",
    "toggles": [
        ("meowie_game_active", "بازی میویی", "بازی میویی روشن", "بازی میویی خاموش"),
    ],
    "actions": [
        (
            "📖 راهنما",
            "INFO::🐾 دکمه‌ی بالا رو روشن کن. بعد داخل گروه بازی یک‌بار دستی "
            "بنویس «میو» تا گروه ذخیره بشه. از اون به بعد میو/ماهی خودکاره. "
            "برای پیشی هم کافیه یک‌بار دستی «پیشی» رو داخل همون گروه بفرستی؛ "
            "از اون به بعد خودش وقتی ظرفیت پر شد دوباره «پیشی» می‌فرسته و "
            "دکمه‌ی «برداشت میو پوینت ها» رو می‌زنه. "
            "برای عوض کردن گروه، «ریست گروه بازی میویی» رو بفرست.\n"
            "🔧 برای روشن/خاموش کردن هرکدوم از قابلیت‌ها به‌صورت جدا "
            "(مثلاً فقط فروش ماهی یا فقط ارتقا) وارد «⚙️ تنظیمات» شو.",
        ),
        ("🗑 حذف گروه میویی", "حذف گروه میویی"),
    ],
    "children": [("⚙️ تنظیمات", "meowie_settings")],
}

# زیرمنوی «⚙️ تنظیمات» — فقط از طریق دکمه‌ی «⚙️ تنظیمات» توی «مدیریت بازی
# میویی» در دسترسه (خودش توی منوی اصلیِ پنل نشون داده نمی‌شه). باید توسطِ
# bot.py توی PANEL_CATEGORIES با کلیدِ "meowie_settings" ثبت بشه (دقیقاً مثل
# clock_font و friend_enemy_friend/enemy).
SETTINGS_PANEL_CATEGORY = {
    "title": "⚙️ تنظیمات میویی",
    "menu_style": "primary",
    "toggles": [(key, label, on_cmd, off_cmd) for key, label, on_cmd, off_cmd in SETTINGS_TOGGLES],
    "actions": [],
    "parent": "meowie_game",
}


# ─── دیسپچر دستورهای متنی (برای _handle_command) ────────────────────────────
def handle_panel_command(text: str, owner_id: int, ss, gs, edit_coro_factory) -> bool:
    """
    اگه text یکی از دستورهای متنیِ این ماژول بود، پردازشش می‌کنه و True
    برمی‌گردونه. edit_coro_factory یک تابعِ async(t) هست (همون `edit` محلیِ
    _handle_command) که پیام نتیجه رو نمایش می‌ده. فراخوان باید نتیجه رو
    await کنه اگه True برگشت و coroutine ای برگردونده شده باشه.
    این تابع خودش async نیست تا قلاب‌کردنش ساده باشه؛ کالر باید به این شکل
    صدا بزنه:

        handled, coro = meowie_game.handle_panel_command(text, owner_id, ss, gs, edit)
        if handled:
            await coro
            return  (در بدنه‌ی _handle_command به شکل elif ادامه پیدا می‌کنه)
    """
    if text == "بازی میویی روشن":
        ss("meowie_game_active", "1")
        if not gs("meowie_game_group_id", ""):
            msg = (
                "🐱 بازی میویی روشن شد.\n"
                "📍 حالا داخل گروهی که می‌خوای بازی توش انجام بشه، یک‌بار دستی "
                "بنویس «میو» تا همون گروه به‌عنوان گروه بازی ثبت بشه."
            )
        else:
            msg = "🐱 بازی میویی روشن شد و روی گروه قبلاً ثبت‌شده ادامه پیدا می‌کنه."
        return True, edit_coro_factory(msg)

    if text == "بازی میویی خاموش":
        ss("meowie_game_active", "0")
        return True, edit_coro_factory("🐱 بازی میویی خاموش شد.")

    if text in ("ریست گروه بازی میویی", "حذف گروه میویی"):
        ss("meowie_game_group_id", "")
        return True, edit_coro_factory(
            "🗑 گروه بازی میویی حذف شد. دفعه‌ی بعد که «میو» رو داخل هر "
            "گروهی (دستی) بنویسی، همون گروه به‌عنوان گروه جدید ثبت می‌شه."
        )

    # ─── تاگل‌های ریزِ زیرمنوی «⚙️ تنظیمات» — همه‌شون از روی یک لیست واحد
    # (SETTINGS_TOGGLES) پردازش می‌شن تا لازم نباشه هفت‌تا if/elif جدا بنویسیم.
    for key, label, on_cmd, off_cmd in SETTINGS_TOGGLES:
        if text == on_cmd:
            ss(key, "1")
            return True, edit_coro_factory(f"✅ {label} روشن شد.")
        if text == off_cmd:
            ss(key, "0")
            return True, edit_coro_factory(f"⛔️ {label} خاموش شد.")

    return False, None


# ─── هندلرهای Telethon (برای _register_handlers) ────────────────────────────
def register_handlers(cl, owner_id: int, db):
    """
    دو هندلر روی کلاینت سلفِ owner_id ثبت می‌کنه. db همون ماژول database
    (یا database_supabase) پروژه‌ست که get_setting/set_setting داره.
    """

    def gs(key, default=None):
        return db.get_setting(owner_id, key, default)

    def ss(key, value):
        db.set_setting(owner_id, key, value)

    # 1) پیام دستیِ «میو» خودِ کاربر → بایند کردن گروه بازی (فقط یک‌بار) و
    # ردگیریِ آیدیِ پیام (برای اینکه بعداً بشه پاسخِ ربات رو دقیقاً به همین
    # کاربر نسبت داد، نه به هر کاربر دیگه‌ای که توی همون گروهه).
    @cl.on(events.NewMessage(outgoing=True, pattern=r"^\s*میو\s*$"))
    async def _meowie_bind_group(event):
        try:
            if gs("meowie_game_active", "0") != "1":
                return
            if not event.is_group:
                return

            # همیشه آیدیِ آخرین «میو»یی که خودمون فرستادیم رو ثبت کن — چه
            # برای بایند اولیه، چه برای «میو»هایی که بعداً خودِ کاربر دستی
            # می‌فرسته. این آیدی توی _process برای تشخیص «این پاسخ واقعاً
            # جواب منه یا جواب یه کاربر دیگه‌ست» استفاده می‌شه.
            ss("meowie_last_meow_msg_id", str(event.message.id))

            if gs("meowie_game_group_id", ""):
                return  # قبلاً بایند شده، فقط ردگیری آیدی کافی بود

            ss("meowie_game_group_id", str(event.chat_id))
            # ⏳ به‌جای صفر، یه فاصله‌ی کوتاه (grace) می‌ذاریم؛ چون همین «میو»یی
            # که کاربر الان دستی فرستاد قبلاً کول‌داون رو مصرف کرده و بات
            # میویی چند لحظه دیگه با زمان واقعی جواب می‌ده. اگه این‌جا صفر
            # می‌ذاشتیم، حلقه‌ی پس‌زمینه ممکن بود قبل از رسیدن همون جواب،
            # یه «میو»ی تکراری و زودهنگام بفرسته.
            ss("meowie_next_meow_ts", str(time.time() + 15))
            ss("meowie_next_fish_ts", str(time.time() + 15))
            ss("meowie_next_myohaam_ts", str(time.time() + 15))
            try:
                await event.reply(
                    "🐾 این گروه به‌عنوان گروه بازی میویی ثبت شد. از این به بعد "
                    "میو/ماهی به‌صورت خودکار مدیریت می‌شه."
                )
            except Exception:
                pass
        except Exception as e:
            print(f"❌ [{owner_id}] خطا در بایند گروه بازی میویی: {e}")

    # 1ب) پیام دستیِ «ماهی» خودِ کاربر → فقط ردگیریِ آیدی (برای همون دلیل بالا)
    @cl.on(events.NewMessage(outgoing=True, pattern=r"^\s*ماهی\s*$"))
    async def _meowie_track_fish(event):
        try:
            if gs("meowie_game_active", "0") != "1":
                return
            if not event.is_group:
                return
            if gs("meowie_game_group_id", "") != str(event.chat_id):
                return
            ss("meowie_last_fish_msg_id", str(event.message.id))
        except Exception as e:
            print(f"❌ [{owner_id}] خطا در ردگیری پیام ماهی: {e}")

    # 1ج) پیام دستیِ «پیشی» خودِ کاربر → ردگیریِ آیدی + فعال کردنِ خودکارسازیِ
    # پیشی (فقط با اولین بارِ فرستادنِ دستیِ آن توسط کاربر شروع می‌شه، نه
    # خودبه‌خود از لحظه‌ی بایند شدنِ گروه؛ چون تا وقتی خودِ کاربر یک‌بار
    # وضعیتِ گربه رو نبینه، ما نه نرخ تولید داریم نه ظرفیت، پس چیزی برای
    # زمان‌بندی نداریم).
    @cl.on(events.NewMessage(outgoing=True, pattern=r"^\s*پیشی\s*$"))
    async def _meowie_track_pishi(event):
        try:
            if gs("meowie_game_active", "0") != "1":
                return
            if not event.is_group:
                return
            if gs("meowie_game_group_id", "") != str(event.chat_id):
                return
            ss("meowie_last_pishi_msg_id", str(event.message.id))
            ss("meowie_pishi_started", "1")
        except Exception as e:
            print(f"❌ [{owner_id}] خطا در ردگیری پیام پیشی: {e}")

    # 1د) پیام دستیِ «میوهام» خودِ کاربر → فقط ردگیریِ آیدی (خودِ ارسالِ
    # خودکارش وابسته به هیچ آماری نیست، پس نیازی به فلگِ جداگونه‌ی «شروع
    # شد یا نه» مثل پیشی نداره؛ همون بایند شدنِ گروه کافیه).
    @cl.on(events.NewMessage(outgoing=True, pattern=r"^\s*میوهام\s*$"))
    async def _meowie_track_myohaam(event):
        try:
            if gs("meowie_game_active", "0") != "1":
                return
            if not event.is_group:
                return
            if gs("meowie_game_group_id", "") != str(event.chat_id):
                return
            ss("meowie_last_myohaam_msg_id", str(event.message.id))
        except Exception as e:
            print(f"❌ [{owner_id}] خطا در ردگیری پیام میوهام: {e}")

    # 2) پیام‌های ورودی از @MeowieeeQBot داخل گروه ثبت‌شده — هم پیام‌های
    # تازه (NewMessage) و هم پیام‌هایی که ادیت می‌شن (MessageEdited).
    #
    # چرا MessageEdited هم لازمه؟ چون ربات بازی معمولاً اول پیام رو بدون
    # دکمه می‌فرسته (مثلاً حالت «در حال گرفتن ماهی...») و چند ثانیه بعد
    # (طبق مشاهده‌ی شما ۱۰ تا ۱۵ ثانیه) همون پیام رو ادیت می‌کنه و دکمه‌ی
    # «بده پیشی بخوره» رو بهش اضافه می‌کنه. اگه فقط NewMessage گوش بدیم،
    # دکمه‌ای که بعداً با ادیت اضافه شده رو اصلاً نمی‌بینیم.
    async def _process(event):
        try:
            if gs("meowie_game_active", "0") != "1":
                return

            group_id_raw = gs("meowie_game_group_id", "")
            if not group_id_raw:
                return
            try:
                if event.chat_id != int(group_id_raw):
                    return
            except (TypeError, ValueError):
                return

            sender = await event.get_sender()
            sender_username = (getattr(sender, "username", "") or "").lower()
            if sender_username not in MEOWIE_BOT_USERNAMES:
                return

            text = event.raw_text or ""
            clean_text = _clean(text)
            now = time.time()
            reply_to_id = getattr(event.message, "reply_to_msg_id", None)

            print(f"🐾 [{owner_id}] پیام از @{sender_username} ({'edit' if isinstance(event, events.MessageEdited.Event) else 'new'}, reply_to={reply_to_id}): {text[:200]!r}")

            def _is_mine(expected_key: str) -> bool:
                """
                چون ممکنه چند کاربرِ مختلفِ همین سیستم عضوِ یه گروهِ مشترک
                باشن، همه‌شون همین پیام‌های ربات بازی رو می‌بینن. برای اینکه
                تایمرِ هر کاربر فقط از روی پاسخِ دستورِ خودش آپدیت بشه (نه
                دستورِ یه کاربر دیگه)، اگه پیام یه ریپلای باشه، فقط وقتی
                قبولش می‌کنیم که دقیقاً ریپلایِ همون پیامی باشه که خودمون
                فرستادیم. اگه پیام اصلاً ریپلای نبود (بعضی نسخه‌های ربات
                شاید ریپلای نکنن)، برای عقب نگه‌نداشتنِ کاربرهایی که تنها
                عضو گروهشونن، محتاطانه قبولش می‌کنیم.
                """
                if reply_to_id is None:
                    return True
                expected = gs(expected_key, "")
                if not expected:
                    return True
                try:
                    return int(expected) == int(reply_to_id)
                except (TypeError, ValueError):
                    return True

            # (الف) پیام‌های دکمه‌دار — دو نوعِ کاملاً متفاوت داریم که نباید
            # قاطیِ هم بشن: (۱) پیام صید ماهی با دکمه‌ی «بده پیشی بخوره»،
            # (۲) پیام وضعیتِ گربه/پیشی با دکمه‌ی «برداشت میو پوینت ها».
            # قبلاً این‌جا با دیدنِ *هر* پیامِ دکمه‌دار بلافاصله چکِ
            # «meowie_last_fish_msg_id» انجام می‌شد و اگه مچ نمی‌شد کل تابع
            # return می‌کرد — همین باعث می‌شد پیامِ وضعیتِ پیشی (که ریپلایِ
            # دستورِ «ماهی» نیست) اصلاً به بخشِ پارسِ پیشی نرسه. برای رفعش،
            # اول بر اساسِ متنِ خودِ دکمه‌ها تشخیص می‌دیم کدوم پیامه، بعد
            # فقط همون‌جوری‌ که مربوطه رو با _is_mine چک می‌کنیم.
            buttons = getattr(event.message, "buttons", None)
            has_fish_btn = False
            has_pishi_withdraw_btn = False
            has_sell_fish_btn = False
            has_freezer_btn = False
            has_upgrade_btn = False
            if buttons:
                for row in buttons:
                    for btn in row:
                        btn_text = getattr(btn, "text", "") or ""
                        if "بده پیشی بخوره" in btn_text:
                            has_fish_btn = True
                        if "برداشت میو پوینت ها" in btn_text:
                            has_pishi_withdraw_btn = True
                        if "فروش ماهی" in btn_text:
                            has_sell_fish_btn = True
                        if "بندازش تو یخچال" in btn_text:
                            has_freezer_btn = True
                        if "ارتقا سطح" in btn_text:
                            has_upgrade_btn = True

            if has_fish_btn:
                if not _is_mine("meowie_last_fish_msg_id"):
                    print(f"⏭️ [{owner_id}] پیام صید ماهی مالِ یه کاربر دیگه‌ست (ریپلای مچ نشد) — نادیده گرفته شد.")
                    return

                # گربه سیره یا نه؟ از آخرین آماری که از پیامِ «پیشی» خونده و
                # ذخیره کرده بودیم استفاده می‌کنیم (belly_cur >= belly_max).
                belly_cur = float(gs("meowie_cat_belly_cur", "0") or "0")
                belly_max = float(gs("meowie_cat_belly_max", "0") or "0")
                cat_is_full = belly_max > 0 and belly_cur >= belly_max

                target_btn_text = None
                if cat_is_full:
                    if gs("meowie_auto_sell_fish_active", "1") != "1":
                        # فروش ماهی خودکار خاموشه — فقط اگه یخچال داشته باشیم
                        # ماهی رو بی‌خطر نگه می‌داریم، وگرنه دست نمی‌زنیم و
                        # می‌ذاریم کاربر خودش تصمیم بگیره.
                        if has_freezer_btn:
                            target_btn_text = "بندازش تو یخچال"
                            print(f"🧊 [{owner_id}] گربه سیره ولی «فروش ماهی خودکار» خاموشه → فقط انداختن تو یخچال.")
                        else:
                            print(f"⏸️ [{owner_id}] گربه سیره و «فروش ماهی خودکار» خاموشه و یخچالی هم نیست — کاری انجام نشد.")
                            return
                    else:
                        fish_value_m = re.search(r"ارزش\s*:\s*([\d,\.]+)", clean_text)
                        fish_value = None
                        if fish_value_m:
                            try:
                                fish_value = float(fish_value_m.group(1).replace(",", ""))
                            except ValueError:
                                fish_value = None

                        if fish_value is not None and fish_value >= _FISH_SELL_THRESHOLD:
                            target_btn_text = "فروش ماهی"
                            print(f"🐟 [{owner_id}] گربه سیره و ارزشِ ماهی ({fish_value:g}) >= {_FISH_SELL_THRESHOLD} → فروش.")
                        elif has_freezer_btn:
                            target_btn_text = "بندازش تو یخچال"
                            print(f"🐟 [{owner_id}] گربه سیره و ارزشِ ماهی کمه ولی یخچال دارد → انداختن تو یخچال.")
                        else:
                            target_btn_text = "فروش ماهی"
                            print(f"🐟 [{owner_id}] گربه سیره، ارزشِ ماهی کمه ولی هنوز یخچال نخریده → فروش به‌جاش.")
                else:
                    target_btn_text = "بده پیشی بخوره"

                for row in buttons:
                    for btn in row:
                        btn_text = getattr(btn, "text", "") or ""
                        if target_btn_text in btn_text:
                            print(f"🎣 [{owner_id}] دکمه‌ی «{btn_text}» پیدا شد — در حال کلیک...")
                            try:
                                await event.message.click(text=btn_text)
                                print(f"✅ [{owner_id}] کلیک دکمه‌ی «{btn_text}» موفق بود.")
                            except Exception as e:
                                print(f"❌ [{owner_id}] خطا در کلیک دکمه‌ی «{btn_text}»: {e}")
                            return
                print(f"❔ [{owner_id}] دکمه‌ی «{target_btn_text}» توی پیام پیدا نشد.")
                return

            # (ب) تایید خورده‌شدن ماهی توسط پیشی → دوباره «ماهی» بفرست
            if "پیشی خوردش" in clean_text:
                if not _is_mine("meowie_last_fish_msg_id"):
                    print(f"⏭️ [{owner_id}] پیام «پیشی خوردش» مالِ یه کاربر دیگه‌ست — نادیده گرفته شد.")
                    return
                ss("meowie_next_fish_ts", "0")
                print(f"🐟 [{owner_id}] پیشی ماهی رو خورد — دوباره «ماهی» فرستاده می‌شه.")
                try:
                    sent = await cl.send_message(event.chat_id, "ماهی")
                    ss("meowie_last_fish_msg_id", str(sent.id))
                except Exception as e:
                    print(f"❌ [{owner_id}] خطا در ارسال مجدد ماهی: {e}")
                return

            # (ب-۲) پیام وضعیتِ گربه/پیشی — همون پیامی که با نوشتنِ «پیشی»
            # می‌گیریم و توش «تولید میو پوینت در ثانیه» و «ظرفیت» هست.
            # اینجا نه فقط وقتی دکمه‌ی برداشت داره بلکه هر وقت این دو عبارت
            # با هم پیدا بشن پردازش می‌کنیم (چون بعضی وقتا دکمه دیرتر با
            # ادیت اضافه می‌شه، ولی خودِ آمار از همون پیامِ اول موجوده).
            if "تولید میو پوینت در ثانیه" in clean_text and "ظرفیت" in clean_text:
                if not _is_mine("meowie_last_pishi_msg_id"):
                    print(f"⏭️ [{owner_id}] پیام وضعیتِ پیشی مالِ یه کاربر دیگه‌ست — نادیده گرفته شد.")
                    return

                def _num(pattern):
                    mm = re.search(pattern, clean_text)
                    if not mm:
                        return None
                    raw = mm.group(1).replace(",", "").strip()
                    try:
                        return float(raw)
                    except ValueError:
                        return None

                produced = _num(r"میو\s*پوینت\s*های\s*تولید\s*شده\s*:\s*([\d,\.]+)")
                rate = _num(r"تولید\s*میو\s*پوینت\s*در\s*ثانیه\s*:\s*([\d,\.]+)")
                capacity = _num(r"ظرفیت\s*:\s*([\d,\.]+)")
                upgrade_cost = _num(r"هزینه\s*ارتقا\s*سطح\s*:\s*([\d,\.]+)")
                if upgrade_cost is not None:
                    ss("meowie_upgrade_cost", str(upgrade_cost))

                # شکمِ گربه («شکم :  عاشقتمیووو (8 / 8)») رو هم همین‌جا ذخیره
                # می‌کنیم تا بعداً موقع پردازشِ پیامِ «ماهی» بدونیم گربه سیره
                # یا نه (بدونِ این ذخیره‌سازی، موقعِ گرفتنِ ماهی هیچ اطلاعی
                # از وضعیتِ فعلیِ شکمِ گربه نداریم).
                mb = re.search(r"شکم\s*:.*?\(\s*([\d,\.]+)\s*/\s*([\d,\.]+)\s*\)", clean_text)
                if mb:
                    try:
                        belly_cur = float(mb.group(1).replace(",", ""))
                        belly_max = float(mb.group(2).replace(",", ""))
                        ss("meowie_cat_belly_cur", str(belly_cur))
                        ss("meowie_cat_belly_max", str(belly_max))
                        print(f"🐈 [{owner_id}] شکمِ گربه: {belly_cur:g} / {belly_max:g}")
                    except ValueError:
                        pass

                # اگه از پیامِ «میوهام» فهمیده بودیم موجودی برای ارتقا کافیه
                # (meowie_want_upgrade == "1")، و این پیامِ پیشی الان دکمه‌ی
                # «ارتقا سطح» رو داره، همین‌جا کلیکش کن.
                if gs("meowie_want_upgrade", "0") == "1" and has_upgrade_btn:
                    if gs("meowie_auto_upgrade_active", "1") != "1":
                        print(f"⏸️ [{owner_id}] موجودی برای ارتقا کافیه ولی «ارتقا خودکار پیشی» خاموشه — کاری انجام نشد.")
                        ss("meowie_want_upgrade", "0")
                    else:
                        for row in buttons:
                            for btn in row:
                                btn_text = getattr(btn, "text", "") or ""
                                if "ارتقا سطح" in btn_text:
                                    print(f"⬆️ [{owner_id}] موجودی کافیه — کلیکِ دکمه‌ی «{btn_text}»...")
                                    try:
                                        await event.message.click(text=btn_text)
                                        print(f"✅ [{owner_id}] ارتقاءِ سطح موفق بود.")
                                    except Exception as e:
                                        print(f"❌ [{owner_id}] خطا در کلیک دکمه‌ی ارتقا: {e}")
                                    ss("meowie_want_upgrade", "0")
                                    return

                if produced is None or rate is None or capacity is None:
                    print(f"❔ [{owner_id}] پیام وضعیتِ پیشی پیدا شد ولی نتونستم عدد تولیدشده/نرخ/ظرفیت رو استخراج کنم.")
                    return

                remaining = capacity - produced

                if rate > 0 and remaining > 0:
                    secs_needed = remaining / rate
                    next_ts = now + secs_needed
                    ss("meowie_next_pishi_ts", str(next_ts))
                    print(
                        f"🐱 [{owner_id}] پیشی: تولیدشده={produced:g} نرخ={rate:g}/ثانیه "
                        f"ظرفیت={capacity:g} → {secs_needed:.0f} ثانیه تا پر شدن "
                        f"(ارسال بعدی حدود {time.strftime('%H:%M:%S', time.localtime(next_ts))})."
                    )
                    return

                # ظرفیت پره (یا نرخ صفر/منفیه) → همین الان دکمه‌ی برداشت رو بزن
                print(f"🐱 [{owner_id}] پیشی: ظرفیت پره (تولیدشده={produced:g} / ظرفیت={capacity:g}) — تلاش برای برداشت.")
                if gs("meowie_auto_withdraw_active", "1") != "1":
                    print(f"⏸️ [{owner_id}] ظرفیت پره ولی «برداشت خودکار امتیاز» خاموشه — کاری انجام نشد.")
                    return
                if has_pishi_withdraw_btn:
                    for row in buttons:
                        for btn in row:
                            btn_text = getattr(btn, "text", "") or ""
                            if "برداشت میو پوینت ها" in btn_text:
                                print(f"💰 [{owner_id}] دکمه‌ی «{btn_text}» پیدا شد — در حال کلیک...")
                                try:
                                    await event.message.click(text=btn_text)
                                    print(f"✅ [{owner_id}] برداشتِ میو پوینت‌ها موفق بود.")
                                except Exception as e:
                                    print(f"❌ [{owner_id}] خطا در کلیک دکمه‌ی برداشت: {e}")
                                # بعد از برداشت، یه فاصله‌ی کوتاه بذار تا لوپِ پس‌زمینه
                                # دوباره «پیشی» بفرسته و آمارِ تازه (صفرشده) رو بگیره.
                                ss("meowie_next_pishi_ts", str(time.time() + 5))
                                return
                    print(f"❔ [{owner_id}] دکمه‌ی برداشت پیدا نشد؛ شاید هنوز با ادیت اضافه نشده.")
                else:
                    # دکمه هنوز نیومده (احتمالاً با ادیتِ بعدی میاد) — کمی بعد دوباره چک کن
                    ss("meowie_next_pishi_ts", str(time.time() + 5))
                return

            # (ب-۳) پیام پروفایل «میوهام» — توش «میو پوینت ها : X» کلِ
            # موجودیِ کاربره. اگه این موجودی به هزینه‌ی ارتقا (که از پیامِ
            # پیشی ذخیره کرده بودیم) برسه، دوباره «پیشی» می‌فرستیم تا
            # دکمه‌ی «ارتقا سطح» بیاد و فلگِ meowie_want_upgrade رو ۱ می‌کنیم
            # تا همون‌جا (بخشِ بالا) کلیک بشه.
            if "پروفایل میویی" in clean_text or ("میو پوینت ها" in clean_text and "رتبه" in clean_text):
                if not _is_mine("meowie_last_myohaam_msg_id"):
                    print(f"⏭️ [{owner_id}] پیام میوهام مالِ یه کاربر دیگه‌ست — نادیده گرفته شد.")
                    return

                pm = re.search(r"میو\s*پوینت\s*ها\s*:\s*([\d,\.]+)", clean_text)
                if not pm:
                    print(f"❔ [{owner_id}] پیام میوهام پیدا شد ولی نتونستم عددِ میو پوینت ها رو استخراج کنم.")
                    return
                try:
                    total_points = float(pm.group(1).replace(",", ""))
                except ValueError:
                    print(f"❔ [{owner_id}] عددِ میو پوینت ها ({pm.group(1)!r}) قابلِ تبدیل نبود.")
                    return

                upgrade_cost = float(gs("meowie_upgrade_cost", "0") or "0")
                print(f"📋 [{owner_id}] میوهام: موجودی={total_points:g} / هزینه‌ی ارتقا={upgrade_cost:g}")

                if upgrade_cost > 0 and total_points >= upgrade_cost:
                    if gs("meowie_auto_upgrade_active", "1") != "1":
                        print(f"⏸️ [{owner_id}] موجودی کافیه برای ارتقا ولی «ارتقا خودکار پیشی» خاموشه — کاری انجام نشد.")
                        return
                    print(f"⬆️ [{owner_id}] موجودی کافیه برای ارتقا — ارسالِ «پیشی» برای گرفتنِ دکمه.")
                    ss("meowie_want_upgrade", "1")
                    try:
                        sent = await cl.send_message(event.chat_id, "پیشی")
                        ss("meowie_last_pishi_msg_id", str(sent.id))
                    except Exception as e:
                        print(f"❌ [{owner_id}] خطا در ارسالِ پیشی برای ارتقا: {e}")
                else:
                    print(f"ℹ️ [{owner_id}] موجودی کافی نیست — کاری برای ارتقا انجام نمی‌شه.")
                return

            # (ج) پیام‌های کول‌داون/امتیاز میو یا ماهی — به‌جای تکیه به
            # حرف‌اضافه‌ی «بعد از»/«باید» (که جهتش وابسته به نسخه‌ی ربات و
            # می‌تونه برعکس باشه)، فقط MM:SS رو پیدا می‌کنیم و بر اساس
            # اسم موضوع («ماهی» در برابر «میو») تصمیم می‌گیریم که این زمان
            # مال کدوم تایمره. «ماهی» رو اول چک می‌کنیم چون احتمالاً «میو»
            # به‌عنوان برندینگ ربات (Meowie) توی خیلی از پیام‌ها هست، ولی
            # «ماهی» فقط توی پیام‌های واقعاً مربوط به ماهی میاد.
            #
            # نکته: این تشخیص کاملاً مستقل از اینه که چه چیزی باعث این پاسخ
            # شده — چه حلقه‌ی خودکار «میو»/«ماهی» فرستاده باشه، چه خودِ
            # کاربر دستی نوشته باشه. یعنی اگه کاربر خودش وسط کار «میو» یا
            # «ماهی» بنویسه و ربات یه زمانِ متفاوت از چیزی که قبلاً ذخیره
            # کرده بودیم برگردونه، همین‌جا مقدار ذخیره‌شده رو با مقدار
            # واقعیِ جدید عوض می‌کنیم (فقط اگه واقعاً فرق داشته باشه).
            m = re.search(r"(\d+):(\d+)", clean_text)
            if m:
                secs = int(m.group(1)) * 60 + int(m.group(2))
                new_ts = now + secs
                if "ماهی" in clean_text:
                    if not _is_mine("meowie_last_fish_msg_id"):
                        print(f"⏭️ [{owner_id}] کول‌داون ماهی مالِ یه کاربر دیگه‌ست — نادیده گرفته شد.")
                        return
                    old_ts = float(gs("meowie_next_fish_ts", "0") or "0")
                    if abs(new_ts - old_ts) >= 1:
                        ss("meowie_next_fish_ts", str(new_ts))
                        print(f"⏱️ [{owner_id}] (تشخیص: ماهی) تایمر عوض شد: {old_ts:.0f} → {new_ts:.0f} (+{secs}s)")
                    else:
                        print(f"⏱️ [{owner_id}] (تشخیص: ماهی) تایمر فرقی نکرده، عوض نشد.")
                elif "میو" in clean_text:
                    if not _is_mine("meowie_last_meow_msg_id"):
                        print(f"⏭️ [{owner_id}] کول‌داون میو مالِ یه کاربر دیگه‌ست — نادیده گرفته شد.")
                        return
                    old_ts = float(gs("meowie_next_meow_ts", "0") or "0")
                    if abs(new_ts - old_ts) >= 1:
                        ss("meowie_next_meow_ts", str(new_ts))
                        print(f"⏱️ [{owner_id}] (تشخیص: میو) تایمر عوض شد: {old_ts:.0f} → {new_ts:.0f} (+{secs}s)")
                    else:
                        print(f"⏱️ [{owner_id}] (تشخیص: میو) تایمر فرقی نکرده، عوض نشد.")
                else:
                    print(f"❔ [{owner_id}] زمان {secs} ثانیه پیدا شد ولی معلوم نشد میو مال میو هست یا ماهی — پیام رو بالا ببین.")
                return

            print(f"❔ [{owner_id}] پیام از @{sender_username} با هیچ الگویی مچ نشد (بالا رو ببین).")

        except Exception as e:
            print(f"❌ [{owner_id}] خطا در پردازش پیام بازی میویی: {e}")

    @cl.on(events.NewMessage(incoming=True))
    async def _meowie_incoming(event):
        await _process(event)

    @cl.on(events.MessageEdited(incoming=True))
    async def _meowie_edited(event):
        await _process(event)


# ─── حلقه‌ی پس‌زمینه (برای asyncio.ensure_future کنار بقیه‌ی لوپ‌ها) ─────────
async def meowie_loop(cl, owner_id: int, db):
    """
    هر چند ثانیه چک می‌کنه که آیا وقتِ ارسال دوباره‌ی «میو» یا «ماهی» رسیده؛
    اگه رسیده باشه می‌فرسته. زمان‌بندی واقعی از روی پاسخ خودِ ربات بازی
    (توسط register_handlers) به‌روزرسانی می‌شه، این حلقه فقط trigger می‌کنه.
    """
    while True:
        try:
            if db.get_setting(owner_id, "meowie_game_active", "0") != "1":
                await asyncio.sleep(5)
                continue

            group_id_raw = db.get_setting(owner_id, "meowie_game_group_id", "")
            if not group_id_raw:
                await asyncio.sleep(5)
                continue

            try:
                group_id = int(group_id_raw)
            except ValueError:
                await asyncio.sleep(5)
                continue

            now = time.time()

            next_meow = float(db.get_setting(owner_id, "meowie_next_meow_ts", "0") or "0")
            if now >= next_meow and db.get_setting(owner_id, "meowie_auto_meow_active", "1") == "1":
                print(f"🐾 [{owner_id}] زمان میو رسید ({now:.0f} >= {next_meow:.0f}) — ارسال «میو».")
                try:
                    sent = await cl.send_message(group_id, "میو")
                    db.set_setting(owner_id, "meowie_last_meow_msg_id", str(sent.id))
                except Exception as e:
                    print(f"❌ [{owner_id}] خطا در ارسال میو: {e}")
                db.set_setting(owner_id, "meowie_next_meow_ts", str(now + _FALLBACK_RETRY_SECONDS))

            next_fish = float(db.get_setting(owner_id, "meowie_next_fish_ts", "0") or "0")
            if now >= next_fish and db.get_setting(owner_id, "meowie_auto_fish_active", "1") == "1":
                print(f"🐟 [{owner_id}] زمان ماهی رسید ({now:.0f} >= {next_fish:.0f}) — ارسال «ماهی».")
                try:
                    sent = await cl.send_message(group_id, "ماهی")
                    db.set_setting(owner_id, "meowie_last_fish_msg_id", str(sent.id))
                except Exception as e:
                    print(f"❌ [{owner_id}] خطا در ارسال ماهی: {e}")
                db.set_setting(owner_id, "meowie_next_fish_ts", str(now + _FALLBACK_RETRY_SECONDS))

            # «پیشی» فقط بعد از اینکه کاربر خودش یک‌بار دستی «پیشی» رو
            # فرستاده باشه شروع می‌شه (meowie_pishi_started)؛ برخلافِ میو/ماهی
            # که پیش‌فرضشون صفره و همون لحظه‌ی بایند شروع می‌کنن، چون تا
            # وقتی آمارِ اولیه (نرخ/ظرفیت) رو نبینیم چیزی برای زمان‌بندی
            # نداریم.
            if (
                db.get_setting(owner_id, "meowie_pishi_started", "0") == "1"
                and db.get_setting(owner_id, "meowie_auto_pishi_active", "1") == "1"
            ):
                next_pishi = float(db.get_setting(owner_id, "meowie_next_pishi_ts", "0") or "0")
                if now >= next_pishi:
                    print(f"🐱 [{owner_id}] زمان پیشی رسید ({now:.0f} >= {next_pishi:.0f}) — ارسال «پیشی».")
                    try:
                        sent = await cl.send_message(group_id, "پیشی")
                        db.set_setting(owner_id, "meowie_last_pishi_msg_id", str(sent.id))
                    except Exception as e:
                        print(f"❌ [{owner_id}] خطا در ارسال پیشی: {e}")
                    db.set_setting(owner_id, "meowie_next_pishi_ts", str(now + _FALLBACK_RETRY_SECONDS))

            # «میوهام» هر چند ساعت یک‌بار (پیش‌فرض ۶ ساعت) خودکار فرستاده
            # می‌شه؛ برخلافِ پیشی، وابسته به هیچ آماری نیست، پس از همون
            # لحظه‌ی بایند شدنِ گروه شروع می‌کنه (next_myohaam_ts در بایند
            # مقداردهی می‌شه).
            if db.get_setting(owner_id, "meowie_auto_myohaam_active", "1") == "1":
                interval = float(db.get_setting(owner_id, "meowie_myohaam_interval_seconds", "21600") or "21600")
                next_myohaam = float(db.get_setting(owner_id, "meowie_next_myohaam_ts", "0") or "0")
                if now >= next_myohaam and next_myohaam > 0:
                    print(f"📋 [{owner_id}] زمان میوهام رسید ({now:.0f} >= {next_myohaam:.0f}) — ارسال «میوهام».")
                    try:
                        sent = await cl.send_message(group_id, "میوهام")
                        db.set_setting(owner_id, "meowie_last_myohaam_msg_id", str(sent.id))
                    except Exception as e:
                        print(f"❌ [{owner_id}] خطا در ارسال میوهام: {e}")
                    db.set_setting(owner_id, "meowie_next_myohaam_ts", str(now + interval))

            await asyncio.sleep(5)
        except Exception as e:
            print(f"خطا در meowie_loop ({owner_id}): {e}")
            await asyncio.sleep(15)
