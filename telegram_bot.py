import threading
import time
import secrets
import telebot
from telebot import types
import database as db
import db_cache
import config
import datetime
import random
import re
import emoji as EM
from telethon.tl.custom import Button as _TLButton  # فقط برای پنل دکمه‌ای بات کمکی (helper_bot.py، مبتنی بر Telethon)

# ─── وقت تهران ───────────────────────────────────────────────────────────────
_TEHRAN_OFFSET = datetime.timezone(datetime.timedelta(hours=3, minutes=30))

def _now_tehran() -> datetime.datetime:
    return datetime.datetime.now(_TEHRAN_OFFSET)

def _fmt_tehran(dt) -> str:
    """تبدیل datetime به رشته فارسی با وقت تهران"""
    if dt is None:
        return "نامشخص"
    if isinstance(dt, str):
        try:
            dt = datetime.datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except Exception:
            return dt
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    tehran = dt.astimezone(_TEHRAN_OFFSET)
    return tehran.strftime("%Y/%m/%d — %H:%M")


# ─── دکمه‌های پنل مدیریت سلف (برای بات کمکی / helper_bot.py) ───────────────────
PANEL_PAGE_SIZE = 8


def get_all_commands_buttons(panel_commands, page: int = 0, prefix: str = "panel_cmd_", page_prefix: str = "panel_page_", owner_suffix: str = ""):
    """
    از روی یک لیست از آیتم‌ها (هر آیتم: (key, label, command_text) یا
    (key, label, command_text, style)) یک صفحه از دکمه‌های اینلاین Telethon
    می‌سازه، به‌همراه دکمه‌های ناوبری بعدی/قبلی.
    prefix: پیشوند callback_data دکمه‌های آیتم (مثلاً "panel_cmd_" یا
            "panel_item_automation_"). هر دکمه callback_data معادل
            f"{prefix}{index}{owner_suffix}" می‌گیره.
    page_prefix: پیشوند callback_data دکمه‌های صفحه‌بندی (بعدی/قبلی).
    owner_suffix: پسوندی مثل "_123456" که آیدی تلگرام صاحبِ پنل رو به
                  callback_data می‌چسبونه تا فقط خودش بتونه دکمه‌ها رو بزنه.

    رنگ دکمه (style): از نسخه‌های جدید Bot API/Telethon، دکمه‌های شیشه‌ای
    می‌تونن رنگ واقعی (success=سبز، danger=قرمز، primary=آبی) داشته باشن؛
    بدون هیچ ایموجی‌ای توی متن دکمه. اگه آیتم چهارمین مقدار (style) رو نداشته
    باشه، پیش‌فرض "primary" استفاده می‌شه.
    """
    total = len(panel_commands)
    total_pages = max(1, (total + PANEL_PAGE_SIZE - 1) // PANEL_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))

    start = page * PANEL_PAGE_SIZE
    end = min(start + PANEL_PAGE_SIZE, total)

    rows = []
    for idx in range(start, end):
        item = panel_commands[idx]
        _, label, _cmd = item[0], item[1], item[2]
        style = item[3] if len(item) > 3 else "primary"
        rows.append([_TLButton.inline(label, data=f"{prefix}{idx}{owner_suffix}", style=style)])

    if total_pages > 1:
        nav_row = []
        if page > 0:
            nav_row.append(_TLButton.inline("قبلی", data=f"{page_prefix}{page - 1}{owner_suffix}", style="primary"))
        nav_row.append(_TLButton.inline(f"{page + 1}/{total_pages}", data="panel_noop"))
        if page < total_pages - 1:
            nav_row.append(_TLButton.inline("بعدی", data=f"{page_prefix}{page + 1}{owner_suffix}", style="primary"))
        rows.append(nav_row)

    return rows



def _format_plan_remaining(owner_id: int) -> str:
    """متن باقی‌مانده‌ی پلن سلف یک کاربر (برای نمایش به مالک در لیست کاربران).
    expires_at به‌صورت زمان محلی تهران (بدون tzinfo) ذخیره شده، پس مستقیم با
    زمان فعلی تهران مقایسه می‌شود (بدون تبدیل از UTC)."""
    try:
        sub = db.get_subscription(owner_id)
    except Exception:
        sub = None
    if not sub or not sub.get("expires_at"):
        return "بدون پلن"

    exp = sub["expires_at"]
    if isinstance(exp, str):
        try:
            exp = datetime.datetime.fromisoformat(exp)
        except Exception:
            return "نامشخص"
    if exp.tzinfo is not None:
        exp = exp.replace(tzinfo=None)

    now_teh = datetime.datetime.now(_TEHRAN_OFFSET).replace(tzinfo=None)
    secs = (exp - now_teh).total_seconds()
    if secs <= 0:
        return "❌ منقضی شده"

    days = int(secs // 86400)
    hours = int((secs % 86400) // 3600)
    minutes = int((secs % 3600) // 60)
    if days > 0:
        return f"{days} روز و {hours} ساعت"
    if hours > 0:
        return f"{hours} ساعت و {minutes} دقیقه"
    return f"{minutes} دقیقه"

def _remaining_str(dt) -> str:
    """باقی‌مانده زمان تا انقضا به فارسی"""
    if dt is None:
        return "نامشخص"
    if isinstance(dt, str):
        try:
            dt = datetime.datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except Exception:
            return "نامشخص"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    now = datetime.datetime.now(datetime.timezone.utc)
    diff = dt - now
    if diff.total_seconds() <= 0:
        return "منقضی شده"
    days = diff.days
    hours = diff.seconds // 3600
    minutes = (diff.seconds % 3600) // 60
    if days > 0:
        return f"{days} روز و {hours} ساعت"
    elif hours > 0:
        return f"{hours} ساعت و {minutes} دقیقه"
    else:
        return f"{minutes} دقیقه"

_bot = None
BOT_USERNAME = None
OWNER_TG_ID = 8540004957

# ─── پردازش ایموجی‌های پرمیوم در پیام «ارسال به کانال» ────────────────────────
# الگو: متن[ایدی_عددی_ایموجی_پرمیوم]  → ایموجی پرمیوم جلوی متن قرار می‌گیرد
_PREMIUM_EMOJI_RE = re.compile(r'\[(\d{6,25})\]')
# نکته مهم: تلگرام آفست/طول entityها را بر اساس واحدهای UTF-16 محاسبه می‌کند،
# نه تعداد کاراکتر پایتون. ایموجی‌های خارج از BMP (مثل 🔸) دو واحد UTF-16
# هستند، پس به‌جای آن از یک نماد داخل BMP (یک واحدی) استفاده می‌کنیم تا
# آفست‌ها همیشه درست باشند و ایموجی پرمیوم واقعاً اعمال شود.
_PREMIUM_EMOJI_PLACEHOLDER = "★"

def _utf16len(s):
    """طول رشته بر حسب واحدهای UTF-16 (همان واحدی که تلگرام برای offset/length استفاده می‌کند)"""
    return len(s.encode('utf-16-le')) // 2

def _parse_premium_emojis(text, source_entities=None):
    """
    در متن دنبال الگوهای [ایدی_عددی] می‌گردد و آن‌ها را به ایموجی پرمیوم
    (custom_emoji entity) که جلوی همان نقطه از متن قرار می‌گیرد تبدیل می‌کند.
    فرمت‌های قبلی پیام (بولد، ایتالیک، نقل‌قول، کد، لینک و ...) که از طریق
    تلگرام روی پیام اعمال شده‌اند (source_entities) هم حفظ و با آفست جدید
    (بر حسب UTF-16) بازسازی می‌شوند.
    برمی‌گرداند: (new_text, list_of_MessageEntity)
    """
    text = text or ""
    matches = list(_PREMIUM_EMOJI_RE.finditer(text))

    if not matches:
        new_entities = []
        for ent in (source_entities or []):
            new_entities.append(types.MessageEntity(
                type=ent.type, offset=ent.offset, length=ent.length,
                url=getattr(ent, "url", None), user=getattr(ent, "user", None),
                language=getattr(ent, "language", None),
                custom_emoji_id=getattr(ent, "custom_emoji_id", None)
            ))
        return text, new_entities

    ph_len = _utf16len(_PREMIUM_EMOJI_PLACEHOLDER)

    # بازه‌های جایگزین‌شونده، بر حسب آفست UTF-16 در متن قدیمی: (start, end, emoji_id)
    repls = []
    for m in matches:
        s_utf16 = _utf16len(text[:m.start()])
        e_utf16 = s_utf16 + _utf16len(m.group(0))
        repls.append((s_utf16, e_utf16, m.group(1)))

    def map_pos(old_pos_utf16):
        """نگاشت یک آفست UTF-16 از متن قدیمی به متن جدید"""
        delta = 0
        for (s, e, _eid) in repls:
            if old_pos_utf16 <= s:
                break
            if old_pos_utf16 >= e:
                delta += ph_len - (e - s)
            else:
                # داخل بازه‌ی جایگزین‌شده افتاده -> به ابتدای آن می‌چسبانیم
                return s + delta
        return old_pos_utf16 + delta

    # ساخت متن جدید + entityهای ایموجی پرمیوم (آفست‌ها بر حسب UTF-16)
    new_text = ""
    last = 0
    custom_entities = []
    new_utf16_pos = 0
    for m in matches:
        seg = text[last:m.start()]
        new_text += seg
        new_utf16_pos += _utf16len(seg)
        custom_entities.append(types.MessageEntity(
            type="custom_emoji", offset=new_utf16_pos, length=ph_len, custom_emoji_id=m.group(1)
        ))
        new_text += _PREMIUM_EMOJI_PLACEHOLDER
        new_utf16_pos += ph_len
        last = m.end()
    new_text += text[last:]

    # بازسازی entityهای قبلی (بولد/ایتالیک/نقل‌قول/کد/...) با آفست جدید
    new_entities = list(custom_entities)
    for ent in (source_entities or []):
        ns = map_pos(ent.offset)
        ne = map_pos(ent.offset + ent.length)
        if ne > ns:
            new_entities.append(types.MessageEntity(
                type=ent.type, offset=ns, length=ne - ns,
                url=getattr(ent, "url", None), user=getattr(ent, "user", None),
                language=getattr(ent, "language", None),
                custom_emoji_id=getattr(ent, "custom_emoji_id", None)
            ))

    return new_text, new_entities

# ─── کش ──────────────────────────────────────────────────────────────────────
class SmartCache:
    def __init__(self):
        self._data = {}
        self._timestamps = {}
    
    def get(self, key, default=None):
        if key in self._data and key in self._timestamps:
            ttl = self._get_ttl(key)
            if time.time() - self._timestamps[key] < ttl:
                return self._data[key]
            else:
                del self._data[key]
                del self._timestamps[key]
        return default
    
    def set(self, key, value):
        self._data[key] = value
        self._timestamps[key] = time.time()
    
    def invalidate(self, pattern=None):
        if pattern is None:
            self._data.clear()
            self._timestamps.clear()
        else:
            keys_to_del = [k for k in list(self._data.keys()) if k.startswith(pattern)]
            for k in keys_to_del:
                self._data.pop(k, None)
                self._timestamps.pop(k, None)
    
    def _get_ttl(self, key):
        if key.startswith("membership_"):
            return 900
        if key.startswith("account_"):
            return 300
        if key.startswith("stats_"):
            return 60
        if key.startswith("challenge_"):
            return 120
        return 300

cache = SmartCache()
_owner_states = {}
# ─── شرط‌بندی‌های فعال: bet_id -> {creator_tg_id, opponent_tg_id or None} ────
_active_bets = {}

# ══════════════════════════════════════════════════════════════════════════════
# 🔐 سیستم ساخت اکانت و لاگین تلگرام از طریق ربات
# ══════════════════════════════════════════════════════════════════════════════
_reg_sessions: dict = {}
_REG_TIMEOUT = 300

# ─── سیستم تایید ورود توسط ادمین ────────────────────────────────────────────
# نگه‌داری پیام اصلی /start کاربر تا زمانی که ادمین تصمیم بگیرد (برای ادامه‌ی خودکار)
_pending_start_messages: dict = {}

_tg_loop = None


def _get_tg_loop():
    global _tg_loop
    import asyncio as _asyncio
    if _tg_loop is None or _tg_loop.is_closed():
        _tg_loop = _asyncio.new_event_loop()
        t = threading.Thread(target=_tg_loop.run_forever, daemon=True)
        t.start()
    return _tg_loop


def _run_tg(coro):
    import asyncio as _asyncio
    return _asyncio.run_coroutine_threadsafe(coro, _get_tg_loop()).result(timeout=30)


def _kp_markup(digits, mode="code"):
    prefix = f"reg_kp_{mode}_"
    markup = types.InlineKeyboardMarkup(row_width=3)
    # ✅ دکمه‌های اعداد با رنگ primary (آبی)
    markup.add(
        types.InlineKeyboardButton("1", callback_data=f"{prefix}1", style="primary"),
        types.InlineKeyboardButton("2", callback_data=f"{prefix}2", style="primary"),
        types.InlineKeyboardButton("3", callback_data=f"{prefix}3", style="primary"),
    )
    markup.add(
        types.InlineKeyboardButton("4", callback_data=f"{prefix}4", style="primary"),
        types.InlineKeyboardButton("5", callback_data=f"{prefix}5", style="primary"),
        types.InlineKeyboardButton("6", callback_data=f"{prefix}6", style="primary"),
    )
    markup.add(
        types.InlineKeyboardButton("7", callback_data=f"{prefix}7", style="primary"),
        types.InlineKeyboardButton("8", callback_data=f"{prefix}8", style="primary"),
        types.InlineKeyboardButton("9", callback_data=f"{prefix}9", style="primary"),
    )
    markup.add(
        types.InlineKeyboardButton("⬅️", callback_data=f"{prefix}del", style="danger"),  # 🔴 قرمز
        types.InlineKeyboardButton("0", callback_data=f"{prefix}0", style="primary"),   # 🔵 آبی
        types.InlineKeyboardButton("✔️", callback_data=f"{prefix}confirm", style="success"),  # 🟢 سبز
    )
    markup.add(types.InlineKeyboardButton(" لغو", callback_data="reg_cancel", style="danger", icon_custom_emoji_id="5832353674281620438"))  # 🔴 قرمز
    return markup


def _kp_display(digits, mode="code"):
    if mode in ("2fa", "pw"):
        return "●" * len(digits) if digits else "_ _ _ _ _"
    return digits if digits else "_ _ _ _ _"


def _reg_expired(tg_id):
    s = _reg_sessions.get(tg_id)
    return not s or time.time() > s.get("expires", 0)


def _reg_clear(tg_id):
    _reg_sessions.pop(tg_id, None)


def get_bot():
    return _bot


def _check_membership_cached(user_id):
    cache_key = f"membership_{user_id}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        is_member, missing = db.check_user_membership(_bot, user_id)
        result = (is_member, missing)
        cache.set(cache_key, result)
        return result
    except Exception as e:
        print(f"⚠️ خطا در بررسی عضویت: {e}")
        return True, []


def _get_account_cached(tg_id):
    cache_key = f"account_{tg_id}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    account = db.get_account_by_tg_id(tg_id)
    if account:
        cache.set(cache_key, account)
    return account


def _is_user_banned(account_id):
    try:
        return db.get_setting(account_id, "self_banned", "0") == "1"
    except Exception:
        return False


def _get_account_by_id(account_id):
    """دریافت اکانت بر اساس آیدی عددی پنل؛ اگر db تابع مستقیم نداشت، از لیست کامل پیدا می‌کند"""
    try:
        if hasattr(db, "get_account_by_id"):
            acc = db.get_account_by_id(account_id)
            if acc:
                return acc
    except Exception:
        pass
    try:
        for a in db.get_all_accounts():
            if a.get("id") == account_id:
                return a
    except Exception:
        pass
    return None


def start_token_bot():
    global _bot, BOT_USERNAME

    if not config.BOT_TOKEN:
        print("⚠️ BOT_TOKEN تنظیم نشده — ربات الماس غیرفعال است")
        return

    try:
        _bot = telebot.TeleBot(config.BOT_TOKEN, parse_mode="HTML", threaded=True, num_threads=8)
        me = _bot.get_me()
        BOT_USERNAME = me.username
        print(f"🤖 ربات الماس: @{BOT_USERNAME}")
    except Exception as e:
        print(f"❌ خطا در اتصال ربات الماس: {e}")
        _bot = None
        return

    for _ in range(3):
        try:
            _bot.delete_webhook(drop_pending_updates=True)
            time.sleep(2)
            break
        except:
            time.sleep(2)

    # ─── جوین اجباری پیش‌فرض ────────────────────────────────────────────────
    # این دو تا همیشه باید توی لیست کانال‌های جوین اجباری باشن. به‌جای
    # هاردکد کردن یه لیست جدا و موازی، از همون سیستم دیتابیسیِ موجود
    # (amel_forced_channels / add_forced_channel) استفاده می‌کنیم که پنل
    # ادمین هم داره ازش استفاده می‌کنه — پس هم توی این لیست هستن و هم از
    # طریق دستورهای «لیست کانال‌های اجباری» / «حذف کانال» قابل مدیریتن.
    # add_forced_channel با ON CONFLICT DO NOTHING نوشته شده، پس اجرای
    # دوباره‌ش هر بار که ربات بالا میاد کاملاً بی‌خطره (دوباره اضافه نمی‌شه).
    try:
        db.add_forced_channel("@Gp_SelfNexo")
        db.add_forced_channel("@Ch_SelfNexo")
    except Exception as e:
        print(f"⚠️ خطا در تنظیم جوین اجباری پیش‌فرض: {e}")

    # ─── توابع کمکی ───────────────────────────────────────────────────────────
    def send_forced_channels_menu(message, missing_channels):
        markup = types.InlineKeyboardMarkup(row_width=1)
        for ch in missing_channels:
            ch_clean = ch.lstrip("@")
            # 🟢 دکمه عضویت با رنگ primary (آبی)
            markup.add(types.InlineKeyboardButton(f"📢 عضویت در {ch}", url=f"https://t.me/{ch_clean}", style="primary"))
        # 🟢 دکمه بررسی با رنگ success (سبز)
        markup.add(types.InlineKeyboardButton("✅ بررسی عضویت من", callback_data="check_join", style="success", icon_custom_emoji_id="5830326445422940546"))
        
        channels_list = "\n".join([f"🔸 {ch}" for ch in missing_channels])
        _bot.reply_to(
            message,
            "⛔️ <b>ورود به ربات منوط به عضویت در کانال‌های زیر است:</b>\n\n"
            f"{channels_list}\n\n"
            "👇 روی هر کانال کلیک کنید و Join بزنید، سپس دکمه «بررسی عضویت من» را بزنید:",
            reply_markup=markup
        )

    def require_membership(message):
        if message.chat.type != 'private':
            return True
        is_member, missing = _check_membership_cached(message.from_user.id)
        if not is_member:
            send_forced_channels_menu(message, missing)
            return False
        return True

    def _user_keyboard(show_remove_self=True):
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add(
            types.KeyboardButton("مدیریت سلف", style="primary"),  # 🔵 آبی
        )
        return markup

    def _owner_keyboard(show_remove_self=True):
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add(
            types.KeyboardButton(" مدیریت", style="danger", icon_custom_emoji_id=str(EM.ID_ADMINE)),        # 🔴 قرمز
            types.KeyboardButton(" مدیریت سلف", style="danger", icon_custom_emoji_id=str(EM.ID_SELF_EDIT))   # 🔵 قرمز
        )
        return markup

    def _main_inline_keyboard(account=None):
        # ✅ دکمه‌های اصلی کاربر به‌صورت InlineKeyboardButton
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton(" موجودی", callback_data="menu_balance", style="primary", icon_custom_emoji_id=str(EM.ID_BALANCE)),
            types.InlineKeyboardButton(" هدیه روزانه", callback_data="menu_daily", style="success", icon_custom_emoji_id=str(EM.ID_DAILY_GIFT))
        )
        markup.add(
            types.InlineKeyboardButton(" رفرال", callback_data="menu_referral", style="primary", icon_custom_emoji_id=str(EM.ID_REFERRAL)),
            types.InlineKeyboardButton(" خرید", callback_data="menu_buy", style="success", icon_custom_emoji_id=str(EM.ID_BUY_DIAMOND))
        )
        markup.add(
            types.InlineKeyboardButton(" ماموریت‌ها", callback_data="menu_missions", style="primary", icon_custom_emoji_id=str(EM.ID_MISSION)),
            types.InlineKeyboardButton(" مدیریت سلف", callback_data="self_mgmt_open", style="primary", icon_custom_emoji_id=str(EM.ID_SELF_EDIT))
        )
        markup.add(
            types.InlineKeyboardButton(" راهنما", callback_data="guide_menu", style="success", icon_custom_emoji_id=str(EM.ID_GUIDE))
        )
        if account is not None:
            try:
                is_logged_in = db.get_setting(account["id"], "logged_in", "0") == "1"
            except Exception:
                is_logged_in = True
            if not is_logged_in:
                markup.add(
                    types.InlineKeyboardButton(" ورود سلف با ربات", callback_data="reg_start", style="success", icon_custom_emoji_id=str(EM.ID_SELF_MANAGE))
                )
        return markup

    def _admin_panel_keyboard():
        # ✅ دکمه‌های شیشه‌ای پنل مدیریت
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton(" چنل‌های اجباری", callback_data="admin_channels", style="success", icon_custom_emoji_id=str(EM.ID_FORCED_JOIN)),   # 🔵 آبی
            types.InlineKeyboardButton(" کاربران", callback_data="admin_users", style="primary", icon_custom_emoji_id=str(EM.ID_USERS))              # 🔵 آبی
        )
        markup.add(
            types.InlineKeyboardButton(" مدیریت کاربران", callback_data="admin_manage_users", style="danger", icon_custom_emoji_id=str(EM.ID_USERS))   # 🔴 قرمز
        )
        markup.add(
            types.InlineKeyboardButton(" جام جهانی", callback_data="admin_wc", style="success", icon_custom_emoji_id=str(EM.ID_World_Cup)),              # 🟢 سبز
            types.InlineKeyboardButton(" بازی‌های امروز", callback_data="admin_today_games", style="primary", icon_custom_emoji_id=str(EM.ID_DAY_GAME)) # 🔵 آبی
        )
        markup.add(
            types.InlineKeyboardButton(" انتقال الماس", callback_data="admin_transfer", style="primary", icon_custom_emoji_id=str(EM.ID_Transition)),    # 🔵 آبی
            types.InlineKeyboardButton(" دادن الماس", callback_data="admin_give", style="success", icon_custom_emoji_id=str(EM.ID_GIFT_DIAMOND))           # 🟢 سبز
        )
        markup.add(
            types.InlineKeyboardButton(" تنظیم شماره کارت", callback_data="admin_set_card", style="primary", icon_custom_emoji_id=str(EM.ID_SET_CARD)), # 🔵 آبی
            types.InlineKeyboardButton(" پرداخت‌های معلق", callback_data="admin_payments", style="danger", icon_custom_emoji_id=str(EM.ID_Pending))   # 🔴 قرمز
        )
        markup.add(
            types.InlineKeyboardButton(" پیام عمومی", callback_data="admin_broadcast", style="primary", icon_custom_emoji_id=str(EM.ID_MESSAGE_ALL)),      # 🔵 آبی
            types.InlineKeyboardButton(" ماموریت‌ها", callback_data="admin_missions", style="success", icon_custom_emoji_id=str(EM.ID_MISSION))       # 🟢 سبز
        )
        markup.add(
            types.InlineKeyboardButton(" پیام به کانال", callback_data="admin_channel_msg", style="primary", icon_custom_emoji_id=str(EM.ID_MESSAGE_ALL))  # 🔵 آبی
        )
        markup.add(
            types.InlineKeyboardButton(" شرکت‌کنندگان جام جهانی", callback_data="admin_wc_participants", style="primary", icon_custom_emoji_id=str(EM.ID_UESRS_WC)) # 🔵 آبی
        )
        markup.add(
            types.InlineKeyboardButton(" هدیه", callback_data="admin_gift", style="success", icon_custom_emoji_id=str(EM.ID_GIFT))                 # 🟢 سبز
        )
        markup.add(
            types.InlineKeyboardButton(" مدیریت ادمین‌ها", callback_data="admin_manage_admins", style="primary", icon_custom_emoji_id=str(EM.ID_ADMINE)) # 🔵 آبی
        )
        markup.add(
            types.InlineKeyboardButton(" مدیریت راهنما", callback_data="admin_guide_manage", style="success", icon_custom_emoji_id=str(EM.ID_HELP))    # 🟢 سبز
        )
        markup.add(
            types.InlineKeyboardButton(" تنظیمات خوش‌آمد", callback_data="admin_welcome_settings", style="primary", icon_custom_emoji_id=str(EM.ID_WELCOME)) # 🔵 آبی
        )
        markup.add(
            types.InlineKeyboardButton(" قرعه‌کشی", callback_data="admin_lottery", style="success", icon_custom_emoji_id=str(EM.ID_BET))           # 🟢 سبز
        )
        markup.add(
            types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin_panel", style="danger")               # 🔴 قرمز
        )
        return markup

    def _manage_user_card(account):
        """ساخت متن و کیبورد پنل مدیریت یک کاربر خاص"""
        acc_id = account["id"]
        bal = db.get_token_balance(acc_id)
        banned = _is_user_banned(acc_id)
        try:
            tg_id = db.get_telegram_id_by_owner(acc_id)
        except Exception:
            tg_id = None
        try:
            from bot import bot_manager
            self_running = bot_manager.is_running(acc_id)
        except Exception:
            self_running = False

        ban_text = "🚫 بن شده" if banned else "✅ بن نیست"
        self_text = "🟢 روشن" if self_running else "🔴 خاموش"
        text = (
            f"👤 <b>مدیریت کاربر</b>\n\n"
            f"🆔 یوزرنیم: <b>{account.get('username', '-')}</b>\n"
            f"🔢 آیدی پنل: <code>{acc_id}</code>\n"
            f"📱 آیدی تلگرام: <code>{tg_id or '-'}</code>\n"
            f"💎 موجودی: <b>{bal} الماس</b>\n"
            f"🚦 وضعیت بن: {ban_text}\n"
            f"🤖 وضعیت سلف: {self_text}"
        )

        markup = types.InlineKeyboardMarkup(row_width=2)
        if banned:
            markup.add(types.InlineKeyboardButton("✅ رفع بن", callback_data=f"mu_unban_{acc_id}", style="success"))
        else:
            markup.add(types.InlineKeyboardButton("🚫 بن از سلف", callback_data=f"mu_ban_{acc_id}", style="danger"))
        markup.add(
            types.InlineKeyboardButton("➕ دادن الماس", callback_data=f"mu_give_{acc_id}", style="success"),
            types.InlineKeyboardButton("➖ کسر الماس", callback_data=f"mu_deduct_{acc_id}", style="danger")
        )
        if self_running:
            markup.add(types.InlineKeyboardButton("🔴 خاموش کردن سلف", callback_data=f"mu_stopself_{acc_id}", style="danger"))
        markup.add(types.InlineKeyboardButton("🔄 بروزرسانی", callback_data=f"mu_view_{acc_id}", style="primary"))
        markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin_panel", style="danger"))
        return text, markup

    # ══════════════════════════════════════════════════════════════════════════
    # 🎯 دستور شرط بندی — فقط در گروه سلف
    # ══════════════════════════════════════════════════════════════════════════
    SELF_GROUP = getattr(config, 'WORLD_CUP_GROUP', '@Gp_SelfNexo')
    BET_TAX = 0.17

    def _is_self_group(chat):
        """بررسی می‌کند آیا پیام از گروه سلف است"""
        if chat.type not in ('group', 'supergroup'):
            return False
        username = getattr(chat, 'username', None)
        if username and f"@{username.lower()}" == SELF_GROUP.lower():
            return True
        return False

    @_bot.message_handler(
        func=lambda m: m.text and m.text.strip().startswith("شرط بندی "),
        chat_types=['group', 'supergroup']
    )
    def cmd_bet(message):
        try:
            if not _is_self_group(message.chat):
                return

            parts = message.text.strip().split()
            if len(parts) < 3:
                return _bot.reply_to(message, "❗ فرمت: شرط بندی [مقدار]\nمثال: شرط بندی 100")

            try:
                amount = int(parts[2])
                if amount < 1:
                    return _bot.reply_to(message, "❌ مقدار باید بیشتر از ۰ باشد.")
            except ValueError:
                return _bot.reply_to(message, "❌ مقدار باید عدد باشد.")

            account = _get_account_cached(message.from_user.id)
            if not account:
                return _bot.reply_to(message, "⚠️ ابتدا در پنل ربات ثبت‌نام کنید.")

            if _is_user_banned(account["id"]):
                return _bot.reply_to(message, "🚫 شما توسط مالک از سلف بن شده‌اید و امکان شرط‌بندی ندارید.")

            balance = db.get_token_balance(account["id"])
            if balance < amount:
                return _bot.reply_to(
                    message,
                    f"❌ موجودی کافی ندارید!\nنیاز: {amount} الماس — موجودی: {balance} الماس"
                )

            bet_id = db.create_bet(account["id"], message.from_user.id, amount, message.chat.id)
            if not bet_id:
                return _bot.reply_to(message, "❌ خطا در ساخت شرط‌بندی. دوباره امتحان کنید.")

            _active_bets[bet_id] = {
                "creator_tg_id": message.from_user.id,
                "opponent_tg_id": None,
            }

            creator_name = (
                f"@{message.from_user.username}" if message.from_user.username
                else message.from_user.first_name
            )
            payout = round(amount * 2 * (1 - BET_TAX))

            markup = types.InlineKeyboardMarkup()
            # 🟢 دکمه ورود به شرط‌بندی با رنگ success (سبز)
            markup.add(
                types.InlineKeyboardButton(
                    "⚔️ ورود به شرط‌بندی",
                    callback_data=f"join_bet_{bet_id}",
                    style="success",
                    icon_custom_emoji_id=str(EM.ID_BET_JOIN)
                )
            )
            # 🔴 دکمه لغو شرط‌بندی برای سازنده
            markup.add(
                types.InlineKeyboardButton(
                    "❌ لغو شرط‌بندی",
                    callback_data=f"cancel_bet_{bet_id}",
                    style="danger",
                    icon_custom_emoji_id=str(EM.ID_CANCEL)
                )
            )

            msg = _bot.reply_to(
                message,
                f"🎲 <b>شرط‌بندی باز شد!</b>\n\n"
                f"👤 سازنده: {creator_name}\n"
                f"💎 مبلغ: <b>{amount} الماس</b>\n"
                f"🏆 جایزه برنده: <b>{payout} الماس</b> (بعد از ۱۷٪ مالیات)\n\n"
                f"⏳ منتظر حریف...\n"
                f"(اولین نفری که دکمه بزند وارد می‌شود)",
                reply_markup=markup
            )
            db.update_bet_message(bet_id, msg.message_id)

            # تایمر ۵ دقیقه — اگر کسی وارد نشد، لغو و برگشت موجودی
            threading.Timer(300, _auto_cancel_bet, args=[bet_id, message.chat.id, msg.message_id]).start()

        except Exception as e:
            print(f"❌ خطا در cmd_bet: {e}")
            _bot.reply_to(message, f"❌ خطا: {e}")

    # ── Callback: ورود به شرط‌بندی ─────────────────────────────────────────────
    @_bot.callback_query_handler(func=lambda call: call.data.startswith("join_bet_"))
    def callback_join_bet(call):
        try:
            bet_id = int(call.data.split("_")[2])

            # بررسی حافظه محلی اول (سریع‌تر)
            bet_mem = _active_bets.get(bet_id)
            if bet_mem is None:
                return _bot.answer_callback_query(call.id, "❌ این شرط‌بندی یافت نشد یا منقضی شده.", show_alert=True)

            if bet_mem["opponent_tg_id"] is not None:
                return _bot.answer_callback_query(call.id, "❌ این شرط‌بندی قبلاً تکمیل شده است.", show_alert=True)

            if bet_mem["creator_tg_id"] == call.from_user.id:
                return _bot.answer_callback_query(call.id, "❌ شما سازنده این شرط هستید! منتظر حریف باشید.", show_alert=True)

            account = _get_account_cached(call.from_user.id)
            if not account:
                return _bot.answer_callback_query(call.id, "❌ ابتدا در پنل ربات ثبت‌نام کنید.", show_alert=True)

            # ورود به دیتابیس (کسر موجودی نفر دوم + آپدیت وضعیت)
            success, msg_txt = db.join_bet(bet_id, account["id"], call.from_user.id)
            if not success:
                return _bot.answer_callback_query(call.id, msg_txt, show_alert=True)

            # علامت‌گذاری در حافظه
            bet_mem["opponent_tg_id"] = call.from_user.id

            opponent_name = (
                f"@{call.from_user.username}" if call.from_user.username
                else call.from_user.first_name
            )
            _bot.answer_callback_query(call.id, "✅ وارد شرط‌بندی شدید! بازی شروع می‌شود...", show_alert=True)

            bet = db.get_bet(bet_id)
            if not bet:
                return

            # اجرای شرط و انتخاب برنده
            ok, winner, payout = db.finish_bet(bet_id)
            if not ok:
                return

            # پیدا کردن نام برنده
            winner_tg_id = winner["tg_id"]
            try:
                winner_chat = _bot.get_chat(winner_tg_id)
                winner_name = (
                    f"@{winner_chat.username}" if winner_chat.username
                    else winner_chat.first_name
                )
            except Exception:
                winner_name = str(winner_tg_id)

            amount = bet["amount"]
            total = amount * 2
            tax = round(total * BET_TAX)

            result_text = (
                f"🎉 <b>شرط‌بندی به پایان رسید!</b>\n\n"
                f"⚔️ حریف: {opponent_name}\n"
                f"💎 مبلغ هر نفر: {amount} الماس\n"
                f"💰 مجموع: {total} الماس\n"
                f"🏛 مالیات (۱۷٪): {tax} الماس\n\n"
                f"🏆 <b>برنده: {winner_name}</b>\n"
                f"{EM.EMOJI_DIAMONDS} <b>جایزه: {payout} الماس</b>"
            )

            # ویرایش پیام اصلی
            try:
                _bot.edit_message_text(
                    result_text,
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id
                )
            except Exception:
                _bot.send_message(call.message.chat.id, result_text)

            # اطلاع به برنده در پیوی
            try:
                _bot.send_message(
                    winner_tg_id,
                    f"🎉 <b>تبریک! شرط‌بندی را بردید!</b>\n💎 <b>{payout} الماس</b> به حسابتان واریز شد."
                )
            except Exception:
                pass

            # اطلاع به بازنده
            loser_tg_id = (
                bet["creator_tg_id"] if winner_tg_id == bet["opponent_tg_id"]
                else bet["opponent_tg_id"]
            )
            try:
                _bot.send_message(
                    loser_tg_id,
                    f"😔 متأسفانه این بار نبردید.\n💎 {amount} الماس از حسابتان کسر شد."
                )
            except Exception:
                pass

            _active_bets.pop(bet_id, None)

        except Exception as e:
            print(f"❌ خطا در callback_join_bet: {e}")
            try:
                _bot.answer_callback_query(call.id, f"❌ خطا: {str(e)[:100]}", show_alert=True)
            except Exception:
                pass

    # ── Callback: لغو دستی شرط‌بندی توسط سازنده ────────────────────────────────
    @_bot.callback_query_handler(func=lambda call: call.data.startswith("cancel_bet_"))
    def callback_cancel_bet(call):
        try:
            bet_id = int(call.data.split("_")[2])

            bet_mem = _active_bets.get(bet_id)
            if bet_mem is None:
                return _bot.answer_callback_query(call.id, "❌ این شرط‌بندی یافت نشد یا قبلاً لغو شده.", show_alert=True)

            if bet_mem["opponent_tg_id"] is not None:
                return _bot.answer_callback_query(call.id, "❌ حریف وارد شده — دیگر نمی‌توانید لغو کنید.", show_alert=True)

            if bet_mem["creator_tg_id"] != call.from_user.id:
                return _bot.answer_callback_query(call.id, "❌ فقط سازنده شرط می‌تواند لغو کند.", show_alert=True)

            db.cancel_bet(bet_id)
            _active_bets.pop(bet_id, None)

            try:
                _bot.edit_message_text(
                    "🚫 <b>شرط‌بندی لغو شد!</b>\n\nسازنده شرط را لغو کرد.\n💎 مبلغ به سازنده بازگشت داده شد.",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id
                )
            except Exception:
                pass

            _bot.answer_callback_query(call.id, "✅ شرط‌بندی لغو شد و مبلغ بازگشت داده شد.", show_alert=True)

        except Exception as e:
            print(f"❌ خطا در callback_cancel_bet: {e}")
            try:
                _bot.answer_callback_query(call.id, f"❌ خطا: {str(e)[:100]}", show_alert=True)
            except Exception:
                pass

    # ── لغو خودکار شرط (تایمر ۵ دقیقه) ────────────────────────────────────────
    def _auto_cancel_bet(bet_id, chat_id, message_id):
        try:
            bet_mem = _active_bets.get(bet_id)
            if bet_mem is None or bet_mem["opponent_tg_id"] is not None:
                return  # شرط تکمیل شده

            db.cancel_bet(bet_id)
            _active_bets.pop(bet_id, None)

            try:
                _bot.edit_message_text(
                    "⏰ <b>شرط‌بندی لغو شد!</b>\n\nهیچ حریفی وارد نشد.\n💎 مبلغ به سازنده بازگشت داده شد.",
                    chat_id=chat_id,
                    message_id=message_id
                )
            except Exception:
                _bot.send_message(chat_id, "⏰ یک شرط‌بندی به دلیل نبود حریف لغو شد.")
        except Exception as e:
            print(f"❌ خطا در _auto_cancel_bet: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # 💰 دستور موجودی در گروه
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.message_handler(func=lambda m: m.text and m.text == "موجودی", chat_types=['group', 'supergroup'])
    def cmd_balance_group(message):
        try:
            account = _get_account_cached(message.from_user.id)
            if not account:
                return _bot.reply_to(message, "⚠️ ابتدا در پنل ربات ثبت‌نام کنید.")
            
            stats = db.get_token_stats(account["id"])
            _bot.reply_to(
                message,
                f"{EM.EMOJI_DIAMONDS} <b>موجودی شما:</b>\n\n"
                f"💰 الماس: <b>{stats['balance']}</b>\n"
                f"📊 کل دریافتی: <b>{stats['total_earned']}</b>"
            )
        except Exception as e:
            print(f"❌ خطا در cmd_balance_group: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # 💎 انتقال الماس
    # ══════════════════════════════════════════════════════════════════════════
    def _transfer_success_message(amount, sender_name, receiver_name, tax_msg):
        """پیام موفقیت انتقال الماس با ایموجی‌های پرمیوم (tg-emoji)"""
        return (
            f'<tg-emoji emoji-id="5278467510604160626">💎</tg-emoji> '
            f'<b>{amount} الماس با موفقیت انتقال یافت</b> '
            f'<tg-emoji emoji-id="6111444480286528430">✅</tg-emoji>\n\n'
            f'<tg-emoji emoji-id="5782766782200682322">📤</tg-emoji> <b>فرستنده:</b>\n@{sender_name}\n\n'
            f'<tg-emoji emoji-id="4958472587123360612">📥</tg-emoji> <b>گیرنده:</b>\n@{receiver_name}\n'
            f'<tg-emoji emoji-id="4956601935592424315">💰</tg-emoji> {tax_msg}'
        )

    @_bot.message_handler(func=lambda m: m.text and m.text.startswith("انتقال ") and not m.text.startswith("انتقال اشتراک"), chat_types=['private', 'group', 'supergroup'])
    def cmd_transfer(message):
        try:
            parts = message.text.split()

            # ── حالت گروه مدیریت: ریپلای روی پیام کاربر + «انتقال [عدد]» ──────
            if len(parts) == 2 and message.reply_to_message:
                target_user = message.reply_to_message.from_user
                if not target_user or target_user.is_bot:
                    return _bot.reply_to(message, "❌ نمی‌توان به این کاربر الماس انتقال داد.")

                try:
                    amount = int(parts[1])
                    if amount < 1:
                        return _bot.reply_to(message, "❌ مقدار باید بیشتر از 0 باشد.")
                except ValueError:
                    return _bot.reply_to(message, "❌ مقدار باید عدد باشد.")

                if target_user.id == message.from_user.id:
                    return _bot.reply_to(message, "❌ نمی‌توانید به خودتان الماس انتقال دهید.")

                from_account = _get_account_cached(message.from_user.id)
                if not from_account:
                    return _bot.reply_to(message, "⚠️ ابتدا در پنل ربات ثبت‌نام کنید.")

                to_account = db.get_account_by_tg_id(target_user.id)
                if not to_account:
                    return _bot.reply_to(message, "❌ این کاربر در پنل ربات ثبت‌نام نکرده است.")

                success, msg = db.transfer_diamonds(from_account["id"], to_account["id"], amount)

                if success:
                    cache.invalidate(f"account_{message.from_user.id}")
                    to_tg_id = db.get_telegram_id_by_owner(to_account["id"])
                    if to_tg_id:
                        try:
                            _bot.send_message(
                                to_tg_id,
                                f"{EM.EMOJI_DIAMONDS} <b>{amount} الماس</b> از @{message.from_user.username or 'کاربر'} دریافت کردید!"
                            )
                        except Exception:
                            pass

                    sender_name = message.from_user.username or "کاربر"
                    receiver_name = target_user.username or "کاربر"
                    formatted_msg = _transfer_success_message(amount, sender_name, receiver_name, msg)
                    return _bot.reply_to(message, formatted_msg)

                return _bot.reply_to(message, msg)

            # ── حالت معمول: «انتقال [یوزرنیم] [عدد]» ─────────────────────────
            if len(parts) < 3:
                return _bot.reply_to(message, "❗ فرمت: انتقال [یوزرنیم] [تعداد]\nمثال: انتقال @ali 10\nیا روی پیام کاربر ریپلای کنید و بنویسید: انتقال [تعداد]")
            
            username = parts[1].lstrip("@")
            try:
                amount = int(parts[2])
                if amount < 1:
                    return _bot.reply_to(message, "❌ مقدار باید بیشتر از 0 باشد.")
            except:
                return _bot.reply_to(message, "❌ مقدار باید عدد باشد.")
            
            from_account = _get_account_cached(message.from_user.id)
            if not from_account:
                return _bot.reply_to(message, "⚠️ ابتدا در پنل ربات ثبت‌نام کنید.")
            
            to_account = db.get_account_by_username(username)
            if not to_account:
                return _bot.reply_to(message, f"❌ کاربر '{username}' یافت نشد.")
            
            if to_account["id"] == from_account["id"]:
                return _bot.reply_to(message, "❌ نمی‌توانید به خودتان الماس انتقال دهید.")
            
            success, msg = db.transfer_diamonds(from_account["id"], to_account["id"], amount)
            
            if success:
                cache.invalidate(f"account_{message.from_user.id}")
                to_tg_id = db.get_telegram_id_by_owner(to_account["id"])
                if to_tg_id:
                    try:
                        _bot.send_message(
                            to_tg_id,
                            f"{EM.EMOJI_DIAMONDS} <b>{amount} الماس</b> از @{message.from_user.username or 'کاربر'} دریافت کردید!"
                        )
                    except:
                        pass

                sender_name = message.from_user.username or "کاربر"
                formatted_msg = _transfer_success_message(amount, sender_name, username, msg)
                return _bot.reply_to(message, formatted_msg)

            _bot.reply_to(message, msg)
            
        except Exception as e:
            print(f"❌ خطا در cmd_transfer: {e}")
            _bot.reply_to(message, f"❌ خطا: {e}")

    @_bot.message_handler(func=lambda m: m.text and m.text.startswith("انتقال اشتراک"), chat_types=['private', 'group', 'supergroup'])
    def cmd_transfer_subscription(message):
        try:
            parts = message.text.split()

            # ── حالت گروه: ریپلای روی پیام کاربر + «انتقال اشتراک» ────────────
            if len(parts) == 2 and message.reply_to_message:
                target_user = message.reply_to_message.from_user
                if not target_user or target_user.is_bot:
                    return _bot.reply_to(message, "❌ نمی‌توان اشتراک را به این کاربر منتقل کرد.")
                if target_user.id == message.from_user.id:
                    return _bot.reply_to(message, "❌ نمی‌توانید اشتراک را به خودتان انتقال دهید.")

                from_account = _get_account_cached(message.from_user.id)
                if not from_account:
                    return _bot.reply_to(message, "⚠️ ابتدا در پنل ربات ثبت‌نام کنید.")

                to_account = db.get_account_by_tg_id(target_user.id)
                if not to_account:
                    return _bot.reply_to(message, "❌ این کاربر در پنل ربات ثبت‌نام نکرده است.")

                success, msg = db.transfer_subscription(from_account["id"], to_account["id"])

                if success:
                    to_tg_id = db.get_telegram_id_by_owner(to_account["id"])
                    if to_tg_id:
                        try:
                            _bot.send_message(
                                to_tg_id,
                                f"📦 <b>اشتراک</b> از @{message.from_user.username or 'کاربر'} به حساب شما منتقل شد!"
                            )
                        except Exception:
                            pass
                return _bot.reply_to(message, msg)

            # ── حالت معمول: «انتقال اشتراک [یوزرنیم]» ─────────────────────────
            if len(parts) < 3:
                return _bot.reply_to(
                    message,
                    "❗ فرمت: انتقال اشتراک [یوزرنیم]\nمثال: انتقال اشتراک @ali\nیا روی پیام کاربر ریپلای کنید و بنویسید: انتقال اشتراک"
                )

            username = parts[2].lstrip("@")

            from_account = _get_account_cached(message.from_user.id)
            if not from_account:
                return _bot.reply_to(message, "⚠️ ابتدا در پنل ربات ثبت‌نام کنید.")

            to_account = db.get_account_by_username(username)
            if not to_account:
                return _bot.reply_to(message, f"❌ کاربر '{username}' یافت نشد.")

            if to_account["id"] == from_account["id"]:
                return _bot.reply_to(message, "❌ نمی‌توانید اشتراک را به خودتان انتقال دهید.")

            success, msg = db.transfer_subscription(from_account["id"], to_account["id"])

            if success:
                to_tg_id = db.get_telegram_id_by_owner(to_account["id"])
                if to_tg_id:
                    try:
                        _bot.send_message(
                            to_tg_id,
                            f"📦 <b>اشتراک</b> از @{message.from_user.username or 'کاربر'} به حساب شما منتقل شد!"
                        )
                    except Exception:
                        pass

            _bot.reply_to(message, msg)

        except Exception as e:
            print(f"❌ خطا در cmd_transfer_subscription: {e}")
            _bot.reply_to(message, f"❌ خطا: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # ⚽ سیستم جام جهانی — football-data.org
    # ══════════════════════════════════════════════════════════════════════════

    # کش محلی نتایج API (برای کاهش مصرف)
    _wc_api_cache = {"matches": [], "results": {}, "last_fetch": 0, "last_result_fetch": 0}
    # وضعیت انتخاب تیم کاربران: tg_id -> {challenge_id, selected_option}
    _wc_pending_bet = {}

    IRAN_TZ = datetime.timezone(datetime.timedelta(hours=3, minutes=30))

    def _wc_utc_to_iran(dt: datetime.datetime) -> datetime.datetime:
        """تبدیل datetime (UTC، naive یا aware) به ساعت ایران (UTC+3:30)"""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(IRAN_TZ)

    def _wc_api_get(endpoint: str) -> dict:
        """فراخوانی API football-data.org"""
        import urllib.request, urllib.error, json as _json
        api_key = getattr(config, "FOOTBALL_API_KEY", "")
        if not api_key:
            print("⚠️ FOOTBALL_API_KEY تنظیم نشده — درخواست به Football API ارسال نشد.")
            return {}
        url = f"https://api.football-data.org/v4/{endpoint}"
        req = urllib.request.Request(url, headers={"X-Auth-Token": api_key})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return _json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode()[:300]
            except Exception:
                pass
            print(f"❌ Football API HTTP {e.code} [{endpoint}]: {body}")
            return {}
        except Exception as e:
            print(f"❌ Football API error [{endpoint}]: {e}")
            return {}

    def _wc_get_matches() -> list:
        """دریافت بازی‌های آینده از API (با کش ۱۰ دقیقه)"""
        now = time.time()
        if now - _wc_api_cache["last_fetch"] < 600 and _wc_api_cache["matches"]:
            return _wc_api_cache["matches"]
        comp = getattr(config, "WC_COMPETITION", "WC")
        data = _wc_api_get(f"competitions/{comp}/matches?status=SCHEDULED")
        matches = data.get("matches", [])
        _wc_api_cache["matches"] = matches
        _wc_api_cache["last_fetch"] = now
        return matches

    def _wc_get_today_matches() -> list:
        """دریافت بازی‌های امروز (هر وضعیتی) — بدون کش، برای دکمه «بازی‌های امروز»"""
        comp = getattr(config, "WC_COMPETITION", "WC")
        today_str = datetime.datetime.now(IRAN_TZ).strftime("%Y-%m-%d")
        data = _wc_api_get(f"competitions/{comp}/matches?dateFrom={today_str}&dateTo={today_str}")
        return data.get("matches", [])

    def _wc_get_finished_matches() -> list:
        """دریافت بازی‌های تمام‌شده (با کش ۵ دقیقه)"""
        now = time.time()
        if now - _wc_api_cache["last_result_fetch"] < 300:
            return _wc_api_cache.get("finished", [])
        comp = getattr(config, "WC_COMPETITION", "WC")
        data = _wc_api_get(f"competitions/{comp}/matches?status=FINISHED")
        finished = data.get("matches", [])
        _wc_api_cache["finished"] = finished
        _wc_api_cache["last_result_fetch"] = now
        return finished

    def _wc_determine_winner(match: dict) -> str:
        """تعیین برنده از نتیجه بازی"""
        score = match.get("score", {})
        winner = score.get("winner")  # HOME_TEAM / AWAY_TEAM / DRAW
        if winner == "HOME_TEAM":
            return "team1"
        elif winner == "AWAY_TEAM":
            return "team2"
        elif winner == "DRAW":
            return "draw"
        return ""

    def _wc_send_challenge_to_channel(challenge_id: int, team1: str, team2: str, match_time_str: str):
        """ارسال چالش به کانال"""
        channel = getattr(config, "WC_CHANNEL_ID", "")
        if not channel:
            print("⚠️ WC_CHANNEL_ID تنظیم نشده! چالش جام جهانی به هیچ کانالی ارسال نمی‌شود.")
            return
        # اگر آیدی کانال به‌صورت عددی (مثل -1001234567) ست شده، به int تبدیل می‌کنیم
        chat_target = channel
        if isinstance(channel, str) and channel.lstrip("-").isdigit():
            chat_target = int(channel)

        min_bet = getattr(config, "WC_MIN_BET", 10)
        max_bet = getattr(config, "WC_MAX_BET", 5000)
        markup = types.InlineKeyboardMarkup(row_width=1)
        # 🔵 دکمه تیم اول با رنگ primary (آبی)
        markup.add(
            types.InlineKeyboardButton(f"🔵 {team1}", callback_data=f"wc_pick_{challenge_id}_team1", style="primary")
        )
        # 🟢 دکمه مساوی با رنگ success (سبز)
        markup.add(
            types.InlineKeyboardButton("🤝 مساوی", callback_data=f"wc_pick_{challenge_id}_draw", style="success")
        )
        # 🔴 دکمه تیم دوم با رنگ danger (قرمز)
        markup.add(
            types.InlineKeyboardButton(f"🔴 {team2}", callback_data=f"wc_pick_{challenge_id}_team2", style="danger")
        )
        now_tehran = _now_tehran().strftime("%Y/%m/%d — %H:%M")
        text = (
            f"⚽️ <b>چالش جام جهانی ۲۰۲۶</b>\n\n"
            f"🆚 <b>{team1}</b>  vs  <b>{team2}</b>\n"
            f"⏰ زمان بازی: <b>{match_time_str}</b>\n"
            f"🕐 ارسال در: {now_tehran} (تهران)\n\n"
            f"💎 محدوده شرط: {min_bet:,} – {max_bet:,} الماس\n"
            f"🏆 برندگان ۲ برابر مبلغ شرط دریافت می‌کنند!\n\n"
            f"👇 روی تیم مورد نظرت کلیک کن:"
        )
        try:
            msg = _bot.send_message(chat_target, text, reply_markup=markup)
            db.set_wc_channel_msg(challenge_id, msg.message_id)
            print(f"✅ چالش به کانال {chat_target} ارسال شد (msg_id={msg.message_id})")
        except Exception as e:
            print(f"❌ ارسال چالش به کانال {chat_target} ناموفق بود: {e}\n"
                  f"   بررسی کنید که ربات ادمین کانال باشد و WC_CHANNEL_ID درست تنظیم شده باشد (مثل @channel یا -100xxxxxxxxxx).")

    def _wc_auto_fetch_and_create():
        """بررسی بازی‌های جدید و ساخت چالش خودکار"""
        try:
            matches = _wc_get_matches()
            for m in matches:
                match_id = str(m.get("id", ""))
                if not match_id or db.wc_challenge_exists(match_id):
                    continue

                home_team = m.get("homeTeam", {})
                away_team = m.get("awayTeam", {})

                # نام تیم را از چند فیلد مختلف امتحان می‌کنیم
                home = (home_team.get("shortName") or home_team.get("name") or "").strip()
                away = (away_team.get("shortName") or away_team.get("name") or "").strip()

                # اگر هنوز تیم‌ها مشخص نشده‌اند (مرحله حذفی) رد می‌کنیم
                if not home or not away:
                    continue

                utc_date = m.get("utcDate", "")
                try:
                    dt = datetime.datetime.strptime(utc_date, "%Y-%m-%dT%H:%M:%SZ")
                    # فقط بازی‌هایی که حداقل ۳۰ دقیقه دیگر شروع می‌شوند
                    if dt < datetime.datetime.utcnow() + datetime.timedelta(minutes=30):
                        continue
                    dt_iran = _wc_utc_to_iran(dt)
                    match_time_str = dt_iran.strftime("%Y-%m-%d %H:%M") + " به وقت ایران"
                except Exception:
                    match_time_str = utc_date
                    dt = utc_date

                challenge_id = db.create_wc_challenge(match_id, home, away, dt)
                if challenge_id:
                    _wc_send_challenge_to_channel(challenge_id, home, away, match_time_str)
                    print(f"✅ چالش جدید ساخته شد: {home} vs {away} (ID: {challenge_id})")
                    time.sleep(0.3)  # جلوگیری از flood در ارسال به کانال
        except Exception as e:
            print(f"❌ _wc_auto_fetch_and_create: {e}")

    def _wc_auto_check_results():
        """بررسی نتایج بازی‌های تمام‌شده و اعلام برنده"""
        try:
            pending = db.get_pending_wc_challenges()
            if not pending:
                return
            finished = _wc_get_finished_matches()
            finished_ids = {str(m["id"]): m for m in finished}
            channel = getattr(config, "WC_CHANNEL_ID", "")

            for ch in pending:
                match_id = str(ch.get("match_id", ""))
                if match_id not in finished_ids:
                    continue
                match = finished_ids[match_id]
                winner_option = _wc_determine_winner(match)
                if not winner_option:
                    continue

                paid = db.finish_wc_challenge(ch["id"], winner_option)

                option_fa = {"team1": ch["team1"], "team2": ch["team2"], "draw": "مساوی"}.get(winner_option, winner_option)
                result_text = (
                    f"🏁 <b>پایان چالش!</b>\n\n"
                    f"⚽️ {ch['team1']} vs {ch['team2']}\n"
                    f"🏆 نتیجه: <b>{option_fa}</b>\n\n"
                    f"✅ برندگان ۲ برابر مبلغ شرطشان دریافت کردند!"
                )
                if channel and ch.get("channel_msg_id"):
                    try:
                        _bot.edit_message_text(result_text, chat_id=channel, message_id=ch["channel_msg_id"])
                    except Exception:
                        try:
                            _bot.send_message(channel, result_text)
                        except Exception:
                            pass

                # اطلاع رسانی به برندگان در پیوی
                for winner in paid:
                    try:
                        _bot.send_message(
                            winner["user_tg_id"],
                            f"🎉 <b>تبریک!</b> شرط‌بندی {ch['team1']} vs {ch['team2']} را بردید!\n"
                            f"{EM.EMOJI_DIAMONDS} <b>{winner['payout']} الماس</b> به حسابتان واریز شد."
                        )
                    except Exception:
                        pass
        except Exception as e:
            print(f"❌ _wc_auto_check_results: {e}")

    def _wc_scheduler():
        """
        حلقه زمانی:
        - هر ۱۵ دقیقه بازی‌های آینده چک می‌شوند
        - اگه بازی ۱ ساعت دیگه شروع بشه و چالشش ارسال نشده → همون لحظه می‌فرسته
        - نتایج بازی‌های تموم‌شده هم چک می‌شه
        """
        POLL = 900  # 15 دقیقه
        SEND_BEFORE_SECONDS = 3600  # 1 ساعت قبل از شروع

        while True:
            try:
                # ─── چک بازی‌های آینده برای ارسال ۱ ساعت قبل ─────────────
                comp = getattr(config, "WC_COMPETITION", "WC")
                data = _wc_api_get(f"competitions/{comp}/matches?status=SCHEDULED,TIMED")
                matches = data.get("matches", [])
                now_utc = datetime.datetime.utcnow()

                for m in matches:
                    match_id = str(m.get("id", ""))
                    if not match_id:
                        continue

                    utc_date = m.get("utcDate", "")
                    try:
                        dt = datetime.datetime.strptime(utc_date, "%Y-%m-%dT%H:%M:%SZ")
                    except Exception:
                        continue

                    seconds_until = (dt - now_utc).total_seconds()

                    # فقط بازی‌هایی که ۱ ساعت مانده تا شروعشون (با تلرانس ۱۵ دقیقه)
                    if not (0 < seconds_until <= SEND_BEFORE_SECONDS + POLL):
                        continue

                    if db.wc_challenge_exists(match_id):
                        continue  # قبلاً ارسال شده

                    home = (m.get("homeTeam", {}).get("shortName") or
                            m.get("homeTeam", {}).get("name") or "").strip()
                    away = (m.get("awayTeam", {}).get("shortName") or
                            m.get("awayTeam", {}).get("name") or "").strip()

                    if not home or not away:
                        continue  # تیم مشخص نشده

                    try:
                        dt_tehran = _wc_utc_to_iran(dt)
                        match_time_str = dt_tehran.strftime("%Y/%m/%d — %H:%M") + " (تهران)"
                    except Exception:
                        match_time_str = utc_date

                    challenge_id = db.create_wc_challenge(match_id, home, away, dt)
                    if challenge_id:
                        _wc_send_challenge_to_channel(challenge_id, home, away, match_time_str)
                        print(f"✅ چالش ۱ ساعت قبل ارسال شد: {home} vs {away}")
                    time.sleep(0.5)

                # ─── چک نتایج بازی‌های تموم‌شده ─────────────────────────────
                _wc_auto_check_results()

            except Exception as e:
                print(f"❌ _wc_scheduler: {e}")
            time.sleep(POLL)

    # اجرای scheduler در Thread جداگانه
    _wc_thread = threading.Thread(target=_wc_scheduler, daemon=True)
    _wc_thread.start()

    # ── تست اولیه دسترسی به کانال جام جهانی ─────────────────────────────────
    _wc_channel_cfg = getattr(config, "WC_CHANNEL_ID", "")
    if not _wc_channel_cfg:
        print("⚠️ WC_CHANNEL_ID تنظیم نشده — چالش‌های جام جهانی به هیچ کانالی ارسال نمی‌شوند.")
    else:
        _wc_target = int(_wc_channel_cfg) if str(_wc_channel_cfg).lstrip("-").isdigit() else _wc_channel_cfg
        try:
            chat_info = _bot.get_chat(_wc_target)
            member = _bot.get_chat_member(_wc_target, _bot.get_me().id)
            if member.status not in ("administrator", "creator"):
                print(f"⚠️ ربات در کانال {_wc_target} ادمین نیست — ارسال پیام به کانال شکست خواهد خورد.")
            else:
                print(f"✅ دسترسی به کانال جام جهانی تأیید شد: {getattr(chat_info, 'title', _wc_target)}")
        except Exception as e:
            print(f"❌ ربات نتوانست به کانال {_wc_target} دسترسی پیدا کند: {e}\n"
                  f"   بررسی کنید ربات در کانال عضو/ادمین باشد و WC_CHANNEL_ID صحیح باشد.")
    if not getattr(config, "FOOTBALL_API_KEY", ""):
        print("⚠️ FOOTBALL_API_KEY تنظیم نشده — هیچ بازی‌ای از Football API دریافت نمی‌شود.")

    # ── Callback: کاربر روی تیم کلیک کرد ────────────────────────────────────
    @_bot.callback_query_handler(func=lambda call: call.data.startswith("wc_pick_"))
    def callback_wc_pick(call):
        try:
            _, _, cid, option = call.data.split("_", 3)
            challenge_id = int(cid)
            challenge = db.get_wc_challenge(challenge_id)
            if not challenge or challenge["status"] != "pending":
                return _bot.answer_callback_query(call.id, "❌ این چالش دیگر فعال نیست.", show_alert=True)

            account = _get_account_cached(call.from_user.id)
            if not account:
                return _bot.answer_callback_query(call.id, "❌ ابتدا در پنل ربات ثبت‌نام کنید.", show_alert=True)

            min_bet = getattr(config, "WC_MIN_BET", 10)
            max_bet = getattr(config, "WC_MAX_BET", 5000)
            option_fa = {"team1": challenge["team1"], "team2": challenge["team2"], "draw": "مساوی"}.get(option, option)

            # ذخیره انتخاب موقت
            _wc_pending_bet[call.from_user.id] = {
                "challenge_id": challenge_id,
                "selected_option": option,
                "account_id": account["id"],
            }

            _bot.answer_callback_query(call.id, f"✅ انتخاب: {option_fa}", show_alert=False)
            try:
                _bot.send_message(
                    call.from_user.id,
                    f"⚽️ انتخاب شما: <b>{option_fa}</b>\n\n"
                    f"💎 مبلغ شرط را وارد کنید ({min_bet} تا {max_bet} الماس):\n"
                    f"مثال: <code>شرکت 200</code>"
                )
            except Exception:
                # اگر چت خصوصی باز نیست
                _bot.answer_callback_query(
                    call.id,
                    f"✅ انتخاب: {option_fa}\n\n"
                    f"برای ثبت شرط، به ربات پیام بده:\nشرکت [مبلغ]\nمثال: شرکت 200",
                    show_alert=True
                )
        except Exception as e:
            print(f"❌ callback_wc_pick: {e}")

    # ── Handler: کاربر مبلغ شرط را وارد کرد ────────────────────────────────
    @_bot.message_handler(func=lambda m: m.text and m.text.strip().startswith("شرکت ") and m.chat.type == "private")
    def cmd_wc_join(message):
        try:
            tg_id = message.from_user.id
            pending = _wc_pending_bet.get(tg_id)
            if not pending:
                return _bot.reply_to(message, "❌ ابتدا روی تیم مورد نظر در کانال کلیک کنید.")

            parts = message.text.strip().split()
            if len(parts) < 2:
                return _bot.reply_to(message, "❌ فرمت: شرکت [مبلغ]\nمثال: شرکت 200")
            try:
                amount = int(parts[1])
            except ValueError:
                return _bot.reply_to(message, "❌ مبلغ باید عدد باشد.")

            min_bet = getattr(config, "WC_MIN_BET", 10)
            max_bet = getattr(config, "WC_MAX_BET", 5000)
            if amount < min_bet or amount > max_bet:
                return _bot.reply_to(message, f"❌ مبلغ باید بین {min_bet} و {max_bet} الماس باشد.")

            challenge_id = pending["challenge_id"]
            selected_option = pending["selected_option"]
            account_id = pending["account_id"]

            challenge = db.get_wc_challenge(challenge_id)
            if not challenge:
                _wc_pending_bet.pop(tg_id, None)
                return _bot.reply_to(message, "❌ چالش یافت نشد.")

            option_fa = {"team1": challenge["team1"], "team2": challenge["team2"], "draw": "مساوی"}.get(selected_option, selected_option)
            success, msg_txt = db.join_wc_challenge(challenge_id, account_id, tg_id, selected_option, amount)
            _wc_pending_bet.pop(tg_id, None)

            if success:
                balance = db.get_token_balance(account_id)
                _bot.reply_to(
                    message,
                    f"✅ <b>شرط ثبت شد!</b>\n\n"
                    f"⚽️ {challenge['team1']} vs {challenge['team2']}\n"
                    f"🎯 انتخاب: <b>{option_fa}</b>\n"
                    f"💎 مبلغ: <b>{amount} الماس</b>\n"
                    f"💰 موجودی باقی‌مانده: {balance} الماس\n\n"
                    f"🏆 در صورت برد، <b>{amount * 2} الماس</b> دریافت می‌کنید!"
                )
            else:
                _bot.reply_to(message, msg_txt)
        except Exception as e:
            print(f"❌ cmd_wc_join: {e}")
            _bot.reply_to(message, f"❌ خطا: {e}")

    # ── Callback قدیمی bet_wc_ (سازگاری) ────────────────────────────────────
    @_bot.callback_query_handler(func=lambda call: call.data.startswith("bet_wc_"))
    def callback_bet_wc(call):
        try:
            parts = call.data.split("_", 3)
            challenge_id = int(parts[2])
            team_choice = parts[3]
            challenge = db.get_wc_challenge(challenge_id)
            if not challenge or challenge["status"] != "pending":
                return _bot.answer_callback_query(call.id, "❌ این چالش فعال نیست.", show_alert=True)
            account = _get_account_cached(call.from_user.id)
            if not account:
                return _bot.answer_callback_query(call.id, "❌ ابتدا در پنل ربات ثبت‌نام کنید.", show_alert=True)
            _wc_pending_bet[call.from_user.id] = {
                "challenge_id": challenge_id,
                "selected_option": team_choice,
                "account_id": account["id"],
            }
            _bot.answer_callback_query(call.id, f"✅ انتخاب ثبت شد! حالا مبلغ رو بنویس:\nشرکت [مبلغ]", show_alert=True)
        except Exception as e:
            print(f"❌ خطا در callback_bet_wc: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # /start
    # ══════════════════════════════════════════════════════════════════════════
    def _grant_free_trial(account_id: int, tg_id: int):
        """یک روز سلف رایگان برای کاربران جدید — فقط اگر اشتراک نداشته باشند
        و فقط پیام خوش‌آمد می‌فرستد؛ set_subscription صدا نمی‌زند چون در ثبت‌نام انجام شده."""
        try:
            existing = db.get_subscription(account_id)
            if existing:
                # اشتراک قبلاً وجود دارد — کاری نکن تا سلف قطع نشه
                return
            # اگر به هر دلیلی در ثبت‌نام ست نشده بود، اینجا ست می‌کنیم
            expires = db.set_subscription(account_id, "free_trial", 1)
            if expires:
                exp_str = _fmt_tehran(expires)
                try:
                    _bot.send_message(
                        tg_id,
                        f"{EM.EMOJI_DAILY_GIFT} <b>یک روز سلف رایگان هدیه گرفتید!</b>\n\n"
                        f"⏰ انقضا: <b>{exp_str}</b> (وقت تهران)\n\n"
                        f"برای تمدید، از منوی 🛒 خرید استفاده کنید."
                    )
                except Exception:
                    pass
                # تایمر اطلاع‌رسانی انقضا
                threading.Timer(86400, _notify_subscription_expired, args=[account_id, tg_id]).start()
        except Exception as e:
            print(f"❌ _grant_free_trial: {e}")

    def _notify_subscription_expired(account_id: int, tg_id: int):
        """اطلاع‌رسانی پایان اشتراک"""
        try:
            sub = db.get_subscription(account_id)
            if not sub:
                return
            exp = sub.get("expires_at")
            if isinstance(exp, str):
                exp = datetime.datetime.fromisoformat(exp.replace("Z", "+00:00"))
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=datetime.timezone.utc)
            if exp > datetime.datetime.now(datetime.timezone.utc):
                return  # هنوز فعاله
            site_url = getattr(config, "SITE_URL", "")
            markup = types.InlineKeyboardMarkup()
            # 🟢 دکمه تمدید با رنگ success (سبز)
            markup.add(types.InlineKeyboardButton("🛒 تمدید اشتراک", callback_data="pur_sub_diamond", style="success"))
            if site_url:
                # 🔵 دکمه وب‌سایت با رنگ primary (آبی)
                markup.add(types.InlineKeyboardButton("🌐 (دردسترس نیست) پنل وب", url=site_url, style="primary"))
            try:
                _bot.send_message(
                    tg_id,
                    "⏰ <b>اشتراک سلف شما به پایان رسید!</b>\n\n"
                    "برای ادامه استفاده از سلف‌بات، اشتراک خود را تمدید کنید. 👇",
                    reply_markup=markup
                )
            except Exception:
                pass
        except Exception as e:
            print(f"❌ _notify_subscription_expired: {e}")

    def _start_subscription_checker():
        """هر ۳۰ دقیقه اشتراک‌های نزدیک به انقضا رو چک می‌کنه"""
        def _checker():
            while True:
                try:
                    time.sleep(1800)  # 30 دقیقه
                    _check_expiring_subscriptions()
                except Exception as e:
                    print(f"❌ subscription checker: {e}")
        threading.Thread(target=_checker, daemon=True).start()

    def _check_expiring_subscriptions():
        try:
            import psycopg2
            from database_supabase import execute_query
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            soon = now_utc + datetime.timedelta(hours=2)
            rows = execute_query(
                "SELECT owner_id, expires_at FROM amel_subscriptions WHERE status IS DISTINCT FROM 'notified' AND expires_at BETWEEN %s AND %s",
                (now_utc, soon), fetch_all=True
            )
            for row in (rows or []):
                owner_id_val = row["owner_id"]
                tg_id = db.get_telegram_id_by_owner(owner_id_val)
                if not tg_id:
                    continue
                exp = row["expires_at"]
                remaining = _remaining_str(exp)
                try:
                    markup = types.InlineKeyboardMarkup()
                    # 🟢 دکمه تمدید با رنگ success (سبز)
                    markup.add(types.InlineKeyboardButton("🛒 تمدید اشتراک", callback_data="pur_sub_diamond", style="success"))
                    _bot.send_message(
                        tg_id,
                        f"⚠️ <b>اشتراک شما در حال انقضاست!</b>\n\n"
                        f"⏰ باقی‌مانده: <b>{remaining}</b>\n\n"
                        f"برای تمدید همین الان اقدام کنید 👇",
                        reply_markup=markup
                    )
                    execute_query(
                        "UPDATE amel_subscriptions SET status='notified' WHERE owner_id=%s",
                        (owner_id_val,)
                    )
                except Exception:
                    pass
        except Exception as e:
            print(f"❌ _check_expiring_subscriptions: {e}")

    _start_subscription_checker()

    # ══════════════════════════════════════════════════════════════════════════
    # 🆕 ساخت اکانت از طریق ربات — فلوی کامل Telethon
    # ══════════════════════════════════════════════════════════════════════════

    # ── ابزار کمکی: ادیت پیام موجود یا ارسال پیام جدید ────────────────────────
    def _send_or_edit(text, chat_id, message_id=None, reply_markup=None):
        if message_id:
            try:
                _bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup=reply_markup)
                return
            except Exception:
                pass
        try:
            _bot.send_message(chat_id, text, reply_markup=reply_markup)
        except Exception:
            pass

    # ── ساخت/اتصال اکانت پس از تایید کد یا ۲FA — بدون پرسیدن رمز پنل ─────────
    def _finalize_registration(tg_id, session, chat_id, message_id=None):
        try:
            tg_user = session["tg_user"]
            saved_session = session["saved_session"]
            phone = session.get("phone", "")
            tg_id_val = tg_user["id"]

            # بررسی اکانت تکراری — اول بر اساس همین کاربری که با ربات چت می‌کند
            # (مثلاً بعد از «حذف سلف») و در غیر این صورت بر اساس اکانت تلگرامی
            # که الان واردش شده. این کار باعث می‌شود اگر کاربر اکانت قبلی خودش
            # را داشته باشد، حتی با لاگین یک اکانت تلگرام دیگر، یوزرنیم/دارایی‌های
            # قبلی‌اش حفظ شود و یوزرنیم جدید ساخته نشود.
            existing = db.get_account_by_tg_id(tg_id) or db.get_account_by_tg_id(tg_id_val)
            if existing:
                db.set_setting(existing["id"], "session_data", saved_session)
                db.set_setting(existing["id"], "logged_in", "1")
                if phone:
                    db.set_setting(existing["id"], "phone", phone)
                db.save_telegram_user_id(existing["id"], tg_id)
                _reg_clear(tg_id)

                def _start_existing(_acc_id):
                    time.sleep(1.5)
                    try:
                        from bot import bot_manager
                        from app import get_loop
                        bot_manager.start(_acc_id, get_loop(), check_tokens=False)
                    except Exception as _e:
                        print(f"⚠️ bot_manager.start (existing): {_e}")
                threading.Thread(target=_start_existing, args=(existing["id"],), daemon=True).start()

                _send_or_edit(
                    f"✅ <b>خوش برگشتید!</b>\n\n"
                    f"👤 {tg_user['name']}\n"
                    f"🆔 اکانت موجود بود — سلف‌بات فعال شد!\n\n"
                    f"{EM.EMOJI_BALANCE} موجودی: <b>{db.get_token_balance(existing['id'])}</b> الماس",
                    chat_id, message_id,
                )
                return

            # ساخت یوزرنیم از نام یا username تلگرام
            base_username = (tg_user.get("username") or tg_user["name"] or f"user{tg_id_val}").lower()
            base_username = "".join(c for c in base_username if c.isalnum() or c == "_")[:20] or f"user{tg_id_val}"

            candidate = base_username
            suffix = 1
            while db.get_account_by_username(candidate):
                candidate = f"{base_username}{suffix}"
                suffix += 1

            # 🔓 رمز پنل وب دیگر از کاربر پرسیده نمی‌شود — به‌صورت خودکار تولید می‌شود
            auto_password = secrets.token_urlsafe(24)
            new_id = db.create_account(candidate, auto_password)
            if not new_id:
                _reg_clear(tg_id)
                _send_or_edit("❌ خطا در ساخت اکانت. دوباره /start بزنید.", chat_id, message_id)
                return

            db.init_user_settings(new_id)
            db.set_setting(new_id, "session_data", saved_session)
            db.set_setting(new_id, "logged_in", "1")
            if phone:
                db.set_setting(new_id, "phone", phone)
            db.save_telegram_user_id(new_id, tg_id)

            # هدیه خوش‌آمد
            db.add_tokens(new_id, config.WELCOME_TOKENS)

            # اشتراک رایگان یک‌روزه برای کاربر جدید
            try:
                if not db.get_subscription(new_id):
                    db.set_subscription(new_id, "free_trial", 1)
            except Exception as _e:
                print(f"⚠️ set free_trial on register: {_e}")

            _reg_clear(tg_id)

            def _start_new(_acc_id, _tg_id):
                time.sleep(1.5)
                try:
                    from bot import bot_manager
                    from app import get_loop
                    bot_manager.start(_acc_id, get_loop(), check_tokens=False)
                except Exception as _e:
                    print(f"⚠️ bot_manager.start (new): {_e}")
                threading.Timer(86400, _notify_subscription_expired, args=[_acc_id, _tg_id]).start()
            threading.Thread(target=_start_new, args=(new_id, tg_id), daemon=True).start()

            site_url = getattr(config, "SITE_URL", "")
            markup_done = types.InlineKeyboardMarkup()
            if site_url:
                markup_done.add(types.InlineKeyboardButton("🌐 ورود به پنل وب", url=site_url, style="primary"))

            _send_or_edit(
                f"🎉 <b>اکانت ساخته شد!</b>\n\n"
                f"👤 نام: <b>{tg_user['name']}</b>\n"
                f"🔑 یوزرنیم پنل: <code>{candidate}</code>\n\n"
                f"{EM.EMOJI_DAILY_GIFT} <b>{config.WELCOME_TOKENS} الماس</b> هدیه خوش‌آمد دریافت کردید!\n"
                f"⏰ <b>۱ روز سلف رایگان</b> فعال شد!\n\n"
                f"✅ سلف‌بات در حال اتصال است — چند لحظه صبر کنید.",
                chat_id, message_id,
                reply_markup=markup_done,
            )

            ref_tg_id = session.get("referrer_tg_id")
            if ref_tg_id:
                threading.Thread(target=_process_referral_async, args=(ref_tg_id, tg_id_val), daemon=True).start()

        except Exception as e:
            _reg_clear(tg_id)
            _send_or_edit(f"❌ خطا: {str(e)[:300]}\n\nدوباره /start بزنید.", chat_id, message_id)

    # ── ارسال کد تایید تلگرام به یک شماره (مشترک بین ثبت‌نام و اتصال مجدد) ───
    def _send_verification_code(tg_id, phone, chat_id=None):
        chat_id = chat_id or tg_id
        session = _reg_sessions.setdefault(tg_id, {"digits": "", "expires": time.time() + _REG_TIMEOUT})
        wait_msg = None
        try:
            wait_msg = _bot.send_message(chat_id, "⏳ در حال ارسال کد تأیید...")
        except Exception:
            pass

        try:
            from telethon import TelegramClient
            from telethon.sessions import StringSession

            async def _send_code():
                cl = TelegramClient(StringSession(), config.API_ID, config.API_HASH)
                await cl.connect()
                result = await cl.send_code_request(phone)
                partial = cl.session.save()
                await cl.disconnect()
                return result.phone_code_hash, partial

            phone_hash, partial_sess = _run_tg(_send_code())
            session["phone"] = phone
            session["phone_hash"] = phone_hash
            session["partial_session"] = partial_sess
            session["step"] = "code"
            session["digits"] = ""
            session["expires"] = time.time() + _REG_TIMEOUT

            if wait_msg:
                try:
                    _bot.delete_message(chat_id, wait_msg.message_id)
                except Exception:
                    pass

            sent = _bot.send_message(
                tg_id,
                f"📲 <b>کد تأیید</b>\n\n"
                f"کد ارسال‌شده به <b>{phone}</b> را با کیپد زیر وارد کنید:\n\n"
                f"<code>{_kp_display('', 'code')}</code>",
                reply_markup=_kp_markup("", "code"),
            )
            session["msg_id"] = sent.message_id

        except Exception as e:
            _reg_clear(tg_id)
            if wait_msg:
                try:
                    _bot.delete_message(chat_id, wait_msg.message_id)
                except Exception:
                    pass
            _bot.send_message(chat_id, f"❌ خطا در ارسال کد: {str(e)}\n\nدوباره /start بزنید.")

    # ── مرحله ۱: کاربر «ساخت اکانت / وصل کردن سلف» را می‌زند ──────────────────
    @_bot.callback_query_handler(func=lambda call: call.data == "reg_start")
    def callback_reg_start(call):
        tg_id = call.from_user.id
        _bot.answer_callback_query(call.id)

        # اگر قبلاً شماره‌ای برای این کاربر ثبت شده (ثبت‌نام قبلی/اتصال مجدد سلف)
        # → بدون سوال دوباره، مستقیم کد تایید به همان شماره فرستاده می‌شود
        existing_acc = db.get_account_by_tg_id(tg_id)
        stored_phone = db.get_setting(existing_acc["id"], "phone", "") if existing_acc else ""

        if stored_phone:
            _reg_sessions[tg_id] = {
                "step": "sending_code",
                "digits": "",
                "expires": time.time() + _REG_TIMEOUT,
            }
            try:
                _bot.edit_message_text(
                    f"📲 در حال ارسال کد تایید به شماره ثبت‌شده‌ی شما...\n<code>{stored_phone}</code>",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                )
            except Exception:
                pass
            _send_verification_code(tg_id, stored_phone, chat_id=call.message.chat.id)
            return

        _reg_sessions[tg_id] = {
            "step": "await_contact",
            "digits": "",
            "expires": time.time() + _REG_TIMEOUT,
        }
        try:
            _bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass

        kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        kb.add(types.KeyboardButton("📱 تایید شماره من", request_contact=True))
        _bot.send_message(
            tg_id,
            "📱 <b>مرحله ۱ از ۲ — شماره تلفن</b>\n\n"
            "برای ادامه، شماره خودت رو با دکمه زیر تایید کن 👇\n\n"
            "⏱ این فرم ۵ دقیقه اعتبار دارد.",
            reply_markup=kb,
        )

    # ── مرحله ۱b: دریافت شماره از طریق دکمه‌ی اشتراک‌گذاری مخاطب ─────────────
    @_bot.message_handler(
        content_types=["contact"],
        func=lambda m: m.chat.type == "private"
        and m.from_user.id in _reg_sessions
        and _reg_sessions[m.from_user.id].get("step") == "await_contact"
        and not _reg_expired(m.from_user.id)
    )
    def handle_reg_contact(message):
        tg_id = message.from_user.id
        contact = message.contact

        if contact.user_id and contact.user_id != tg_id:
            _bot.reply_to(
                message,
                "❗️ لطفاً شماره‌ی خودتان را با دکمه ارسال کنید، نه شخص دیگر.",
            )
            return

        phone = contact.phone_number.strip()
        if not phone.startswith("+"):
            phone = "+" + phone

        try:
            _bot.send_message(tg_id, "✅ شماره دریافت شد.", reply_markup=types.ReplyKeyboardRemove())
        except Exception:
            pass

        session = _reg_sessions[tg_id]
        session["phone"] = phone
        session["step"] = "sending_code"
        session["expires"] = time.time() + _REG_TIMEOUT

        _send_verification_code(tg_id, phone, chat_id=message.chat.id)

    # ── لغو با فرستادن /start در حین انتظار شماره ────────────────────────────
    @_bot.message_handler(
        func=lambda m: m.chat.type == "private"
        and m.from_user.id in _reg_sessions
        and _reg_sessions[m.from_user.id].get("step") == "await_contact"
        and m.content_type == "text"
        and not (m.text or "").startswith("/start")
    )
    def handle_reg_await_contact_other_text(message):
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        kb.add(types.KeyboardButton("📱 تایید شماره من", request_contact=True))
        _bot.reply_to(
            message,
            "👆 لطفاً با زدن دکمه زیر شماره خودتان را ارسال کنید.",
            reply_markup=kb,
        )

    # ── مرحله ۲fa: دریافت رمز دومرحله‌ای به صورت متن ────────────────────────

    @_bot.message_handler(
        func=lambda m: m.chat.type == "private"
        and m.from_user.id in _reg_sessions
        and _reg_sessions[m.from_user.id].get("step") == "2fa"
        and not _reg_expired(m.from_user.id)
    )
    def handle_reg_2fa_text(message):
        tg_id = message.from_user.id
        session = _reg_sessions[tg_id]
        password = message.text.strip()

        if not password:
            _bot.reply_to(message, "❗ رمز نمی‌تواند خالی باشد. دوباره تایپ کنید:")
            return

        try:
            _bot.delete_message(message.chat.id, message.message_id)
        except Exception:
            pass

        wait_msg = _bot.send_message(tg_id, "⏳ در حال تأیید رمز دو مرحله‌ای...")

        try:
            from telethon import TelegramClient
            from telethon.sessions import StringSession

            partial_sess = session["partial_session"]

            async def _verify_2fa():
                cl = TelegramClient(StringSession(partial_sess), config.API_ID, config.API_HASH)
                await cl.connect()
                await cl.sign_in(password=password)
                me = await cl.get_me()
                sess = cl.session.save()
                await cl.disconnect()
                return sess, me

            sess, me = _run_tg(_verify_2fa())
            session["saved_session"] = sess
            session["tg_user"] = {"id": me.id, "name": me.first_name, "username": getattr(me, "username", "")}
            session["digits"] = ""

            try:
                _bot.delete_message(tg_id, wait_msg.message_id)
            except Exception:
                pass

            _finalize_registration(tg_id, session, message.chat.id)

        except Exception as e:
            try:
                _bot.delete_message(tg_id, wait_msg.message_id)
            except Exception:
                pass
            session["digits"] = ""
            _bot.send_message(
                tg_id,
                "❌ رمز دو مرحله‌ای اشتباه است!\n\nدوباره رمز را تایپ کنید و بفرستید:",
            )

    # ── مرحله ۲ & ۳: کیپد (code / pw) ──────────────────────────────────────
    @_bot.callback_query_handler(func=lambda call: call.data.startswith("reg_kp_"))
    def callback_reg_kp(call):
        tg_id = call.from_user.id

        if _reg_expired(tg_id):
            _reg_clear(tg_id)
            _bot.answer_callback_query(call.id, "⏰ سشن منقضی شده! دوباره /start بزنید.", show_alert=True)
            try:
                _bot.edit_message_text("⏰ سشن منقضی شد.", chat_id=call.message.chat.id, message_id=call.message.message_id)
            except Exception:
                pass
            return

        session = _reg_sessions[tg_id]
        # parse: reg_kp_{mode}_{action}
        parts = call.data.split("_", 3)   # ["reg","kp",mode,action]
        mode = parts[2]
        action = parts[3]

        digits = session.get("digits", "")

        if action == "del":
            digits = digits[:-1]
        elif action == "confirm":
            _process_reg_confirm(call, tg_id, session, mode, digits)
            return
        elif action.isdigit():
            if len(digits) >= 10:
                _bot.answer_callback_query(call.id, "❗ حداکثر ۱۰ رقم", show_alert=True)
                return
            digits += action
        else:
            _bot.answer_callback_query(call.id)
            return

        session["digits"] = digits
        display = _kp_display(digits, mode)

        label_map = {
            "code": "📲 <b>مرحله ۲ از ۲ — کد تأیید</b>\n\nکد دریافتی را وارد کنید:",
            "2fa": "🔒 <b>رمز دو مرحله‌ای</b>\n\nرمز دو مرحله‌ای تلگرام را وارد کنید:",
        }
        text = f"{label_map.get(mode, '')}\n\n<code>{display}</code>"

        try:
            _bot.edit_message_text(
                text,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=_kp_markup(digits, mode),
            )
        except Exception:
            pass
        _bot.answer_callback_query(call.id)

    def _process_reg_confirm(call, tg_id, session, mode, digits):
        """پردازش تأیید در هر مرحله"""
        if not digits:
            _bot.answer_callback_query(call.id, "❗ چیزی وارد نکردید!", show_alert=True)
            return

        _bot.answer_callback_query(call.id, "⏳ در حال بررسی...")

        # ── تأیید کد تلگرام ──────────────────────────────────────────────────
        if mode == "code":
            try:
                from telethon import TelegramClient
                from telethon.sessions import StringSession
                from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, PhoneCodeExpiredError

                phone = session["phone"]
                phone_hash = session["phone_hash"]
                partial_sess = session["partial_session"]

                async def _verify_code():
                    cl = TelegramClient(StringSession(partial_sess), config.API_ID, config.API_HASH)
                    await cl.connect()
                    await cl.sign_in(phone=phone, code=digits, phone_code_hash=phone_hash)
                    me = await cl.get_me()
                    sess = cl.session.save()
                    await cl.disconnect()
                    return sess, me

                try:
                    sess, me = _run_tg(_verify_code())
                    # کد درسته — ساخت/اتصال اکانت بدون پرسیدن رمز عبور پنل
                    session["saved_session"] = sess
                    session["tg_user"] = {"id": me.id, "name": me.first_name, "username": getattr(me, "username", "")}
                    session["digits"] = ""
                    _finalize_registration(tg_id, session, call.message.chat.id, call.message.message_id)

                except Exception as e:
                    err_str = str(e)
                    if "SessionPasswordNeeded" in err_str or "password" in err_str.lower():
                        # نیاز به ۲FA
                        session["step"] = "2fa"
                        session["digits"] = ""
                        session["expires"] = time.time() + _REG_TIMEOUT
                        try:
                            _bot.edit_message_text(
                                "🔒 <b>رمز دو مرحله‌ای</b>\n\n"
                                "حساب شما رمز دو مرحله‌ای دارد.\n"
                                "رمز را تایپ کنید و بفرستید:",
                                chat_id=call.message.chat.id,
                                message_id=call.message.message_id,
                            )
                        except Exception:
                            pass
                    elif "PhoneCodeInvalid" in err_str or "PHONE_CODE_INVALID" in err_str:
                        session["digits"] = ""
                        try:
                            _bot.edit_message_text(
                                "❌ کد اشتباه بود! دوباره وارد کنید:\n\n"
                                f"<code>{_kp_display('', 'code')}</code>",
                                chat_id=call.message.chat.id,
                                message_id=call.message.message_id,
                                reply_markup=_kp_markup("", "code"),
                            )
                        except Exception:
                            pass
                    elif "PhoneCodeExpired" in err_str or "PHONE_CODE_EXPIRED" in err_str:
                        _reg_clear(tg_id)
                        try:
                            _bot.edit_message_text(
                                "⏰ کد منقضی شده! دوباره /start بزنید.",
                                chat_id=call.message.chat.id,
                                message_id=call.message.message_id,
                            )
                        except Exception:
                            pass
                    else:
                        _reg_clear(tg_id)
                        try:
                            _bot.edit_message_text(
                                f"❌ خطا: {err_str[:200]}\n\nدوباره /start بزنید.",
                                chat_id=call.message.chat.id,
                                message_id=call.message.message_id,
                            )
                        except Exception:
                            pass

            except Exception as e:
                _reg_clear(tg_id)
                try:
                    _bot.edit_message_text(f"❌ خطای داخلی: {str(e)[:200]}", chat_id=call.message.chat.id, message_id=call.message.message_id)
                except Exception:
                    pass

        # ── تأیید ۲FA ────────────────────────────────────────────────────────
        elif mode == "2fa":
            try:
                from telethon import TelegramClient
                from telethon.sessions import StringSession

                partial_sess = session["partial_session"]

                async def _verify_2fa():
                    cl = TelegramClient(StringSession(partial_sess), config.API_ID, config.API_HASH)
                    await cl.connect()
                    await cl.sign_in(password=digits)
                    me = await cl.get_me()
                    sess = cl.session.save()
                    await cl.disconnect()
                    return sess, me

                try:
                    sess, me = _run_tg(_verify_2fa())
                    session["saved_session"] = sess
                    session["tg_user"] = {"id": me.id, "name": me.first_name, "username": getattr(me, "username", "")}
                    session["digits"] = ""
                    _finalize_registration(tg_id, session, call.message.chat.id, call.message.message_id)
                except Exception as e:
                    session["digits"] = ""
                    try:
                        _bot.send_message(
                            call.message.chat.id,
                            "❌ رمز دو مرحله‌ای اشتباه است!\n\nدوباره رمز را تایپ کنید و بفرستید:",
                        )
                    except Exception:
                        pass

            except Exception as e:
                _reg_clear(tg_id)
                try:
                    _bot.edit_message_text(f"❌ خطا: {str(e)[:200]}", chat_id=call.message.chat.id, message_id=call.message.message_id)
                except Exception:
                    pass

    # ── لغو فرایند ───────────────────────────────────────────────────────────
    @_bot.callback_query_handler(func=lambda call: call.data == "reg_cancel")
    def callback_reg_cancel(call):
        tg_id = call.from_user.id
        _reg_clear(tg_id)
        _bot.answer_callback_query(call.id)
        try:
            _bot.edit_message_text("❌ فرایند ثبت‌نام لغو شد.\n\nبرای شروع مجدد /start بزنید.", chat_id=call.message.chat.id, message_id=call.message.message_id)
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════════════════════
    # ══════════════════════════════════════════════════════════════════════════
    # 🤖 مدیریت سلف — منوی مرکزی
    # ══════════════════════════════════════════════════════════════════════════
    def _self_management_keyboard(account_id):
        """کیبورد منوی مدیریت سلف — وضعیت دینامیک"""
        from bot import bot_manager
        markup = types.InlineKeyboardMarkup(row_width=1)
        is_logged = db.get_setting(account_id, "logged_in", "0") == "1"
        is_running = bot_manager.is_running(account_id)
        is_paused  = bot_manager.is_paused(account_id)

        if not is_logged:
            # سلف وصل نیست — فقط دکمه وصل کردن
            markup.add(types.InlineKeyboardButton(
                " وصل کردن سلف", callback_data="reg_start", style="success",
                icon_custom_emoji_id=str(EM.ID_REFERRAL)))
        else:
            if is_running and not is_paused:
                # سلف روشن است — دکمه خاموش کردن
                markup.add(types.InlineKeyboardButton(
                    " خاموش کردن سلف", callback_data="self_mgmt_stop", style="danger",
                    icon_custom_emoji_id=str(EM.ID_SELF_OFF)))
            else:
                # سلف خاموش یا pause است — دکمه روشن کردن
                markup.add(types.InlineKeyboardButton(
                    " روشن کردن سلف", callback_data="self_mgmt_start", style="success",
                    icon_custom_emoji_id=str(EM.ID_SELF_ON)))
            # حذف سلف همیشه نمایش داده می‌شود
            markup.add(types.InlineKeyboardButton(
                " حذف سلف از اکانت تلگرام", callback_data="remove_self_ask", style="danger",
                icon_custom_emoji_id=str(EM.ID_SELF_DELETE)))

        markup.add(types.InlineKeyboardButton(
            "🔙 بازگشت", callback_data="self_mgmt_back", style="danger"))
        return markup

    def _self_management_text(account_id):
        """متن وضعیت سلف"""
        from bot import bot_manager
        is_logged  = db.get_setting(account_id, "logged_in", "0") == "1"
        is_running = bot_manager.is_running(account_id)
        is_paused  = bot_manager.is_paused(account_id)
        sub        = db.get_subscription(account_id)

        if not is_logged:
            status_icon = "⚫️"
            status_text = "وصل نشده"
        elif is_running and not is_paused:
            status_icon = "🟢"
            status_text = "فعال و در حال اجرا"
        elif is_running and is_paused:
            status_icon = "🟡"
            status_text = "متوقف موقت (پلن منقضی)"
        else:
            status_icon = "🔴"
            status_text = "خاموش"

        # وضعیت اشتراک
        if sub:
            import datetime as _dt
            exp = sub.get("expires_at")
            if isinstance(exp, str):
                try:
                    exp = _dt.datetime.fromisoformat(exp)
                except Exception:
                    exp = None
            if exp:
                if exp.tzinfo:
                    exp = exp.replace(tzinfo=None)
                now_local = _dt.datetime.now()
                diff = exp - now_local
                if diff.total_seconds() > 0:
                    days = diff.days
                    hours = diff.seconds // 3600
                    mins  = (diff.seconds % 3600) // 60
                    if days > 0:
                        remaining = f"{days} روز و {hours} ساعت"
                    elif hours > 0:
                        remaining = f"{hours} ساعت و {mins} دقیقه"
                    else:
                        remaining = f"{mins} دقیقه"
                    sub_line = f"✅ فعال — باقی‌مانده: <b>{remaining}</b>"
                else:
                    sub_line = "❌ منقضی شده"
            else:
                sub_line = "❓ نامشخص"
        else:
            sub_line = "❌ اشتراک ندارید"

        return (
            f"🤖 <b>مدیریت سلف</b>\n\n"
            f"{status_icon} وضعیت: <b>{status_text}</b>\n"
            f"📦 اشتراک: {sub_line}\n\n"
            f"از دکمه‌های زیر استفاده کنید:"
        )

    @_bot.message_handler(func=lambda m: m.text == "مدیریت سلف", chat_types=['private'])
    def cmd_self_management(message):
        try:
            account = _get_account_cached(message.from_user.id)
            if not account:
                return _bot.reply_to(message, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.",
                                     reply_markup=_main_inline_keyboard())
            if _is_user_banned(account["id"]):
                return _bot.reply_to(
                    message,
                    "🚫 <b>شما توسط مالک از سلف بن شده‌اید.</b>\nامکان مدیریت سلف برای شما غیرفعال است.",
                    reply_markup=_main_inline_keyboard()
                )
            _bot.send_message(
                message.chat.id,
                _self_management_text(account["id"]),
                reply_markup=_self_management_keyboard(account["id"])
            )
        except Exception as e:
            print(f"❌ خطا در cmd_self_management: {e}")

    @_bot.callback_query_handler(func=lambda call: call.data in (
        "self_mgmt_stop", "self_mgmt_start", "self_mgmt_back", "self_mgmt_open"
    ))
    def callback_self_management(call):
        try:
            account = _get_account_cached(call.from_user.id)
            if not account:
                return _bot.answer_callback_query(call.id, "❌ اکانت یافت نشد.", show_alert=True)

            acc_id = account["id"]
            data   = call.data

            if data == "self_mgmt_open":
                if _is_user_banned(acc_id):
                    return _bot.answer_callback_query(
                        call.id, "🚫 شما توسط مالک از سلف بن شده‌اید.", show_alert=True)
                _bot.answer_callback_query(call.id)
                try:
                    _bot.edit_message_text(
                        _self_management_text(acc_id),
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        reply_markup=_self_management_keyboard(acc_id)
                    )
                except Exception:
                    pass
                return

            if data == "self_mgmt_back":
                _bot.answer_callback_query(call.id)
                try:
                    _bot.edit_message_text(
                        "📋 منوی اصلی:",
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        reply_markup=_main_inline_keyboard(account)
                    )
                except Exception:
                    pass
                return

            if data == "self_mgmt_stop":
                from bot import bot_manager
                import time as _time
                if not bot_manager.is_running(acc_id):
                    _bot.answer_callback_query(call.id, "⚠️ سلف از قبل خاموش است.", show_alert=True)
                else:
                    bot_manager.stop(acc_id)
                    # صبر کوتاه تا state بروز شود
                    _time.sleep(0.8)
                    _bot.answer_callback_query(call.id, "🔴 سلف خاموش شد.")

            elif data == "self_mgmt_start":
                from bot import bot_manager
                from app import get_loop
                import time as _time
                if _is_user_banned(acc_id):
                    return _bot.answer_callback_query(
                        call.id, "🚫 شما توسط مالک از سلف بن شده‌اید.", show_alert=True)
                if not db.get_setting(acc_id, "logged_in", "0") == "1":
                    return _bot.answer_callback_query(
                        call.id, "❌ سلف وصل نیست. ابتدا از «وصل کردن سلف» استفاده کنید.", show_alert=True)
                if not db.is_subscribed(acc_id):
                    return _bot.answer_callback_query(
                        call.id, "❌ اشتراک ندارید یا منقضی شده. ابتدا پلن تهیه کنید.", show_alert=True)
                if bot_manager.is_running(acc_id) and not bot_manager.is_paused(acc_id):
                    _bot.answer_callback_query(call.id, "✅ سلف از قبل روشن است.", show_alert=True)
                else:
                    bot_manager.start(acc_id, get_loop(), check_tokens=False, is_restart=True)
                    # صبر کوتاه تا heartbeat ثبت شود
                    _time.sleep(1.2)
                    _bot.answer_callback_query(call.id, "🟢 سلف روشن شد!")

            # ادیت پیام با وضعیت جدید
            try:
                _bot.edit_message_text(
                    _self_management_text(acc_id),
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=_self_management_keyboard(acc_id)
                )
            except Exception:
                pass

        except Exception as e:
            print(f"❌ خطا در callback_self_management: {e}")
            try:
                _bot.answer_callback_query(call.id, f"❌ خطا: {str(e)[:80]}", show_alert=True)
            except Exception:
                pass

    # ══════════════════════════════════════════════════════════════════════════
    # 🗑 حذف سلف از اکانت تلگرام (بدون از دست رفتن دارایی‌ها)
    # ══════════════════════════════════════════════════════════════════════════
    def _logout_telegram_session(session_data):
        """سعی می‌کند سشن تلگرام را به‌صورت کامل خارج (logout) کند تا واقعاً از اکانت بیرون بیاد"""
        if not session_data:
            return
        try:
            from telethon import TelegramClient
            from telethon.sessions import StringSession

            async def _do_logout():
                cl = TelegramClient(StringSession(session_data), config.API_ID, config.API_HASH)
                await cl.connect()
                try:
                    await cl.log_out()
                except Exception:
                    pass
                finally:
                    if cl.is_connected():
                        await cl.disconnect()

            _run_tg(_do_logout())
        except Exception as e:
            print(f"⚠️ خطا در خروج سشن تلگرام: {e}")

    def _remove_self_from_account(account_id: int):
        """سلف را از اکانت تلگرام فعلی خارج می‌کند ولی دارایی‌ها (الماس، یوزرنیم و ...) را حفظ می‌کند"""
        session_data = db.get_setting(account_id, "session_data", "")

        # ۱) متوقف کردن کلاینت در حال اجرا
        try:
            from bot import bot_manager
            bot_manager.stop(account_id)
        except Exception as e:
            print(f"⚠️ خطا در توقف سلف: {e}")

        # ۲) خروج واقعی سشن از اکانت تلگرام (revoke)
        _logout_telegram_session(session_data)

        # ۳) پاک‌سازی session در دیتابیس — اکانت/دارایی‌ها دست‌نخورده باقی می‌مانند
        db.set_setting(account_id, "session_data", "")
        db.set_setting(account_id, "logged_in", "0")

    @_bot.callback_query_handler(func=lambda call: call.data == "remove_self_ask")
    def callback_remove_self_ask(call):
        try:
            account = _get_account_cached(call.from_user.id)
            if not account:
                return _bot.answer_callback_query(call.id, "❌ ابتدا در پنل وب ثبت‌نام کنید.", show_alert=True)
            if db.get_setting(account["id"], "logged_in", "0") != "1":
                return _bot.answer_callback_query(call.id, "⚠️ سلف فعالی برای حذف وجود ندارد.", show_alert=True)

            markup = types.InlineKeyboardMarkup(row_width=2)
            # 🟢 دکمه تأیید با رنگ success (سبز)
            markup.add(
                types.InlineKeyboardButton("✅ بله، حذف کن", callback_data="remove_self_yes", style="success", icon_custom_emoji_id="5830326445422940546"),
                types.InlineKeyboardButton("❌ انصراف", callback_data="remove_self_no", style="danger", icon_custom_emoji_id="5832353674281620438")  # 🔴 قرمز
            )
            _bot.answer_callback_query(call.id)
            try:
                _bot.edit_message_text(
                    "⚠️ <b>مطمئن هستید؟</b>\n\n"
                    "با تأیید، سلف از اکانت تلگرامی که الان به آن وصل است خارج می‌شود.\n"
                    "💎 الماس‌ها و یوزرنیم پنل شما <b>حفظ می‌شوند</b>.\n\n"
                    "بعد از خروج می‌توانید دوباره با همین اکانت یا یک اکانت تلگرام دیگر، سلف را وصل کنید — بدون نیاز به ساخت اکانت جدید.",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=markup,
                )
            except Exception:
                pass
        except Exception as e:
            print(f"❌ خطا در callback_remove_self_ask: {e}")

    @_bot.callback_query_handler(func=lambda call: call.data == "remove_self_no")
    def callback_remove_self_no(call):
        _bot.answer_callback_query(call.id, "لغو شد.")
        try:
            _bot.edit_message_text("❌ عملیات لغو شد.", chat_id=call.message.chat.id, message_id=call.message.message_id)
        except Exception:
            pass

    @_bot.callback_query_handler(func=lambda call: call.data == "remove_self_yes")
    def callback_remove_self_yes(call):
        try:
            account = _get_account_cached(call.from_user.id)
            if not account:
                return _bot.answer_callback_query(call.id, "❌ ابتدا در پنل وب ثبت‌نام کنید.", show_alert=True)

            _bot.answer_callback_query(call.id, "⏳ در حال خروج سلف...")
            try:
                _bot.edit_message_text("⏳ در حال خروج سلف از اکانت تلگرام...", chat_id=call.message.chat.id, message_id=call.message.message_id)
            except Exception:
                pass

            _remove_self_from_account(account["id"])
            cache.invalidate(f"account_{call.from_user.id}")

            # آپدیت keyboard پایین صفحه (دکمه حذف سلف همچنان نمایش داده می‌شود)
            kb = _owner_keyboard() if call.from_user.id == OWNER_TG_ID else _user_keyboard()
            try:
                _bot.send_message(
                    call.message.chat.id,
                    "✅ <b>سلف با موفقیت از اکانت تلگرام خارج شد.</b>\n\n"
                    f"👤 یوزرنیم پنل شما (<b>{account['username']}</b>) و موجودی الماس حفظ شدند.\n\n"
                    "هر زمان خواستید، با همین اکانت یا یک اکانت تلگرام دیگر دوباره وصل شوید 👇",
                    reply_markup=types.InlineKeyboardMarkup(row_width=1).add(
                        types.InlineKeyboardButton("🤖 وصل کردن دوباره سلف", callback_data="reg_start", style="success")
                    ),
                )
                _bot.send_message(call.message.chat.id, "منوی اصلی:", reply_markup=kb)
            except Exception:
                pass
        except Exception as e:
            print(f"❌ خطا در callback_remove_self_yes: {e}")
            try:
                _bot.answer_callback_query(call.id, f"❌ خطا: {str(e)[:80]}", show_alert=True)
            except Exception:
                pass

    def _send_start_approval_request(message):
        """برای ادمین درخواست ورود کاربر را با دکمه‌های تایید/رد ارسال می‌کند"""
        tg_id = message.from_user.id
        tg_user = message.from_user
        full_name = ((tg_user.first_name or "") + (" " + tg_user.last_name if tg_user.last_name else "")).strip()
        username_part = f"@{tg_user.username}" if tg_user.username else "ندارد"

        # درخواست کاربر را برای اجرای خودکار پس از تایید نگه می‌داریم
        _pending_start_messages[tg_id] = message
        db_cache.set_start_approval_status(tg_id, "pending")

        _bot.reply_to(
            message,
            "🔒 <b>درخواست ورود شما ثبت شد.</b>\n\n"
            "درخواست شما برای ورود به ربات برای ادمین ارسال شد ✅\n"
            "لطفاً منتظر تایید ادمین بمانید."
        )

        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("✅ تایید", callback_data=f"start_approve_{tg_id}", style="success"),
            types.InlineKeyboardButton("❌ رد", callback_data=f"start_reject_{tg_id}", style="danger"),
        )
        try:
            _bot.send_message(
                OWNER_TG_ID,
                "🔔 <b>درخواست ورود جدید</b>\n\n"
                f"👤 نام: {full_name or 'نامشخص'}\n"
                f"🆔 آیدی: <code>{tg_id}</code>\n"
                f"🔗 یوزرنیم: {username_part}\n\n"
                "آیا این کاربر اجازه ورود به ربات را دارد؟",
                reply_markup=markup
            )
        except Exception as e:
            print(f"❌ خطا در ارسال درخواست ورود به ادمین: {e}")

    @_bot.callback_query_handler(func=lambda call: call.data.startswith("start_approve_") or call.data.startswith("start_reject_"))
    def callback_start_approval(call):
        try:
            if call.from_user.id != OWNER_TG_ID and not db.is_sub_admin(call.from_user.id):
                _bot.answer_callback_query(call.id, "⛔️ شما اجازه انجام این کار را ندارید", show_alert=True)
                return

            is_approve = call.data.startswith("start_approve_")
            target_tg_id = int(call.data.split("_")[-1])

            if is_approve:
                db_cache.set_start_approval_status(target_tg_id, "approved")
                _bot.answer_callback_query(call.id, "✅ کاربر تایید شد")
                try:
                    _bot.edit_message_text(
                        call.message.text + "\n\n✅ <b>تایید شد</b>",
                        call.message.chat.id, call.message.message_id
                    )
                except Exception:
                    pass

                orig_message = _pending_start_messages.pop(target_tg_id, None)
                try:
                    _bot.send_message(target_tg_id, "✅ درخواست ورود شما توسط ادمین تایید شد! به ربات خوش آمدید 🎉")
                except Exception:
                    pass
                if orig_message is not None:
                    try:
                        cmd_start(orig_message)
                    except Exception as e:
                        print(f"❌ خطا در اجرای خودکار start پس از تایید: {e}")
            else:
                db_cache.set_start_approval_status(target_tg_id, "rejected")
                _pending_start_messages.pop(target_tg_id, None)
                _bot.answer_callback_query(call.id, "❌ کاربر رد شد")
                try:
                    _bot.edit_message_text(
                        call.message.text + "\n\n❌ <b>رد شد</b>",
                        call.message.chat.id, call.message.message_id
                    )
                except Exception:
                    pass
                try:
                    _bot.send_message(target_tg_id, "❌ درخواست ورود شما توسط ادمین رد شد.")
                except Exception:
                    pass
        except Exception as e:
            print(f"❌ خطا در callback_start_approval: {e}")

    @_bot.message_handler(commands=["start"])
    def cmd_start(message):
        try:
            tg_id = message.from_user.id

            # ── دروازه‌ی تایید ادمین ────────────────────────────────────────────
            # ✅ کسایی که از قبل توی دیتابیس دائمی حساب دارن (یعنی قبلاً لاگین/تایید
            # شده بودن) نیازی به تایید دوباره ندارن — even اگه بعد از ری‌استارت
            # سرور، جدول موقتِ start_approvals (که SQLite محلیه) خالی شده باشه.
            if tg_id != OWNER_TG_ID:
                already_has_account = False
                try:
                    already_has_account = db.get_account_by_tg_id(tg_id) is not None
                except Exception:
                    already_has_account = False

                if already_has_account:
                    db_cache.set_start_approval_status(tg_id, "approved")
                else:
                    approval_status = db_cache.get_start_approval_status(tg_id)
                    if approval_status != "approved":
                        if approval_status == "pending":
                            _bot.reply_to(
                                message,
                                "⏳ درخواست ورود شما هنوز در انتظار تایید ادمین است.\n"
                                "لطفاً کمی صبر کنید."
                            )
                        else:
                            _send_start_approval_request(message)
                        return

            parts = message.text.strip().split()
            ref_code = parts[1] if len(parts) > 1 else None

            if ref_code and ref_code.startswith("ref_"):
                try:
                    referrer_id = int(ref_code[4:])
                    threading.Thread(target=_process_referral_async, args=(referrer_id, tg_id), daemon=True).start()
                except Exception:
                    pass

            is_member, missing = _check_membership_cached(tg_id)
            if not is_member:
                send_forced_channels_menu(message, missing)
                return

            account = _get_account_cached(tg_id)
            site_url = getattr(config, "SITE_URL", "")

            if not account:
                # ذخیره کد رفرال در سشن در صورت وجود
                if ref_code and ref_code.startswith("ref_"):
                    try:
                        referrer_tg = int(ref_code[4:])
                        _reg_sessions[tg_id] = _reg_sessions.get(tg_id, {})
                        _reg_sessions[tg_id]["referrer_tg_id"] = referrer_tg
                    except Exception:
                        pass

                markup = types.InlineKeyboardMarkup(row_width=1)
                # 🟢 دکمه ساخت اکانت با ربات با رنگ success (سبز)
                markup.add(
                    types.InlineKeyboardButton("🤖 ساخت اکانت با ربات", callback_data="reg_start", style="success")
                )
                if site_url:
                    # 🔵 دکمه ساخت با وب‌سایت با رنگ primary (آبی)
                    markup.add(types.InlineKeyboardButton("🌐 ساخت اکانت با وب سایت", url=site_url + "/register", style="primary"))
                markup.add(types.InlineKeyboardButton("📖 راهنما", callback_data="guide_menu", style="primary"))
                _bot.reply_to(
                    message,
                    "👋 <b>سلام!</b>\n\n"
                    "❌ اکانت نداری! برای استفاده از ربات باید اکانت بسازی:\n\n"
                    "🤖 <b>ساخت با ربات</b> — مستقیم از همینجا، بدون نیاز به سایت\n"
                    "🌐 <b>ساخت با وب سایت</b> — از طریق پنل وب",
                    reply_markup=markup,
                )
                return

            # سلف رایگان فقط برای کاربری که هیچ اشتراکی ندارد
            # (جلوگیری از فراخوانی set_subscription هر بار و قطع شدن سلف)
            try:
                if not db.get_subscription(account["id"]):
                    threading.Thread(target=_grant_free_trial, args=[account["id"], tg_id], daemon=True).start()
            except Exception:
                pass

            # اگر سلف از اکانت حذف شده → دکمه وصل کردن دوباره نمایش بده
            if message.chat.type == 'private':
                is_logged_in = db.get_setting(account["id"], "logged_in", "0") == "1"
                if not is_logged_in:
                    kb_reconnect = types.InlineKeyboardMarkup(row_width=1)
                    kb_reconnect.add(
                        types.InlineKeyboardButton(" وصل کردن سلف", callback_data="reg_start", style="success")
                    )
                    _bot.reply_to(
                        message,
                        f"👋 سلام <b>{account['username']}</b>!\n\n"
                        "⚠️ <b>سلف شما به اکانت وصل نیست.</b>\n"
                        "برای وصل کردن دوباره دکمه زیر را بزنید:",
                        parse_mode="HTML",
                        reply_markup=kb_reconnect
                    )
                    return

            stats = db.get_token_stats(account["id"])
            sub = db.get_subscription(account["id"])

            now_tehran = _now_tehran().strftime("%Y/%m/%d — %H:%M")

            # وضعیت اشتراک
            if sub:
                sub_exp = sub.get("expires_at")
                plan_fa = {"weekly": "هفتگی", "monthly": "ماهانه", "bimonthly": "دو ماهه", "free_trial": "رایگان"}.get(sub.get("plan", ""), sub.get("plan", ""))
                import datetime as _dt
                exp_dt = sub_exp
                if isinstance(exp_dt, str):
                    exp_dt = _dt.datetime.fromisoformat(exp_dt.replace("Z", "+00:00"))
                if exp_dt and exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=_dt.timezone.utc)
                is_active = exp_dt and exp_dt > _dt.datetime.now(_dt.timezone.utc)
                sub_status = (
                    f"✅ فعال — پلن {plan_fa}\n"
                    f"   📅 انقضا: {_fmt_tehran(sub_exp)}\n"
                    f"   ⏳ باقی‌مانده: {_remaining_str(sub_exp)}"
                ) if is_active else "❌ اشتراک ندارید"
            else:
                sub_status = "❌ اشتراک ندارید"

            if message.chat.type == 'private':
                kb_markup = _owner_keyboard() if tg_id == OWNER_TG_ID else _user_keyboard()
            else:
                kb_markup = None

            # ── ساخت متن خوش‌آمد از قالب قابل تنظیم ─────────────────────────
            default_welcome = (
                "👋 سلام {name}!\n\n"
                "🕐 وقت تهران: {time}\n\n"
                "💎 موجودی الماس: {balance}\n"
                "📊 کل دریافتی: {total_earned}\n\n"
                "📦 اشتراک سلف:\n{sub_status}"
            )
            welcome_template = db.get_global_setting("welcome_text", default_welcome)
            tg_user = message.from_user
            full_name = ((tg_user.first_name or "") + (" " + tg_user.last_name if tg_user.last_name else "")).strip()
            mention = f"<a href='tg://user?id={tg_id}'>{full_name or account['username']}</a>"
            welcome_text = welcome_template.format(
                name=account["username"],
                name_full=full_name or account["username"],
                mention=mention,
                tag=f"@{account['username']}",
                tg_id=tg_id,
                time=now_tehran,
                balance=stats["balance"],
                total_earned=stats["total_earned"],
                sub_status=sub_status,
            )

            # ── عکس خوش‌آمد ───────────────────────────────────────────────────
            welcome_photo = db.get_global_setting("welcome_photo_id", "")

            if welcome_photo and message.chat.type == 'private':
                _bot.send_photo(
                    message.chat.id,
                    welcome_photo,
                    caption=welcome_text,
                    reply_markup=kb_markup
                )
            else:
                _bot.reply_to(message, welcome_text, reply_markup=kb_markup)

            if message.chat.type == 'private':
                _bot.send_message(message.chat.id, "📋 منوی اصلی:", reply_markup=_main_inline_keyboard(account))

            if message.chat.type == 'private':
                sponsors = getattr(config, 'SPONSORS', [])
                if sponsors:
                    sponsors_text = "🤝 <b>اسپانسرهای رسمی پروژه:</b>\n"
                    for sp in sponsors:
                        sponsors_text += f"🔸 @{sp['username']}\n"
                    sponsors_text += f"\n👑 <b>مالک:</b> @{config.OWNER_USERNAME}"
                    _bot.send_message(message.chat.id, sponsors_text)
        except Exception as e:
            print(f"❌ خطا در cmd_start: {e}")

    def _process_referral_async(referrer_id, tg_id):
        try:
            if db.process_referral(referrer_id, tg_id):
                referrer_tg = db.get_telegram_id_by_owner(referrer_id)
                if referrer_tg and _bot:
                    _bot.send_message(referrer_tg, 
                        f"🎉 یک نفر با لینک شما عضو شد!\n"
                        f"<b>+{config.REFERRAL_TOKENS} الماس</b> دریافت کردید 💎")
        except Exception as e:
            print(f"❌ خطا در رفرال: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # Callback: بررسی عضویت
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.callback_query_handler(func=lambda call: call.data == "check_join")
    def callback_check_join(call):
        try:
            cache.invalidate(f"membership_{call.from_user.id}")
            is_member, missing = _check_membership_cached(call.from_user.id)
            if is_member:
                _bot.answer_callback_query(call.id, "عضویت تأیید شد! ✅")
                try: 
                    _bot.delete_message(call.message.chat.id, call.message.message_id)
                except: 
                    pass
                cmd_start(call.message)
            else:
                _bot.answer_callback_query(call.id, f"هنوز در {len(missing)} کانال عضو نشده‌اید! ❌", show_alert=True)
        except Exception as e:
            print(f"❌ خطا در callback_check_join: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # دکمه‌های منوی اصلی
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.message_handler(func=lambda m: m.text == "💎 موجودی", chat_types=['private'])
    def cmd_balance(message):
        try:
            if not require_membership(message):
                return
            account = _get_account_cached(message.from_user.id)
            if not account:
                return _bot.reply_to(message, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.", reply_markup=_main_inline_keyboard())
            stats = db.get_token_stats(account["id"])
            ref_count = db.get_referral_count(account["id"])
            token_price = getattr(config, 'TOKEN_PRICE_TOMAN', 200)
            _bot.reply_to(message,
                f"{EM.EMOJI_DIAMONDS} <b>موجودی الماس</b>\n\n"
                f"💰 فعلی: <b>{stats['balance']}</b>\n"
                f"📊 کل: <b>{stats['total_earned']}</b>\n"
                f"👥 رفرال: <b>{ref_count}</b> نفر\n"
                f"💵 قیمت هر الماس: <b>{token_price} تومان</b>",
                reply_markup=_main_inline_keyboard(account))
        except Exception as e:
            print(f"❌ خطا در cmd_balance: {e}")

    @_bot.callback_query_handler(func=lambda call: call.data == "menu_balance")
    def callback_menu_balance(call):
        try:
            if call.message.chat.type != 'private':
                return
            account = _get_account_cached(call.from_user.id)
            if not account:
                return _bot.answer_callback_query(call.id, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.", show_alert=True)
            stats = db.get_token_stats(account["id"])
            ref_count = db.get_referral_count(account["id"])
            token_price = getattr(config, 'TOKEN_PRICE_TOMAN', 200)
            _bot.answer_callback_query(call.id)
            _bot.send_message(call.message.chat.id,
                f"{EM.EMOJI_DIAMONDS} <b>موجودی الماس</b>\n\n"
                f"💰 فعلی: <b>{stats['balance']}</b>\n"
                f"📊 کل: <b>{stats['total_earned']}</b>\n"
                f"👥 رفرال: <b>{ref_count}</b> نفر\n"
                f"💵 قیمت هر الماس: <b>{token_price} تومان</b>",
                reply_markup=_main_inline_keyboard(account))
        except Exception as e:
            print(f"❌ خطا در callback_menu_balance: {e}")

    @_bot.message_handler(func=lambda m: m.text == "🎁 هدیه روزانه", chat_types=['private'])
    def cmd_daily(message):
        _do_daily(message.from_user.id, message.chat.id, reply_to=message.message_id)

    @_bot.callback_query_handler(func=lambda call: call.data == "menu_daily")
    def callback_menu_daily(call):
        _bot.answer_callback_query(call.id)
        _do_daily(call.from_user.id, call.message.chat.id)

    def _do_daily(tg_id, chat_id, reply_to=None):
        try:
            account = _get_account_cached(tg_id)
            if not account:
                return _bot.send_message(chat_id, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.", reply_markup=_main_inline_keyboard())
            success, msg = db.claim_daily_token(account["id"])
            cache.invalidate(f"account_{tg_id}")
            if success:
                stats = db.get_token_stats(account["id"])
                text = f"{msg}\n\n💎 موجودی جدید: <b>{stats['balance']}</b>"
            else:
                text = msg
            kwargs = {"reply_markup": _main_inline_keyboard(account)}
            if reply_to:
                kwargs["reply_to_message_id"] = reply_to
            _bot.send_message(chat_id, text, **kwargs)
        except Exception as e:
            print(f"❌ خطا در _do_daily: {e}")

    @_bot.message_handler(func=lambda m: m.text == "🔗 رفرال", chat_types=['private'])
    def cmd_referral(message):
        _do_referral(message.from_user.id, message.chat.id, reply_to=message.message_id)

    @_bot.callback_query_handler(func=lambda call: call.data == "menu_referral")
    def callback_menu_referral(call):
        _bot.answer_callback_query(call.id)
        _do_referral(call.from_user.id, call.message.chat.id)

    def _do_referral(tg_id, chat_id, reply_to=None):
        try:
            account = _get_account_cached(tg_id)
            if not account:
                return _bot.send_message(chat_id, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.", reply_markup=_main_inline_keyboard())
            link = f"https://t.me/{BOT_USERNAME}?start=ref_{account['id']}"
            ref_count = db.get_referral_count(account["id"])
            token_price = getattr(config, 'TOKEN_PRICE_TOMAN', 200)
            referral_value = config.REFERRAL_TOKENS * token_price
            kwargs = {"reply_markup": _main_inline_keyboard(account)}
            if reply_to:
                kwargs["reply_to_message_id"] = reply_to
            _bot.send_message(chat_id,
                f"🔗 <b>لینک رفرال شما:</b>\n<code>{link}</code>\n\n"
                f"👥 تعداد: <b>{ref_count}</b>\n"
                f"🎁 پاداش: <b>{config.REFERRAL_TOKENS} الماس</b> (معادل {referral_value} تومان)",
                **kwargs)
        except Exception as e:
            print(f"❌ خطا در _do_referral: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # 🛒 سیستم خرید و اشتراک
    # ══════════════════════════════════════════════════════════════════════════

    # ── تعریف پلن‌ها ──────────────────────────────────────────────────────────
    MONTHLY_TOMAN   = 90_000
    PLANS = {
        "weekly":    {"fa": "هفتگی",    "days": 7,  "toman": MONTHLY_TOMAN // 4,  "diamonds": 100},
        "monthly":   {"fa": "ماهانه",   "days": 30, "toman": MONTHLY_TOMAN,        "diamonds": 360},
        "bimonthly": {"fa": "دو ماهه",  "days": 60, "toman": MONTHLY_TOMAN * 2,   "diamonds": 700},
    }
    DIAMOND_RATE    = 250   # هر الماس = ۲۵۰ تومان (۱۰۰ الماس = ۲۵,۰۰۰ تومان)
    DIAMOND_MIN_BUY = 100   # حداقل خرید الماس

    # وضعیت موقت کاربران برای خرید
    _purchase_states = {}  # tg_id -> {step, data}

    def _get_card_number():
        return db.get_global_setting("card_number", "----")

    def _purchase_main_keyboard():
        markup = types.InlineKeyboardMarkup(row_width=1)
        # 🟢 دکمه‌های خرید با رنگ success (سبز)
        markup.add(
            types.InlineKeyboardButton(" خرید اشتراک با الماس", callback_data="pur_sub_diamond", style="success", icon_custom_emoji_id=str(EM.ID_DIAMONDS)),
        )
        # 🔵 دکمه‌های خرید با کارت با رنگ primary (آبی)
        markup.add(
            types.InlineKeyboardButton(" خرید اشتراک با کارت", callback_data="pur_sub_card", style="primary", icon_custom_emoji_id=str(EM.ID_SET_CARD)),
        )
        # 🟢 دکمه خرید الماس با رنگ success (سبز)
        markup.add(
            types.InlineKeyboardButton(" خرید الماس", callback_data="pur_buy_diamond", style="success", icon_custom_emoji_id=str(EM.ID_DIAMONDS)),
        )
        return markup

    def _plans_keyboard(prefix: str):
        markup = types.InlineKeyboardMarkup(row_width=1)
        for key, p in PLANS.items():
            # 🔵 دکمه‌های پلن با رنگ primary (آبی)
            markup.add(types.InlineKeyboardButton(
                f"{p['fa']} — {p['toman']:,} تومان / {p['diamonds']} الماس",
                callback_data=f"{prefix}_{key}",
                style="primary"
            ))
        # 🔴 دکمه بازگشت با رنگ danger (قرمز)
        markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="pur_back", style="danger"))
        return markup

    @_bot.message_handler(func=lambda m: m.text and m.text.strip() in ("🛒 خرید الماس", "🛒 خرید"), chat_types=['private'])
    def cmd_buy(message):
        _do_buy(message.from_user.id, message.chat.id, reply_to=message.message_id)

    @_bot.callback_query_handler(func=lambda call: call.data == "menu_buy")
    def callback_menu_buy(call):
        _bot.answer_callback_query(call.id)
        _do_buy(call.from_user.id, call.message.chat.id)

    def _do_buy(tg_id, chat_id, reply_to=None):
        try:
            account = _get_account_cached(tg_id)
            if not account:
                return _bot.send_message(chat_id, "❌ ابتدا در پنل وب ثبت‌نام کنید.", reply_markup=_main_inline_keyboard())
            balance = db.get_token_balance(account["id"])
            kwargs = {"reply_markup": _purchase_main_keyboard()}
            if reply_to:
                kwargs["reply_to_message_id"] = reply_to
            _bot.send_message(chat_id,
                f"🛒 <b>منوی خرید</b>\n\n"
                f"{EM.EMOJI_BALANCE} موجودی فعلی شما: <b>{balance} الماس</b>\n\n"
                f"یکی از گزینه‌های زیر را انتخاب کنید:",
                **kwargs)
        except Exception as e:
            print(f"❌ خطا در _do_buy: {e}")

    # ── Callback اصلی خرید ────────────────────────────────────────────────────
    @_bot.callback_query_handler(func=lambda call: call.data.startswith("pur_"))
    def callback_purchase(call):
        try:
            data = call.data
            tg_id = call.from_user.id
            account = _get_account_cached(tg_id)
            if not account:
                return _bot.answer_callback_query(call.id, "❌ ابتدا در پنل وب ثبت‌نام کنید.", show_alert=True)

            # ── بازگشت ──────────────────────────────────────────────────────
            if data == "pur_back":
                balance = db.get_token_balance(account["id"])
                _purchase_states.pop(tg_id, None)
                return _bot.edit_message_text(
                    f"🛒 <b>منوی خرید</b>\n\n💎 موجودی: <b>{balance} الماس</b>\n\nیکی از گزینه‌های زیر را انتخاب کنید:",
                    chat_id=call.message.chat.id, message_id=call.message.message_id,
                    reply_markup=_purchase_main_keyboard()
                )

            # ── اشتراک با الماس ─────────────────────────────────────────────
            elif data == "pur_sub_diamond":
                balance = db.get_token_balance(account["id"])
                text = (
                    f"{EM.EMOJI_DIAMONDS} <b>خرید اشتراک با الماس</b>\n\n"
                    f"موجودی شما: <b>{balance} الماس</b>\n\n"
                    f"یک پلن را انتخاب کنید:"
                )
                _bot.edit_message_text(text, chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=_plans_keyboard("pur_sdiam"))
                _bot.answer_callback_query(call.id)

            elif data.startswith("pur_sdiam_"):
                plan_key = data.split("_", 2)[2]
                plan = PLANS.get(plan_key)
                if not plan:
                    return _bot.answer_callback_query(call.id, "❌ پلن نامعتبر", show_alert=True)
                balance = db.get_token_balance(account["id"])
                cost = plan["diamonds"]
                if balance < cost:
                    need = cost - balance
                    markup = types.InlineKeyboardMarkup()
                    # 🟢 دکمه خرید الماس با رنگ success (سبز)
                    markup.add(types.InlineKeyboardButton("🛍 خرید الماس", callback_data="pur_buy_diamond", style="success"))
                    # 🔴 دکمه بازگشت با رنگ danger (قرمز)
                    markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="pur_sub_diamond", style="danger"))
                    return _bot.edit_message_text(
                        f"❌ <b>موجودی کافی نیست!</b>\n\n"
                        f"{EM.EMOJI_BALANCE} موجودی: {balance} الماس\n"
                        f"💎 نیاز: {cost} الماس\n"
                        f"💎 کمبود: {need} الماس\n\n"
                        f"💡 برای کسب الماس:\n"
                        f"• دریافت هدیه روزانه 🎁\n"
                        f"• دعوت دوستان 🔗\n"
                        f"• خرید الماس 🛍",
                        chat_id=call.message.chat.id, message_id=call.message.message_id,
                        reply_markup=markup
                    )
                # کسر الماس و فعال‌سازی
                db.deduct_tokens(account["id"], cost)
                expires = db.set_subscription(account["id"], plan_key, plan["days"])
                exp_str = expires.strftime("%Y-%m-%d") if expires else "نامشخص"
                _bot.edit_message_text(
                    f"✅ <b>اشتراک {plan['fa']} فعال شد!</b>\n\n"
                    f"💎 {cost} الماس کسر شد\n"
                    f"📅 انقضا: <b>{exp_str}</b>",
                    chat_id=call.message.chat.id, message_id=call.message.message_id
                )
                _bot.answer_callback_query(call.id, f"✅ اشتراک {plan['fa']} فعال شد!", show_alert=True)

            # ── اشتراک با کارت ──────────────────────────────────────────────
            elif data == "pur_sub_card":
                _bot.edit_message_text(
                    "💳 <b>خرید اشتراک با کارت</b>\n\nیک پلن را انتخاب کنید:",
                    chat_id=call.message.chat.id, message_id=call.message.message_id,
                    reply_markup=_plans_keyboard("pur_scard")
                )
                _bot.answer_callback_query(call.id)

            elif data.startswith("pur_scard_"):
                plan_key = data.split("_", 2)[2]
                plan = PLANS.get(plan_key)
                if not plan:
                    return _bot.answer_callback_query(call.id, "❌ پلن نامعتبر", show_alert=True)
                card = _get_card_number()
                payment_id = db.create_payment(
                    account["id"], tg_id, "subscription",
                    plan=plan_key, toman_amount=plan["toman"]
                )
                _purchase_states[tg_id] = {"step": "waiting_receipt_sub", "payment_id": payment_id}
                markup = types.InlineKeyboardMarkup()
                # 🔴 دکمه بازگشت با رنگ danger (قرمز)
                markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="pur_sub_card", style="danger"))
                _bot.edit_message_text(
                    f"💳 <b>پرداخت اشتراک {plan['fa']}</b>\n\n"
                    f"💰 مبلغ: <b>{plan['toman']:,} تومان</b>\n"
                    f"💳 شماره کارت: <code>{card}</code>\n"
                    f"👤 به نام: <b>غفاری</b>\n\n"
                    f"بعد از واریز، تصویر رسید را ارسال کنید 👇",
                    chat_id=call.message.chat.id, message_id=call.message.message_id,
                    reply_markup=markup
                )
                _bot.answer_callback_query(call.id)

            # ── خرید الماس ──────────────────────────────────────────────────
            elif data == "pur_buy_diamond":
                card = _get_card_number()
                _purchase_states[tg_id] = {"step": "waiting_diamond_amount"}
                markup = types.InlineKeyboardMarkup()
                # 🔴 دکمه بازگشت با رنگ danger (قرمز)
                markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="pur_back", style="danger"))
                _bot.edit_message_text(
                    f"🛍 <b>خرید الماس</b>\n\n"
                    f"💎 نرخ: هر ۱۰۰ الماس = <b>{100 * DIAMOND_RATE:,} تومان</b>\n"
                    f"📌 حداقل خرید: <b>{DIAMOND_MIN_BUY} الماس</b>\n\n"
                    f"چه تعداد الماس می‌خوای؟ (عدد بنویس)\n"
                    f"مثال: <code>200</code>",
                    chat_id=call.message.chat.id, message_id=call.message.message_id,
                    reply_markup=markup
                )
                _bot.answer_callback_query(call.id)

            # ── تأیید/رد پرداخت توسط ادمین ─────────────────────────────────
            elif data.startswith("pur_approve_") or data.startswith("pur_reject_"):
                if tg_id != OWNER_TG_ID:
                    return _bot.answer_callback_query(call.id, "❌ فقط مالک دسترسی دارد", show_alert=True)
                action = "approve" if data.startswith("pur_approve_") else "reject"
                payment_id = int(data.split("_")[2])
                payment = db.get_payment(payment_id)
                if not payment:
                    return _bot.answer_callback_query(call.id, "❌ پرداخت یافت نشد", show_alert=True)
                if payment["status"] != "pending":
                    return _bot.answer_callback_query(call.id, "⚠️ این پرداخت قبلاً پردازش شده", show_alert=True)

                if action == "approve":
                    db.update_payment(payment_id, status="approved")
                    user_account = db.get_account(payment["owner_id"])

                    if payment["type"] == "subscription":
                        plan_key = payment["plan"]
                        plan = PLANS.get(plan_key, {})
                        expires = db.set_subscription(payment["owner_id"], plan_key, plan.get("days", 30))
                        exp_str = expires.strftime("%Y-%m-%d") if expires else "نامشخص"
                        try:
                            _bot.send_message(
                                payment["tg_id"],
                                f"✅ <b>پرداخت تأیید شد!</b>\n\n"
                                f"🎉 اشتراک {plan.get('fa','')  } شما فعال شد\n"
                                f"📅 انقضا: <b>{exp_str}</b>"
                            )
                        except Exception: pass

                    elif payment["type"] == "diamond":
                        amount = payment["diamond_amount"]
                        db.add_tokens(payment["owner_id"], amount)
                        try:
                            _bot.send_message(
                                payment["tg_id"],
                                f"✅ <b>پرداخت تأیید شد!</b>\n\n"
                                f"{EM.EMOJI_DIAMONDS} <b>{amount} الماس</b> به حسابتان اضافه شد!"
                            )
                        except Exception: pass

                    # 🟢 دکمه تأیید با رنگ success (سبز)
                    _bot.edit_message_reply_markup(
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        reply_markup=types.InlineKeyboardMarkup().add(
                            types.InlineKeyboardButton("✅ تأیید شد", callback_data="noop", style="success", icon_custom_emoji_id="5830326445422940546")
                        )
                    )
                    _bot.answer_callback_query(call.id, "✅ پرداخت تأیید شد!", show_alert=True)

                else:  # reject
                    db.update_payment(payment_id, status="rejected")
                    try:
                        _bot.send_message(
                            payment["tg_id"],
                            "❌ <b>پرداخت شما رد شد.</b>"
                        )
                    except Exception: pass
                    # 🔴 دکمه رد با رنگ danger (قرمز)
                    _bot.edit_message_reply_markup(
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        reply_markup=types.InlineKeyboardMarkup().add(
                            types.InlineKeyboardButton("❌ رد شد", callback_data="noop", style="danger")
                        )
                    )
                    _bot.answer_callback_query(call.id, "❌ پرداخت رد شد", show_alert=True)

            elif data == "noop":
                _bot.answer_callback_query(call.id)

        except Exception as e:
            print(f"❌ خطا در callback_purchase: {e}")
            _bot.answer_callback_query(call.id, f"❌ خطا: {str(e)[:80]}", show_alert=True)

    # ── دریافت پیام‌های مرتبط با خرید (مبلغ الماس + رسید) ───────────────────
    @_bot.message_handler(
        func=lambda m: m.from_user.id in _purchase_states and m.chat.type == "private",
        content_types=["text", "photo", "document"]
    )
    def handle_purchase_state(message):
        try:
            tg_id = message.from_user.id
            state = _purchase_states.get(tg_id, {})
            step = state.get("step")
            account = _get_account_cached(tg_id)
            if not account:
                return

            # ── کاربر تعداد الماس رو نوشت ───────────────────────────────
            if step == "waiting_diamond_amount":
                try:
                    amount = int(message.text.strip())
                except (ValueError, AttributeError):
                    return _bot.reply_to(message, "❌ لطفاً یک عدد معتبر وارد کنید.")
                if amount < DIAMOND_MIN_BUY:
                    return _bot.reply_to(message, f"❌ حداقل {DIAMOND_MIN_BUY} الماس باید خرید.")
                toman = amount * DIAMOND_RATE
                card = _get_card_number()
                payment_id = db.create_payment(
                    account["id"], tg_id, "diamond",
                    diamond_amount=amount, toman_amount=toman
                )
                _purchase_states[tg_id] = {"step": "waiting_receipt_diamond", "payment_id": payment_id}
                markup = types.InlineKeyboardMarkup()
                # 🔴 دکمه لغو با رنگ danger (قرمز)
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="pur_back", style="danger", icon_custom_emoji_id="5832353674281620438"))
                _bot.reply_to(message,
                    f"🛍 <b>خرید {amount} الماس</b>\n\n"
                    f"💰 مبلغ: <b>{toman:,} تومان</b>\n"
                    f"💳 شماره کارت: <code>{card}</code>\n"
                    f"👤 به نام: <b>غفاری</b>\n\n"
                    f"بعد از واریز، تصویر رسید را ارسال کنید 👇",
                    reply_markup=markup
                )

            # ── کاربر رسید فرستاد ────────────────────────────────────────
            elif step in ("waiting_receipt_sub", "waiting_receipt_diamond"):
                payment_id = state.get("payment_id")
                if not payment_id:
                    return

                # دریافت file_id
                file_id = None
                if message.photo:
                    file_id = message.photo[-1].file_id
                elif message.document:
                    file_id = message.document.file_id
                else:
                    return _bot.reply_to(message, "❌ لطفاً تصویر رسید را ارسال کنید.")

                db.update_payment(payment_id, receipt_file_id=file_id)
                payment = db.get_payment(payment_id)

                # ارسال به ادمین
                username = message.from_user.username
                user_display = f"@{username}" if username else str(tg_id)

                if step == "waiting_receipt_sub":
                    plan = PLANS.get(payment.get("plan", ""), {})
                    desc = f"اشتراک {plan.get('fa', '')} — {payment.get('toman_amount', 0):,} تومان"
                else:
                    desc = f"خرید {payment.get('diamond_amount', 0)} الماس — {payment.get('toman_amount', 0):,} تومان"

                admin_text = (
                    f"🧾 <b>رسید جدید</b>\n\n"
                    f"👤 کاربر: {user_display}\n"
                    f"🆔 تلگرام: <code>{tg_id}</code>\n"
                    f"📦 نوع: {desc}\n"
                    f"🔢 شناسه پرداخت: <code>{payment_id}</code>"
                )
                admin_markup = types.InlineKeyboardMarkup(row_width=2)
                # 🟢 دکمه تأیید با رنگ success (سبز)
                admin_markup.add(
                    types.InlineKeyboardButton("✅ تأیید", callback_data=f"pur_approve_{payment_id}", style="success", icon_custom_emoji_id="5830326445422940546"),
                )
                # 🔴 دکمه رد با رنگ danger (قرمز)
                admin_markup.add(
                    types.InlineKeyboardButton("❌ رد", callback_data=f"pur_reject_{payment_id}", style="danger", icon_custom_emoji_id="5832353674281620438")
                )
                try:
                    admin_msg = _bot.send_photo(
                        OWNER_TG_ID, file_id,
                        caption=admin_text,
                        reply_markup=admin_markup
                    )
                    db.update_payment(payment_id, admin_msg_id=admin_msg.message_id)
                except Exception as e:
                    print(f"❌ ارسال رسید به ادمین: {e}")

                _purchase_states.pop(tg_id, None)
                _bot.reply_to(message,
                    "✅ <b>رسید دریافت شد!</b>\n\n"
                    "⏳ پس از تأیید توسط ادمین، اشتراک/الماس شما فعال می‌شود.\n"
                    "معمولاً کمتر از ۳۰ دقیقه طول می‌کشد."
                )

        except Exception as e:
            print(f"❌ خطا در handle_purchase_state: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # 📢 پنل مدیریت مالک
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.message_handler(func=lambda m: m.text == "مدیریت", chat_types=['private'])
    def cmd_admin_panel(message):
        if message.from_user.id != OWNER_TG_ID:
            return
        _bot.reply_to(message, 
            "📢 <b>پنل مدیریت مالک</b>\n\nیکی از گزینه‌های زیر را انتخاب کنید:",
            reply_markup=_admin_panel_keyboard())

    # ══════════════════════════════════════════════════════════════════════════
    # 🎯 Callback handler پنل مدیریت
    # ══════════════════════════════════════════════════════════════════════════
    def _get_sub_admin_perm_for_data(data):
        """بازگشت کلید دسترسی متناظر با callback data برای ادمین فرعی"""
        _perm_prefixes = [
            ("admin_channels", "channels"), ("rmch_", "channels"), ("addch_prompt", "channels"),
            ("admin_users", "users"),
            ("admin_manage_users", "manage_users"),
            ("admin_wc_participants", "wc_participants"),
            ("admin_wc", "wc"), ("wcwin_", "wc"), ("wc_", "wc"),
            ("admin_today_games", "today_games"),
            ("admin_transfer", "transfer"),
            ("admin_give", "give"),
            ("admin_set_card", "set_card"),
            ("admin_payments", "payments"),
            ("admin_broadcast", "broadcast"),
            ("admin_channel_msg", "channel_msg"),
            ("admin_missions", "missions"), ("add_mission_prompt", "missions"), ("del_mission_", "missions"),
            ("admin_gift", "gift"),
            ("admin_guide_manage", "guide_manage"), ("admin_guide_add", "guide_manage"),
            ("guide_type_media", "guide_manage"), ("guide_type_text", "guide_manage"),
            ("admin_welcome_settings", "welcome_settings"),
        ]
        for prefix, perm in _perm_prefixes:
            if data.startswith(prefix) or data == prefix:
                return perm
        return None

    _LOTTERY_ORDINALS = ["اول", "دوم", "سوم", "چهارم", "پنجم", "ششم", "هفتم", "هشتم", "نهم", "دهم"]

    def _lottery_ordinal(i0: int) -> str:
        """i0 صفر-پایه است (۰ یعنی نفر اول)."""
        return _LOTTERY_ORDINALS[i0] if i0 < len(_LOTTERY_ORDINALS) else f"{i0+1}م"

    def _lottery_prize_type_markup():
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("💎 الماس", callback_data="lottery_prize_diamond", style="primary"),
            types.InlineKeyboardButton("⭐️ اشتراک", callback_data="lottery_prize_sub", style="success"),
        )
        markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="admin_lottery", style="danger"))
        return markup

    def _lottery_plan_markup():
        markup = types.InlineKeyboardMarkup(row_width=3)
        markup.add(
            types.InlineKeyboardButton("۱ روزه", callback_data="lottery_plan_1", style="primary"),
            types.InlineKeyboardButton("۲ روزه", callback_data="lottery_plan_2", style="primary"),
            types.InlineKeyboardButton("۷ روزه", callback_data="lottery_plan_7", style="primary"),
        )
        markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="admin_lottery", style="danger"))
        return markup

    def _lottery_confirm_markup():
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("✅ تأیید و ثبت", callback_data="lottery_confirm", style="success", icon_custom_emoji_id="5830326445422940546"),
            types.InlineKeyboardButton("❌ لغو", callback_data="admin_lottery", style="danger", icon_custom_emoji_id="5832353674281620438")
        )
        return markup

    def _lottery_confirm_text(d):
        prize_text = ""
        for i, p in enumerate(d["prizes"]):
            prize_text += f"\n🥇 نفر {_lottery_ordinal(i)}: <b>{p}</b>"
        return (
            f"📋 <b>تأیید قرعه‌کشی</b>\n\n"
            f"⏰ زمان: <b>{d['start_time']}</b> تا <b>{d['end_time']}</b>\n"
            f"🏆 تعداد برنده: <b>{d['winners_count']} نفر</b>\n"
            f"🎁 جوایز:{prize_text}\n\n"
            "آیا تأیید می‌کنید؟"
        )

    def _lottery_advance_prize(state_data, prize_detail: dict):
        """جایزه‌ی نفر فعلی رو ثبت می‌کنه و یا مرحله‌ی جایزه‌ی نفر بعدی رو
        برمی‌گردونه، یا (اگه آخرین نفر بود) صفحه‌ی تأیید نهایی رو.
        خروجی: (متن, مارکاپ) که caller خودش تصمیم می‌گیره edit کنه یا reply."""
        d = state_data["data"]
        d["prizes"].append(prize_detail["label"])
        d["prize_details"].append(prize_detail)
        current = d["current_prize"]
        total = d["winners_count"]

        if current < total:
            d["current_prize"] = current + 1
            state_data["state"] = "lottery_prize_choose"
            next_ord = _lottery_ordinal(current)
            text = (
                f"✅ جایزه نفر {_lottery_ordinal(current - 1)}: <b>{prize_detail['label']}</b>\n\n"
                f"📝 نوع جایزه‌ی نفر <b>{next_ord}</b> را انتخاب کنید:"
            )
            return text, _lottery_prize_type_markup()
        else:
            state_data["state"] = "lottery_awaiting_confirm"
            return _lottery_confirm_text(d), _lottery_confirm_markup()

    @_bot.callback_query_handler(func=lambda call: call.data.startswith("admin_") or call.data.startswith("rmch_") or call.data.startswith("wcwin_") or call.data.startswith("wc_") or call.data == "addch_prompt" or call.data == "add_mission_prompt" or call.data.startswith("del_mission_") or call.data in ("guide_type_media", "guide_type_text") or call.data.startswith("admin_perm_") or call.data.startswith("lottery_"))
    def callback_admin(call):
        uid = call.from_user.id
        data = call.data

        if uid != OWNER_TG_ID:
            # ادمین فرعی: فقط admin_panel و دسترسی‌های مجاز
            if not db.is_sub_admin(uid):
                return _bot.answer_callback_query(call.id, "❌ دسترسی ندارید", show_alert=True)
            # مدیریت دسترسی‌ها فقط برای مالک
            if data.startswith("admin_perm_") or data == "admin_manage_admins" or data == "admin_add_admin" or data.startswith("admin_del_admin_"):
                return _bot.answer_callback_query(call.id, "❌ این بخش فقط برای مالک است", show_alert=True)
            if data not in ("admin_panel", "admin_back"):
                perm = _get_sub_admin_perm_for_data(data)
                if perm is None or not db.sub_admin_has_permission(uid, perm):
                    return _bot.answer_callback_query(call.id, "❌ شما به این بخش دسترسی ندارید", show_alert=True)
        
        # دکمه‌های غیرفعال (نمایشی)
        if call.data == "admin_users_noop":
            return _bot.answer_callback_query(call.id)
        
        try:
            if data == "admin_panel" or data == "admin_back":
                _bot.edit_message_text(
                    "📢 <b>پنل مدیریت مالک</b>\n\nیکی از گزینه‌های زیر را انتخاب کنید:",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=_admin_panel_keyboard()
                )
                _bot.answer_callback_query(call.id)
                return
            
            elif data == "admin_channels":
                channels = db.get_forced_channels()
                markup = types.InlineKeyboardMarkup(row_width=1)
                if channels:
                    text = "📢 <b>چنل‌های اجباری فعلی:</b>\n\n"
                    for ch in channels:
                        text += f"🔸 <code>{ch}</code>\n"
                        ch_clean = ch.lstrip("@")
                        # 🔴 دکمه حذف با رنگ danger (قرمز)
                        markup.add(types.InlineKeyboardButton(f"❌ حذف {ch}", callback_data=f"rmch_{ch_clean}", style="danger"))
                else:
                    text = "📋 لیست چنل‌ها خالی است.\n\n"
                text += "\nبرای افزودن چنل جدید از دکمه زیر استفاده کنید:"
                # 🟢 دکمه افزودن با رنگ success (سبز)
                markup.add(types.InlineKeyboardButton("➕ افزودن چنل جدید", callback_data="addch_prompt", style="success"))
                # 🔴 دکمه بازگشت با رنگ danger (قرمز)
                markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin_panel", style="danger"))
                _bot.edit_message_text(
                    text,
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=markup
                )
                _bot.answer_callback_query(call.id)
                return
            
            elif data.startswith("rmch_"):
                ch = data[5:]
                if not ch.startswith("@"):
                    ch = "@" + ch
                if db.remove_forced_channel(ch):
                    cache.invalidate("membership_")
                    _bot.answer_callback_query(call.id, f"✅ چنل {ch} حذف شد")
                    call.data = "admin_channels"
                    callback_admin(call)
                else:
                    _bot.answer_callback_query(call.id, "❌ خطا در حذف")
                return
            
            elif data == "addch_prompt":
                _owner_states[call.from_user.id] = {"state": "waiting_channel"}
                markup = types.InlineKeyboardMarkup()
                # 🔴 دکمه لغو با رنگ danger (قرمز)
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="admin_panel", style="danger", icon_custom_emoji_id="5832353674281620438"))
                _bot.edit_message_text(
                    "📝 آیدی چنل را ارسال کنید (با @ شروع شود):\n\nمثال: <code>@mychannel</code>",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=markup
                )
                _bot.answer_callback_query(call.id)
                return
            
            elif data == "admin_users" or data.startswith("admin_users_p"):
                accounts = db.get_all_accounts()
                if not accounts:
                    text = "هیچ کاربری ثبت نشده."
                    markup = types.InlineKeyboardMarkup()
                    markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin_panel", style="danger"))
                    _bot.edit_message_text(text, chat_id=call.message.chat.id,
                        message_id=call.message.message_id, reply_markup=markup)
                    _bot.answer_callback_query(call.id)
                    return

                # Pagination — هر صفحه ۲۰ کاربر
                PAGE_SIZE = 20
                total = len(accounts)
                total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
                try:
                    page = int(data.split("_p")[-1]) if data.startswith("admin_users_p") else 1
                except Exception:
                    page = 1
                page = max(1, min(page, total_pages))
                start_idx = (page - 1) * PAGE_SIZE
                page_accounts = accounts[start_idx: start_idx + PAGE_SIZE]

                lines = [f"👥 <b>کاربران ({total} نفر) — صفحه {page}/{total_pages}</b>\n"]
                for i, acc in enumerate(page_accounts, start_idx + 1):
                    bal = db.get_token_balance(acc["id"])
                    remaining = _format_plan_remaining(acc["id"])
                    # آیدی تلگرام مستقیماً از query برگشته (بدون query جداگانه)
                    tg_id_val = acc.get("telegram_user_id")
                    tg_username_val = acc.get("tg_username") or ""
                    if tg_id_val:
                        pv_link = f"tg://user?id={tg_id_val}"
                        username_part = f"@{tg_username_val} | " if tg_username_val else ""
                        tg_id_line = f"📱 <a href='{pv_link}'>{username_part}پیوی کاربر</a> (<code>{tg_id_val}</code>)"
                    else:
                        tg_id_line = "📱 تلگرام: ─"
                    lines.append(
                        f"┌─ <b>#{i} {acc['username']}</b>\n"
                        f"├ 🆔 پنل: <code>{acc['id']}</code>\n"
                        f"├ {tg_id_line}\n"
                        f"├ 💎 موجودی: <b>{bal} الماس</b>\n"
                        f"└ ⏳ پلن: {remaining}"
                    )
                text = "\n\n".join(lines)

                markup = types.InlineKeyboardMarkup(row_width=3)
                nav_buttons = []
                if page > 1:
                    nav_buttons.append(types.InlineKeyboardButton(
                        "◀️ قبلی", callback_data=f"admin_users_p{page - 1}"))
                nav_buttons.append(types.InlineKeyboardButton(
                    f"📄 {page}/{total_pages}", callback_data="admin_users_noop"))
                if page < total_pages:
                    nav_buttons.append(types.InlineKeyboardButton(
                        "بعدی ▶️", callback_data=f"admin_users_p{page + 1}"))
                if nav_buttons:
                    markup.add(*nav_buttons)
                markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin_panel", style="danger"))
                _bot.edit_message_text(
                    text,
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=markup
                )
                _bot.answer_callback_query(call.id)
                return
            
            elif data == "admin_wc":
                markup = types.InlineKeyboardMarkup(row_width=1)
                # 🟢 دکمه ایجاد چالش با رنگ success (سبز)
                markup.add(types.InlineKeyboardButton("➕ ایجاد چالش جدید", callback_data="wc_new", style="success"))
                # 🔵 دکمه چالش‌های فعال با رنگ primary (آبی)
                markup.add(types.InlineKeyboardButton("📋 چالش‌های فعال", callback_data="wc_list", style="primary"))
                # 🔴 دکمه بازگشت با رنگ danger (قرمز)
                markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin_panel", style="danger"))
                _bot.edit_message_text(
                    "🏆 <b>مدیریت چالش‌های جام جهانی</b>\n\nیک گزینه را انتخاب کنید:",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=markup
                )
                _bot.answer_callback_query(call.id)
                return
            
            elif data == "wc_new":
                _owner_states[call.from_user.id] = {"state": "wc_team1", "data": {}}
                markup = types.InlineKeyboardMarkup()
                # 🔴 دکمه لغو با رنگ danger (قرمز)
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="admin_wc", style="danger", icon_custom_emoji_id="5832353674281620438"))
                _bot.edit_message_text(
                    "🏆 <b>ایجاد چالش جدید</b>\n\n"
                    "📝 مرحله ۱ از ۴:\nنام <b>تیم اول</b> را ارسال کنید:\n\nمثال: <code>ایران</code>",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=markup
                )
                _bot.answer_callback_query(call.id)
                return
            
            elif data == "wc_list":
                challenges = db.get_active_challenges()
                if not challenges:
                    text = "📋 هیچ چالش فعالی وجود ندارد."
                    markup = types.InlineKeyboardMarkup()
                    # 🔴 دکمه بازگشت با رنگ danger (قرمز)
                    markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin_wc", style="danger"))
                else:
                    text = "🏆 <b>چالش‌های فعال:</b>\n\n"
                    markup = types.InlineKeyboardMarkup(row_width=2)
                    for c in challenges:
                        text += f"<b>ID {c['id']}:</b> {c['team1']} vs {c['team2']}\n"
                        text += f"⏰ {c['match_time']} | 💎 {c['bet_amount']}\n\n"
                        # 🟢 دکمه‌های تعیین برنده با رنگ success (سبز)
                        markup.add(
                            types.InlineKeyboardButton(f"✅ {c['team1']}", callback_data=f"wcwin_{c['id']}_{c['team1']}", style="success"),
                            types.InlineKeyboardButton(f"✅ {c['team2']}", callback_data=f"wcwin_{c['id']}_{c['team2']}", style="success")
                        )
                    # 🔴 دکمه بازگشت با رنگ danger (قرمز)
                    markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin_wc", style="danger"))
                _bot.edit_message_text(
                    text,
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=markup
                )
                _bot.answer_callback_query(call.id)
                return
            
            elif data.startswith("wcwin_"):
                parts = data.split("_", 2)
                challenge_id = int(parts[1])
                winner_team = parts[2]
                db.set_challenge_winner(challenge_id, winner_team)
                success, results = db.settle_challenge_bets(challenge_id)
                if success:
                    won_count = sum(1 for r in results if r["result"] == "won")
                    lost_count = sum(1 for r in results if r["result"] == "lost")
                    _bot.answer_callback_query(call.id, f"✅ برنده: {winner_team}\n🏆 {won_count} برنده | ❌ {lost_count} بازنده", show_alert=True)
                    for r in results:
                        if r["result"] == "won":
                            try:
                                _bot.send_message(r["user_tg_id"], f"🎉 تبریک! شرط شما درست بود.\n💎 <b>{r['amount']} الماس</b> دریافت کردید.")
                            except: 
                                pass
                else:
                    _bot.answer_callback_query(call.id, f"❌ خطا: {results}", show_alert=True)
                return
            
            elif data == "admin_today_games":
                _bot.answer_callback_query(call.id, "⏳ در حال دریافت بازی‌های امروز...")
                try:
                    today_matches = _wc_get_today_matches()
                except Exception as e:
                    today_matches = None
                    print(f"❌ خطا در دریافت بازی‌های امروز: {e}")

                if today_matches is None:
                    text = "❌ خطا در ارتباط با Football API.\nلاگ سرور را بررسی کنید."
                    markup = types.InlineKeyboardMarkup()
                    # 🔴 دکمه بازگشت با رنگ danger (قرمز)
                    markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin_panel", style="danger"))
                    try:
                        _bot.edit_message_text(text, chat_id=call.message.chat.id,
                            message_id=call.message.message_id, reply_markup=markup)
                    except Exception:
                        _bot.send_message(call.message.chat.id, text, reply_markup=markup)
                    return

                if not getattr(config, "FOOTBALL_API_KEY", ""):
                    text = "⚠️ FOOTBALL_API_KEY تنظیم نشده است."
                    markup = types.InlineKeyboardMarkup()
                    # 🔴 دکمه بازگشت با رنگ danger (قرمز)
                    markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin_panel", style="danger"))
                    _bot.edit_message_text(text, chat_id=call.message.chat.id,
                        message_id=call.message.message_id, reply_markup=markup)
                    return

                if not today_matches:
                    text = "📭 امروز بازی‌ای ثبت نشده."
                    markup = types.InlineKeyboardMarkup()
                    # 🔴 دکمه بازگشت با رنگ danger (قرمز)
                    markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin_panel", style="danger"))
                    _bot.edit_message_text(text, chat_id=call.message.chat.id,
                        message_id=call.message.message_id, reply_markup=markup)
                    return

                # ساخت لیست بازی‌ها با دکمه ارسال برای هر کدام
                status_fa = {
                    "SCHEDULED": "⏳", "TIMED": "⏳",
                    "LIVE": "🔴", "IN_PLAY": "🔴", "PAUSED": "⏸️",
                    "FINISHED": "✅", "POSTPONED": "📌",
                    "SUSPENDED": "⛔️", "CANCELLED": "❌",
                }
                lines = ["📅 <b>بازی‌های امروز — جام جهانی</b>\n"]
                markup = types.InlineKeyboardMarkup(row_width=1)

                for m in today_matches:
                    match_id = str(m.get("id", ""))
                    home = (m.get("homeTeam", {}).get("shortName") or
                            m.get("homeTeam", {}).get("name") or "؟")
                    away = (m.get("awayTeam", {}).get("shortName") or
                            m.get("awayTeam", {}).get("name") or "؟")
                    st = status_fa.get(m.get("status", ""), "❓")
                    utc_date = m.get("utcDate", "")
                    time_str = utc_date
                    try:
                        dt = datetime.datetime.strptime(utc_date, "%Y-%m-%dT%H:%M:%SZ")
                        time_str = _wc_utc_to_iran(dt).strftime("%H:%M")
                    except Exception:
                        pass

                    # نشون می‌ده چالش قبلاً ساخته شده یا نه
                    already = db.wc_challenge_exists(match_id)
                    sent_icon = "📤" if already else "📨"

                    lines.append(f"{st} <b>{home}</b> vs <b>{away}</b> — ⏰{time_str}")
                    # 🟢 دکمه ارسال چالش با رنگ success (سبز)
                    markup.add(
                        types.InlineKeyboardButton(
                            f"{sent_icon} ارسال چالش: {home} vs {away}",
                            callback_data=f"wc_sendnow_{match_id}",
                            style="success"
                        )
                    )

                # 🔴 دکمه بازگشت با رنگ danger (قرمز)
                markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin_panel", style="danger"))
                text = "\n".join(lines)

                try:
                    _bot.edit_message_text(text, chat_id=call.message.chat.id,
                        message_id=call.message.message_id, reply_markup=markup)
                except Exception:
                    _bot.send_message(call.message.chat.id, text, reply_markup=markup)
                return

            elif data.startswith("wc_sendnow_"):
                # ادمین دستی روی دکمه ارسال چالش زد
                match_id = data[len("wc_sendnow_"):]
                _bot.answer_callback_query(call.id, "⏳ در حال ارسال چالش...")
                try:
                    today_matches = _wc_get_today_matches()
                    target = next((m for m in today_matches if str(m.get("id")) == match_id), None)
                    if not target:
                        return _bot.answer_callback_query(call.id, "❌ بازی یافت نشد", show_alert=True)

                    home = (target.get("homeTeam", {}).get("shortName") or
                            target.get("homeTeam", {}).get("name") or "؟")
                    away = (target.get("awayTeam", {}).get("shortName") or
                            target.get("awayTeam", {}).get("name") or "؟")

                    if not home.strip() or not away.strip():
                        return _bot.answer_callback_query(call.id, "❌ نام تیم‌ها هنوز مشخص نیست", show_alert=True)

                    utc_date = target.get("utcDate", "")
                    try:
                        dt = datetime.datetime.strptime(utc_date, "%Y-%m-%dT%H:%M:%SZ")
                        match_time_str = _wc_utc_to_iran(dt).strftime("%Y/%m/%d — %H:%M") + " (تهران)"
                    except Exception:
                        dt = utc_date
                        match_time_str = utc_date

                    # اگه قبلاً ساخته شده فقط دوباره بفرسته
                    if db.wc_challenge_exists(match_id):
                        # چالش موجوده — فقط مجدد به کانال بفرست
                        from database_supabase import execute_query
                        row = execute_query(
                            "SELECT * FROM worldcup_challenges WHERE match_id=%s",
                            (match_id,), fetch_one=True
                        )
                        if row:
                            _wc_send_challenge_to_channel(row["id"], home, away, match_time_str)
                            _bot.answer_callback_query(call.id, "✅ چالش مجدداً ارسال شد!", show_alert=True)
                            return

                    challenge_id = db.create_wc_challenge(match_id, home, away, dt)
                    if challenge_id:
                        _wc_send_challenge_to_channel(challenge_id, home, away, match_time_str)
                        _bot.answer_callback_query(call.id, f"✅ چالش {home} vs {away} ارسال شد!", show_alert=True)
                    else:
                        _bot.answer_callback_query(call.id, "❌ خطا در ساخت چالش", show_alert=True)
                except Exception as e:
                    print(f"❌ wc_sendnow: {e}")
                    _bot.answer_callback_query(call.id, f"❌ خطا: {str(e)[:80]}", show_alert=True)
                return
            
            elif data == "admin_transfer":
                _owner_states[call.from_user.id] = {"state": "transfer_user", "data": {}}
                markup = types.InlineKeyboardMarkup()
                # 🔴 دکمه لغو با رنگ danger (قرمز)
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="admin_panel", style="danger", icon_custom_emoji_id="5832353674281620438"))
                _bot.edit_message_text(
                    "💎 <b>انتقال الماس (از طرف سیستم)</b>\n\n"
                    "📝 یوزرنیم کاربر مقصد را ارسال کنید:\n\nمثال: <code>ali</code>",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=markup
                )
                _bot.answer_callback_query(call.id)
                return
            
            elif data == "admin_give":
                _owner_states[call.from_user.id] = {"state": "give_user", "data": {}}
                markup = types.InlineKeyboardMarkup()
                # 🔴 دکمه لغو با رنگ danger (قرمز)
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="admin_panel", style="danger", icon_custom_emoji_id="5832353674281620438"))
                _bot.edit_message_text(
                    "💰 <b>دادن الماس به کاربر</b>\n\n"
                    "📝 یوزرنیم کاربر را ارسال کنید:\n\nمثال: <code>ali</code>",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=markup
                )
                _bot.answer_callback_query(call.id)
                return

            elif data == "admin_set_card":
                cur_card = db.get_global_setting("card_number", "تنظیم نشده")
                _owner_states[call.from_user.id] = {"state": "set_card"}
                markup = types.InlineKeyboardMarkup()
                # 🔴 دکمه لغو با رنگ danger (قرمز)
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="admin_panel", style="danger", icon_custom_emoji_id="5832353674281620438"))
                _bot.edit_message_text(
                    f"💳 <b>تنظیم شماره کارت</b>\n\n"
                    f"کارت فعلی: <code>{cur_card}</code>\n\n"
                    f"شماره کارت جدید را ارسال کنید:",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=markup
                )
                _bot.answer_callback_query(call.id)
                return

            elif data == "admin_payments":
                payments = db.get_pending_payments()
                if not payments:
                    _bot.answer_callback_query(call.id, "✅ هیچ پرداخت معلقی وجود ندارد", show_alert=True)
                    return
                lines = [f"🧾 <b>پرداخت‌های معلق ({len(payments)} مورد)</b>\n"]
                for p in payments[:10]:
                    ptype = "اشتراک" if p["type"] == "subscription" else "الماس"
                    lines.append(f"• ID {p['id']} — {ptype} — {p.get('toman_amount',0):,} تومان")
                _bot.answer_callback_query(call.id)
                _bot.send_message(call.message.chat.id, "\n".join(lines))
                return

            elif data == "admin_broadcast":
                _owner_states[call.from_user.id] = {"state": "broadcast_msg"}
                markup = types.InlineKeyboardMarkup()
                # 🔴 دکمه لغو با رنگ danger (قرمز)
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="admin_panel", style="danger", icon_custom_emoji_id="5832353674281620438"))
                _bot.edit_message_text(
                    "📣 <b>ارسال پیام عمومی</b>\n\n"
                    "پیام خود را ارسال کنید (متن، عکس یا لینک):\n"
                    "به تمام کاربران ثبت‌شده ارسال می‌شود.",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=markup
                )
                _bot.answer_callback_query(call.id)
                return

            elif data == "admin_channel_msg":
                _owner_states[call.from_user.id] = {"state": "channel_msg_text"}
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="admin_panel", style="danger", icon_custom_emoji_id="5832353674281620438"))
                _bot.edit_message_text(
                    "📢 <b>ارسال پیام به کانال</b>\n\n"
                    "پیام خود را ارسال کنید. فرمت‌هایی مثل <b>بولد</b>، <i>ایتالیک</i>، "
                    "نقل‌قول و... که با کیبورد تلگرام روی متن اعمال کنید حفظ می‌شود.\n\n"
                    "برای قرار دادن ایموجی پرمیوم جلوی متن، ایدی عددی آن را داخل کروشه "
                    "درست قبل از همان قسمت از متن بنویسید:\n"
                    "<code>با سلام و خسته نباشید[5436203513149404753]</code>\n\n"
                    "پیام نهایی به کانال ارسال خواهد شد.",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=markup
                )
                _bot.answer_callback_query(call.id)
                return

            elif data == "admin_manage_users":
                _owner_states[call.from_user.id] = {"state": "manage_user_lookup"}
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="admin_panel", style="danger", icon_custom_emoji_id="5832353674281620438"))
                _bot.edit_message_text(
                    "👮 <b>مدیریت کاربران</b>\n\n"
                    "یوزرنیم پنل یا آیدی عددی تلگرام کاربر مورد نظر را ارسال کنید:",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=markup
                )
                _bot.answer_callback_query(call.id)
                return

            elif data == "admin_wc_participants":
                participants = db.get_wc_participants()
                if not participants:
                    text = "📭 هیچ شرکت‌کننده‌ای در جام جهانی ثبت نشده."
                else:
                    lines = [f"⚽️ <b>شرکت‌کنندگان جام جهانی ({len(participants)} نفر):</b>\n"]
                    for i, p in enumerate(participants[:50], 1):
                        uname = f"@{p['username']}"
                        lines.append(f"{i}. <b>{uname}</b> — 🎯{p['bet_count']} شرط | 💎{p['total_bet']} الماس")
                    text = "\n".join(lines)
                markup = types.InlineKeyboardMarkup()
                # 🔴 دکمه بازگشت با رنگ danger (قرمز)
                markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin_panel", style="danger"))
                _bot.edit_message_text(text, chat_id=call.message.chat.id,
                    message_id=call.message.message_id, reply_markup=markup)
                _bot.answer_callback_query(call.id)
                return

            elif data == "admin_gift":
                # ── انتخاب نوع هدیه ──────────────────────────────────────────────
                markup = types.InlineKeyboardMarkup(row_width=2)
                markup.add(
                    types.InlineKeyboardButton(" الماس", callback_data="admin_gift_diamond", style="primary", icon_custom_emoji_id=str(EM.ID_DIAMONDS)),
                    types.InlineKeyboardButton(" پلن", callback_data="admin_gift_panel", style="success", icon_custom_emoji_id=str(EM.ID_Pending))
                )
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="admin_panel", style="danger", icon_custom_emoji_id="5832353674281620438"))
                _bot.edit_message_text(
                    "🎁 <b>هدیه به کاربر</b>\n\n"
                    "نوع هدیه را وارد کنید:",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=markup
                )
                _bot.answer_callback_query(call.id)
                return

            elif data == "admin_gift_diamond":
                # ── هدیه الماس: تعداد الماس ──────────────────────────────────────
                _owner_states[call.from_user.id] = {"state": "gift_diamond_amount", "data": {"gift_type": "diamond"}}
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="admin_panel", style="danger", icon_custom_emoji_id="5832353674281620438"))
                _bot.edit_message_text(
                    "💎 <b>هدیه الماس</b>\n\n"
                    "تعداد الماس هدیه را وارد کنید:\n\nمثال: <code>100</code>",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=markup
                )
                _bot.answer_callback_query(call.id)
                return

            elif data == "admin_gift_panel":
                # ── هدیه پنل: انتخاب نوع پلن ────────────────────────────────────
                markup = types.InlineKeyboardMarkup(row_width=1)
                markup.add(
                    types.InlineKeyboardButton("📅 پنل یک ماهه (30 روز)", callback_data="admin_gift_plan_30", style="primary"),
                    types.InlineKeyboardButton("📅 پنل یک هفته‌ای (7 روز)", callback_data="admin_gift_plan_7", style="primary"),
                    types.InlineKeyboardButton("📅 پنل یک روزه (1 روز)", callback_data="admin_gift_plan_1", style="primary")
                )
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="admin_panel", style="danger", icon_custom_emoji_id="5832353674281620438"))
                _bot.edit_message_text(
                    "📋 <b>هدیه پنل</b>\n\n"
                    "نوع پنل هدیه را انتخاب کنید:",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=markup
                )
                _bot.answer_callback_query(call.id)
                return

            elif data.startswith("admin_gift_plan_"):
                # ── هدیه پنل: ایدی عددی کاربر ───────────────────────────────────
                days = int(data.split("_")[-1])
                plan_names = {30: "یک ماهه", 7: "یک هفته‌ای", 1: "یک روزه"}
                plan_label = plan_names.get(days, f"{days} روزه")
                _owner_states[call.from_user.id] = {
                    "state": "gift_tg_id",
                    "data": {"gift_type": "panel", "days": days, "plan_label": plan_label}
                }
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="admin_panel", style="danger", icon_custom_emoji_id="5832353674281620438"))
                _bot.edit_message_text(
                    f"📋 <b>پنل {plan_label}</b>\n\n"
                    "ایدی عددی تلگرام کاربر مورد نظر را وارد کنید:",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=markup
                )
                _bot.answer_callback_query(call.id)
                return

            elif data.startswith("admin_gift_confirm_"):
                # ── تایید هدیه ───────────────────────────────────────────────────
                gift_key = data[len("admin_gift_confirm_"):]
                gift_info = _owner_states.get(call.from_user.id, {}).get("gift_pending")
                if not gift_info or gift_info.get("key") != gift_key:
                    _bot.answer_callback_query(call.id, "❌ اطلاعات هدیه منقضی شده. دوباره تلاش کنید.", show_alert=True)
                    return

                tg_id = gift_info["tg_id"]
                account = gift_info["account"]
                gift_type = gift_info["gift_type"]

                if gift_type == "diamond":
                    amount = gift_info["amount"]
                    db.add_tokens(account["id"], amount)
                    new_balance = db.get_token_balance(account["id"])
                    gift_desc = f"💎 {amount} الماس"
                    try:
                        _bot.send_message(
                            tg_id,
                            f"{EM.EMOJI_DAILY_GIFT} <b>تبریک! شما از طرف مالک هدیه گرفتید!</b>\n\n"
                            f"🎊 مشخصات هدیه:\n"
                            f"╔══════════════════╗\n"
                            f"  💎 <b>الماس هدیه:</b> {amount} الماس\n"
                            f"  💰 <b>موجودی جدید:</b> {new_balance} الماس\n"
                            f"╚══════════════════╝"
                        )
                    except Exception:
                        pass
                    admin_msg = (
                        f"✅ <b>هدیه با موفقیت ارسال شد!</b>\n\n"
                        f"👤 کاربر: <b>{account['username']}</b>\n"
                        f"💎 هدیه: <b>{amount} الماس</b>\n"
                        f"💰 موجودی جدید: <b>{new_balance}</b>"
                    )
                else:
                    days = gift_info["days"]
                    plan_label = gift_info["plan_label"]
                    db.set_subscription(account["id"], "gift", days)
                    sub = db.get_subscription(account["id"])
                    end_date = sub.get("end_date", "نامشخص") if sub else "نامشخص"
                    gift_desc = f"📋 پنل {plan_label}"
                    try:
                        _bot.send_message(
                            tg_id,
                            f"{EM.EMOJI_DAILY_GIFT} <b>تبریک! شما از طرف مالک هدیه گرفتید!</b>\n\n"
                            f"🎊 مشخصات هدیه:\n"
                            f"╔══════════════════╗\n"
                            f"  📋 <b>پنل هدیه:</b> {plan_label} ({days} روز)\n"
                            f"  📅 <b>تاریخ انقضا:</b> {end_date}\n"
                            f"╚══════════════════╝"
                        )
                    except Exception:
                        pass
                    admin_msg = (
                        f"✅ <b>هدیه با موفقیت ارسال شد!</b>\n\n"
                        f"👤 کاربر: <b>{account['username']}</b>\n"
                        f"📋 هدیه: <b>پنل {plan_label}</b>\n"
                        f"📅 انقضا: <b>{end_date}</b>"
                    )

                _owner_states.pop(call.from_user.id, None)
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("🔙 پنل مدیریت", callback_data="admin_panel", style="primary"))
                _bot.edit_message_text(
                    admin_msg,
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=markup
                )
                _bot.answer_callback_query(call.id, "✅ هدیه ارسال شد!", show_alert=False)
                return

            elif data == "admin_gift_cancel":
                _owner_states.pop(call.from_user.id, None)
                markup = _admin_panel_keyboard()
                _bot.edit_message_text(
                    "❌ <b>عملیات هدیه لغو شد.</b>",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=markup
                )
                _bot.answer_callback_query(call.id)
                return

            elif data == "guide_type_media":
                # مالک یا ادمین انتخاب کرد: ارسال تصویری
                state_info = _owner_states.get(call.from_user.id, {})
                if state_info.get("state") == "guide_type":
                    state_info["state"] = "guide_send_media"
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="admin_guide_manage", style="danger", icon_custom_emoji_id="5832353674281620438"))
                _bot.edit_message_text(
                    "🎥 <b>ارسال آموزش تصویری</b>\n\n"
                    "ویدیو یا عکس آموزشی را در همین پیوی ارسال کنید:",
                    chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
                _bot.answer_callback_query(call.id)
                return

            elif data == "guide_type_text":
                # مالک یا ادمین انتخاب کرد: ارسال متنی
                state_info = _owner_states.get(call.from_user.id, {})
                if state_info.get("state") == "guide_type":
                    state_info["state"] = "guide_send_text"
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="admin_guide_manage", style="danger", icon_custom_emoji_id="5832353674281620438"))
                _bot.edit_message_text(
                    "📝 <b>ارسال آموزش متنی</b>\n\n"
                    "متن آموزش را ارسال کنید:",
                    chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
                _bot.answer_callback_query(call.id)
                return

            elif data == "admin_guide_manage":
                # ── مدیریت راهنما ──────────────────────────────────────────────
                import json as _json
                raw = db.get_global_setting("guide_list", "[]")
                try:
                    guides = _json.loads(raw)
                except Exception:
                    guides = []
                markup = types.InlineKeyboardMarkup(row_width=1)
                if guides:
                    txt = f"📚 <b>راهنماهای ثبت‌شده ({len(guides)} آموزش):</b>\n\n"
                    for i, g in enumerate(guides):
                        txt += f"{'🎥' if g['type'] == 'video' else '🖼' if g['type'] == 'photo' else '📝'} {g['name']}\n"
                        markup.add(types.InlineKeyboardButton(
                            f"❌ حذف «{g['name']}»", callback_data=f"admin_guide_del_{i}", style="danger"))
                else:
                    txt = "📚 <b>مدیریت راهنما</b>\n\nهیچ آموزشی ثبت نشده."
                markup.add(types.InlineKeyboardButton("➕ اضافه کردن راهنما", callback_data="admin_guide_add", style="success"))
                markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin_panel", style="danger"))
                _bot.edit_message_text(txt, chat_id=call.message.chat.id,
                    message_id=call.message.message_id, reply_markup=markup)
                _bot.answer_callback_query(call.id)
                return

            elif data == "admin_guide_add":
                # ── شروع فلوی افزودن راهنما ────────────────────────────────────
                _owner_states[call.from_user.id] = {"state": "guide_name", "data": {}}
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="admin_guide_manage", style="danger", icon_custom_emoji_id="5832353674281620438"))
                _bot.edit_message_text(
                    "📚 <b>افزودن راهنما</b>\n\nاسم آموزش را وارد کنید:",
                    chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
                _bot.answer_callback_query(call.id)
                return

            elif data.startswith("admin_guide_del_"):
                import json as _json
                idx = int(data[len("admin_guide_del_"):])
                raw = db.get_global_setting("guide_list", "[]")
                try:
                    guides = _json.loads(raw)
                except Exception:
                    guides = []
                if 0 <= idx < len(guides):
                    guides.pop(idx)
                    db.set_global_setting("guide_list", _json.dumps(guides, ensure_ascii=False))
                # نمایش دوباره لیست
                markup = types.InlineKeyboardMarkup(row_width=1)
                if guides:
                    txt = f"📚 <b>راهنماهای ثبت‌شده ({len(guides)} آموزش):</b>\n\n"
                    for i, g in enumerate(guides):
                        txt += f"{'🎥' if g['type'] == 'video' else '🖼' if g['type'] == 'photo' else '📝'} {g['name']}\n"
                        markup.add(types.InlineKeyboardButton(
                            f"❌ حذف «{g['name']}»", callback_data=f"admin_guide_del_{i}", style="danger"))
                else:
                    txt = "📚 <b>مدیریت راهنما</b>\n\nهیچ آموزشی ثبت نشده."
                markup.add(types.InlineKeyboardButton("➕ اضافه کردن راهنما", callback_data="admin_guide_add", style="success"))
                markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin_panel", style="danger"))
                _bot.edit_message_text(txt, chat_id=call.message.chat.id,
                    message_id=call.message.message_id, reply_markup=markup)
                _bot.answer_callback_query(call.id, "✅ آموزش حذف شد")
                return

            elif data == "admin_welcome_settings":
                # ── صفحه اصلی تنظیمات خوش‌آمد ───────────────────────────────
                cur_text = db.get_global_setting("welcome_text", "")
                cur_photo = db.get_global_setting("welcome_photo_id", "")
                preview = (cur_text[:120] + "...") if len(cur_text) > 120 else cur_text
                markup = types.InlineKeyboardMarkup(row_width=1)
                markup.add(
                    types.InlineKeyboardButton("✏️ تغییر متن خوش‌آمد", callback_data="admin_welcome_edit_text", style="primary"),
                    types.InlineKeyboardButton("🖼 تغییر عکس خوش‌آمد", callback_data="admin_welcome_edit_photo", style="primary"),
                )
                if cur_photo:
                    markup.add(types.InlineKeyboardButton("🗑 حذف عکس خوش‌آمد", callback_data="admin_welcome_del_photo", style="danger"))
                markup.add(types.InlineKeyboardButton("👁 پیش‌نمایش", callback_data="admin_welcome_preview", style="success"))
                markup.add(types.InlineKeyboardButton("🔄 بازگشت به پیش‌فرض", callback_data="admin_welcome_reset", style="danger"))
                markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin_panel", style="danger"))

                info = (
                    "✏️ <b>تنظیمات پیام خوش‌آمد</b>\n\n"
                    f"{'🖼 عکس: تنظیم شده ✅' if cur_photo else '🖼 عکس: تنظیم نشده ❌'}\n\n"
                    f"📝 <b>متن فعلی:</b>\n<code>{preview or '(پیش‌فرض)'}</code>\n\n"
                    "━━━━━━━━━━━━━━━━\n"
                    "📌 <b>متغیرهای قابل استفاده:</b>\n"
                    "  <code>{name}</code> — یوزرنیم کاربر\n"
                    "  <code>{name_full}</code> — نام کامل تلگرام\n"
                    "  <code>{mention}</code> — منشن با نام\n"
                    "  <code>{tag}</code> — @یوزرنیم\n"
                    "  <code>{tg_id}</code> — ایدی عددی\n"
                    "  <code>{time}</code> — وقت تهران\n"
                    "  <code>{balance}</code> — موجودی الماس\n"
                    "  <code>{total_earned}</code> — کل دریافتی\n"
                    "  <code>{sub_status}</code> — وضعیت اشتراک"
                )
                _bot.edit_message_text(info,
                    chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
                _bot.answer_callback_query(call.id)
                return

            elif data == "admin_welcome_edit_text":
                _owner_states[call.from_user.id] = {"state": "welcome_edit_text", "data": {}}
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="admin_welcome_settings", style="danger", icon_custom_emoji_id="5832353674281620438"))
                _bot.edit_message_text(
                    "✏️ <b>تغییر متن خوش‌آمد</b>\n\n"
                    "متن جدید را ارسال کنید.\n\n"
                    "متغیرهای قابل استفاده:\n"
                    "<code>{name}</code>  <code>{name_full}</code>  <code>{mention}</code>\n"
                    "<code>{tag}</code>  <code>{tg_id}</code>  <code>{time}</code>\n"
                    "<code>{balance}</code>  <code>{total_earned}</code>  <code>{sub_status}</code>",
                    chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
                _bot.answer_callback_query(call.id)
                return

            elif data == "admin_welcome_edit_photo":
                _owner_states[call.from_user.id] = {"state": "welcome_edit_photo", "data": {}}
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="admin_welcome_settings", style="danger", icon_custom_emoji_id="5832353674281620438"))
                _bot.edit_message_text(
                    "🖼 <b>تغییر عکس خوش‌آمد</b>\n\n"
                    "عکس جدید را ارسال کنید.\n"
                    "این عکس همراه با متن خوش‌آمد برای کاربران نمایش داده می‌شود.",
                    chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
                _bot.answer_callback_query(call.id)
                return

            elif data == "admin_welcome_del_photo":
                db.set_global_setting("welcome_photo_id", "")
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin_welcome_settings", style="danger"))
                _bot.edit_message_text("✅ عکس خوش‌آمد حذف شد.",
                    chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
                _bot.answer_callback_query(call.id, "✅ حذف شد")
                return

            elif data == "admin_welcome_reset":
                db.set_global_setting("welcome_text", "")
                db.set_global_setting("welcome_photo_id", "")
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin_welcome_settings", style="danger"))
                _bot.edit_message_text("✅ متن و عکس خوش‌آمد به پیش‌فرض بازگشت.",
                    chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
                _bot.answer_callback_query(call.id)
                return

            elif data == "admin_welcome_preview":
                # پیش‌نمایش برای خود مالک
                default_welcome = (
                    "👋 سلام {name}!\n\n"
                    "🕐 وقت تهران: {time}\n\n"
                    "💎 موجودی الماس: {balance}\n"
                    "📊 کل دریافتی: {total_earned}\n\n"
                    "📦 اشتراک سلف:\n{sub_status}"
                )
                template = db.get_global_setting("welcome_text", default_welcome) or default_welcome
                tg_user = call.from_user
                full_name = ((tg_user.first_name or "") + (" " + tg_user.last_name if tg_user.last_name else "")).strip()
                mention = f"<a href='tg://user?id={tg_user.id}'>{full_name}</a>"
                try:
                    preview_text = template.format(
                        name=tg_user.username or "مالک",
                        name_full=full_name or "مالک",
                        mention=mention,
                        tag=f"@{tg_user.username}" if tg_user.username else f"#{tg_user.id}",
                        tg_id=tg_user.id,
                        time=_now_tehran().strftime("%Y/%m/%d — %H:%M"),
                        balance="999",
                        total_earned="9999",
                        sub_status="✅ فعال — پیش‌نمایش",
                    )
                except Exception as fmt_err:
                    preview_text = f"❌ خطا در قالب‌بندی: {fmt_err}"
                welcome_photo = db.get_global_setting("welcome_photo_id", "")
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin_welcome_settings", style="danger"))
                if welcome_photo:
                    try:
                        _bot.send_photo(call.message.chat.id, welcome_photo,
                            caption=f"👁 <b>پیش‌نمایش:</b>\n\n{preview_text}", reply_markup=markup)
                        _bot.answer_callback_query(call.id)
                    except Exception:
                        _bot.send_message(call.message.chat.id,
                            f"👁 <b>پیش‌نمایش:</b>\n\n{preview_text}", reply_markup=markup)
                else:
                    _bot.send_message(call.message.chat.id,
                        f"👁 <b>پیش‌نمایش:</b>\n\n{preview_text}", reply_markup=markup)
                _bot.answer_callback_query(call.id)
                return

            elif data == "admin_manage_admins":
                # ── لیست ادمین‌های فرعی فعلی ─────────────────────────────────
                admins = db.get_sub_admins()
                markup = types.InlineKeyboardMarkup(row_width=1)
                if admins:
                    text_lines = [f"👮 <b>ادمین‌های فرعی ({len(admins)} نفر):</b>\n"]
                    for a in admins:
                        name = a.get("name") or "بدون نام"
                        tg_id_a = a["telegram_id"]
                        perms = a.get("permissions") or ""
                        perm_count = len([p for p in perms.split(",") if p]) if perms else 0
                        text_lines.append(f"• {name} — <code>{tg_id_a}</code> | {perm_count} دسترسی")
                        markup.add(types.InlineKeyboardButton(
                            f"🔑 دسترسی‌های {name}", callback_data=f"admin_perm_edit_{tg_id_a}", style="primary"
                        ))
                        markup.add(types.InlineKeyboardButton(
                            f"❌ حذف {name}", callback_data=f"admin_del_admin_{tg_id_a}", style="danger"
                        ))
                    admin_text = "\n".join(text_lines)
                else:
                    admin_text = "👮 <b>ادمین‌های فرعی</b>\n\nهنوز هیچ ادمینی اضافه نشده."
                markup.add(types.InlineKeyboardButton("➕ افزودن ادمین", callback_data="admin_add_admin", style="success"))
                markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin_panel", style="danger"))
                _bot.edit_message_text(
                    admin_text,
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=markup
                )
                _bot.answer_callback_query(call.id)
                return

            elif data == "admin_add_admin":
                _owner_states[call.from_user.id] = {"state": "add_admin_id", "data": {}}
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="admin_manage_admins", style="danger", icon_custom_emoji_id="5832353674281620438"))
                _bot.edit_message_text(
                    "👮 <b>افزودن ادمین فرعی</b>\n\n"
                    "ایدی عددی تلگرام ادمین جدید را وارد کنید:",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=markup
                )
                _bot.answer_callback_query(call.id)
                return

            elif data.startswith("admin_del_admin_"):
                del_tg_id = int(data[len("admin_del_admin_"):])
                db.remove_sub_admin(del_tg_id)
                # برگشت به لیست
                admins = db.get_sub_admins()
                markup = types.InlineKeyboardMarkup(row_width=1)
                if admins:
                    text_lines = [f"👮 <b>ادمین‌های فرعی ({len(admins)} نفر):</b>\n"]
                    for a in admins:
                        name = a.get("name") or "بدون نام"
                        tg_id = a["telegram_id"]
                        text_lines.append(f"• {name} — <code>{tg_id}</code>")
                        markup.add(types.InlineKeyboardButton(
                            f"❌ حذف {name}", callback_data=f"admin_del_admin_{tg_id}", style="danger"
                        ))
                    admin_text = "\n".join(text_lines)
                else:
                    admin_text = "👮 <b>ادمین‌های فرعی</b>\n\nهنوز هیچ ادمینی اضافه نشده."
                markup.add(types.InlineKeyboardButton("➕ افزودن ادمین", callback_data="admin_add_admin", style="success"))
                markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin_panel", style="danger"))
                _bot.edit_message_text(
                    "✅ ادمین حذف شد.\n\n" + admin_text,
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=markup
                )
                _bot.answer_callback_query(call.id, "✅ ادمین حذف شد")
                return

            elif data.startswith("admin_perm_edit_"):
                # ── ویرایش دسترسی‌های یک ادمین فرعی ─────────────────────────
                edit_tg_id = int(data[len("admin_perm_edit_"):])
                admin_info = db.get_sub_admin(edit_tg_id)
                if not admin_info:
                    _bot.answer_callback_query(call.id, "❌ ادمین یافت نشد", show_alert=True)
                    return
                current_perms = set((admin_info.get("permissions") or "").split(","))
                current_perms.discard("")
                name = admin_info.get("name") or "بدون نام"
                markup = types.InlineKeyboardMarkup(row_width=1)
                for perm_key, perm_label in db.ADMIN_PERMISSIONS:
                    has_perm = perm_key in current_perms
                    icon = "✅" if has_perm else "⬜️"
                    markup.add(types.InlineKeyboardButton(
                        f"{icon} {perm_label}",
                        callback_data=f"admin_perm_toggle_{edit_tg_id}_{perm_key}",
                        style="success" if has_perm else "primary"
                    ))
                markup.add(types.InlineKeyboardButton("🔙 بازگشت به ادمین‌ها", callback_data="admin_manage_admins", style="danger"))
                _bot.edit_message_text(
                    f"🔑 <b>دسترسی‌های ادمین: {name}</b>\n<code>{edit_tg_id}</code>\n\n"
                    "برای فعال/غیرفعال کردن هر بخش روی آن کلیک کنید:\n"
                    "✅ = دسترسی دارد | ⬜️ = دسترسی ندارد",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=markup
                )
                _bot.answer_callback_query(call.id)
                return

            elif data.startswith("admin_perm_toggle_"):
                # ── تغییر وضعیت یک دسترسی ────────────────────────────────────
                parts = data[len("admin_perm_toggle_"):].split("_", 1)
                if len(parts) < 2:
                    return _bot.answer_callback_query(call.id)
                toggle_tg_id = int(parts[0])
                perm_key = parts[1]
                admin_info = db.get_sub_admin(toggle_tg_id)
                if not admin_info:
                    return _bot.answer_callback_query(call.id, "❌ ادمین یافت نشد", show_alert=True)
                current_perms = set((admin_info.get("permissions") or "").split(","))
                current_perms.discard("")
                if perm_key in current_perms:
                    current_perms.discard(perm_key)
                    msg = "❌ دسترسی حذف شد"
                else:
                    current_perms.add(perm_key)
                    msg = "✅ دسترسی اضافه شد"
                db.update_sub_admin_permissions(toggle_tg_id, ",".join(current_perms))
                _bot.answer_callback_query(call.id, msg)
                # رفرش صفحه دسترسی‌ها
                call.data = f"admin_perm_edit_{toggle_tg_id}"
                callback_admin(call)
                return

            elif data == "admin_missions":
                missions = db.get_active_missions()
                markup = types.InlineKeyboardMarkup(row_width=1)
                if missions:
                    text = "🎯 <b>ماموریت‌های فعال:</b>\n\n"
                    for m in missions:
                        text += f"🔸 {m['channel_username']} — 💎{m['reward']} الماس\n"
                        # 🔴 دکمه حذف با رنگ danger (قرمز)
                        markup.add(types.InlineKeyboardButton(f"❌ حذف {m['channel_username']}", callback_data=f"del_mission_{m['id']}", style="danger"))
                else:
                    text = "📋 هیچ ماموریتی تعریف نشده.\n\n"
                # 🟢 دکمه افزودن ماموریت با رنگ success (سبز)
                markup.add(types.InlineKeyboardButton("➕ افزودن ماموریت", callback_data="add_mission_prompt", style="success"))
                # 🔴 دکمه بازگشت با رنگ danger (قرمز)
                markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin_panel", style="danger"))
                _bot.edit_message_text(text, chat_id=call.message.chat.id,
                    message_id=call.message.message_id, reply_markup=markup)
                _bot.answer_callback_query(call.id)
                return

            elif data.startswith("del_mission_"):
                mid = int(data.split("_")[2])
                db.remove_mission(mid)
                _bot.answer_callback_query(call.id, "✅ ماموریت حذف شد")
                call.data = "admin_missions"
                callback_admin(call)
                return

            elif data == "add_mission_prompt":
                _owner_states[call.from_user.id] = {"state": "mission_channel", "data": {}}
                markup = types.InlineKeyboardMarkup()
                # 🔴 دکمه لغو با رنگ danger (قرمز)
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="admin_missions", style="danger", icon_custom_emoji_id="5832353674281620438"))
                _bot.edit_message_text(
                    "🎯 <b>افزودن ماموریت</b>\n\nآیدی کانال را ارسال کنید (با @):\nمثال: <code>@mychannel</code>",
                    chat_id=call.message.chat.id, message_id=call.message.message_id,
                    parse_mode="HTML", reply_markup=markup
                )
                _bot.answer_callback_query(call.id)
                return

            elif data == "admin_lottery":
                import json as _json
                # نمایش قرعه‌کشی‌های فعال
                active = []
                try:
                    raw = db.get_global_setting("lotteries", "[]")
                    active = _json.loads(raw)
                except Exception:
                    active = []
                
                now_teh = _now_tehran()
                markup = types.InlineKeyboardMarkup(row_width=1)
                markup.add(types.InlineKeyboardButton("➕ ایجاد قرعه‌کشی جدید", callback_data="lottery_create", style="success"))
                
                text = "🎰 <b>مدیریت قرعه‌کشی</b>\n\n"
                if active:
                    text += "📋 قرعه‌کشی‌های فعال:\n"
                    for lot in active:
                        status = "🟢 فعال" if lot.get("status") == "active" else "✅ پایان یافته"
                        text += f"\n• {lot.get('start_time','?')} تا {lot.get('end_time','?')} — {lot.get('winners_count','?')} برنده — {status}"
                        if lot.get("status") == "active":
                            markup.add(types.InlineKeyboardButton(
                                f"❌ لغو قرعه‌کشی {lot.get('start_time','?')}",
                                callback_data=f"lottery_cancel_{lot.get('id','')}",
                                style="danger"
                            ))
                else:
                    text += "هیچ قرعه‌کشی فعالی وجود ندارد."
                
                markup.add(types.InlineKeyboardButton("🔙 پنل مدیریت", callback_data="admin_panel", style="danger"))
                _bot.edit_message_text(text, chat_id=call.message.chat.id,
                    message_id=call.message.message_id, reply_markup=markup)
                _bot.answer_callback_query(call.id)

            elif data == "lottery_create":
                _owner_states[uid] = {"state": "lottery_start_time", "data": {}}
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="admin_lottery", style="danger", icon_custom_emoji_id="5832353674281620438"))
                _bot.edit_message_text(
                    "🎰 <b>ایجاد قرعه‌کشی</b>\n\n"
                    "📝 <b>مرحله ۱ از ۵: ساعت شروع</b>\n\n"
                    "ساعت شروع قرعه‌کشی را ارسال کنید:\n"
                    "مثال: <code>22:00</code>",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=markup
                )
                _bot.answer_callback_query(call.id)

            elif data.startswith("lottery_cancel_"):
                import json as _json
                lot_id = data[len("lottery_cancel_"):]
                try:
                    raw = db.get_global_setting("lotteries", "[]")
                    lotteries = _json.loads(raw)
                    lotteries = [l for l in lotteries if str(l.get("id","")) != lot_id]
                    db.set_global_setting("lotteries", _json.dumps(lotteries, ensure_ascii=False))
                except Exception:
                    pass
                _bot.answer_callback_query(call.id, "✅ قرعه‌کشی لغو شد", show_alert=True)
                # برگشت به صفحه قرعه‌کشی
                markup = types.InlineKeyboardMarkup(row_width=1)
                markup.add(types.InlineKeyboardButton("➕ ایجاد قرعه‌کشی جدید", callback_data="lottery_create", style="success"))
                markup.add(types.InlineKeyboardButton("🔙 پنل مدیریت", callback_data="admin_panel", style="danger"))
                _bot.edit_message_text("🎰 <b>مدیریت قرعه‌کشی</b>\n\n✅ قرعه‌کشی لغو شد.",
                    chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

            elif data == "lottery_prize_sub":
                state_data = _owner_states.get(uid)
                if not state_data or state_data.get("state") != "lottery_prize_choose":
                    _bot.answer_callback_query(call.id, "این مرحله منقضی شده، دوباره شروع کنید.", show_alert=True)
                    return
                current = state_data["data"]["current_prize"]
                _bot.edit_message_text(
                    f"📝 مدت اشتراک نفر <b>{_lottery_ordinal(current - 1)}</b> را انتخاب کنید:",
                    chat_id=call.message.chat.id, message_id=call.message.message_id,
                    reply_markup=_lottery_plan_markup()
                )
                _bot.answer_callback_query(call.id)

            elif data == "lottery_prize_diamond":
                state_data = _owner_states.get(uid)
                if not state_data or state_data.get("state") != "lottery_prize_choose":
                    _bot.answer_callback_query(call.id, "این مرحله منقضی شده، دوباره شروع کنید.", show_alert=True)
                    return
                current = state_data["data"]["current_prize"]
                state_data["state"] = "lottery_diamond_amount"
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="admin_lottery", style="danger"))
                _bot.edit_message_text(
                    f"📝 تعداد الماس جایزه‌ی نفر <b>{_lottery_ordinal(current - 1)}</b> را وارد کنید:\n"
                    "مثال: <code>10</code>",
                    chat_id=call.message.chat.id, message_id=call.message.message_id,
                    reply_markup=markup
                )
                _bot.answer_callback_query(call.id)

            elif data.startswith("lottery_plan_"):
                state_data = _owner_states.get(uid)
                if not state_data or state_data.get("state") != "lottery_prize_choose":
                    _bot.answer_callback_query(call.id, "این مرحله منقضی شده، دوباره شروع کنید.", show_alert=True)
                    return
                days = int(data[len("lottery_plan_"):])
                prize_detail = {"type": "subscription", "days": days, "label": f"اشتراک {days} روزه"}
                next_text, next_markup = _lottery_advance_prize(state_data, prize_detail)
                _bot.edit_message_text(
                    next_text, chat_id=call.message.chat.id, message_id=call.message.message_id,
                    reply_markup=next_markup
                )
                _bot.answer_callback_query(call.id)

            elif data == "lottery_confirm":
                import json as _json
                import uuid as _uuid
                state_data = _owner_states.get(uid, {})
                lot_data = state_data.get("data", {})
                
                start_h, start_m = map(int, lot_data["start_time"].split(":"))
                end_h, end_m = map(int, lot_data["end_time"].split(":"))
                now_teh = _now_tehran()
                
                # تنظیم زمان شروع و پایان برای امروز
                start_dt = now_teh.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
                end_dt = now_teh.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
                if end_dt <= start_dt:
                    end_dt += datetime.timedelta(days=1)
                if start_dt <= now_teh:
                    start_dt += datetime.timedelta(days=1)
                    end_dt += datetime.timedelta(days=1)
                
                lot_id = str(_uuid.uuid4())[:8]
                lottery_entry = {
                    "id": lot_id,
                    "start_time": lot_data["start_time"],
                    "end_time": lot_data["end_time"],
                    "winners_count": lot_data["winners_count"],
                    "prizes": lot_data["prizes"],
                    "prize_details": lot_data.get("prize_details", []),
                    "start_ts": start_dt.isoformat(),
                    "end_ts": end_dt.isoformat(),
                    "status": "active",
                    "participants": [],
                    "channel": getattr(config, "WC_CHANNEL_ID", "")
                }
                
                # ذخیره در دیتابیس
                try:
                    raw = db.get_global_setting("lotteries", "[]")
                    lotteries = _json.loads(raw)
                except Exception:
                    lotteries = []
                lotteries.append(lottery_entry)
                db.set_global_setting("lotteries", _json.dumps(lotteries, ensure_ascii=False))
                _owner_states.pop(uid, None)
                
                _bot.answer_callback_query(call.id, "✅ قرعه‌کشی ثبت شد!", show_alert=True)
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("🎰 مدیریت قرعه‌کشی", callback_data="admin_lottery", style="success"))
                markup.add(types.InlineKeyboardButton("🔙 پنل مدیریت", callback_data="admin_panel", style="danger"))
                _bot.edit_message_text(
                    f"✅ <b>قرعه‌کشی با موفقیت ثبت شد!</b>\n\n"
                    f"⏰ شروع: <b>{lot_data['start_time']}</b>\n"
                    f"⏰ پایان: <b>{lot_data['end_time']}</b>\n"
                    f"🏆 تعداد برنده: <b>{lot_data['winners_count']} نفر</b>\n"
                    f"🎁 جوایز: {' | '.join(lot_data['prizes'])}\n\n"
                    f"ربات در ساعت {lot_data['start_time']} قرعه‌کشی را اعلام می‌کند.",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=markup
                )

            else:
                _bot.answer_callback_query(call.id, "❌ گزینه نامعتبر")
        
        except Exception as e:
            print(f"❌ خطا در callback_admin: {e}")
            try:
                _bot.answer_callback_query(call.id, f"❌ خطا: {str(e)[:100]}", show_alert=True)
            except: 
                pass

    # ══════════════════════════════════════════════════════════════════════════
    # 👮 مدیریت تک‌تک کاربران (بن/رفع بن، دادن/کسر الماس، خاموش کردن سلف)
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.callback_query_handler(func=lambda call: call.data.startswith("mu_"))
    def callback_manage_user(call):
        if call.from_user.id != OWNER_TG_ID and not db.is_sub_admin(call.from_user.id):
            return _bot.answer_callback_query(call.id, "❌ دسترسی ندارید", show_alert=True)
        try:
            parts = call.data.split("_", 2)
            action = parts[1]
            acc_id = int(parts[2])
            account = _get_account_by_id(acc_id)
            if not account:
                return _bot.answer_callback_query(call.id, "❌ کاربر یافت نشد.", show_alert=True)

            if action == "ban":
                db.set_setting(acc_id, "self_banned", "1")
                tg_id = db.get_telegram_id_by_owner(acc_id)
                if tg_id:
                    try:
                        _bot.send_message(tg_id, "🚫 <b>شما توسط مالک از سلف بن شدید.</b>\nامکان مدیریت/شرط‌بندی سلف برای شما غیرفعال شد.")
                    except Exception:
                        pass
                _bot.answer_callback_query(call.id, "🚫 کاربر بن شد.")

            elif action == "unban":
                db.set_setting(acc_id, "self_banned", "0")
                tg_id = db.get_telegram_id_by_owner(acc_id)
                if tg_id:
                    try:
                        _bot.send_message(tg_id, "✅ <b>بن شما توسط مالک برداشته شد.</b>\nمی‌توانید دوباره از سلف استفاده کنید.")
                    except Exception:
                        pass
                _bot.answer_callback_query(call.id, "✅ بن کاربر برداشته شد.")

            elif action == "stopself":
                try:
                    from bot import bot_manager
                    bot_manager.stop(acc_id)
                    _bot.answer_callback_query(call.id, "🔴 سلف کاربر خاموش شد.")
                except Exception as e:
                    _bot.answer_callback_query(call.id, f"❌ خطا در خاموش کردن سلف: {str(e)[:80]}", show_alert=True)

            elif action == "give":
                _owner_states[call.from_user.id] = {"state": "manage_give_amount", "data": {"account_id": acc_id}}
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data=f"mu_view_{acc_id}", style="danger"))
                _bot.edit_message_text(
                    f"➕ <b>دادن الماس به {account.get('username','-')}</b>\n\nتعداد الماس را وارد کنید:",
                    chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup
                )
                _bot.answer_callback_query(call.id)
                return

            elif action == "deduct":
                _owner_states[call.from_user.id] = {"state": "manage_deduct_amount", "data": {"account_id": acc_id}}
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data=f"mu_view_{acc_id}", style="danger"))
                _bot.edit_message_text(
                    f"➖ <b>کسر الماس از {account.get('username','-')}</b>\n\nتعداد الماس را وارد کنید:",
                    chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup
                )
                _bot.answer_callback_query(call.id)
                return

            elif action == "view":
                _bot.answer_callback_query(call.id)

            else:
                return _bot.answer_callback_query(call.id, "❌ عملیات نامعتبر", show_alert=True)

            # نمایش مجدد کارت کاربر با وضعیت بروزشده
            account = _get_account_by_id(acc_id) or account
            card_text, card_markup = _manage_user_card(account)
            try:
                _bot.edit_message_text(
                    card_text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=card_markup
                )
            except Exception:
                _bot.send_message(call.message.chat.id, card_text, reply_markup=card_markup)

        except Exception as e:
            print(f"❌ خطا در callback_manage_user: {e}")
            try:
                _bot.answer_callback_query(call.id, f"❌ خطا: {str(e)[:100]}", show_alert=True)
            except Exception:
                pass

    # ══════════════════════════════════════════════════════════════════════════
    # 📨 State handler
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.message_handler(func=lambda m: (m.from_user.id == OWNER_TG_ID or db.is_sub_admin(m.from_user.id)) and m.from_user.id in _owner_states, chat_types=['private'],
                          content_types=["text", "photo", "document", "video"])
    def handle_owner_state(message):
        try:
            state_data = _owner_states[message.from_user.id]
            state = state_data["state"]
            text = (message.text or "").strip()

            # ── مدیریت کاربران: جستجو با یوزرنیم یا آیدی عددی تلگرام ────────────
            if state == "manage_user_lookup":
                _owner_states.pop(message.from_user.id, None)
                account = None
                if text.isdigit():
                    account = _get_account_cached(int(text))
                if not account:
                    account = db.get_account_by_username(text.lstrip("@"))
                if not account:
                    _bot.reply_to(message, "❌ کاربری با این مشخصات پیدا نشد.", reply_markup=_owner_keyboard())
                    return
                card_text, card_markup = _manage_user_card(account)
                _bot.reply_to(message, card_text, reply_markup=card_markup)
                return

            if state == "manage_give_amount":
                acc_id = state_data["data"]["account_id"]
                try:
                    amount = int(text)
                    if amount <= 0:
                        raise ValueError
                except ValueError:
                    return _bot.reply_to(message, "❌ مقدار باید عدد مثبت باشد. دوباره ارسال کنید:")
                _owner_states.pop(message.from_user.id, None)
                db.add_tokens(acc_id, amount)
                new_balance = db.get_token_balance(acc_id)
                tg_id = db.get_telegram_id_by_owner(acc_id)
                if tg_id:
                    try:
                        _bot.send_message(tg_id, f"{EM.EMOJI_DAILY_GIFT} <b>{amount} الماس</b> از طرف مالک دریافت کردید!\n{EM.EMOJI_BALANCE} موجودی جدید: <b>{new_balance}</b>")
                    except Exception:
                        pass
                account = _get_account_by_id(acc_id) or {"id": acc_id, "username": "-"}
                card_text, card_markup = _manage_user_card(account)
                _bot.reply_to(message, f"✅ {amount} الماس اضافه شد. موجودی جدید: {new_balance}\n\n{card_text}", reply_markup=card_markup)
                return

            if state == "manage_deduct_amount":
                acc_id = state_data["data"]["account_id"]
                try:
                    amount = int(text)
                    if amount <= 0:
                        raise ValueError
                except ValueError:
                    return _bot.reply_to(message, "❌ مقدار باید عدد مثبت باشد. دوباره ارسال کنید:")
                _owner_states.pop(message.from_user.id, None)
                current_balance = db.get_token_balance(acc_id)
                if amount > current_balance:
                    amount = current_balance
                db.add_tokens(acc_id, -amount)
                new_balance = db.get_token_balance(acc_id)
                tg_id = db.get_telegram_id_by_owner(acc_id)
                if tg_id:
                    try:
                        _bot.send_message(tg_id, f"⚠️ <b>{amount} الماس</b> توسط مالک از موجودی شما کسر شد.\n{EM.EMOJI_BALANCE} موجودی جدید: <b>{new_balance}</b>")
                    except Exception:
                        pass
                account = _get_account_by_id(acc_id) or {"id": acc_id, "username": "-"}
                card_text, card_markup = _manage_user_card(account)
                _bot.reply_to(message, f"✅ {amount} الماس کسر شد. موجودی جدید: {new_balance}\n\n{card_text}", reply_markup=card_markup)
                return

            # ── پیام به کانال (با پشتیبانی از ایموجی پرمیوم و فرمت‌بندی) ───────
            if state == "channel_msg_text":
                _owner_states.pop(message.from_user.id, None)
                target = (
                    getattr(config, 'PUBLISH_CHANNEL_ID', None)
                    or getattr(config, 'WC_CHANNEL_ID', None)
                    or getattr(config, 'WORLD_CUP_GROUP', None)
                )
                if not target:
                    _bot.reply_to(
                        message,
                        "❌ مقصد ارسال تنظیم نشده.\n"
                        "متغیر <code>PUBLISH_CHANNEL_ID</code> را در config.py تنظیم کنید "
                        "(مثل <code>@channel</code> یا <code>-100xxxxxxxxxx</code>).",
                        reply_markup=_owner_keyboard()
                    )
                    return
                try:
                    if message.content_type == "text":
                        raw_text = message.text or ""
                        raw_entities = message.entities or []
                        new_text, new_entities = _parse_premium_emojis(raw_text, raw_entities)
                        _bot.send_message(target, new_text, entities=new_entities)
                    else:
                        raw_caption = message.caption or ""
                        raw_entities = message.caption_entities or []
                        new_caption, new_entities = _parse_premium_emojis(raw_caption, raw_entities)
                        if message.photo:
                            _bot.send_photo(target, message.photo[-1].file_id, caption=new_caption, caption_entities=new_entities)
                        elif message.video:
                            _bot.send_video(target, message.video.file_id, caption=new_caption, caption_entities=new_entities)
                        elif message.document:
                            _bot.send_document(target, message.document.file_id, caption=new_caption, caption_entities=new_entities)
                        else:
                            _bot.reply_to(message, "❌ نوع پیام پشتیبانی نمی‌شود.", reply_markup=_owner_keyboard())
                            return
                    _bot.reply_to(message, "✅ پیام با موفقیت به کانال ارسال شد.", reply_markup=_owner_keyboard())
                except Exception as e:
                    _bot.reply_to(message, f"❌ خطا در ارسال به کانال: {e}\nمطمئن شوید ربات در مقصد ادمین است.", reply_markup=_owner_keyboard())
                return

            # ── پیام عمومی ─────────────────────────────────────────────────────
            if state == "broadcast_msg":
                _owner_states.pop(message.from_user.id, None)
                tg_ids = db.get_all_telegram_ids()
                _bot.reply_to(message, f"⏳ در حال ارسال به {len(tg_ids)} کاربر...")
                sent, failed = 0, 0
                for tid in tg_ids:
                    try:
                        if message.photo:
                            _bot.send_photo(tid, message.photo[-1].file_id, caption=message.caption or "")
                        elif message.document:
                            _bot.send_document(tid, message.document.file_id, caption=message.caption or "")
                        else:
                            _bot.send_message(tid, message.text)
                        sent += 1
                    except Exception:
                        failed += 1
                _bot.reply_to(message,
                    f"✅ ارسال تمام شد!\n\n📤 موفق: {sent}\n❌ بلاک‌شده/خطا: {failed}",
                    reply_markup=_owner_keyboard())
                return

            if state == "waiting_channel":
                if not text.startswith("@"):
                    text = "@" + text
                if db.add_forced_channel(text):
                    cache.invalidate("membership_")
                    _bot.reply_to(message, f"✅ چنل <b>{text}</b> اضافه شد.", reply_markup=_owner_keyboard())
                else:
                    _bot.reply_to(message, f"⚠️ خطا یا تکراری است.", reply_markup=_owner_keyboard())
                _owner_states.pop(message.from_user.id, None)
            
            elif state == "wc_team1":
                state_data["data"]["team1"] = text
                state_data["state"] = "wc_team2"
                _bot.reply_to(message, f"✅ تیم اول: <b>{text}</b>\n\n📝 مرحله  از ۴:\nنام <b>تیم دوم</b> را ارسال کنید:")
            
            elif state == "wc_team2":
                state_data["data"]["team2"] = text
                state_data["state"] = "wc_time"
                _bot.reply_to(message, f"✅ تیم دوم: <b>{text}</b>\n\n📝 مرحله  از ۴:\n ساعت بازی را ارسال کنید:\n\nمثال: <code>20:30</code>")
            
            elif state == "wc_time":
                state_data["data"]["time"] = text
                state_data["state"] = "wc_bet"
                _bot.reply_to(message, f"✅ ساعت: <b>{text}</b>\n\n📝 مرحله ۴ از ۴:\n💎 مبلغ شرط (الماس) را ارسال کنید:\n\nمثال: <code>10</code>")
            
            elif state == "wc_bet":
                try:
                    bet_amount = int(text)
                except:
                    return _bot.reply_to(message, "❌ مبلغ باید عدد باشد. دوباره تلاش کنید:")
                
                data = state_data["data"]
                challenge_id = db.create_world_cup_challenge(data["team1"], data["team2"], data["time"], bet_amount)
                
                group = getattr(config, 'WORLD_CUP_GROUP', '@amelselfgap')
                markup = types.InlineKeyboardMarkup(row_width=2)
                # 🔵 دکمه تیم اول با رنگ primary (آبی)
                markup.add(
                    types.InlineKeyboardButton(f"🔵 {data['team1']}", callback_data=f"bet_wc_{challenge_id}_{data['team1']}", style="primary")
                )
                # 🔴 دکمه تیم دوم با رنگ danger (قرمز)
                markup.add(
                    types.InlineKeyboardButton(f"🔴 {data['team2']}", callback_data=f"bet_wc_{challenge_id}_{data['team2']}", style="danger")
                )
                
                try:
                    msg = _bot.send_message(group,
                        f"⚽️ <b>چالش جام جهانی!</b>\n\n"
                        f"🆚 <b>{data['team1']}</b> در برابر <b>{data['team2']}</b>\n"
                        f"⏰ ساعت: <b>{data['time']}</b>\n"
                        f"💎 مبلغ شرط: <b>{bet_amount} الماس</b>\n\n"
                        f"کدام تیم برنده می‌شود؟ شرط ببندید!",
                        reply_markup=markup)
                    db.update_challenge_message(challenge_id, msg.message_id, msg.chat.id)
                    _bot.reply_to(message, 
                        f"✅ چالش با موفقیت ایجاد شد!\n\n"
                        f"🆚 {data['team1']} vs {data['team2']}\n"
                        f"⏰ {data['time']} | 💎 {bet_amount}\n"
                        f"📢 ID چالش: <code>{challenge_id}</code>",
                        reply_markup=_owner_keyboard())
                except Exception as e:
                    _bot.reply_to(message, f"❌ خطا در ارسال به گروه: {e}\nمطمئن شوید ربات در {group} ادمین است.", reply_markup=_owner_keyboard())
                
                _owner_states.pop(message.from_user.id, None)
            
            elif state == "transfer_user":
                state_data["data"]["username"] = text.lstrip("@")
                state_data["state"] = "transfer_amount"
                _bot.reply_to(message, f"📝 کاربر: <b>{text}</b>\n\n💎 مبلغ الماس را ارسال کنید:")
            
            elif state == "transfer_amount":
                try:
                    amount = int(text)
                except:
                    return _bot.reply_to(message, "❌ مبلغ باید عدد باشد:")
                
                username = state_data["data"]["username"]
                to_account = db.get_account_by_username(username)
                if not to_account:
                    _bot.reply_to(message, f"❌ کاربر '{username}' یافت نشد.", reply_markup=_owner_keyboard())
                    _owner_states.pop(message.from_user.id, None)
                    return
                
                db.add_tokens(to_account["id"], amount)
                new_balance = db.get_token_balance(to_account["id"])
                
                to_tg_id = db.get_telegram_id_by_owner(to_account["id"])
                if to_tg_id:
                    try:
                        _bot.send_message(to_tg_id, f"{EM.EMOJI_DAILY_GIFT} <b>{amount} الماس</b> از طرف سیستم دریافت کردید!\n{EM.EMOJI_BALANCE} موجودی جدید: <b>{new_balance}</b>")
                    except: 
                        pass
                
                _bot.reply_to(message, 
                    f"✅ <b>{amount} الماس</b> به <b>{to_account['username']}</b> داده شد.\n💎 موجودی جدید: <b>{new_balance}</b>",
                    reply_markup=_owner_keyboard())
                _owner_states.pop(message.from_user.id, None)
            
            elif state == "give_user":
                state_data["data"]["username"] = text.lstrip("@")
                state_data["state"] = "give_amount"
                _bot.reply_to(message, f"📝 کاربر: <b>{text}</b>\n\n💎 مبلغ الماس را ارسال کنید:")
            
            elif state == "give_amount":
                try:
                    amount = int(text)
                except:
                    return _bot.reply_to(message, "❌ مبلغ باید عدد باشد:")
                
                username = state_data["data"]["username"]
                account = db.get_account_by_username(username)
                if not account:
                    _bot.reply_to(message, f"❌ کاربر '{username}' یافت نشد.", reply_markup=_owner_keyboard())
                    _owner_states.pop(message.from_user.id, None)
                    return
                
                db.add_tokens(account["id"], amount)
                new_balance = db.get_token_balance(account["id"])
                token_price = getattr(config, 'TOKEN_PRICE_TOMAN', 200)
                
                tg_id = db.get_telegram_id_by_owner(account["id"])
                if tg_id:
                    try:
                        _bot.send_message(tg_id, f"{EM.EMOJI_DAILY_GIFT} <b>{amount} الماس</b> از طرف مالک دریافت کردید!\n{EM.EMOJI_BALANCE} موجودی جدید: <b>{new_balance}</b>")
                    except: 
                        pass
                
                _bot.reply_to(message, 
                    f"✅ <b>{amount}</b> الماس به <b>{account['username']}</b> داده شد.\n"
                    f"{EM.EMOJI_BALANCE} موجودی جدید: <b>{new_balance}</b> (معادل {new_balance * token_price} تومان)",
                    reply_markup=_owner_keyboard())
                _owner_states.pop(message.from_user.id, None)

            elif state == "gift_diamond_amount":
                # ── مرحله ۱ الماس: دریافت تعداد الماس ──────────────────────────
                try:
                    amount = int(text)
                    if amount <= 0:
                        return _bot.reply_to(message, "❌ تعداد الماس باید بیشتر از صفر باشد. دوباره وارد کنید:")
                except ValueError:
                    return _bot.reply_to(message, "❌ لطفاً یک عدد معتبر وارد کنید:")
                state_data["data"]["amount"] = amount
                state_data["state"] = "gift_tg_id"
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="admin_panel", style="danger", icon_custom_emoji_id="5832353674281620438"))
                _bot.reply_to(
                    message,
                    f"{EM.EMOJI_DIAMONDS} <b>هدیه الماس: {amount} الماس</b>\n\n"
                    "ایدی عددی تلگرام کاربر مورد نظر را وارد کنید:",
                    reply_markup=markup
                )

            elif state == "gift_tg_id":
                # ── دریافت ایدی عددی تلگرام ──────────────────────────────────────
                try:
                    tg_id = int(text)
                except ValueError:
                    return _bot.reply_to(message, "❌ ایدی عددی باید فقط شامل اعداد باشد. دوباره وارد کنید:")

                account = db.get_account_by_tg_id(tg_id)
                if not account:
                    return _bot.reply_to(
                        message,
                        "❌ کاربری با این ایدی عددی در سیستم یافت نشد.\n"
                        "مطمئن شوید کاربر قبلاً در ربات ثبت‌نام کرده باشد.",
                        reply_markup=_owner_keyboard()
                    )

                gift_type = state_data["data"]["gift_type"]
                balance = db.get_token_balance(account["id"])
                sub = db.get_subscription(account["id"])
                if sub and sub.get("end_date"):
                    plan_remaining = sub["end_date"]
                else:
                    plan_remaining = "ندارد"

                if gift_type == "diamond":
                    amount = state_data["data"]["amount"]
                    gift_desc = f"💎 {amount} الماس"
                else:
                    days = state_data["data"]["days"]
                    plan_label = state_data["data"]["plan_label"]
                    gift_desc = f"📋 پنل {plan_label} ({days} روز)"

                # ذخیره اطلاعات تایید هدیه
                import hashlib, time
                gift_key = hashlib.md5(f"{tg_id}{time.time()}".encode()).hexdigest()[:8]
                state_data["gift_pending"] = {
                    "key": gift_key,
                    "tg_id": tg_id,
                    "account": account,
                    "gift_type": gift_type,
                    "amount": state_data["data"].get("amount"),
                    "days": state_data["data"].get("days"),
                    "plan_label": state_data["data"].get("plan_label"),
                }
                state_data["state"] = "gift_awaiting_confirm"

                confirm_text = (
                    f"{EM.EMOJI_DAILY_GIFT} <b>تایید هدیه</b>\n\n"
                    f"👤 <b>کاربر:</b> {account.get('username', 'نامشخص')}\n"
                    f"🆔 <b>ایدی تلگرام:</b> @{account.get('username', '-')}\n"
                    f"🔢 <b>ایدی عددی:</b> <code>{tg_id}</code>\n"
                    f"📋 <b>پلن باقی‌مانده:</b> {plan_remaining}\n"
                    f"💰 <b>موجودی:</b> {balance} الماس\n"
                    f"{EM.EMOJI_DAILY_GIFT} <b>{gift_desc} هدیه</b>\n\n"
                    f"آیا تایید می‌کنید؟"
                )
                markup = types.InlineKeyboardMarkup(row_width=2)
                markup.add(
                    types.InlineKeyboardButton("✅ تایید", callback_data=f"admin_gift_confirm_{gift_key}", style="success", icon_custom_emoji_id="5830326445422940546"),
                    types.InlineKeyboardButton("❌ لغو", callback_data="admin_gift_cancel", style="danger", icon_custom_emoji_id="5832353674281620438")
                )
                _bot.reply_to(message, confirm_text, reply_markup=markup)

            elif state == "gift_awaiting_confirm":
                _bot.reply_to(message, "⏳ لطفاً روی دکمه تایید یا لغو کلیک کنید.")

            elif state == "set_card":
                card = text.strip().replace("-", "").replace(" ", "")
                db.set_global_setting("card_number", card)
                _bot.reply_to(message,
                    f"✅ شماره کارت ذخیره شد:\n<code>{card}</code>",
                    reply_markup=_owner_keyboard())
                _owner_states.pop(message.from_user.id, None)

            elif state == "mission_channel":
                ch = text.strip()
                if not ch.startswith("@"):
                    ch = "@" + ch
                if "data" not in state_data:
                    state_data["data"] = {}
                state_data["data"]["channel"] = ch
                state_data["state"] = "mission_reward"
                _bot.reply_to(message,
                    f"✅ کانال: <b>{ch}</b>\n\n💎 مقدار جایزه (الماس) را ارسال کنید:",
                    parse_mode="HTML")

            elif state == "mission_reward":
                try:
                    reward = int(text.strip())
                    if reward < 1:
                        return _bot.reply_to(message, "❌ جایزه باید بیشتر از ۰ باشد.")
                except ValueError:
                    return _bot.reply_to(message, "❌ مقدار باید عدد باشد.")
                ch = state_data.get("data", {}).get("channel")
                if not ch:
                    _bot.reply_to(message, "❌ خطا: آیدی کانال یافت نشد. دوباره تلاش کنید.", reply_markup=_owner_keyboard())
                    _owner_states.pop(message.from_user.id, None)
                    return
                db.add_mission(ch, reward)
                _bot.reply_to(message,
                    f"✅ ماموریت اضافه شد!\n🔸 {ch} — 💎{reward} الماس",
                    parse_mode="HTML",
                    reply_markup=_owner_keyboard())
                _owner_states.pop(message.from_user.id, None)

            elif state == "add_admin_id":
                try:
                    new_admin_id = int(text.strip())
                except ValueError:
                    return _bot.reply_to(message, "❌ ایدی عددی باید فقط شامل اعداد باشد. دوباره وارد کنید:")
                # نام ادمین رو از تلگرام بگیریم اگه ممکنه
                try:
                    chat_info = _bot.get_chat(new_admin_id)
                    name = (chat_info.first_name or "") + (" " + chat_info.last_name if chat_info.last_name else "")
                    name = name.strip() or str(new_admin_id)
                except Exception:
                    name = str(new_admin_id)
                db.add_sub_admin(new_admin_id, name)
                # اطلاع به ادمین جدید
                try:
                    _bot.send_message(
                        new_admin_id,
                        "👮 <b>شما به عنوان ادمین اضافه شدید!</b>\n\n"
                        "برای دسترسی به پنل مدیریت دستور /subadmin را ارسال کنید."
                    )
                except Exception:
                    pass
                _bot.reply_to(message,
                    f"✅ <b>{name}</b> (<code>{new_admin_id}</code>) به عنوان ادمین اضافه شد.",
                    reply_markup=_owner_keyboard())
                _owner_states.pop(message.from_user.id, None)

            elif state == "welcome_edit_text":
                # ذخیره متن خوش‌آمد جدید
                new_text = message.text or ""
                if not new_text.strip():
                    return _bot.reply_to(message, "❌ متن نمی‌تواند خالی باشد:")
                # اعتبارسنجی متغیرها
                try:
                    new_text.format(
                        name="test", name_full="test", mention="test",
                        tag="test", tg_id=0, time="test",
                        balance=0, total_earned=0, sub_status="test"
                    )
                except KeyError as e:
                    return _bot.reply_to(message,
                        f"❌ متغیر نامعتبر: <code>{e}</code>\n\n"
                        "متغیرهای مجاز:\n"
                        "<code>{name}</code>  <code>{name_full}</code>  <code>{mention}</code>\n"
                        "<code>{tag}</code>  <code>{tg_id}</code>  <code>{time}</code>\n"
                        "<code>{balance}</code>  <code>{total_earned}</code>  <code>{sub_status}</code>")
                db.set_global_setting("welcome_text", new_text)
                _owner_states.pop(message.from_user.id, None)
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("🔙 تنظیمات خوش‌آمد", callback_data="admin_welcome_settings", style="primary"))
                _bot.reply_to(message, "✅ متن خوش‌آمد ذخیره شد!", reply_markup=markup)

            elif state == "welcome_edit_photo":
                # ذخیره عکس خوش‌آمد
                photo_id = None
                if message.photo:
                    photo_id = message.photo[-1].file_id
                elif message.document and message.document.mime_type and message.document.mime_type.startswith("image"):
                    photo_id = message.document.file_id
                else:
                    return _bot.reply_to(message, "❌ لطفاً یک عکس ارسال کنید:")
                db.set_global_setting("welcome_photo_id", photo_id)
                _owner_states.pop(message.from_user.id, None)
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("🔙 تنظیمات خوش‌آمد", callback_data="admin_welcome_settings", style="primary"))
                _bot.reply_to(message, "✅ عکس خوش‌آمد ذخیره شد!", reply_markup=markup)

            elif state == "guide_name":
                # مرحله ۱: دریافت اسم آموزش
                guide_name = text.strip()
                if not guide_name:
                    return _bot.reply_to(message, "❌ اسم آموزش نمی‌تواند خالی باشد:")
                state_data["data"]["name"] = guide_name
                state_data["state"] = "guide_type"
                markup = types.InlineKeyboardMarkup(row_width=1)
                markup.add(
                    types.InlineKeyboardButton("🎥 ارسال آموزش تصویری (ویدیو/عکس)", callback_data="guide_type_media", style="primary"),
                    types.InlineKeyboardButton("📝 ارسال آموزش متنی", callback_data="guide_type_text", style="success")
                )
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="admin_guide_manage", style="danger", icon_custom_emoji_id="5832353674281620438"))
                _bot.reply_to(message,
                    f"✅ اسم آموزش: <b>{guide_name}</b>\n\n"
                    "نوع آموزش را انتخاب کنید:",
                    reply_markup=markup)

            elif state == "guide_send_media":
                # مرحله ۳: دریافت ویدیو یا عکس
                import json as _json
                file_id = None
                media_type = None
                if message.video:
                    file_id = message.video.file_id
                    media_type = "video"
                elif message.photo:
                    file_id = message.photo[-1].file_id
                    media_type = "photo"
                elif message.document and message.document.mime_type and message.document.mime_type.startswith("video"):
                    file_id = message.document.file_id
                    media_type = "video"
                else:
                    return _bot.reply_to(message, "❌ لطفاً یک ویدیو یا عکس ارسال کنید:")

                # ذخیره به پیوی مالک به عنوان دیتابیس
                try:
                    fwd = _bot.forward_message(OWNER_TG_ID, message.chat.id, message.message_id)
                    stored_msg_id = fwd.message_id
                except Exception:
                    stored_msg_id = None

                guide_name = state_data["data"]["name"]
                raw = db.get_global_setting("guide_list", "[]")
                try:
                    guides = _json.loads(raw)
                except Exception:
                    guides = []
                guides.append({
                    "name": guide_name,
                    "type": media_type,
                    "file_id": file_id,
                    "stored_msg_id": stored_msg_id
                })
                db.set_global_setting("guide_list", _json.dumps(guides, ensure_ascii=False))
                _owner_states.pop(message.from_user.id, None)
                _bot.reply_to(message,
                    f"✅ آموزش «<b>{guide_name}</b>» ({'ویدیو' if media_type == 'video' else 'عکس'}) ذخیره شد.",
                    reply_markup=_owner_keyboard())

            elif state == "guide_send_text":
                # مرحله ۳: دریافت متن آموزش
                import json as _json
                content = message.text or ""
                if not content.strip():
                    return _bot.reply_to(message, "❌ متن آموزش نمی‌تواند خالی باشد:")
                guide_name = state_data["data"]["name"]
                raw = db.get_global_setting("guide_list", "[]")
                try:
                    guides = _json.loads(raw)
                except Exception:
                    guides = []
                guides.append({
                    "name": guide_name,
                    "type": "text",
                    "content": content,
                    "file_id": None
                })
                db.set_global_setting("guide_list", _json.dumps(guides, ensure_ascii=False))
                _owner_states.pop(message.from_user.id, None)
                _bot.reply_to(message,
                    f"✅ آموزش متنی «<b>{guide_name}</b>» ذخیره شد.",
                    reply_markup=_owner_keyboard())

            # ── قرعه‌کشی states ─────────────────────────────────────────────
            elif state == "lottery_start_time":
                import re as _re
                t = text.strip()
                if not _re.match(r"^\d{1,2}:\d{2}$", t):
                    return _bot.reply_to(message, "❌ فرمت اشتباه است.\nمثال: <code>22:00</code>")
                h, m = map(int, t.split(":"))
                if not (0 <= h <= 23 and 0 <= m <= 59):
                    return _bot.reply_to(message, "❌ ساعت نامعتبر است.")
                state_data["data"]["start_time"] = t
                state_data["state"] = "lottery_end_time"
                _bot.reply_to(message,
                    f"✅ ساعت شروع: <b>{t}</b>\n\n"
                    "📝 <b>مرحله ۲ از ۵: ساعت پایان</b>\n\n"
                    "ساعت پایان قرعه‌کشی را ارسال کنید:\n"
                    "مثال: <code>23:00</code>")

            elif state == "lottery_end_time":
                import re as _re
                t = text.strip()
                if not _re.match(r"^\d{1,2}:\d{2}$", t):
                    return _bot.reply_to(message, "❌ فرمت اشتباه است.\nمثال: <code>23:00</code>")
                h, m = map(int, t.split(":"))
                if not (0 <= h <= 23 and 0 <= m <= 59):
                    return _bot.reply_to(message, "❌ ساعت نامعتبر است.")
                state_data["data"]["end_time"] = t
                state_data["state"] = "lottery_winners_count"
                _bot.reply_to(message,
                    f"✅ ساعت پایان: <b>{t}</b>\n\n"
                    "📝 <b>مرحله ۳ از ۵: تعداد برنده</b>\n\n"
                    "تعداد برندگان را وارد کنید:\n"
                    "مثال: <code>3</code>")

            elif state == "lottery_winners_count":
                if not text.isdigit() or int(text) < 1 or int(text) > 20:
                    return _bot.reply_to(message, "❌ عدد معتبر بین ۱ تا ۲۰ وارد کنید.")
                cnt = int(text)
                state_data["data"]["winners_count"] = cnt
                state_data["data"]["prizes"] = []
                state_data["data"]["prize_details"] = []
                state_data["data"]["current_prize"] = 1
                state_data["state"] = "lottery_prize_choose"
                _bot.reply_to(
                    message,
                    f"✅ تعداد برنده: <b>{cnt} نفر</b>\n\n"
                    "📝 <b>مرحله ۴: تعیین جایزه نفر اول</b>\n\n"
                    "نوع جایزه را انتخاب کنید:",
                    reply_markup=_lottery_prize_type_markup()
                )

            elif state == "lottery_diamond_amount":
                if not text.isdigit() or int(text) < 1:
                    return _bot.reply_to(message, "❌ یک عدد معتبر (بیشتر از صفر) وارد کنید.\nمثال: <code>10</code>")
                amount = int(text)
                prize_detail = {"type": "diamond", "amount": amount, "label": f"{amount} 💎 الماس"}
                sent_text, markup = _lottery_advance_prize(state_data, prize_detail)
                _bot.reply_to(message, sent_text, reply_markup=markup)
        
        except Exception as e:
            print(f"❌ خطا در handle_owner_state: {e}")
            _bot.reply_to(message, f"❌ خطا: {e}", reply_markup=_owner_keyboard())
            _owner_states.pop(message.from_user.id, None)

    # ══════════════════════════════════════════════════════════════════════════
    # دستورات متنی قدیمی
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.message_handler(commands=["addchannel", "removechannel", "give", "users", "wc_create", "wc_winner", "transfer"])
    def cmd_text_commands(message):
        if message.from_user.id != OWNER_TG_ID:
            return
        _bot.reply_to(message, 
            "📢 تمام دستورات مدیریتی به پنل دکمه‌ای منتقل شدند.\n\n"
            "روی دکمه <b>📢 مدیریت</b> کلیک کنید.",
            reply_markup=_owner_keyboard())

    # ══════════════════════════════════════════════════════════════════════════
    # 👮 پنل ادمین فرعی
    # ══════════════════════════════════════════════════════════════════════════
    def _subadmin_panel_keyboard():
        """کیبورد پنل ادمین فرعی با دسترسی‌های محدود"""
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton(" کاربران", callback_data="sa_users", style="primary", ),
            types.InlineKeyboardButton("📅 بازی‌های امروز", callback_data="sa_today_games", style="primary")
        )
        markup.add(
            types.InlineKeyboardButton("📣 پیام عمومی", callback_data="sa_broadcast", style="primary"),
            types.InlineKeyboardButton("🎯 ماموریت‌ها", callback_data="sa_missions", style="success")
        )
        markup.add(
            types.InlineKeyboardButton("👥 شرکت‌کنندگان جام جهانی", callback_data="sa_wc_participants", style="primary")
        )
        markup.add(
            types.InlineKeyboardButton("🎁 هدیه", callback_data="sa_gift", style="success")
        )
        markup.add(
            types.InlineKeyboardButton("📚 مدیریت راهنما", callback_data="sa_guide_manage", style="primary")
        )
        return markup

    # state مخصوص ادمین‌های فرعی
    _subadmin_states = {}

    @_bot.message_handler(commands=["subadmin"], chat_types=['private'])
    def cmd_subadmin_panel(message):
        tg_id = message.from_user.id
        if not db.is_sub_admin(tg_id):
            return _bot.reply_to(message, "❌ شما دسترسی ادمین ندارید.")
        _bot.reply_to(message,
            "👮 <b>پنل مدیریت ادمین</b>\n\nیکی از گزینه‌های زیر را انتخاب کنید:",
            reply_markup=_subadmin_panel_keyboard())

    @_bot.callback_query_handler(func=lambda call: call.data.startswith("sa_"))
    def callback_subadmin(call):
        tg_id = call.from_user.id
        if not db.is_sub_admin(tg_id):
            return _bot.answer_callback_query(call.id, "❌ دسترسی ندارید", show_alert=True)

        data = call.data

        try:
            # ── کاربران ──────────────────────────────────────────────────────
            if data == "sa_users":
                accounts = db.get_all_accounts()
                lines = [f"👥 <b>لیست کاربران ({len(accounts)} نفر):</b>\n"]
                for a in accounts[:30]:
                    uname = a.get("username", "-")
                    lines.append(f"• @{uname}")
                if len(accounts) > 30:
                    lines.append(f"\n... و {len(accounts)-30} نفر دیگر")
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="sa_back", style="danger"))
                _bot.edit_message_text("\n".join(lines),
                    chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
                _bot.answer_callback_query(call.id)

            # ── بازی‌های امروز ───────────────────────────────────────────────
            elif data == "sa_today_games":
                today = _now_tehran().strftime("%Y-%m-%d")
                challenges = db.get_today_challenges(today) if hasattr(db, 'get_today_challenges') else []
                if not challenges:
                    text = "📅 <b>بازی‌های امروز</b>\n\nهیچ بازی‌ای برای امروز ثبت نشده."
                else:
                    lines = [f"📅 <b>بازی‌های امروز ({len(challenges)} بازی):</b>\n"]
                    for c in challenges:
                        lines.append(f"⚽ {c.get('team1','-')} vs {c.get('team2','-')} — {c.get('time','-')}")
                    text = "\n".join(lines)
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="sa_back", style="danger"))
                _bot.edit_message_text(text,
                    chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
                _bot.answer_callback_query(call.id)

            # ── پیام عمومی ───────────────────────────────────────────────────
            elif data == "sa_broadcast":
                _subadmin_states[tg_id] = {"state": "sa_broadcast_msg"}
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="sa_back", style="danger", icon_custom_emoji_id="5832353674281620438"))
                _bot.edit_message_text(
                    "📣 <b>ارسال پیام عمومی</b>\n\nپیام خود را ارسال کنید:",
                    chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
                _bot.answer_callback_query(call.id)

            # ── ماموریت‌ها ───────────────────────────────────────────────────
            elif data == "sa_missions":
                missions = db.get_active_missions()
                markup = types.InlineKeyboardMarkup(row_width=1)
                if missions:
                    text = "🎯 <b>ماموریت‌های فعال:</b>\n\n"
                    for m in missions:
                        text += f"🔸 {m['channel_username']} — 💎{m['reward']} الماس\n"
                        markup.add(types.InlineKeyboardButton(
                            f"❌ حذف {m['channel_username']}", callback_data=f"sa_del_mission_{m['id']}", style="danger"))
                else:
                    text = "📋 هیچ ماموریتی تعریف نشده.\n\n"
                markup.add(types.InlineKeyboardButton("➕ افزودن ماموریت", callback_data="sa_add_mission", style="success"))
                markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="sa_back", style="danger"))
                _bot.edit_message_text(text,
                    chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
                _bot.answer_callback_query(call.id)

            elif data == "sa_add_mission":
                _subadmin_states[tg_id] = {"state": "sa_mission_channel", "data": {}}
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="sa_missions", style="danger", icon_custom_emoji_id="5832353674281620438"))
                _bot.edit_message_text(
                    "🎯 <b>افزودن ماموریت</b>\n\nیوزرنیم کانال را ارسال کنید:\n\nمثال: <code>@mychannel</code>",
                    chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
                _bot.answer_callback_query(call.id)

            elif data.startswith("sa_del_mission_"):
                mission_id = int(data[len("sa_del_mission_"):])
                db.remove_mission(mission_id)
                _bot.answer_callback_query(call.id, "✅ ماموریت حذف شد")
                # refresh
                missions = db.get_active_missions()
                markup = types.InlineKeyboardMarkup(row_width=1)
                if missions:
                    text = "🎯 <b>ماموریت‌های فعال:</b>\n\n"
                    for m in missions:
                        text += f"🔸 {m['channel_username']} — 💎{m['reward']} الماس\n"
                        markup.add(types.InlineKeyboardButton(
                            f"❌ حذف {m['channel_username']}", callback_data=f"sa_del_mission_{m['id']}", style="danger"))
                else:
                    text = "📋 هیچ ماموریتی تعریف نشده.\n\n"
                markup.add(types.InlineKeyboardButton("➕ افزودن ماموریت", callback_data="sa_add_mission", style="success"))
                markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="sa_back", style="danger"))
                _bot.edit_message_text(text,
                    chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

            # ── شرکت‌کنندگان جام جهانی ──────────────────────────────────────
            elif data == "sa_wc_participants":
                participants = db.get_wc_participants()
                if not participants:
                    text = "📭 هیچ شرکت‌کننده‌ای ثبت نشده."
                else:
                    lines = [f"⚽️ <b>شرکت‌کنندگان جام جهانی ({len(participants)} نفر):</b>\n"]
                    for i, p in enumerate(participants[:50], 1):
                        uname = f"@{p['username']}"
                        lines.append(f"{i}. <b>{uname}</b> — 🎯{p['bet_count']} شرط | 💎{p['total_bet']} الماس")
                    text = "\n".join(lines)
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="sa_back", style="danger"))
                _bot.edit_message_text(text,
                    chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
                _bot.answer_callback_query(call.id)

            # ── هدیه ─────────────────────────────────────────────────────────
            elif data == "sa_gift":
                markup = types.InlineKeyboardMarkup(row_width=2)
                markup.add(
                    types.InlineKeyboardButton("💎 الماس", callback_data="sa_gift_diamond", style="primary"),
                    types.InlineKeyboardButton("📋 پنل", callback_data="sa_gift_panel", style="success")
                )
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="sa_back", style="danger", icon_custom_emoji_id="5832353674281620438"))
                _bot.edit_message_text(
                    "🎁 <b>هدیه به کاربر</b>\n\nنوع هدیه را انتخاب کنید:",
                    chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
                _bot.answer_callback_query(call.id)

            elif data == "sa_gift_diamond":
                _subadmin_states[tg_id] = {"state": "sa_gift_diamond_amount", "data": {"gift_type": "diamond"}}
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="sa_back", style="danger", icon_custom_emoji_id="5832353674281620438"))
                _bot.edit_message_text(
                    "💎 <b>هدیه الماس</b>\n\nتعداد الماس هدیه را وارد کنید:",
                    chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
                _bot.answer_callback_query(call.id)

            elif data == "sa_gift_panel":
                markup = types.InlineKeyboardMarkup(row_width=1)
                markup.add(
                    types.InlineKeyboardButton("📅 پنل یک ماهه (30 روز)", callback_data="sa_gift_plan_30", style="primary"),
                    types.InlineKeyboardButton("📅 پنل یک هفته‌ای (7 روز)", callback_data="sa_gift_plan_7", style="primary"),
                    types.InlineKeyboardButton("📅 پنل یک روزه (1 روز)", callback_data="sa_gift_plan_1", style="primary")
                )
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="sa_back", style="danger", icon_custom_emoji_id="5832353674281620438"))
                _bot.edit_message_text(
                    "📋 <b>هدیه پنل</b>\n\nنوع پنل هدیه را انتخاب کنید:",
                    chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
                _bot.answer_callback_query(call.id)

            elif data.startswith("sa_gift_plan_"):
                days = int(data.split("_")[-1])
                plan_names = {30: "یک ماهه", 7: "یک هفته‌ای", 1: "یک روزه"}
                plan_label = plan_names.get(days, f"{days} روزه")
                _subadmin_states[tg_id] = {
                    "state": "sa_gift_tg_id",
                    "data": {"gift_type": "panel", "days": days, "plan_label": plan_label}
                }
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="sa_back", style="danger", icon_custom_emoji_id="5832353674281620438"))
                _bot.edit_message_text(
                    f"📋 <b>پنل {plan_label}</b>\n\nایدی عددی تلگرام کاربر را وارد کنید:",
                    chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
                _bot.answer_callback_query(call.id)

            elif data.startswith("sa_gift_confirm_"):
                gift_key = data[len("sa_gift_confirm_"):]
                gift_info = _subadmin_states.get(tg_id, {}).get("gift_pending")
                if not gift_info or gift_info.get("key") != gift_key:
                    _bot.answer_callback_query(call.id, "❌ اطلاعات منقضی شده.", show_alert=True)
                    return
                target_tg_id = gift_info["tg_id"]
                account = gift_info["account"]
                gift_type = gift_info["gift_type"]
                if gift_type == "diamond":
                    amount = gift_info["amount"]
                    db.add_tokens(account["id"], amount)
                    new_balance = db.get_token_balance(account["id"])
                    try:
                        _bot.send_message(target_tg_id,
                            f"{EM.EMOJI_DAILY_GIFT} <b>تبریک! شما از طرف مالک هدیه گرفتید!</b>\n\n"
                            f"🎊 مشخصات هدیه:\n💎 <b>الماس هدیه:</b> {amount} الماس\n"
                            f"💰 <b>موجودی جدید:</b> {new_balance} الماس")
                    except Exception:
                        pass
                    result_text = f"✅ <b>{amount} الماس</b> به <b>{account['username']}</b> هدیه داده شد."
                else:
                    days = gift_info["days"]
                    plan_label = gift_info["plan_label"]
                    db.set_subscription(account["id"], "gift", days)
                    try:
                        _bot.send_message(target_tg_id,
                            f"{EM.EMOJI_DAILY_GIFT} <b>تبریک! شما از طرف مالک هدیه گرفتید!</b>\n\n"
                            f"🎊 مشخصات هدیه:\n📋 <b>پنل هدیه:</b> {plan_label} ({days} روز)")
                    except Exception:
                        pass
                    result_text = f"✅ پنل <b>{plan_label}</b> به <b>{account['username']}</b> هدیه داده شد."
                _subadmin_states.pop(tg_id, None)
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("🔙 پنل مدیریت", callback_data="sa_back", style="primary"))
                _bot.edit_message_text(result_text,
                    chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
                _bot.answer_callback_query(call.id, "✅ هدیه ارسال شد!")

            elif data == "sa_gift_cancel":
                _subadmin_states.pop(tg_id, None)
                markup = _subadmin_panel_keyboard()
                _bot.edit_message_text("❌ عملیات هدیه لغو شد.",
                    chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
                _bot.answer_callback_query(call.id)

            # ── بازگشت ───────────────────────────────────────────────────────
            elif data == "sa_back":
                _subadmin_states.pop(tg_id, None)
                _bot.edit_message_text(
                    "👮 <b>پنل مدیریت ادمین</b>\n\nیکی از گزینه‌های زیر را انتخاب کنید:",
                    chat_id=call.message.chat.id, message_id=call.message.message_id,
                    reply_markup=_subadmin_panel_keyboard())
                _bot.answer_callback_query(call.id)

            elif data == "sa_guide_manage":
                # ادمین فرعی مدیریت راهنما
                import json as _json
                raw = db.get_global_setting("guide_list", "[]")
                try:
                    guides = _json.loads(raw)
                except Exception:
                    guides = []
                markup = types.InlineKeyboardMarkup(row_width=1)
                if guides:
                    txt = f"📚 <b>راهنماهای ثبت‌شده ({len(guides)} آموزش):</b>\n\n"
                    for i, g in enumerate(guides):
                        txt += f"{'🎥' if g['type'] == 'video' else '🖼' if g['type'] == 'photo' else '📝'} {g['name']}\n"
                        markup.add(types.InlineKeyboardButton(
                            f"❌ حذف «{g['name']}»", callback_data=f"sa_guide_del_{i}", style="danger"))
                else:
                    txt = "📚 <b>مدیریت راهنما</b>\n\nهیچ آموزشی ثبت نشده."
                markup.add(types.InlineKeyboardButton("➕ اضافه کردن راهنما", callback_data="sa_guide_add", style="success"))
                markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="sa_back", style="danger"))
                _bot.edit_message_text(txt, chat_id=call.message.chat.id,
                    message_id=call.message.message_id, reply_markup=markup)
                _bot.answer_callback_query(call.id)

            elif data == "sa_guide_add":
                _owner_states[tg_id] = {"state": "guide_name", "data": {}, "is_subadmin": True}
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="sa_guide_manage", style="danger", icon_custom_emoji_id="5832353674281620438"))
                _bot.edit_message_text(
                    "📚 <b>افزودن راهنما</b>\n\nاسم آموزش را وارد کنید:",
                    chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
                _bot.answer_callback_query(call.id)

            elif data.startswith("sa_guide_del_"):
                import json as _json
                idx = int(data[len("sa_guide_del_"):])
                raw = db.get_global_setting("guide_list", "[]")
                try:
                    guides = _json.loads(raw)
                except Exception:
                    guides = []
                if 0 <= idx < len(guides):
                    guides.pop(idx)
                    db.set_global_setting("guide_list", _json.dumps(guides, ensure_ascii=False))
                markup = types.InlineKeyboardMarkup(row_width=1)
                if guides:
                    txt = f"📚 <b>راهنماهای ثبت‌شده ({len(guides)} آموزش):</b>\n\n"
                    for i, g in enumerate(guides):
                        txt += f"{'🎥' if g['type'] == 'video' else '🖼' if g['type'] == 'photo' else '📝'} {g['name']}\n"
                        markup.add(types.InlineKeyboardButton(
                            f"❌ حذف «{g['name']}»", callback_data=f"sa_guide_del_{i}", style="danger"))
                else:
                    txt = "📚 هیچ آموزشی ثبت نشده."
                markup.add(types.InlineKeyboardButton("➕ اضافه کردن راهنما", callback_data="sa_guide_add", style="success"))
                markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="sa_back", style="danger"))
                _bot.edit_message_text(txt, chat_id=call.message.chat.id,
                    message_id=call.message.message_id, reply_markup=markup)
                _bot.answer_callback_query(call.id, "✅ آموزش حذف شد")

        except Exception as e:
            print(f"❌ خطا در callback_subadmin: {e}")
            _bot.answer_callback_query(call.id, f"❌ خطا: {e}", show_alert=True)

    @_bot.message_handler(func=lambda m: m.from_user.id in _subadmin_states and m.chat.type == 'private',
                          content_types=["text", "photo", "document"])
    def handle_subadmin_state(message):
        tg_id = message.from_user.id
        if not db.is_sub_admin(tg_id):
            return
        try:
            state_data = _subadmin_states[tg_id]
            state = state_data["state"]
            text = (message.text or "").strip()

            if state == "sa_broadcast_msg":
                _subadmin_states.pop(tg_id, None)
                tg_ids = db.get_all_telegram_ids()
                _bot.reply_to(message, f"⏳ در حال ارسال به {len(tg_ids)} کاربر...")
                sent, failed = 0, 0
                for tid in tg_ids:
                    try:
                        if message.photo:
                            _bot.send_photo(tid, message.photo[-1].file_id, caption=message.caption or "")
                        elif message.document:
                            _bot.send_document(tid, message.document.file_id, caption=message.caption or "")
                        else:
                            _bot.send_message(tid, message.text)
                        sent += 1
                    except Exception:
                        failed += 1
                _bot.reply_to(message, f"✅ ارسال تمام شد!\n📤 موفق: {sent}\n❌ خطا: {failed}",
                    reply_markup=_subadmin_panel_keyboard())

            elif state == "sa_mission_channel":
                ch = text.strip()
                if not ch.startswith("@"):
                    ch = "@" + ch
                state_data["data"]["channel"] = ch
                state_data["state"] = "sa_mission_reward"
                _bot.reply_to(message, f"✅ کانال: <b>{ch}</b>\n\n💎 مقدار جایزه (الماس) را وارد کنید:")

            elif state == "sa_mission_reward":
                try:
                    reward = int(text.strip())
                    if reward < 1:
                        return _bot.reply_to(message, "❌ جایزه باید بیشتر از ۰ باشد.")
                except ValueError:
                    return _bot.reply_to(message, "❌ مقدار باید عدد باشد.")
                ch = state_data.get("data", {}).get("channel")
                db.add_mission(ch, reward)
                _subadmin_states.pop(tg_id, None)
                _bot.reply_to(message, f"✅ ماموریت اضافه شد!\n🔸 {ch} — 💎{reward} الماس",
                    reply_markup=_subadmin_panel_keyboard())

            elif state == "sa_gift_diamond_amount":
                try:
                    amount = int(text)
                    if amount <= 0:
                        return _bot.reply_to(message, "❌ تعداد باید بیشتر از صفر باشد:")
                except ValueError:
                    return _bot.reply_to(message, "❌ عدد معتبر وارد کنید:")
                state_data["data"]["amount"] = amount
                state_data["state"] = "sa_gift_tg_id"
                _bot.reply_to(message,
                    f"{EM.EMOJI_DIAMONDS} <b>هدیه الماس: {amount} الماس</b>\n\nایدی عددی تلگرام کاربر را وارد کنید:")

            elif state == "sa_gift_tg_id":
                try:
                    target_tg_id = int(text)
                except ValueError:
                    return _bot.reply_to(message, "❌ ایدی عددی باید فقط شامل اعداد باشد:")
                account = db.get_account_by_tg_id(target_tg_id)
                if not account:
                    return _bot.reply_to(message, "❌ کاربری با این ایدی یافت نشد.")
                gift_type = state_data["data"]["gift_type"]
                balance = db.get_token_balance(account["id"])
                sub = db.get_subscription(account["id"])
                plan_remaining = sub["end_date"] if sub and sub.get("end_date") else "ندارد"
                if gift_type == "diamond":
                    amount = state_data["data"]["amount"]
                    gift_desc = f"💎 {amount} الماس"
                else:
                    days = state_data["data"]["days"]
                    plan_label = state_data["data"]["plan_label"]
                    gift_desc = f"📋 پنل {plan_label} ({days} روز)"
                import hashlib, time as _time
                gift_key = hashlib.md5(f"{target_tg_id}{_time.time()}".encode()).hexdigest()[:8]
                state_data["gift_pending"] = {
                    "key": gift_key, "tg_id": target_tg_id, "account": account,
                    "gift_type": gift_type,
                    "amount": state_data["data"].get("amount"),
                    "days": state_data["data"].get("days"),
                    "plan_label": state_data["data"].get("plan_label"),
                }
                state_data["state"] = "sa_gift_awaiting_confirm"
                confirm_text = (
                    f"{EM.EMOJI_DAILY_GIFT} <b>تایید هدیه</b>\n\n"
                    f"👤 <b>کاربر:</b> {account.get('username', 'نامشخص')}\n"
                    f"🔢 <b>ایدی عددی:</b> <code>{target_tg_id}</code>\n"
                    f"📋 <b>پلن باقی‌مانده:</b> {plan_remaining}\n"
                    f"💰 <b>موجودی:</b> {balance} الماس\n"
                    f"{EM.EMOJI_DAILY_GIFT} <b>{gift_desc} هدیه</b>\n\nآیا تایید می‌کنید؟"
                )
                markup = types.InlineKeyboardMarkup(row_width=2)
                markup.add(
                    types.InlineKeyboardButton("✅ تایید", callback_data=f"sa_gift_confirm_{gift_key}", style="success", icon_custom_emoji_id="5830326445422940546"),
                    types.InlineKeyboardButton("❌ لغو", callback_data="sa_gift_cancel", style="danger", icon_custom_emoji_id="5832353674281620438")
                )
                _bot.reply_to(message, confirm_text, reply_markup=markup)

            elif state == "sa_gift_awaiting_confirm":
                _bot.reply_to(message, "⏳ لطفاً روی دکمه تایید یا لغو کلیک کنید.")

        except Exception as e:
            print(f"❌ خطا در handle_subadmin_state: {e}")
            _bot.reply_to(message, f"❌ خطا: {e}")
            _subadmin_states.pop(tg_id, None)

    # ══════════════════════════════════════════════════════════════════════════
    # 🎯 سیستم ماموریت‌ها
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.message_handler(func=lambda m: m.text == "🎯 ماموریت‌ها", chat_types=['private'])
    def cmd_missions(message):
        try:
            if not require_membership(message):
                return
            _do_missions(message.from_user.id, message.chat.id)
        except Exception as e:
            print(f"❌ خطا در cmd_missions: {e}")

    @_bot.callback_query_handler(func=lambda call: call.data == "menu_missions")
    def callback_menu_missions(call):
        _bot.answer_callback_query(call.id)
        _do_missions(call.from_user.id, call.message.chat.id)

    # ══════════════════════════════════════════════════════════════════════════
    # 📖 سیستم راهنما
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.message_handler(func=lambda m: m.text == "📖 راهنما", chat_types=['private'])
    def cmd_guide(message):
        _show_guide_menu(message.from_user.id, message.chat.id)

    @_bot.callback_query_handler(func=lambda call: call.data == "guide_menu" or call.data.startswith("guide_view_"))
    def callback_guide(call):
        _bot.answer_callback_query(call.id)
        tg_id = call.from_user.id
        chat_id = call.message.chat.id
        data = call.data

        if data == "guide_menu":
            _show_guide_menu(tg_id, chat_id)
        elif data.startswith("guide_view_"):
            import json as _json
            idx = int(data[len("guide_view_"):])
            raw = db.get_global_setting("guide_list", "[]")
            try:
                guides = _json.loads(raw)
            except Exception:
                guides = []
            if idx < 0 or idx >= len(guides):
                return _bot.send_message(chat_id, "❌ این آموزش یافت نشد.")
            g = guides[idx]
            back_markup = types.InlineKeyboardMarkup()
            back_markup.add(types.InlineKeyboardButton("🔙 بازگشت به راهنما", callback_data="guide_menu", style="primary"))
            try:
                if g["type"] == "video":
                    _bot.send_video(chat_id, g["file_id"],
                        caption=f"🎥 <b>{g['name']}</b>", reply_markup=back_markup)
                elif g["type"] == "photo":
                    _bot.send_photo(chat_id, g["file_id"],
                        caption=f"🖼 <b>{g['name']}</b>", reply_markup=back_markup)
                else:
                    _bot.send_message(chat_id,
                        f"📝 <b>{g['name']}</b>\n\n{g.get('content', '')}",
                        reply_markup=back_markup)
            except Exception as e:
                _bot.send_message(chat_id, f"❌ خطا در نمایش آموزش: {e}")

    def _show_guide_menu(tg_id, chat_id):
        import json as _json
        raw = db.get_global_setting("guide_list", "[]")
        try:
            guides = _json.loads(raw)
        except Exception:
            guides = []
        if not guides:
            account = _get_account_cached(tg_id)
            return _bot.send_message(chat_id,
                "📚 <b>راهنما</b>\n\nهنوز هیچ آموزشی اضافه نشده.",
                reply_markup=_main_inline_keyboard(account))
        markup = types.InlineKeyboardMarkup(row_width=1)
        for i, g in enumerate(guides):
            icon = "🎥" if g["type"] == "video" else "🖼" if g["type"] == "photo" else "📝"
            markup.add(types.InlineKeyboardButton(
                f"{icon} {g['name']}", callback_data=f"guide_view_{i}", style="primary"))
        account = _get_account_cached(tg_id)
        if account:
            markup.add(types.InlineKeyboardButton("🔙 منوی اصلی", callback_data="back_main", style="danger"))
        _bot.send_message(chat_id,
            f"📚 <b>راهنما</b>\n\n{len(guides)} آموزش موجود است. یکی را انتخاب کنید:",
            reply_markup=markup)

    def _do_missions(tg_id, chat_id):
        try:
            account = _get_account_cached(tg_id)
            if not account:
                return _bot.send_message(chat_id, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.", reply_markup=_main_inline_keyboard())

            missions = db.get_active_missions()
            if not missions:
                return _bot.send_message(chat_id, "📭 در حال حاضر ماموریت فعالی وجود ندارد.", reply_markup=_main_inline_keyboard(account))

            completed_ids = db.get_completed_mission_ids(account["id"])
            markup = types.InlineKeyboardMarkup(row_width=1)
            lines = ["🎯 <b>ماموریت‌ها</b>\n\nبرای دریافت جایزه، در کانال‌های زیر عضو شوید:\n"]
            for m in missions:
                done = m["id"] in completed_ids
                status = "✅" if done else "⏳"
                ch_clean = m["channel_username"].lstrip("@")
                lines.append(f"{status} {m['channel_username']} — 💎{m['reward']} الماس")
                if not done:
                    # 🔵 دکمه عضویت با رنگ primary (آبی)
                    markup.add(types.InlineKeyboardButton(
                        f"🔗 عضویت در {m['channel_username']}",
                        url=f"https://t.me/{ch_clean}",
                        style="primary"
                    ))
            # 🟢 دکمه بررسی با رنگ success (سبز)
            markup.add(types.InlineKeyboardButton("✅ بررسی و دریافت جایزه", callback_data="check_missions", style="success", icon_custom_emoji_id="5830326445422940546"))
            _bot.send_message(chat_id, "\n".join(lines), reply_markup=markup)
        except Exception as e:
            print(f"❌ خطا در _do_missions: {e}")
    @_bot.callback_query_handler(func=lambda call: call.data == "check_missions")
    def callback_check_missions(call):
        try:
            account = _get_account_cached(call.from_user.id)
            if not account:
                return _bot.answer_callback_query(call.id, "❌ ابتدا در پنل وب ثبت‌نام کنید.", show_alert=True)

            missions = db.get_active_missions()
            completed_ids = db.get_completed_mission_ids(account["id"])
            total_reward = 0
            newly_done = []

            for m in missions:
                if m["id"] in completed_ids:
                    continue
                ch = m["channel_username"]
                try:
                    member = _bot.get_chat_member(ch, call.from_user.id)
                    if member.status in ("member", "administrator", "creator"):
                        if db.complete_mission(account["id"], m["id"], m["reward"]):
                            total_reward += m["reward"]
                            newly_done.append(ch)
                except Exception:
                    pass  # کانال پیدا نشد یا خطا

            if not newly_done:
                pending = [m["channel_username"] for m in missions if m["id"] not in completed_ids]
                if not pending:
                    return _bot.answer_callback_query(call.id, "✅ همه ماموریت‌ها قبلاً انجام شده!", show_alert=True)
                return _bot.answer_callback_query(call.id,
                    f"❌ ماموریت انجام نشده!\nابتدا در {len(pending)} کانال عضو شوید سپس دوباره بررسی کنید.",
                    show_alert=True)

            cache.invalidate(f"account_{call.from_user.id}")
            new_balance = db.get_token_balance(account["id"])
            _bot.answer_callback_query(call.id,
                f"🎉 تبریک! {len(newly_done)} ماموریت انجام شد!\n💎 +{total_reward} الماس دریافت کردید!",
                show_alert=True)
        except Exception as e:
            print(f"❌ خطا در callback_check_missions: {e}")
            _bot.answer_callback_query(call.id, f"❌ خطا: {str(e)[:80]}", show_alert=True)

    # ══════════════════════════════════════════════════════════════════════════
    # ✅ پیام‌های ناشناخته
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.message_handler(func=lambda m: True, chat_types=['private'])
    def cmd_unknown(message):
        try:
            account = _get_account_cached(message.from_user.id)
            if not account:
                return _bot.reply_to(message, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.", reply_markup=_main_inline_keyboard())
            
            kb = _owner_keyboard() if message.from_user.id == OWNER_TG_ID else _user_keyboard()
            _bot.reply_to(message, "⚠️ دستور نامعتبر. از دکمه‌های زیر استفاده کنید:", reply_markup=kb)
        except Exception as e:
            print(f"❌ خطا در cmd_unknown: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # 🃏 بازی حکم
    # ══════════════════════════════════════════════════════════════════════════

    # ── ساختار کارت‌ها ─────────────────────────────────────────────────────────
    _SUITS = {"♥️": "hearts", "♠️": "spades", "♦️": "diamonds", "♣️": "clubs"}
    _SUIT_NAMES = {
        "hearts":   "♥️ دل",
        "spades":   "♠️ پیک",
        "diamonds": "♦️ خشت",
        "clubs":    "♣️ گشنیز",
    }
    _RANKS = ["2","3","4","5","6","7","8","9","10","J","Q","K","A"]
    _RANK_VALUES = {r: i for i, r in enumerate(_RANKS)}
    _SUIT_EMOJI = {v: k for k, v in _SUITS.items()}

    # ── حافظه بازی‌ها ───────────────────────────────────────────────────────────
    _hokm_games: dict = {}   # chat_id(int) -> game_state(dict)
    _hokm_lock = threading.Lock()

    def _hokm_make_deck():
        deck = [{"suit": s, "rank": r}
                for s in _SUITS.values()
                for r in _RANKS]
        random.shuffle(deck)
        return deck

    def _hokm_card_label(card):
        return f"{_SUIT_EMOJI[card['suit']]}{card['rank']}"

    def _hokm_card_value(card, trump, lead):
        if card["suit"] == trump:
            return 100 + _RANK_VALUES[card["rank"]]
        if card["suit"] == lead:
            return 50  + _RANK_VALUES[card["rank"]]
        return _RANK_VALUES[card["rank"]]

    def _hokm_get(chat_id) -> Optional[dict]:
        """یک بازی فعال برای chat_id پیدا نمی‌کند — deprecated، از _hokm_get_by_id استفاده کنید"""
        with _hokm_lock:
            g = _hokm_games.get(chat_id)
            return g if g and g["state"] != "finished" else None

    def _hokm_get_by_id(game_id: str) -> Optional[dict]:
        """دریافت بازی بر اساس game_id"""
        with _hokm_lock:
            g = _hokm_games.get(game_id)
            return g if g and g["state"] != "finished" else None

    def _hokm_find_by_player(user_id):
        with _hokm_lock:
            for gid, g in _hokm_games.items():
                if user_id in g["players"] and g["state"] != "finished":
                    return gid, g
        return None, None

    def _hokm_lobby_text(g):
        players = g["players"]
        names = "\n".join(f"  • {g['names'].get(uid,'?')}" for uid in players)
        return (
            f"🎮 <b>بازی حکم</b>\n"
            f"👤 سازنده: {g['creator_name']}\n"
            f"💰 شرط: <b>{g['bet']} الماس</b>\n"
            f"👥 بازیکنان ({len(players)}/4):\n{names}\n\n"
            f"{'✅ آماده شروع! تایمر شروع شد...' if len(players)>=2 else '⏳ منتظر بازیکن دیگر...'}"
        )

    def _hokm_lobby_kb(game_id):
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("➕ ورود / خروج", callback_data=f"hokm_join_{game_id}"),
            types.InlineKeyboardButton("❌ لغو",         callback_data=f"hokm_cancel_{game_id}", icon_custom_emoji_id="5832353674281620438"),
        )
        return kb

    # ── دستور شروع بازی در گروه ─────────────────────────────────────────────
    @_bot.message_handler(
        func=lambda m: m.text and re.match(r'^حکم\s+\d+$', m.text.strip()),
        chat_types=['group','supergroup']
    )
    def cmd_hokm_start(message):
        try:
            if not _is_self_group(message.chat):
                return
            chat_id = message.chat.id
            user_id = message.from_user.id

            bet = int(message.text.strip().split()[1])
            if bet < 1:
                return _bot.reply_to(message, "❌ مبلغ شرط باید بیشتر از صفر باشد.")

            account = db.get_account_by_tg_id(user_id)
            if not account:
                return _bot.reply_to(message, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.")

            # جلوگیری از ورود یک کاربر به دو بازی همزمان
            _, existing = _hokm_find_by_player(user_id)
            if existing:
                return _bot.reply_to(message, "⚠️ شما در حال حاضر در یک بازی حکم هستید!")

            balance = db.get_token_balance(account["id"])
            if balance < bet:
                return _bot.reply_to(message,
                    f"❌ موجودی کافی ندارید!\nنیاز: {bet} الماس — موجودی: {balance} الماس")

            uname = message.from_user.username
            display = f"@{uname}" if uname else message.from_user.first_name
            game_id = f"{chat_id}_{user_id}_{int(time.time())}"

            game = {
                "game_id":     game_id,
                "chat_id":     chat_id,
                "creator_id":  user_id,
                "creator_name": display,
                "bet":         bet,
                "players":     [user_id],
                "names":       {user_id: display},
                "accounts":    {user_id: account["id"]},
                "state":       "lobby",
                "msg_id":      None,
                "timer":       None,
                "deck":        [],
                "hands":       {},
                "trump":       None,
                "hakem":       None,
                "teams":       {},
                "round_cards": {},
                "round_lead":  None,
                "lead_suit":   None,
                "tricks":      {0: 0, 1: 0},
                "total_tricks": 0,
                "turn_order":  [],
                "current_turn_idx": 0,
                "player_msg":  {},   # uid -> message_id پیام شخصی هر بازیکن در پیوی (برای ادیت به‌جای ارسال پیام جدید)
            }

            with _hokm_lock:
                _hokm_games[game_id] = game

            msg = _bot.send_message(
                chat_id,
                _hokm_lobby_text(game),
                parse_mode="HTML",
                reply_markup=_hokm_lobby_kb(game_id)
            )
            game["msg_id"] = msg.message_id

        except Exception as e:
            print(f"❌ cmd_hokm_start: {e}")

    # ── ورود / خروج ─────────────────────────────────────────────────────────
    @_bot.callback_query_handler(func=lambda c: c.data.startswith("hokm_join_"))
    def callback_hokm_join(call):
        try:
            game_id = call.data[len("hokm_join_"):]
            chat_id = call.message.chat.id
            user_id = call.from_user.id
            game = _hokm_get_by_id(game_id)

            if not game:
                return _bot.answer_callback_query(call.id, "❌ بازی یافت نشد یا تمام شده.")

            if game["state"] != "lobby":
                return _bot.answer_callback_query(call.id, "⏳ بازی در حال اجراست.")

            account = db.get_account_by_tg_id(user_id)
            if not account:
                return _bot.answer_callback_query(call.id, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.", show_alert=True)

            # خروج
            if user_id in game["players"]:
                if user_id == game["creator_id"]:
                    return _bot.answer_callback_query(call.id, "سازنده نمی‌تواند خروج بزند. از لغو استفاده کنید.", show_alert=True)
                game["players"].remove(user_id)
                game["names"].pop(user_id, None)
                game["accounts"].pop(user_id, None)
                _bot.answer_callback_query(call.id, "✅ از بازی خارج شدید.")
            else:
                # ورود
                if len(game["players"]) >= 4:
                    return _bot.answer_callback_query(call.id, "❌ بازی پر است.")
                balance = db.get_token_balance(account["id"])
                if balance < game["bet"]:
                    return _bot.answer_callback_query(call.id,
                        f"❌ موجودی کافی ندارید! نیاز: {game['bet']} الماس", show_alert=True)
                uname = call.from_user.username
                display = f"@{uname}" if uname else call.from_user.first_name
                game["players"].append(user_id)
                game["names"][user_id] = display
                game["accounts"][user_id] = account["id"]
                _bot.answer_callback_query(call.id, "✅ وارد بازی شدید!")

                # وقتی نفر دوم آمد → تایمر ۱۰ ثانیه
                if len(game["players"]) == 2:
                    def _lobby_timer():
                        time.sleep(10)
                        g = _hokm_get_by_id(game_id)
                        if g and g["state"] == "lobby" and len(g["players"]) >= 2:
                            _hokm_begin(game_id)
                    t = threading.Thread(target=_lobby_timer, daemon=True)
                    t.start()
                    game["timer"] = t

                # وقتی ۴ نفر کامل شد → فوری شروع
                if len(game["players"]) == 4:
                    if game.get("timer"):
                        game["timer"] = None  # کنسل منطقی
                    _hokm_begin(game_id)
                    return

            # به‌روزرسانی پیام لابی
            try:
                _bot.edit_message_text(
                    _hokm_lobby_text(game),
                    chat_id, game["msg_id"],
                    parse_mode="HTML",
                    reply_markup=_hokm_lobby_kb(game_id)
                )
            except Exception:
                pass

        except Exception as e:
            print(f"❌ callback_hokm_join: {e}")

    # ── لغو بازی ────────────────────────────────────────────────────────────
    @_bot.callback_query_handler(func=lambda c: c.data.startswith("hokm_cancel_"))
    def callback_hokm_cancel(call):
        try:
            game_id = call.data[len("hokm_cancel_"):]
            chat_id = call.message.chat.id
            game = _hokm_get_by_id(game_id)

            if not game:
                return _bot.answer_callback_query(call.id, "❌ بازی یافت نشد.")
            if call.from_user.id != game["creator_id"]:
                return _bot.answer_callback_query(call.id, "❌ فقط سازنده می‌تواند لغو کند.", show_alert=True)
            if game["state"] != "lobby":
                return _bot.answer_callback_query(call.id, "❌ بازی شروع شده، نمی‌توان لغو کرد.", show_alert=True)

            game["state"] = "finished"
            with _hokm_lock:
                _hokm_games.pop(game_id, None)

            _bot.edit_message_text(
                "❌ بازی حکم لغو شد.",
                chat_id, call.message.message_id
            )
            _bot.answer_callback_query(call.id, "✅ بازی لغو شد.")
        except Exception as e:
            print(f"❌ callback_hokm_cancel: {e}")

    # ── کمکی: پیام واحد هر بازیکن — فقط یک‌بار ارسال، بقیه موارد فقط ادیت می‌شود ──
    def _hokm_send_or_edit(uid, game, text, kb=None):
        """به‌جای ارسال پیام تازه در هر مرحله، یک پیام واحد برای هر بازیکن نگه می‌دارد و همان را ادیت می‌کند."""
        try:
            msg_id = game.get("player_msg", {}).get(uid)
            if msg_id:
                try:
                    _bot.edit_message_text(text, uid, msg_id, parse_mode="HTML", reply_markup=kb)
                    return
                except Exception as ex:
                    if "message is not modified" in str(ex).lower():
                        return
                    # پیام قبلی پاک شده یا قابل ادیت نبود → یک‌بار پیام جدید ارسال و ذخیره می‌شود
            msg = _bot.send_message(uid, text, parse_mode="HTML", reply_markup=kb)
            game.setdefault("player_msg", {})[uid] = msg.message_id
        except Exception as e:
            print(f"⚠️ _hokm_send_or_edit {uid}: {e}")

    def _hokm_status_lines(game, uid):
        """خطوط وضعیت کلی بازی برای پیام شخصی هر بازیکن"""
        lines = ["🎮 <b>بازی حکم</b>"]
        if game.get("trump"):
            lines.append(f"🔱 حکم: <b>{_SUIT_NAMES[game['trump']]}</b>")
        if game.get("state") == "playing":
            t0 = game["tricks"][0]
            t1 = game["tricks"][1]
            lines.append(f"📊 تیم حاکم: {t0} | تیم رقیب: {t1}")
            if game.get("round_cards"):
                played = " | ".join(
                    f"{game['names'][u]}: {_hokm_card_label(c)}"
                    for u, c in game["round_cards"].items()
                )
                lines.append(f"🃏 دست جاری: {played}")
            if game.get("turn_order"):
                current = game["turn_order"][game["current_turn_idx"]]
                who = "شما" if current == uid else game["names"].get(current, "?")
                lines.append(f"🎯 نوبت: <b>{who}</b>")
        return lines

    def _hokm_send_hand(uid, game, extra_lines=None, extra_rows=None):
        """نمایش/به‌روزرسانی پیام شخصی بازیکن: وضعیت بازی + کارت‌های دست (فقط نوع و عدد کارت، بدون ایموجی اضافه)"""
        try:
            lines = _hokm_status_lines(game, uid)
            if extra_lines:
                lines.append("")
                lines.extend(extra_lines)

            hand = game["hands"].get(uid, [])
            lines.append("")
            lines.append(f"🃏 کارت‌های شما ({len(hand)} کارت):")

            kb = types.InlineKeyboardMarkup(row_width=4)
            if extra_rows:
                for row in extra_rows:
                    kb.row(*row)
            card_btns = [
                types.InlineKeyboardButton(
                    _hokm_card_label(card),
                    callback_data=f"hokm_play_{game['chat_id']}_{uid}_{i}"
                )
                for i, card in enumerate(hand)
            ]
            if card_btns:
                kb.add(*card_btns)

            _hokm_send_or_edit(uid, game, "\n".join(lines), kb)
        except Exception as e:
            print(f"⚠️ _hokm_send_hand {uid}: {e}")

    def _hokm_broadcast_hands(game, extra_lines=None):
        """به‌روزرسانی پیام شخصی همه بازیکنان با ادیت (بدون ارسال پیام جدید)"""
        for uid in game["players"]:
            _hokm_send_hand(uid, game, extra_lines=extra_lines)

    # ── شروع بازی ────────────────────────────────────────────────────────────
    def _hokm_begin(game_id):
        try:
            game = _hokm_get_by_id(game_id)
            if not game or game["state"] != "lobby":
                return
            game["state"] = "determine_hakem"
            chat_id = game["chat_id"]

            players = game["players"]
            names_list = "\n".join(f"  • {game['names'][uid]}" for uid in players)

            # ویرایش پیام گروه
            try:
                _bot.edit_message_text(
                    f"✅ <b>بازی حکم شروع شد!</b>\n\n"
                    f"👥 بازیکنان:\n{names_list}\n\n"
                    f"📩 برای ادامه بازی به پیوی ربات مراجعه کنید.",
                    chat_id, game["msg_id"],
                    parse_mode="HTML"
                )
            except Exception:
                pass

            # کسر شرط از همه
            for uid in players:
                acc_id = game["accounts"][uid]
                db.deduct_tokens(acc_id, game["bet"])

            # تعیین حاکم (تک = Ace)
            deck = _hokm_make_deck()
            drawn = {}
            remaining_deck = deck[:]
            for uid in players:
                card = remaining_deck.pop(0)
                drawn[uid] = card

            ace_holders = [uid for uid in players if drawn[uid]["rank"] == "A"]
            if ace_holders:
                hakem = random.choice(ace_holders)
            else:
                hakem = max(players, key=lambda u: _RANK_VALUES[drawn[u]["rank"]])

            game["hakem"] = hakem
            game["deck"] = _hokm_make_deck()
            hakem_name = game["names"][hakem]

            # پیام شخصی هر بازیکن — فقط همین یک‌بار ارسال می‌شود؛ مراحل بعدی همین پیام را ادیت می‌کنند
            for uid in players:
                card_label = _hokm_card_label(drawn[uid])
                is_hakem = (uid == hakem)
                kb = types.InlineKeyboardMarkup()
                kb.add(types.InlineKeyboardButton(
                    "🃏 شروع بازی", callback_data=f"hokm_ready_{chat_id}"
                ))
                text = (
                    f"🎮 <b>بازی حکم شروع شد!</b>\n\n"
                    f"🎲 کارت قرعه شما: <b>{card_label}</b>\n"
                    f"👑 حاکم: <b>{hakem_name}</b>{'  ← شما!' if is_hakem else ''}\n\n"
                    f"برای ادامه دکمه زیر را بزنید:"
                )
                _hokm_send_or_edit(uid, game, text, kb)

        except Exception as e:
            print(f"❌ _hokm_begin: {e}")

    # ── دکمه شروع بازی در پیوی ───────────────────────────────────────────────
    _hokm_ready: dict = {}   # chat_id -> set of ready user_ids

    @_bot.callback_query_handler(func=lambda c: c.data.startswith("hokm_ready_"))
    def callback_hokm_ready(call):
        try:
            chat_id = int(call.data[len("hokm_ready_"):])
            user_id = call.from_user.id
            gid, game = _hokm_find_by_player(user_id)
            if not game:
                return _bot.answer_callback_query(call.id, "❌ بازی یافت نشد.")
            game_id = gid

            _hokm_ready.setdefault(chat_id, set()).add(user_id)
            _bot.answer_callback_query(call.id, "✅ آماده‌اید!")
            try:
                _bot.edit_message_text(
                    "✅ <b>آماده‌اید!</b>\nمنتظر بقیه بازیکنان بمانید...",
                    call.message.chat.id, call.message.message_id,
                    parse_mode="HTML"
                )
            except Exception:
                pass

            # همه آماده شدند → پخش ۴ کارت اولیه
            if _hokm_ready[chat_id] == set(game["players"]):
                _hokm_deal_initial(game_id)

        except Exception as e:
            print(f"❌ callback_hokm_ready: {e}")

    # ── پخش ۴ کارت اولیه و نمایش به هر بازیکن ───────────────────────────────
    def _hokm_deal_initial(game_id):
        try:
            game = _hokm_get_by_id(game_id)
            if not game:
                return
            game["state"] = "pick_trump"
            chat_id = game["chat_id"]
            deck = game["deck"]
            random.shuffle(deck)

            # ۴ کارت به هر بازیکن
            for uid in game["players"]:
                game["hands"][uid] = [deck.pop() for _ in range(4)]

            game["deck"] = deck
            hakem = game["hakem"]

            # همان پیام قبلی هر بازیکن ادیت می‌شود و کارت‌های اولیه نشان داده می‌شود
            for uid in game["players"]:
                if uid == hakem:
                    trump_rows = [
                        [
                            types.InlineKeyboardButton("♥️ دل", callback_data=f"hokm_trump_{chat_id}_hearts"),
                            types.InlineKeyboardButton("♠️ پیک", callback_data=f"hokm_trump_{chat_id}_spades"),
                        ],
                        [
                            types.InlineKeyboardButton("♦️ خشت", callback_data=f"hokm_trump_{chat_id}_diamonds"),
                            types.InlineKeyboardButton("♣️ گشنیز", callback_data=f"hokm_trump_{chat_id}_clubs"),
                        ],
                    ]
                    _hokm_send_hand(
                        uid, game,
                        extra_lines=["👑 شما حاکم هستید! حکم را انتخاب کنید:"],
                        extra_rows=trump_rows
                    )
                else:
                    _hokm_send_hand(uid, game, extra_lines=["⏳ منتظر انتخاب حکم توسط حاکم..."])

        except Exception as e:
            print(f"❌ _hokm_deal_initial: {e}")

    # ── انتخاب حکم توسط حاکم ────────────────────────────────────────────────
    @_bot.callback_query_handler(func=lambda c: c.data.startswith("hokm_trump_"))
    def callback_hokm_trump(call):
        try:
            parts = call.data.split("_")   # hokm_trump_CHATID_SUIT
            suit = parts[3]
            user_id = call.from_user.id
            game_id, game = _hokm_find_by_player(user_id)

            if not game:
                return _bot.answer_callback_query(call.id, "❌ بازی یافت نشد.")
            if user_id != game["hakem"]:
                return _bot.answer_callback_query(call.id, "❌ فقط حاکم می‌تواند حکم انتخاب کند.", show_alert=True)
            if game["state"] != "pick_trump":
                return _bot.answer_callback_query(call.id, "❌ زمان انتخاب حکم تمام شده.")

            game["trump"] = suit
            suit_label = _SUIT_NAMES[suit]
            _bot.answer_callback_query(call.id, f"✅ حکم {suit_label} انتخاب شد!")

            # پخش کامل کارت‌ها (پیام شخصی هر بازیکن همین‌جا ادیت می‌شود و حکم انتخابی را نشان می‌دهد)
            _hokm_deal_full(game_id)

        except Exception as e:
            print(f"❌ callback_hokm_trump: {e}")

    # ── پخش کامل کارت‌ها ─────────────────────────────────────────────────────
    def _hokm_deal_full(game_id):
        try:
            game = _hokm_get_by_id(game_id)
            if not game:
                return
            game["state"] = "playing"
            deck = game["deck"]
            random.shuffle(deck)

            players = game["players"]

            for uid in players:
                current = len(game["hands"].get(uid, []))
                need = 13 - current
                for _ in range(need):
                    if deck:
                        game["hands"][uid].append(deck.pop())

            game["deck"] = deck

            hakem = game["hakem"]
            idx_hakem = players.index(hakem)
            game["teams"] = {}
            for uid in players:
                if len(players) == 4:
                    if uid == hakem or players[(idx_hakem + 2) % 4] == uid:
                        game["teams"][uid] = 0
                    else:
                        game["teams"][uid] = 1
                else:
                    game["teams"][uid] = 0 if uid == hakem else 1

            game["turn_order"] = players[idx_hakem:] + players[:idx_hakem]
            game["current_turn_idx"] = 0
            game["round_cards"] = {}
            game["round_lead"] = game["turn_order"][0]
            game["lead_suit"] = None
            game["tricks"] = {0: 0, 1: 0}
            game["total_tricks"] = 0

            # همان پیام شخصی هر بازیکن ادیت می‌شود: دست کامل + وضعیت جدید بازی + نوبت
            _hokm_broadcast_hands(game)

        except Exception as e:
            print(f"❌ _hokm_deal_full: {e}")

    # ── انتخاب کارت (بازی) ───────────────────────────────────────────────────
    @_bot.callback_query_handler(func=lambda c: c.data.startswith("hokm_play_"))
    def callback_hokm_play(call):
        try:
            parts = call.data.split("_")  # hokm_play_CHATID_UID_IDX
            card_idx = int(parts[4])
            user_id = call.from_user.id
            game_id, game = _hokm_find_by_player(user_id)

            if not game:
                return _bot.answer_callback_query(call.id, "❌ بازی یافت نشد.")
            if game["state"] != "playing":
                return _bot.answer_callback_query(call.id, "⏳ بازی در حال راه‌اندازی است.")
            if user_id not in game["players"]:
                return _bot.answer_callback_query(call.id, "❌ شما در این بازی نیستید.")

            current_uid = game["turn_order"][game["current_turn_idx"]]
            if user_id != current_uid:
                return _bot.answer_callback_query(call.id, "⏳ نوبت شما نیست!", show_alert=True)

            hand = game["hands"].get(user_id, [])
            if card_idx >= len(hand):
                return _bot.answer_callback_query(call.id, "❌ کارت نامعتبر.")

            card = hand[card_idx]

            if game["lead_suit"] and card["suit"] != game["lead_suit"]:
                has_lead = any(c["suit"] == game["lead_suit"] for c in hand)
                if has_lead:
                    return _bot.answer_callback_query(
                        call.id,
                        f"❌ باید از خال {_SUIT_EMOJI.get(game['lead_suit'],'')} پیروی کنید!",
                        show_alert=True
                    )

            hand.pop(card_idx)
            game["round_cards"][user_id] = card
            if not game["lead_suit"]:
                game["lead_suit"] = card["suit"]

            _bot.answer_callback_query(call.id, f"✅ {_hokm_card_label(card)} بازی شد.")

            n = len(game["players"])
            game["current_turn_idx"] = (game["current_turn_idx"] + 1) % n

            # آیا همه در این دست کارت زدند؟
            if len(game["round_cards"]) == n:
                _hokm_resolve_round(game_id)
            else:
                # ادیت پیام شخصی همه بازیکنان: کارت بازی‌شده + نوبت جدید (بدون ارسال پیام جدید)
                _hokm_broadcast_hands(game)

        except Exception as e:
            print(f"❌ callback_hokm_play: {e}")

    # ── تعیین برنده هر دست ───────────────────────────────────────────────────
    def _hokm_resolve_round(game_id):
        try:
            game = _hokm_get_by_id(game_id)
            if not game:
                return

            trump = game["trump"]
            lead_suit = game["lead_suit"]

            winner = max(
                game["round_cards"].keys(),
                key=lambda u: _hokm_card_value(game["round_cards"][u], trump, lead_suit)
            )
            winner_team = game["teams"][winner]
            game["tricks"][winner_team] += 1
            game["total_tricks"] += 1

            cards_played = " | ".join(
                f"{game['names'][u]}: {_hokm_card_label(game['round_cards'][u])}"
                for u in game["players"]
            )
            winner_name = game["names"][winner]

            round_summary = [
                "🏁 <b>دست تمام شد!</b>",
                f"کارت‌ها: {cards_played}",
                f"🏆 برنده: <b>{winner_name}</b>",
            ]

            # ریست دست
            game["round_cards"] = {}
            game["lead_suit"] = None

            win_idx = game["turn_order"].index(winner)
            game["turn_order"] = game["turn_order"][win_idx:] + game["turn_order"][:win_idx]
            game["current_turn_idx"] = 0
            game["round_lead"] = winner

            if game["tricks"][0] >= 7 or game["tricks"][1] >= 7:
                _hokm_finish(game_id, game["tricks"][0] >= 7, round_summary)
            elif game["total_tricks"] >= 13:
                _hokm_finish(game_id, game["tricks"][0] > game["tricks"][1], round_summary)
            else:
                # ادیت پیام شخصی همه بازیکنان: نتیجهٔ دست قبلی + دست جدید
                _hokm_broadcast_hands(game, extra_lines=round_summary)

        except Exception as e:
            print(f"❌ _hokm_resolve_round: {e}")

    def _hokm_card_value(card, trump, lead):
        if card["suit"] == trump:
            return 100 + _RANK_VALUES[card["rank"]]
        if card["suit"] == lead:
            return  50 + _RANK_VALUES[card["rank"]]
        return _RANK_VALUES[card["rank"]]

    # ── پایان بازی و توزیع جایزه ─────────────────────────────────────────────
    def _hokm_finish(game_id, team0_won: bool, round_summary=None):
        try:
            game = _hokm_get_by_id(game_id)
            if not game:
                return
            game["state"] = "finished"
            chat_id = game["chat_id"]

            players = game["players"]
            bet = game["bet"]
            n = len(players)
            total = bet * n
            tax = int(total * 0.10)
            payout_total = total - tax

            winners = [uid for uid in players if game["teams"][uid] == (0 if team0_won else 1)]
            losers  = [uid for uid in players if uid not in winners]

            payout_each = payout_total // len(winners) if winners else 0

            for uid in winners:
                acc_id = game["accounts"][uid]
                db.add_tokens(acc_id, payout_each)

            win_names = ", ".join(game["names"][u] for u in winners)
            lose_names = ", ".join(game["names"][u] for u in losers)

            result_lines = []
            if round_summary:
                result_lines.extend(round_summary)
                result_lines.append("")
            result_lines.extend([
                "🏆 <b>بازی حکم تمام شد!</b>",
                "",
                f"{'🥇 تیم حاکم' if team0_won else '🥇 تیم رقیب'} برنده شد!",
                "",
                f"✅ برندگان: <b>{win_names}</b>",
                f"❌ بازندگان: {lose_names}",
                "",
                f"💰 مجموع شرط: {total} الماس",
                f"🏛 مالیات ۱۰٪: {tax} الماس",
                f"💎 هر برنده: <b>{payout_each} الماس</b>",
                "",
                f"📊 نتیجه: تیم حاکم {game['tricks'][0]} — تیم رقیب {game['tricks'][1]}",
            ])
            result_text = "\n".join(result_lines)

            # ادیت همان پیام شخصی هر بازیکن با نتیجه نهایی و حذف دکمه‌ها
            for uid in players:
                _hokm_send_or_edit(uid, game, result_text, types.InlineKeyboardMarkup())

            # اطلاع در گروه (پیام جدید، چون پیام گروهی قبلاً برای شروع بازی ادیت شده بود)
            try:
                _bot.send_message(chat_id, result_text, parse_mode="HTML")
            except Exception:
                pass

            with _hokm_lock:
                _hokm_games.pop(game_id, None)

        except Exception as e:
            print(f"❌ _hokm_finish: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # 🪨📄✂️ بازی سنگ کاغذ قیچی گروهی — ۵ راند — فقط یک پیام (همیشه ادیت می‌شود)
    # ══════════════════════════════════════════════════════════════════════════
    # فرمت دستور: "بازی 100"  → شرط 100 الماس
    # ساختار: _rps_games[game_id] = {
    #   "chat_id": int, "msg_id": int  ← تنها پیام بازی، فقط همین ادیت می‌شود
    #   "player1": int, "player2": int | None,
    #   "player1_name": str, "player2_name": str | None,
    #   "choice1": str | None, "choice2": str | None,   ← انتخاب راند فعلی
    #   "score1": int, "score2": int,                   ← امتیاز کل
    #   "round": int,                                   ← راند فعلی (1-5)
    #   "last_round_line": str,                          ← خلاصه نتیجه آخرین راند
    #   "account1": int, "account2": int | None,
    #   "bet": int,
    #   "state": "waiting" | "playing" | "resolving" | "finished"
    # }
    _rps_games = {}
    _rps_lock = threading.Lock()
    _RPS_CHOICES = {"rock": "🪨 سنگ", "paper": "📄 کاغذ", "scissors": "✂️ قیچی"}
    _RPS_WINS = {"rock": "scissors", "paper": "rock", "scissors": "paper"}
    _RPS_TAX = 0.10   # ۱۰٪ مالیات
    _RPS_ROUNDS = 1

    def _rps_new_id():
        import uuid
        return str(uuid.uuid4())[:8]

    def _rps_result(c1, c2):
        """نتیجه یک راند: 'win1', 'win2', 'draw'"""
        if c1 == c2:
            return "draw"
        if _RPS_WINS[c1] == c2:
            return "win1"
        return "win2"

    def _rps_score_bar(s1, s2, total_rounds=_RPS_ROUNDS):
        """نوار امتیاز بصری"""
        bar = ""
        for i in range(total_rounds):
            if i < s1:
                bar += "🟦"
            elif i < s1 + s2:
                bar += "🟥"
            else:
                bar += "⬜"
        return bar

    def _rps_status_text(game):
        p1 = game["player1_name"]
        p2 = game.get("player2_name") or "منتظر بازیکن..."
        bet = game["bet"]
        total = bet * 2
        tax = int(total * _RPS_TAX)
        payout = total - tax
        state = game["state"]

        if state == "waiting":
            return (
                "🎮 <b>بازی سنگ کاغذ قیچی!</b>\n\n"
                f"👤 نفر اول: <b>{p1}</b>\n"
                f"👤 نفر دوم: در انتظار...\n\n"
                f"💰 شرط هر نفر: <b>{bet} 💎 الماس</b>\n"
                f"🏆 جایزه برنده: <b>{payout} 💎 الماس</b> (مالیات ۱۰٪)\n\n"
                "⬇️ برای ورود به بازی دکمه زیر را بزنید"
            )

        rnd = min(game.get("round", 1), _RPS_ROUNDS)
        s1 = game.get("score1", 0)
        s2 = game.get("score2", 0)
        bar = _rps_score_bar(s1, s2)
        last_line = game.get("last_round_line", "")

        lines = []
        if state == "finished":
            lines.append("🏁 <b>نتیجه نهایی — سنگ کاغذ قیچی</b>")
        else:
            c1_done = "✅" if game.get("choice1") else "⏳"
            c2_done = "✅" if game.get("choice2") else "⏳"
            lines.append("🎮 <b>سنگ کاغذ قیچی</b>")
            lines.append(f"👤 {p1}  {c1_done}")
            lines.append(f"👤 {p2}  {c2_done}")

        if last_line:
            lines.append("")
            lines.append(last_line)

        lines.append("")
        lines.append(f"📊 امتیاز: {p1} <b>{s1}</b> — <b>{s2}</b> {p2}")
        lines.append(bar)

        if state == "playing":
            lines.append("")
            lines.append(f"💰 شرط: <b>{bet} 💎</b> هر نفر")
            lines.append("⬇️ انتخاب خود را بزنید:")

        return "\n".join(lines)

    def _rps_pick_markup(game_id):
        markup = types.InlineKeyboardMarkup(row_width=3)
        markup.add(
            types.InlineKeyboardButton("🪨 سنگ",   callback_data=f"rps_pick_{game_id}_rock",     style="primary"),  # 🔵 آبی
            types.InlineKeyboardButton("📄 کاغذ",  callback_data=f"rps_pick_{game_id}_paper",    style="success"),  # 🟢 سبز
            types.InlineKeyboardButton("✂️ قیچی", callback_data=f"rps_pick_{game_id}_scissors",  style="danger"),   # 🔴 قرمز
        )
        return markup

    def _rps_join_markup(game_id, bet):
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(types.InlineKeyboardButton(
            f"⚔️ ورود به بازی — {bet} 💎 الماس",
            callback_data=f"rps_join_{game_id}",
            style="success"  # 🟢 سبز
        ))
        return markup

    def _rps_render(game_id):
        """فقط همان یک پیام بازی را ادیت می‌کند — هیچ پیام جدیدی ارسال نمی‌شود"""
        game = _rps_games.get(game_id)
        if not game or not game.get("msg_id"):
            return
        try:
            if game["state"] == "waiting":
                markup = _rps_join_markup(game_id, game["bet"])
            elif game["state"] == "playing":
                markup = _rps_pick_markup(game_id)
            else:
                markup = types.InlineKeyboardMarkup()

            _bot.edit_message_text(
                _rps_status_text(game),
                game["chat_id"], game["msg_id"],
                parse_mode="HTML",
                reply_markup=markup
            )
        except Exception as e:
            print(f"❌ _rps_render: {e}")

    # ── دستور شروع بازی: "بازی 100" ──────────────────────────────────────────
    @_bot.message_handler(func=lambda m: (
        m.chat.type in ("group", "supergroup") and
        m.text and
        re.match(r'^بازی\s+(\d+)$', m.text.strip())
    ))
    def cmd_rps_start(message):
        try:
            if not _is_self_group(message.chat):
                return

            user = message.from_user
            match = re.match(r'^بازی\s+(\d+)$', message.text.strip())
            bet = int(match.group(1))

            if bet <= 0:
                return _bot.reply_to(message, "❌ مقدار شرط باید بیشتر از صفر باشد.")

            account = _get_account_cached(user.id)
            if not account:
                return _bot.reply_to(message, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.")

            balance = db.get_token_balance(account["id"])
            if balance < bet:
                return _bot.reply_to(
                    message,
                    f"❌ موجودی کافی نیست!\n{EM.EMOJI_BALANCE} موجودی شما: {balance} الماس\n💰 شرط: {bet} الماس"
                )

            with _rps_lock:
                for g in _rps_games.values():
                    if user.id in (g["player1"], g.get("player2")) and g["state"] != "finished":
                        return _bot.reply_to(message, "❌ شما هم‌اکنون در یک بازی فعال هستید.")

            # کسر الماس از نفر اول
            if not db.deduct_tokens(account["id"], bet):
                return _bot.reply_to(message, "❌ خطا در کسر موجودی. دوباره امتحان کنید.")
            cache.invalidate(f"account_{user.id}")

            display = f"@{user.username}" if user.username else user.first_name
            game_id = _rps_new_id()
            game = {
                "chat_id": message.chat.id,
                "player1": user.id,
                "player1_name": display,
                "player2": None,
                "player2_name": None,
                "choice1": None,
                "choice2": None,
                "score1": 0,
                "score2": 0,
                "round": 1,
                "msg_id": None,
                "account1": account["id"],
                "account2": None,
                "bet": bet,
                "state": "waiting",
                "last_round_line": "",
            }

            with _rps_lock:
                _rps_games[game_id] = game

            sent = _bot.send_message(
                message.chat.id,
                _rps_status_text(game),
                parse_mode="HTML",
                reply_markup=_rps_join_markup(game_id, bet)
            )
            game["msg_id"] = sent.message_id

            # تایمر ۵ دقیقه — لغو خودکار اگر نفر دوم نیاید
            def _rps_timeout(gid):
                with _rps_lock:
                    g = _rps_games.get(gid)
                    if not g or g["state"] != "waiting":
                        return
                    g["state"] = "finished"
                    _rps_games.pop(gid, None)
                db.add_tokens(g["account1"], g["bet"])
                cache.invalidate(f"account_{g['player1']}")
                try:
                    _bot.edit_message_text(
                        f"⏰ <b>بازی لغو شد!</b>\n\n{g['player1_name']} منتظر حریف ماند ولی کسی نیامد.\n"
                        f"💎 {g['bet']} الماس به حساب برگشت.",
                        g["chat_id"], g["msg_id"],
                        parse_mode="HTML",
                        reply_markup=types.InlineKeyboardMarkup()
                    )
                except Exception:
                    pass
            threading.Timer(300, _rps_timeout, args=[game_id]).start()

        except Exception as e:
            print(f"❌ cmd_rps_start: {e}")

    # ── ورود نفر دوم (روی همان پیام) ─────────────────────────────────────────
    @_bot.callback_query_handler(func=lambda c: c.data.startswith("rps_join_"))
    def callback_rps_join(call):
        try:
            game_id = call.data.split("_", 2)[2]
            user = call.from_user

            with _rps_lock:
                game = _rps_games.get(game_id)
                if not game:
                    return _bot.answer_callback_query(call.id, "❌ بازی یافت نشد.")
                if game["state"] != "waiting":
                    return _bot.answer_callback_query(call.id, "❌ بازی قبلاً شروع شده.", show_alert=True)
                if user.id == game["player1"]:
                    return _bot.answer_callback_query(call.id, "❌ شما سازنده این بازی هستید!", show_alert=True)

                account = _get_account_cached(user.id)
                if not account:
                    return _bot.answer_callback_query(call.id, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.", show_alert=True)

                bet = game["bet"]
                balance = db.get_token_balance(account["id"])
                if balance < bet:
                    return _bot.answer_callback_query(
                        call.id,
                        f"❌ موجودی کافی نیست!\n{EM.EMOJI_BALANCE} موجودی: {balance}\n💰 شرط: {bet}",
                        show_alert=True
                    )

                for gid, g in _rps_games.items():
                    if gid == game_id:
                        continue
                    if user.id in (g["player1"], g.get("player2")) and g["state"] != "finished":
                        return _bot.answer_callback_query(call.id, "❌ شما در یک بازی دیگر هستید.", show_alert=True)

                # کسر الماس از نفر دوم
                if not db.deduct_tokens(account["id"], bet):
                    return _bot.answer_callback_query(call.id, "❌ خطا در کسر موجودی.", show_alert=True)
                cache.invalidate(f"account_{user.id}")

                display = f"@{user.username}" if user.username else user.first_name
                game["player2"] = user.id
                game["player2_name"] = display
                game["account2"] = account["id"]
                game["state"] = "playing"
                game["round"] = 1
                game["choice1"] = None
                game["choice2"] = None
                game["last_round_line"] = ""

            _bot.answer_callback_query(call.id, f"✅ وارد بازی شدید! {bet} الماس کسر شد.")
            _rps_render(game_id)

        except Exception as e:
            print(f"❌ callback_rps_join: {e}")

    # ── انتخاب سنگ/کاغذ/قیچی (روی همان پیام واحد گروه) ───────────────────────
    @_bot.callback_query_handler(func=lambda c: c.data.startswith("rps_pick_"))
    def callback_rps_pick(call):
        try:
            parts = call.data.split("_")  # rps_pick_GAMEID_CHOICE
            game_id = parts[2]
            choice = parts[3]
            user = call.from_user

            both_chosen = False
            with _rps_lock:
                game = _rps_games.get(game_id)
                if not game:
                    return _bot.answer_callback_query(call.id, "❌ بازی یافت نشد.")
                if game["state"] != "playing":
                    return _bot.answer_callback_query(call.id, "❌ بازی در جریان نیست.")

                if user.id == game["player1"]:
                    if game["choice1"]:
                        return _bot.answer_callback_query(call.id, "⚠️ شما قبلاً انتخاب کردید.", show_alert=True)
                    game["choice1"] = choice
                elif user.id == game["player2"]:
                    if game["choice2"]:
                        return _bot.answer_callback_query(call.id, "⚠️ شما قبلاً انتخاب کردید.", show_alert=True)
                    game["choice2"] = choice
                else:
                    return _bot.answer_callback_query(call.id, "❌ شما در این بازی نیستید.", show_alert=True)

                _bot.answer_callback_query(call.id, f"✅ {_RPS_CHOICES.get(choice, choice)} انتخاب شد! منتظر رقیب...")
                both_chosen = bool(game["choice1"] and game["choice2"])

            if both_chosen:
                _rps_resolve_round(game_id)
            else:
                # فقط وضعیت ✅/⏳ روی همان پیام آپدیت شود — بدون لو دادن انتخاب حریف
                _rps_render(game_id)

        except Exception as e:
            print(f"❌ callback_rps_pick: {e}")

    def _rps_resolve_round(game_id):
        """پردازش نتیجه یک راند و آپدیت همان پیام واحد بازی"""
        try:
            is_last = False
            with _rps_lock:
                game = _rps_games.get(game_id)
                if not game or game["state"] != "playing":
                    return

                c1 = game["choice1"]
                c2 = game["choice2"]
                result = _rps_result(c1, c2)
                rnd = game["round"]

                if result == "win1":
                    game["score1"] += 1
                elif result == "win2":
                    game["score2"] += 1

                label1 = _RPS_CHOICES.get(c1, c1)
                label2 = _RPS_CHOICES.get(c2, c2)
                p1 = game["player1_name"]
                p2 = game["player2_name"]

                if result == "win1":
                    round_result_line = f"🏅 {p1} این راند را برد!"
                elif result == "win2":
                    round_result_line = f"🏅 {p2} این راند را برد!"
                else:
                    round_result_line = "🤝 این راند مساوی شد!"

                game["last_round_line"] = (
                    f"📋 راند {rnd}: {p1}={label1} | {p2}={label2}\n{round_result_line}"
                )

                is_last = (rnd >= _RPS_ROUNDS)
                game["choice1"] = None
                game["choice2"] = None
                game["round"] = rnd + 1

                if is_last:
                    game["state"] = "resolving"

            if is_last:
                _rps_finish(game_id)
            else:
                _rps_render(game_id)

        except Exception as e:
            print(f"❌ _rps_resolve_round: {e}")

    def _rps_finish(game_id):
        """پایان بازی — تعیین برنده نهایی، واریز جایزه و ادیت همان پیام واحد"""
        try:
            with _rps_lock:
                game = _rps_games.get(game_id)
                if not game:
                    return
                game = dict(game)  # snapshot برای استفاده خارج از قفل
                _rps_games.pop(game_id, None)

            s1 = game["score1"]
            s2 = game["score2"]
            p1_name = game["player1_name"]
            p2_name = game["player2_name"]
            bet = game["bet"]
            total = bet * 2
            tax = int(total * _RPS_TAX)
            payout = total - tax

            if s1 > s2:
                db.add_tokens(game["account1"], payout)
                cache.invalidate(f"account_{game['player1']}")
                winner_name = p1_name
                result_line = f"🏆 <b>{winner_name}</b> برنده شد! ({s1} — {s2})"
                payout_line = (
                    f"💰 مجموع شرط: {total} 💎\n"
                    f"🏛 مالیات ۱۰٪: {tax} 💎\n"
                    f"💎 جایزه: <b>{payout} الماس</b> به {winner_name}"
                )
            elif s2 > s1:
                db.add_tokens(game["account2"], payout)
                cache.invalidate(f"account_{game['player2']}")
                winner_name = p2_name
                result_line = f"🏆 <b>{winner_name}</b> برنده شد! ({s2} — {s1})"
                payout_line = (
                    f"💰 مجموع شرط: {total} 💎\n"
                    f"🏛 مالیات ۱۰٪: {tax} 💎\n"
                    f"💎 جایزه: <b>{payout} الماس</b> به {winner_name}"
                )
            else:
                # مساوی: برگشت شرط بدون مالیات
                db.add_tokens(game["account1"], bet)
                db.add_tokens(game["account2"], bet)
                cache.invalidate(f"account_{game['player1']}")
                cache.invalidate(f"account_{game['player2']}")
                result_line = f"🤝 <b>مساوی!</b> ({s1} — {s2})"
                payout_line = f"↩️ هر نفر {bet} 💎 الماس دریافت کرد."

            bar = _rps_score_bar(s1, s2)
            final_text = (
                "🏁 <b>نتیجه نهایی — سنگ کاغذ قیچی</b>\n\n"
                f"👤 {p1_name}:  <b>{s1}</b> امتیاز\n"
                f"👤 {p2_name}:  <b>{s2}</b> امتیاز\n"
                f"{bar}\n\n"
                f"{result_line}\n\n"
                f"{payout_line}"
            )

            try:
                _bot.edit_message_text(
                    final_text,
                    game["chat_id"], game["msg_id"],
                    parse_mode="HTML",
                    reply_markup=types.InlineKeyboardMarkup()
                )
            except Exception:
                try:
                    _bot.send_message(game["chat_id"], final_text, parse_mode="HTML")
                except Exception:
                    pass

        except Exception as e:
            print(f"❌ _rps_finish: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # 🎰 قرعه‌کشی — Scheduler
    # ══════════════════════════════════════════════════════════════════════════
    def _lottery_scheduler():
        """
        هر ۳۰ ثانیه چک می‌کند:
        - آیا وقت شروع قرعه‌کشی رسیده؟ → پیام اعلام در کانال + دکمه شرکت
        - آیا وقت پایان قرعه‌کشی رسیده؟ → قرعه‌کشی نهایی و اعلام برندگان
        """
        import json as _json
        import uuid as _uuid

        while True:
            try:
                time.sleep(30)
                now_teh = _now_tehran()
                raw = db.get_global_setting("lotteries", "[]")
                try:
                    lotteries = _json.loads(raw)
                except Exception:
                    lotteries = []

                changed = False
                for lot in lotteries:
                    if lot.get("status") != "active":
                        continue

                    start_ts = lot.get("start_ts")
                    end_ts = lot.get("end_ts")
                    channel = lot.get("channel") or getattr(config, "WC_CHANNEL_ID", "")

                    if not channel:
                        continue

                    # تبدیل رشته به datetime
                    try:
                        start_dt = datetime.datetime.fromisoformat(start_ts)
                        end_dt = datetime.datetime.fromisoformat(end_ts)
                    except Exception:
                        continue

                    # اطمینان از timezone
                    if start_dt.tzinfo is None:
                        start_dt = start_dt.replace(tzinfo=_TEHRAN_OFFSET)
                    if end_dt.tzinfo is None:
                        end_dt = end_dt.replace(tzinfo=_TEHRAN_OFFSET)

                    # ── اعلام شروع قرعه‌کشی ──────────────────────────────
                    if not lot.get("announced") and now_teh >= start_dt:
                        try:
                            prizes = lot.get("prizes", [])
                            ordinals = ["اول", "دوم", "سوم", "چهارم", "پنجم",
                                        "ششم", "هفتم", "هشتم", "نهم", "دهم"]
                            prize_lines = ""
                            for i, p in enumerate(prizes):
                                ord_name = ordinals[i] if i < len(ordinals) else f"{i+1}م"
                                medals = ["🥇","🥈","🥉","🏅","🏅","🏅","🏅","🏅","🏅","🏅"]
                                medal = medals[i] if i < len(medals) else "🎁"
                                prize_lines += f"\n{medal} نفر {ord_name}: <b>{p}</b>"

                            msg_text = (
                                "🎰 <b>قرعه‌کشی شروع شد!</b>\n\n"
                                f"⏰ مهلت شرکت: تا ساعت <b>{lot['end_time']}</b>\n"
                                f"🏆 تعداد برنده: <b>{lot['winners_count']} نفر</b>\n"
                                f"🎁 جوایز:{prize_lines}\n\n"
                                "👇 برای شرکت روی دکمه کلیک کنید:"
                            )
                            markup = types.InlineKeyboardMarkup()
                            markup.add(types.InlineKeyboardButton(
                                "🎰 شرکت در قرعه‌کشی",
                                callback_data=f"join_lottery_{lot['id']}"
                            ))
                            sent_msg = _bot.send_message(channel, msg_text,
                                parse_mode="HTML", reply_markup=markup)
                            lot["announced"] = True
                            lot["announce_msg_id"] = sent_msg.message_id
                            lot["announce_chat_id"] = sent_msg.chat.id
                            changed = True
                            print(f"✅ قرعه‌کشی {lot['id']} اعلام شد")
                        except Exception as e:
                            print(f"❌ خطا در اعلام قرعه‌کشی: {e}")

                    # ── پایان قرعه‌کشی و انتخاب برندگان ─────────────────
                    if lot.get("announced") and not lot.get("finished") and now_teh >= end_dt:
                        try:
                            participants = lot.get("participants", [])
                            winners_count = lot.get("winners_count", 1)
                            prizes = lot.get("prizes", [])

                            if not participants:
                                result_text = (
                                    "🎰 <b>نتیجه قرعه‌کشی</b>\n\n"
                                    "😔 متأسفانه هیچ‌کس در قرعه‌کشی شرکت نکرد."
                                )
                            else:
                                # انتخاب تصادفی برندگان
                                pool = list(participants)
                                random.shuffle(pool)
                                selected = pool[:min(winners_count, len(pool))]
                                # مرتب کردن تصادفی برای رتبه‌بندی
                                random.shuffle(selected)

                                ordinals = ["اول", "دوم", "سوم", "چهارم", "پنجم",
                                            "ششم", "هفتم", "هشتم", "نهم", "دهم"]
                                medals = ["🥇","🥈","🥉","🏅","🏅","🏅","🏅","🏅","🏅","🏅"]
                                prize_details = lot.get("prize_details", [])

                                winner_lines = ""
                                for i, winner in enumerate(selected):
                                    ord_name = ordinals[i] if i < len(ordinals) else f"{i+1}م"
                                    medal = medals[i] if i < len(medals) else "🎁"
                                    prize = prizes[i] if i < len(prizes) else "—"
                                    name = winner.get("name", "کاربر")
                                    username = winner.get("username")
                                    mention = f"@{username}" if username else name

                                    # ── واریز خودکار جایزه (الماس/اشتراک) ──────────
                                    detail = prize_details[i] if i < len(prize_details) else None
                                    if detail:
                                        try:
                                            account = db.get_account_by_tg_id(winner.get("user_id"))
                                            if account:
                                                acc_id = account["id"]
                                                if detail.get("type") == "diamond":
                                                    db.add_tokens(acc_id, int(detail.get("amount", 0)))
                                                elif detail.get("type") == "subscription":
                                                    db.set_subscription(acc_id, "lottery", int(detail.get("days", 0)))
                                            else:
                                                print(f"⚠️ برنده قرعه‌کشی {winner.get('user_id')} حساب متصل ندارد — جایزه واریز نشد.")
                                        except Exception as e:
                                            print(f"❌ خطا در واریز جایزه‌ی قرعه‌کشی: {e}")

                                    winner_lines += f"\n{medal} نفر {ord_name}: {mention} — <b>{prize}</b>"

                                result_text = (
                                    "🎉 <b>نتایج قرعه‌کشی اعلام شد!</b>\n\n"
                                    f"🏆 برندگان ({len(selected)} نفر):{winner_lines}\n\n"
                                    "🎊 تبریک به برندگان عزیز!"
                                )

                            _bot.send_message(channel, result_text, parse_mode="HTML")
                            lot["finished"] = True
                            lot["status"] = "done"
                            changed = True
                            print(f"✅ قرعه‌کشی {lot['id']} پایان یافت")
                        except Exception as e:
                            print(f"❌ خطا در پایان قرعه‌کشی: {e}")

                if changed:
                    db.set_global_setting("lotteries", _json.dumps(lotteries, ensure_ascii=False))

            except Exception as e:
                print(f"❌ خطا در lottery_scheduler: {e}")

    # ── handler شرکت در قرعه‌کشی (از کانال) ─────────────────────────────────
    @_bot.callback_query_handler(func=lambda call: call.data.startswith("join_lottery_"))
    def callback_join_lottery(call):
        import json as _json
        lot_id = call.data[len("join_lottery_"):]
        uid = call.from_user.id
        user = call.from_user

        try:
            raw = db.get_global_setting("lotteries", "[]")
            lotteries = _json.loads(raw)
        except Exception:
            lotteries = []

        for lot in lotteries:
            if lot.get("id") == lot_id and lot.get("status") == "active":
                participants = lot.get("participants", [])
                # بررسی تکراری نبودن
                already = any(p.get("user_id") == uid for p in participants)
                if already:
                    return _bot.answer_callback_query(call.id, "✅ شما قبلاً ثبت‌نام کرده‌اید!", show_alert=True)

                # بررسی وقت
                now_teh = _now_tehran()
                try:
                    end_dt = datetime.datetime.fromisoformat(lot["end_ts"])
                    if end_dt.tzinfo is None:
                        end_dt = end_dt.replace(tzinfo=_TEHRAN_OFFSET)
                    if now_teh > end_dt:
                        return _bot.answer_callback_query(call.id, "⏰ مهلت شرکت تمام شده است.", show_alert=True)
                except Exception:
                    pass

                participants.append({
                    "user_id": uid,
                    "name": f"{user.first_name or ''} {user.last_name or ''}".strip() or "کاربر",
                    "username": user.username
                })
                lot["participants"] = participants
                db.set_global_setting("lotteries", _json.dumps(lotteries, ensure_ascii=False))
                return _bot.answer_callback_query(call.id,
                    f"✅ ثبت‌نام شما انجام شد! ({len(participants)} نفر شرکت کرده‌اند)",
                    show_alert=True)

        _bot.answer_callback_query(call.id, "❌ قرعه‌کشی یافت نشد یا پایان یافته.", show_alert=True)

    # اجرای thread قرعه‌کشی
    t_lottery = threading.Thread(target=_lottery_scheduler, daemon=True)
    t_lottery.start()

    # ══════════════════════════════════════════════════════════════════════════
    # Polling
    # ══════════════════════════════════════════════════════════════════════════
    def _polling_loop():
        while True:
            try:
                _bot.infinity_polling(
                    timeout=10,
                    long_polling_timeout=5,
                    restart_on_change=False,
                    skip_pending=True,
                    interval=0
                )
            except Exception as e:
                if "409" in str(e):
                    time.sleep(10)
                    try:
                        _bot.delete_webhook(drop_pending_updates=True)
                    except:
                        pass
                else:
                    print(f"⚠️ خطای polling: {e}")
                    time.sleep(3)

    t = threading.Thread(target=_polling_loop, daemon=True)
    t.start()
    print(f"✅ ربات الماس @{BOT_USERNAME} استارت شد")
