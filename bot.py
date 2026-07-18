import asyncio
import re
import os
import io
import json
import datetime
import random
import threading
import time
from telethon import TelegramClient, events
from telethon.tl.types import InputMediaDice


def _u16len(s: str) -> int:
    """
    طول رشته بر حسب واحدهای UTF-16 (نه تعداد کاراکتر پایتون).
    تلگرام آفست/طولِ entity ها (بولد، کوت، اسپویلر و ...) رو با UTF-16
    حساب می‌کنه، در حالی که ایموجی‌ها و بعضی کاراکترهای خاص (مثل خیلی از
    ایموجی‌های رایج) در UTF-16 دو واحدی (surrogate pair) هستن ولی len()
    پایتون براشون فقط ۱ می‌شمره. همین اختلاف باعث می‌شد افست/طولِ کوت
    اشتباه محاسبه بشه و متنِ نگهبانِ چت (پیام حذف/ویرایش‌شده) قاطی یا ناقص
    نمایش داده بشه، مخصوصاً وقتی اسم فرستنده ایموجی داشت.
    """
    return len(s.encode("utf-16-le")) // 2

from telethon.sessions import StringSession
from telethon.tl.functions.account import UpdateProfileRequest
from telethon.tl.functions.messages import GetCommonChatsRequest
from telethon.errors import FloodWaitError
import requests
import database as db
import config
from texts import ENEMY_REPLIES, FRIEND_REPLIES
import meowie_game

# ─── فونت‌ها ───────────────────────────────────────────────────────────────────
FONTS = {
    "0": lambda t: t,
    "1": lambda t: _convert_font(t, "𝗔𝗕𝗖𝗗𝗘𝗙𝗚𝗛𝗜𝗝𝗞𝗟𝗠𝗡𝗢𝗣𝗤𝗥𝗦𝗧𝗨𝗩𝗪𝗫𝗬𝗭𝗮𝗯𝗰𝗱𝗲𝗳𝗴𝗵𝗶𝗷𝗸𝗹𝗺𝗻𝗼𝗽𝗾𝗿𝘀𝘁𝘂𝘃𝘄𝘅𝘆𝘇"),
    "2": lambda t: _convert_font(t, "𝘈𝘉𝘊𝘋𝘌𝘍𝘎𝘏𝘐𝘑𝘒𝘓𝘔𝘕𝘖𝘗𝘘𝘙𝘚𝘛𝘜𝘝𝘞𝘟𝘠𝘡𝘢𝘣𝘤𝘥𝘦𝘧𝘨𝘩𝘪𝘫𝘬𝘭𝘮𝘯𝘰𝘱𝘲𝘳𝘴𝘵𝘶𝘷𝘸𝘹𝘺𝘻"),
    "3": lambda t: _convert_font(t, "𝙰𝙱𝙲𝙳𝙴𝙵𝙶𝙷𝙸𝙹𝙺𝙻𝙼𝙽𝙾𝙿𝚀𝚁𝚂𝚃𝚄𝚅𝚆𝚇𝚈𝚉𝚊𝚋𝚌𝚍𝚎𝚏𝚐𝚑𝚒𝚓𝚔𝚕𝚖𝚗𝚘𝚙𝚚𝚛𝚜𝚝𝚞𝚟𝚠𝚡𝚢𝚣"),
    "4": lambda t: _convert_font(t, "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ"),
    "5": lambda t: _convert_font(t, "𝐀𝐁𝐂𝐃𝐄𝐅𝐆𝐇𝐈𝐉𝐊𝐋𝐌𝐍𝐎𝐏𝐐𝐑𝐒𝐓𝐔𝐕𝐖𝐗𝐘𝐙𝐚𝐛𝐜𝐝𝐞𝐟𝐠𝐡𝐢𝐣𝐤𝐥𝐦𝐧𝐨𝐩𝐪𝐫𝐬𝐭𝐮𝐯𝐰𝐱𝐲𝐳"),
    "6": lambda t: _convert_font(t, "𝒜ℬ𝒞𝒟ℰℱ𝒢ℋℐ𝒥𝒦ℒℳ𝒩𝒪𝒫𝒬ℛ𝒮𝒯𝒰𝒱𝒲𝒳𝒴𝒵𝒶𝒷𝒸𝒹ℯ𝒻ℊ𝒽𝒾𝒿𝓀𝓁𝓂𝓃ℴ𝓅𝓆𝓇𝓈𝓉𝓊𝓋𝓌𝓍𝓎𝓏"),
    "7": lambda t: "".join(c + "\u0336" for c in t),
    "8": lambda t: "".join(c + "\u0332" for c in t),
}
_ALPHA = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

LINK_PATTERN = re.compile(
    r"(https?://\S+|t\.me/\S+|telegram\.me/\S+|www\.\S+)", re.IGNORECASE
)

# لینک یک پست خاص، مثل: https://t.me/channelname/123 یا t.me/channelname/123
_POST_LINK_RE = re.compile(
    r"^(?:https?://)?t\.me/([A-Za-z0-9_]+)/(\d+)/?$", re.IGNORECASE
)

# لینک یک پست از کانال خصوصی، مثل: https://t.me/c/3807322753/674
_PRIVATE_POST_LINK_RE = re.compile(
    r"^(?:https?://)?t\.me/c/(\d+)/(\d+)/?$", re.IGNORECASE
)

# ─── سیستم محدودیت زمانی برای منشی و دوست ────────────────────────────────────
_last_secretary_reply = {}  # {chat_id: timestamp}
_last_friend_reply = {}     # {sender_id: timestamp}
SECRETARY_COOLDOWN = 86400  # 24 ساعت
FRIEND_COOLDOWN = 3600      # 1 ساعت

# ─── دستیار هوش مصنوعی (دیپ‌سیک) ──────────────────────────────────────────────
_last_ai_reply = {}  # {chat_id: timestamp} — کول‌داون پاسخ هوش مصنوعی
_last_outgoing_activity = {}  # {owner_id: timestamp} — آخرین باری که خودِ کاربر پیام فرستاده
AI_AWAY_SECONDS = 300  # اگه ۵ دقیقه از آخرین پیامِ خودِ کاربر گذشته باشه، "غایب" در نظر گرفته می‌شه
AI_REPLY_COOLDOWN = 60  # حداقل فاصله بین دو پاسخ هوش مصنوعی در یک چت

# ─── نگهبان چت: کش موقتِ متنِ پیام‌ها برای تشخیصِ حذف/ویرایش ──────────────────
_msg_cache = {}  # {(chat_id, msg_id): text}
_msg_sender_cache = {}  # {(chat_id, msg_id): sender display name}
_msg_media_cache = {}  # {(chat_id, msg_id): saved media file path or None}
_MSG_CACHE_MAX = 2000

# ─── پاسخ خودکار ثابت به همه‌ی پیام‌ها ─────────────────────────────────────────
_last_auto_reply = {}  # {chat_id: timestamp}
AUTO_REPLY_COOLDOWN = 30  # ثانیه

def _convert_font(text, chars):
    result = []
    for ch in text:
        if ch in _ALPHA:
            result.append(chars[_ALPHA.index(ch)])
        else:
            result.append(ch)
    return "".join(result)


def _apply_font(owner_id, text):
    font_id = db.get_setting(owner_id, "selected_font", "0")
    fn = FONTS.get(font_id, FONTS["0"])
    return fn(text)


# ─── فونت‌های مخصوص ساعت (فقط روی ارقام اعمال می‌شود) ──────────────────────────
# ایموجی‌های ساعت آنالوگ برای حالت «ساعت پرمیوم» (ایندکس = ساعت به‌صورت ۱۲ ساعته)
_CLOCK_FACE_EMOJIS = [
    "🕛", "🕐", "🕑", "🕒", "🕓", "🕔",
    "🕕", "🕖", "🕗", "🕘", "🕙", "🕚",
]

CLOCK_FONTS = {
    "0": "0123456789",
    "1": "⓿❶❷❸❹❺❻❼❽❾",
    "2": "𝟬𝟭𝟮𝟯𝟰𝟱𝟲𝟳𝟴𝟵",
    "3": "⓪①②③④⑤⑥⑦⑧⑨",
    "4": "𝟢𝟣𝟤𝟥𝟦𝟧𝟨𝟩𝟪𝟫",
    "5": "0⑴⑵⑶⑷⑸⑹⑺⑻⑼",
    "6": "₀₁₂₃₄₅₆₇₈₉",
    "7": "⁰¹²³⁴⁵⁶⁷⁸⁹",
    "8": "𝟎𝟏𝟐𝟑𝟒𝟓𝟔𝟕𝟖𝟗",
    "9": "𝟘𝟙𝟚𝟛𝟜𝟝𝟞𝟟𝟠𝟡",
}


def _apply_clock_font(owner_id, text):
    font_id = db.get_setting(owner_id, "selected_clock_font", "0")
    digits = CLOCK_FONTS.get(font_id, CLOCK_FONTS["0"])
    return "".join(digits[int(ch)] if ch.isdigit() else ch for ch in text)


_SUPER = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")

def persian_time():
    iran_tz = datetime.timezone(datetime.timedelta(hours=3, minutes=30))
    now = datetime.datetime.now(iran_tz)
    return f"{now.hour:02d}:{now.minute:02d}".translate(_SUPER)


# ─── BotManager: مدیریت چندین کلاینت همزمان ────────────────────────────────────
class BotManager:
    def __init__(self):
        self._bots = {}
        self._timers = {}

    def is_running(self, owner_id: int) -> bool:
        entry = self._bots.get(owner_id)
        return bool(entry and not entry["task"].done())

    def get_client(self, owner_id: int):
        entry = self._bots.get(owner_id)
        return entry["client"] if entry else None

    def get_owner_by_tg_id(self, tg_id: int):
        """
        از روی آیدی تلگرام کاربر (همان آیدی‌ای که به بات کمکی پنل وصل شده)
        owner_id و entry مربوط به سلف در حال اجرای او را پیدا می‌کند.
        فقط بین سلف‌های در حال اجرا جستجو می‌کند.
        """
        for owner_id, entry in self._bots.items():
            if not entry or not entry.get("client"):
                continue
            try:
                if db.get_telegram_id_by_owner(owner_id) == tg_id:
                    return owner_id, entry
            except Exception:
                continue
        return None, None

    def _cancel_timer(self, owner_id: int):
        t = self._timers.pop(owner_id, None)
        if t:
            t.cancel()

    def session_end_time(self, owner_id: int):
        t = self._timers.get(owner_id)
        if t and t.is_alive():
            remaining = t.interval - (time.time() - t._timer_start if hasattr(t, '_timer_start') else 0)
            return max(0, remaining)
        return None

    def start(self, owner_id: int, loop: asyncio.AbstractEventLoop, check_tokens: bool = True,
              is_restart: bool = False) -> bool:
        """
        is_restart=True یعنی این استارت یک «اتصال مجدد خودکار» است (مثلاً بعد از بالا آمدن
        دوباره‌ی سرور روی Render). در این حالت سلف کاربر همیشه باید روشن بماند و کاربر
        نباید مجبور باشد دوباره چیزی بزند؛ پس این حالت هیچ‌وقت استارت را مسدود نمی‌کند:
        اگر از زمان شروع سشن قبلی (ذخیره‌شده در Supabase) کمتر از SESSION_HOURS گذشته باشد،
        فقط زمان واقعی باقی‌مانده به تایمر داده می‌شود؛ اگر هم تمام شده باشد، به‌جای قطع کردن
        کاربر، یک پنجره‌ی تازه (fresh) برایش شروع می‌شود تا ری‌استارت سرور هیچ‌وقت سلف را
        برای کاربر خاموش نکند.
        """
        if self.is_running(owner_id):
            self.stop(owner_id)

        tg_id = db.get_telegram_id_by_owner(owner_id)
        is_owner = (tg_id is not None and tg_id == config.OWNER_TG_ID)

        # ─── چک اشتراک (پلن) ──────────────────────────────────────────────────
        if not is_owner and not db.is_subscribed(owner_id):
            return False

        # ─── محاسبه‌ی زمان باقی‌مانده‌ی سشن (قبل از وصل شدن) ────────────────────
        now_ts = time.time()
        remaining = None
        reset_started_at = False
        if config.BOT_TOKEN and not is_owner:
            if is_restart:
                started_raw = db.get_setting(owner_id, "session_started_at", "")
                try:
                    started_at = float(started_raw) if started_raw else None
                except (TypeError, ValueError):
                    started_at = None
                if started_at is None:
                    started_at = now_ts
                remaining = (config.SESSION_HOURS * 3600) - (now_ts - started_at)
                if remaining <= 0:
                    # سشن قبلی تموم شده، ولی چون این یک اتصال مجدد خودکار بعد از
                    # ری‌استارت سرور است، کاربر را قطع نمی‌کنیم — یک پنجره‌ی تازه می‌دهیم
                    remaining = config.SESSION_HOURS * 3600
                    reset_started_at = True
            else:
                remaining = config.SESSION_HOURS * 3600
                reset_started_at = True

        tokens_deducted = 0
        if config.BOT_TOKEN and check_tokens and not is_owner:
            balance = db.get_token_balance(owner_id)
            if balance < config.TOKENS_PER_SESSION:
                return False
            db.deduct_tokens(owner_id, config.TOKENS_PER_SESSION)
            tokens_deducted = config.TOKENS_PER_SESSION

        entry = {"client": None, "task": None, "stop": False, "is_owner": is_owner,
                 "tokens_deducted": tokens_deducted, "owner_refunded": False, "paused": False}
        self._bots[owner_id] = entry
        task = asyncio.run_coroutine_threadsafe(
            self._run_bot(owner_id), loop
        )
        entry["task"] = task

        if config.BOT_TOKEN and not is_owner:
            self._cancel_timer(owner_id)
            if reset_started_at:
                # شروع تازه‌ی سشن (لاگین جدید، استارت دستی، یا ری‌استارت بعد از تمام
                # شدن پنجره‌ی قبلی) → زمان شروع جدید در Supabase ثبت می‌شود
                db.set_setting(owner_id, "session_started_at", str(now_ts))
            timer = threading.Timer(
                remaining, self.stop, args=[owner_id]
            )
            timer.daemon = True
            timer._timer_start = now_ts
            timer.start()
            self._timers[owner_id] = timer

        # ─── تایمر چک دوره‌ای اشتراک (هر ۵ دقیقه) ──────────────────────────
        if not is_owner:
            self._start_subscription_watcher(owner_id)

        return True

    def pause(self, owner_id: int):
        """کانکشن تلگرام رو نگه می‌داره ولی تمام عملیات سلف رو متوقف می‌کنه"""
        entry = self._bots.get(owner_id)
        if not entry or entry.get("is_owner"):
            return
        if not entry.get("paused"):
            entry["paused"] = True
            print(f"⏸️  [{owner_id}] پلن منقضی — سلف موقتاً متوقف شد (اتصال زنده‌ست)")

    def resume(self, owner_id: int):
        """بعد از تمدید پلن، سلف رو دوباره فعال می‌کنه"""
        entry = self._bots.get(owner_id)
        if not entry:
            return
        if entry.get("paused"):
            entry["paused"] = False
            print(f"▶️  [{owner_id}] پلن تمدید شد — سلف دوباره فعال شد")

    def is_paused(self, owner_id: int) -> bool:
        entry = self._bots.get(owner_id)
        return bool(entry and entry.get("paused"))

    def _subscription_check(self, owner_id: int):
        """هر ۵ دقیقه پلن رو چک می‌کنه — pause/resume می‌کنه، disconnect نمی‌کنه"""
        if not self.is_running(owner_id):
            return
        entry = self._bots.get(owner_id)
        if entry and entry.get("is_owner"):
            return
        if not db.is_subscribed(owner_id):
            self.pause(owner_id)
        else:
            # اگه پلن تمدید شده بود، resume کن
            self.resume(owner_id)
        # چک بعدی ۵ دقیقه دیگه
        self._start_subscription_watcher(owner_id)

    def _start_subscription_watcher(self, owner_id: int):
        """یک تایمر ۵ دقیقه‌ای برای چک پلن راه‌اندازی می‌کنه"""
        t = threading.Timer(300, self._subscription_check, args=[owner_id])
        t.daemon = True
        t.start()
        # نگه داشتن رفرنس در یک دیکشنری جداگانه
        if not hasattr(self, '_sub_watchers'):
            self._sub_watchers = {}
        self._sub_watchers[owner_id] = t

    def stop(self, owner_id: int):
        self._cancel_timer(owner_id)
        # لغو watcher پلن
        if hasattr(self, '_sub_watchers'):
            w = self._sub_watchers.pop(owner_id, None)
            if w:
                w.cancel()
        entry = self._bots.get(owner_id)
        if not entry:
            return
        entry["stop"] = True
        cl = entry.get("client")
        if cl and cl.is_connected():
            try:
                asyncio.run_coroutine_threadsafe(cl.disconnect(), asyncio.get_event_loop())
            except Exception:
                pass

    def stop_all(self):
        for oid in list(self._bots.keys()):
            self.stop(oid)

    async def _run_bot(self, owner_id: int):
        entry = self._bots[owner_id]
        retry_delay = 5

        while not entry["stop"]:
            try:
                session_data = db.get_setting(owner_id, "session_data", "")
                if not session_data:
                    await asyncio.sleep(2)
                    continue

                cl = TelegramClient(
                    StringSession(session_data),
                    config.API_ID,
                    config.API_HASH,
                )
                entry["client"] = cl
                _register_handlers(cl, owner_id, entry)

                await cl.start()
                me = await cl.get_me()
                print(f"✅ [{owner_id}] بات راه‌اندازی شد — {me.first_name} (@{me.username})")

                db.save_telegram_user_id(owner_id, me.id)
                _last_outgoing_activity[owner_id] = time.time()

                # ✅ تشخیص مالک - اصلاح شده با ۳ روش
                me_phone = (me.phone or "").lstrip("+")
                owner_phone = getattr(config, "OWNER_PHONE", "").lstrip("+")
                
                is_now_owner = (
                    me.id == config.OWNER_TG_ID or
                    (bool(owner_phone) and me_phone == owner_phone) or
                    me.username == getattr(config, "OWNER_USERNAME", "")
                )

                if is_now_owner:
                    entry["is_owner"] = True
                    self._cancel_timer(owner_id)
                    if not entry.get("owner_refunded") and entry.get("tokens_deducted", 0) > 0:
                        db.add_tokens(owner_id, entry["tokens_deducted"])
                        entry["owner_refunded"] = True
                        print(f"👑 [{owner_id}] مالک تشخیص داده شد - {entry['tokens_deducted']} توکن برگشت داده شد")
                    print(f"👑 [{owner_id}] مالک: @{me.username} (ID: {me.id}) — تایمر لغو — رایگان ♾️")

                # ✅ استارت ساعت با دقت بالا
                clock_task = asyncio.ensure_future(_clock_loop(cl, owner_id))
                sched_task = asyncio.ensure_future(_scheduler_loop(cl, owner_id))
                typing_task = asyncio.ensure_future(_typing_loop(cl, owner_id))
                tabchi_task = asyncio.ensure_future(_tabchi_loop(cl, owner_id))
                meowie_task = asyncio.ensure_future(meowie_game.meowie_loop(cl, owner_id, db))

                retry_delay = 5
                await cl.run_until_disconnected()

                clock_task.cancel()
                sched_task.cancel()
                typing_task.cancel()
                tabchi_task.cancel()
                meowie_task.cancel()

                if entry["stop"]:
                    break

                # ✅ چک کن session هنوز در دیتابیس وجود داره
                try:
                    session_data = db.get_setting(owner_id, "session_data", "")
                    if not session_data:
                        print(f"⚠️  [{owner_id}] session حذف شده — توقف کامل")
                        break
                except Exception:
                    break

                print(f"⚠️  [{owner_id}] اتصال قطع شد، اتصال مجدد...")

            except Exception as e:
                err_str = str(e)
                print(f"❌ [{owner_id}] خطا: {e}")

                # ✅ اگه session توسط تلگرام باطل شده، نیاز به لاگین مجدد
                if any(k in err_str for k in ("AUTH_KEY_UNREGISTERED", "SESSION_REVOKED",
                                               "USER_DEACTIVATED", "UnauthorizedError")):
                    print(f"❌ [{owner_id}] Session باطل شده — نیاز به لاگین مجدد")
                    db.set_setting(owner_id, "logged_in", "0")
                    db.set_setting(owner_id, "session_data", "")
                    break

                if entry["stop"]:
                    break

            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 120)

        print(f"🛑 [{owner_id}] بات متوقف شد.")


bot_manager = BotManager()



# ─── ثبت هندلرها (per-user) ────────────────────────────────────────────────────
def _register_handlers(cl: TelegramClient, owner_id: int, entry: dict):

    # ─── بازی میویی (@MeowieeeQBot) ───
    meowie_game.register_handlers(cl, owner_id, db)

    # ─── قفل لاگین ──────────────────────────────────────────────────────────
    # منطق: تلگرام هر ورودِ جدید به اکانت رو به‌صورت پیام از طرفِ «اعلان‌های
    # سرویس» (چتِ ۷۷۷۰۰۰) گزارش می‌ده. وقتی این قفل روشنه:
    #   - اگه از قبل یک دستگاهِ دیگه (غیر از خودِ همین سلف) روی اکانت فعال
    #     بوده باشه، ورودِ تازه رو بلافاصله قطع (لاگ‌اوت) می‌کنیم و هرچی
    #     مشخصات ازش گیر بیاد (اپ/دستگاه/سیستم‌عامل/کشور/آی‌پی/زمان) رو تو
    #     سیو مسیج می‌فرستیم.
    #   - اگه هیچ دستگاهِ دیگه‌ای (بجز خودِ سلف) از قبل روی اکانت نبود، یعنی
    #     صاحبِ اکانت از همه‌جا بیرون افتاده بوده و این احتمالاً خودشه که
    #     داره برمی‌گرده — پس کاری باهاش نداریم و اجازه می‌دیم بمونه.
    @cl.on(events.NewMessage(chats=777000))
    async def _login_lock_guard(event):
        if db.get_setting(owner_id, "login_lock_active", "0") != "1":
            return

        msg_text = event.message.text or ""
        # پیامِ رسمیِ تلگرام برای ورودِ جدید بسته به زبانِ اکانت فرق می‌کنه؛
        # چندتا کلیدواژه‌ی رایج (فارسی/انگلیسی) رو پوشش می‌دیم.
        keywords = ("New login", "ورود جدید", "login was detected", "وارد شدید", "new device")
        if not any(k.lower() in msg_text.lower() for k in keywords):
            return

        try:
            from telethon.tl.functions.account import GetAuthorizationsRequest, ResetAuthorizationRequest
            result = await cl(GetAuthorizationsRequest())
            auths = list(result.authorizations)
        except Exception as e:
            print(f"❌ [{owner_id}] قفل لاگین: خطا در گرفتنِ لیستِ سشن‌ها: {e}")
            return

        non_current = [a for a in auths if not getattr(a, "current", False)]
        if not non_current:
            return  # فقط خودِ سلف فعاله؛ چیزی برای بررسی نیست

        # جدیدترین سشنِ غیرِ-جاری همون تلاشِ ورودِ تازه‌ست
        non_current.sort(key=lambda a: getattr(a, "date_created", 0), reverse=True)
        newest = non_current[0]
        others = non_current[1:]  # دستگاه‌های دیگه‌ای که از قبل (پیش از این ورود) موجود بودن

        if not others:
            print(f"🔓 [{owner_id}] قفل لاگین: دستگاهِ دیگه‌ای از قبل روی اکانت نبود — ورودِ جدید مجاز شمرده شد.")
            return

        try:
            await cl(ResetAuthorizationRequest(hash=newest.hash))
            print(f"🔒 [{owner_id}] قفل لاگین: سشنِ تازه‌ی مشکوک قطع شد.")
        except Exception as e:
            print(f"❌ [{owner_id}] قفل لاگین: خطا در قطعِ سشنِ تازه: {e}")

        def _fmt(v, fallback="نامشخص"):
            return str(v) if v not in (None, "") else fallback

        date_created = getattr(newest, "date_created", None)
        try:
            date_str = date_created.strftime("%Y-%m-%d %H:%M:%S UTC") if date_created else "نامشخص"
        except Exception:
            date_str = str(date_created) if date_created else "نامشخص"

        report = (
            "🚨 قفل لاگین: یک تلاشِ ورودِ جدید به اکانتت شناسایی و قطع شد.\n\n"
            f"📱 اپ: {_fmt(getattr(newest, 'app_name', None))} {_fmt(getattr(newest, 'app_version', None), '')}\n"
            f"💻 دستگاه: {_fmt(getattr(newest, 'device_model', None))}\n"
            f"🖥 سیستم‌عامل: {_fmt(getattr(newest, 'platform', None))} {_fmt(getattr(newest, 'system_version', None), '')}\n"
            f"🌍 کشور/منطقه: {_fmt(getattr(newest, 'country', None))} {_fmt(getattr(newest, 'region', None), '')}\n"
            f"🌐 آی‌پی: {_fmt(getattr(newest, 'ip', None))}\n"
            f"🕒 زمانِ تلاشِ ورود: {date_str}"
        )
        try:
            await cl.send_message("me", report)
        except Exception as e:
            print(f"❌ [{owner_id}] قفل لاگین: خطا در ارسالِ گزارش به سیو مسیج: {e}")

    @cl.on(events.NewMessage(incoming=True))
    async def on_incoming(event):
        # اگه پلن منقضی شده، هیچ کاری نکن (اتصال زنده‌ست)
        if entry.get("paused"):
            return
        msg = event.message
        sender = await event.get_sender()
        chat = await event.get_chat()
        sender_id = getattr(sender, "id", 0)
        chat_id = getattr(chat, "id", 0)
        text = msg.text or ""
        is_bot_sender = bool(getattr(sender, "bot", False))

        # نگهبان چت: کش کردن متن پیام برای تشخیص بعدیِ حذف/ویرایش
        # نکته‌ی مهم: کلیدِ کش باید از event.chat_id ساخته بشه (همون چیزی که
        # on_edited/on_deleted استفاده می‌کنن)، نه از chat.id که برای گروه‌ها/
        # سوپرگروه‌ها/کانال‌ها یه عدد متفاوت (بدون پیشوند -100) برمی‌گردونه؛
        # قبلاً همین اختلاف باعث می‌شد کش توی گروه‌ها اصلاً پیدا نشه و پیام‌ها
        # ناقص (یا اصلاً هیچی) ذخیره بشن.
        cache_key = (event.chat_id, msg.id)
        if sender_id != owner_id:
            who_name = getattr(sender, "first_name", None) or getattr(sender, "username", None) or str(sender_id)
            _msg_cache[cache_key] = text
            _msg_sender_cache[cache_key] = who_name

            # اگه پیام رسانه داره (عکس/ویدیو/گیف/استیکر و ...) و «ذخیره پیام
            # حذف‌شده» روشنه، خودِ رسانه رو هم دانلود می‌کنیم تا اگه پیام حذف
            # شد، بشه عینِ همون رسانه رو هم برای خودت فرستاد، نه فقط یه متنِ
            # خالی. این قابلیت فقط تویِ پیوی معنا داره (طبق درخواست)، پس
            # برای گروه/سوپرگروه/کانال اصلاً دانلود نمی‌کنیم.
            if msg.media and event.is_private and db.get_setting(owner_id, "guard_delete_active") == "1":
                try:
                    guard_dir = f"saved_media/_guard/{owner_id}"
                    os.makedirs(guard_dir, exist_ok=True)
                    media_path = await cl.download_media(msg, file=guard_dir + "/")
                    _msg_media_cache[cache_key] = media_path
                except Exception:
                    _msg_media_cache[cache_key] = None
            else:
                _msg_media_cache[cache_key] = None

            if len(_msg_cache) > _MSG_CACHE_MAX:
                for k in list(_msg_cache.keys())[:200]:
                    _msg_cache.pop(k, None)
                    _msg_sender_cache.pop(k, None)
                    old_media = _msg_media_cache.pop(k, None)
                    if old_media:
                        try:
                            os.remove(old_media)
                        except Exception:
                            pass

        # ✅ سکوت: اگه فرستنده توی لیست سکوت باشه و پیوی باشه، پیام دوطرفه پاک می‌شه
        if event.is_private and sender_id and _is_silence_user(owner_id, sender_id):
            try:
                await msg.delete(revoke=True)
            except Exception:
                pass
            return

        # ✅ بررسی آیا ربات تگ شده است (برای گروه‌ها)
        is_tagged = False
        if not event.is_private:
            me = await cl.get_me()
            if msg.entities:
                for entity in msg.entities:
                    if hasattr(entity, 'user_id') and entity.user_id == me.id:
                        is_tagged = True
                        break
            replied_msg = await event.get_reply_message()
            if replied_msg and replied_msg.sender_id == me.id:
                is_tagged = True
            if me.username and me.username.lower() in text.lower():
                is_tagged = True

        # ✅ تگ رندوم توسط اعضای گروه — «تگ [تعداد]» فقط برای مالک/ادمین گروه
        # (این با فرمان «تگ [متن]» خود صاحب سلف که در on_outgoing هندل می‌شه فرق داره؛
        # اینجا هر عضو گروه می‌تونه بزنه، ولی فقط اگه خودش ادمین یا سازنده‌ی گروه باشه)
        if not event.is_private and not is_bot_sender:
            tag_count_match = re.match(r"^تگ\s+(\d+)\s*$", text.strip())
            if tag_count_match:
                requested_count = int(tag_count_match.group(1))
                requested_count = max(1, min(requested_count, 50))
                is_privileged = False
                try:
                    perms = await cl.get_permissions(chat_id, sender_id)
                    is_privileged = bool(perms.is_admin or perms.is_creator)
                except Exception:
                    is_privileged = False

                if is_privileged:
                    try:
                        members = []
                        async for user in cl.iter_participants(chat_id):
                            if user.bot or user.deleted or not user.username:
                                continue
                            members.append(user)
                    except Exception:
                        members = []
                    if members:
                        picked = random.sample(members, min(requested_count, len(members)))
                        mention_text = " ".join(f"@{u.username}" for u in picked)
                        try:
                            await cl.send_message(chat_id, mention_text)
                        except Exception:
                            pass
                    else:
                        try:
                            await event.reply("⛔ عضوی با یوزرنیم برای تگ کردن پیدا نشد.")
                        except Exception:
                            pass
                else:
                    try:
                        await event.reply("⛔ این دستور فقط برای مالک یا ادمین‌های گروه است.")
                    except Exception:
                        pass
                return

        # ✅ اگر در گروه است و تگ نشده، فقط کارهای خودکار را انجام بده
        if not event.is_private and not is_tagged:
            if db.get_setting(owner_id, "auto_seen_active") == "1":
                try:
                    await cl.send_read_acknowledge(chat_id, msg)
                except Exception:
                    pass
            
            if db.get_setting(owner_id, "auto_save_media") == "1" and msg.media:
                try:
                    media_dir = f"saved_media/{owner_id}"
                    os.makedirs(media_dir, exist_ok=True)
                    await cl.download_media(msg, file=media_dir + "/")
                except Exception:
                    pass
            return

        if db.is_silent_chat(owner_id, chat_id) or db.is_silent_user(owner_id, sender_id):
            return

        # ذخیره خودکار مدیا
        if db.get_setting(owner_id, "auto_save_media") == "1" and msg.media:
            try:
                media_dir = f"saved_media/{owner_id}"
                os.makedirs(media_dir, exist_ok=True)
                await cl.download_media(msg, file=media_dir + "/")
            except Exception:
                pass

        # ذخیره مدیای تایمدار
        if event.is_private and msg.media:
            ttl = getattr(msg.media, "ttl_seconds", None)
            if ttl:
                try:
                    me = await cl.get_me()
                    media_dir = f"saved_media/{owner_id}"
                    os.makedirs(media_dir, exist_ok=True)
                    path = await cl.download_media(msg, file=media_dir + "/")
                    if path:
                        await cl.send_file(me.id, path,
                            caption=f"📥 مدیای تایمدار ذخیره شد\n👤 از: {getattr(sender, 'first_name', sender_id)} ({sender_id})")
                except Exception:
                    pass

        # سین خودکار
        if db.get_setting(owner_id, "auto_seen_active") == "1":
            try:
                await cl.send_read_acknowledge(chat_id, msg)
            except Exception:
                pass

        # ✅ جوین اجباری (فقط پیوی، چند کاناله) — با بات‌ها کاری نداشته باش
        if event.is_private and not is_bot_sender and db.get_setting(owner_id, "force_join_active") == "1":
            fj_channels = _get_force_join_channels(owner_id)
            if fj_channels:
                from telethon.tl.functions.channels import GetParticipantRequest
                from telethon.errors import UserNotParticipantError, ChannelPrivateError

                is_member_all = True
                for ch in fj_channels:
                    ch_id = ch.get("id")
                    if not ch_id:
                        continue
                    is_member = False
                    try:
                        channel_entity = await cl.get_entity(int(ch_id) if str(ch_id).lstrip("-").isdigit() else ch_id)
                        await cl(GetParticipantRequest(channel_entity, sender_id))
                        is_member = True
                    except (UserNotParticipantError, KeyError):
                        is_member = False
                    except ChannelPrivateError:
                        is_member = True  # کانال خصوصی — نمی‌تونیم چک کنیم، رد می‌کنیم
                    except Exception:
                        is_member = True  # خطای ناشناخته — رد می‌کنیم تا اشتباهاً بلاک نشه
                    if not is_member:
                        is_member_all = False
                        break

                if not is_member_all:
                    # پیام رو حذف کن
                    try:
                        await msg.delete()
                    except Exception:
                        pass

                    # پیام هشدار جوین اجباری — دقیقاً مثل پنل، از طریقِ inline
                    # query به ربات کمکی و کلیک روی نتیجه توسط خودِ سلف فرستاده
                    # می‌شه؛ یعنی «via @helper_bot» ولی دکمه‌هاش واقعاً کار می‌کنن،
                    # بدون این‌که کاربر مجبور باشه قبلش با ربات کمکی استارت زده باشه.
                    join_msg = db.get_setting(owner_id, "force_join_message",
                        "⛔ برای ارسال پیام ابتدا باید در کانال‌های زیر عضو شوید.")
                    sent_via_helper = False
                    try:
                        if config.HELPER_BOT_TOKEN:
                            from helper_bot import get_helper_client
                            helper = get_helper_client()
                            uname = None
                            if helper:
                                try:
                                    me = await helper.get_me()
                                    uname = me.username
                                except Exception:
                                    uname = None
                            if uname:
                                results = await cl.inline_query(uname, "جوین")
                                if results:
                                    sent = await results[0].click(event.chat_id)
                                    if sent is not None:
                                        sent_via_helper = True
                    except Exception:
                        sent_via_helper = False

                    if not sent_via_helper:
                        # جایگزین: پیام ساده از خود سلف (بدون دکمه چون سلف نمی‌تونه
                        # دکمه‌ی شیشه‌ی واقعی بفرسته)
                        try:
                            links_text = "\n".join(
                                f"📢 {c.get('title', '؟')}: {c.get('link', '')}" for c in fj_channels
                            )
                            await cl.send_message(sender_id, f"{join_msg}\n\n{links_text}")
                        except Exception:
                            pass
                    return

        # ✅ منشی (فقط پیوی - با محدودیت 24 ساعت) — با بات‌ها کاری نداشته باش
        if db.get_setting(owner_id, "secretary_active") == "1" and event.is_private and not is_bot_sender:
            now = time.time()
            last_reply = _last_secretary_reply.get(chat_id, 0)
            
            if now - last_reply >= SECRETARY_COOLDOWN:
                sec_msg = db.get_setting(owner_id, "secretary_message", "در حال حاضر در دسترس نیستم.")
                try:
                    await event.reply(sec_msg)
                    _last_secretary_reply[chat_id] = now
                except Exception:
                    pass
            return

        # ✅ دستیار هوش مصنوعی (دیپ‌سیک) — یا فقط وقتی غایب باشی، یا به همه پیام‌ها
        if (
            db.get_setting(owner_id, "ai_assistant_active") == "1"
            and event.is_private
            and sender_id != owner_id
            and not is_bot_sender
        ):
            always_mode = db.get_setting(owner_id, "ai_reply_always_active") == "1"
            last_active = _last_outgoing_activity.get(owner_id, 0)
            is_away = (time.time() - last_active) >= AI_AWAY_SECONDS
            if (always_mode or is_away) and text.strip():
                now = time.time()
                last_ai_reply = _last_ai_reply.get(chat_id, 0)
                if now - last_ai_reply >= AI_REPLY_COOLDOWN:
                    knowledge = db.get_setting(owner_id, "ai_knowledge_base", "")
                    try:
                        answer = await _ask_deepseek(knowledge, text)
                        if answer:
                            await event.reply(answer)
                            _last_ai_reply[chat_id] = now
                    except Exception as e:
                        print(f"خطا در پاسخ هوش مصنوعی: {e}")


        # ✅ ری‌اکشن خودکار — با بات‌ها کاری نداشته باش
        if not is_bot_sender and db.get_setting(owner_id, "auto_reaction_active") == "1":
            emoji = db.get_setting(owner_id, "auto_reaction_emoji", "❤️")
            try:
                from telethon.tl.functions.messages import SendReactionRequest
                from telethon.tl.types import ReactionEmoji
                await cl(SendReactionRequest(
                    peer=chat_id,
                    msg_id=msg.id,
                    reaction=[ReactionEmoji(emoticon=emoji)],
                    big=False,
                    add_to_recent=True
                ))
            except Exception as e:
                print(f"⚠️ خطا در ری‌اکشن: {e}")

        # ✅ ری‌اکشن اختصاصی برای یک کاربر خاص — با بات‌ها کاری نداشته باش
        react_map = _get_react_map(owner_id)
        if not is_bot_sender and str(sender_id) in react_map:
            try:
                from telethon.tl.functions.messages import SendReactionRequest
                from telethon.tl.types import ReactionEmoji
                await cl(SendReactionRequest(
                    peer=chat_id,
                    msg_id=msg.id,
                    reaction=[ReactionEmoji(emoticon=react_map[str(sender_id)])],
                    big=False,
                    add_to_recent=True
                ))
            except Exception:
                pass

        # ✅ پاسخ خودکار محبت‌آمیز به دوستان (فقط در پیوی - با محدودیت 1 ساعت) — با بات‌ها کاری نداشته باش
        if event.is_private and not is_bot_sender and db.is_friend(owner_id, sender_id):
            now = time.time()
            last_reply = _last_friend_reply.get(sender_id, 0)
            
            if now - last_reply >= FRIEND_COOLDOWN:
                try:
                    await event.reply(random.choice(FRIEND_REPLIES))
                    _last_friend_reply[sender_id] = now
                except Exception:
                    pass

        # پاسخ به دشمن — با بات‌ها کاری نداشته باش
        if not is_bot_sender and db.get_setting(owner_id, "enemy_reply_active") == "1" and db.is_enemy(owner_id, sender_id):
            try:
                await event.reply(random.choice(ENEMY_REPLIES))
            except Exception:
                pass

        # ضد لینک (فقط پیوی)
        if db.get_setting(owner_id, "anti_link_active") == "1" and event.is_private and LINK_PATTERN.search(text):
            try:
                await msg.delete()
            except Exception:
                pass

        # قفل پیوی (حذف پیام ورودی در پیوی)
        if db.get_setting(owner_id, "private_lock_active") == "1" and event.is_private:
            try:
                await msg.delete()
            except Exception:
                pass

        # قفل یوزرنیم (پیامی که داخلش منشن @username باشه)
        if db.get_setting(owner_id, "lock_username_active") == "1" and event.is_private and re.search(r"@\w{4,32}", text):
            try:
                await msg.delete()
            except Exception:
                pass

        # قفل ریپلای (پیامی که روی پیام دیگه‌ای ریپلای شده)
        if db.get_setting(owner_id, "lock_reply_active") == "1" and event.is_private and msg.is_reply:
            try:
                await msg.delete()
            except Exception:
                pass

        # قفل گیف
        if db.get_setting(owner_id, "lock_gif_active") == "1" and event.is_private and msg.gif:
            try:
                await msg.delete()
            except Exception:
                pass

        # قفل عکس
        if db.get_setting(owner_id, "lock_photo_active") == "1" and event.is_private and msg.photo:
            try:
                await msg.delete()
            except Exception:
                pass

        # قفل استیکر
        if db.get_setting(owner_id, "lock_sticker_active") == "1" and event.is_private and msg.sticker:
            try:
                await msg.delete()
            except Exception:
                pass

        # قفل فوروارد (پیامِ فوروارد شده از یک چت دیگه)
        if db.get_setting(owner_id, "lock_forward_active") == "1" and event.is_private and msg.forward:
            try:
                await msg.delete()
            except Exception:
                pass

        # فیلتر کلمات (پیوی): اگه پیام حاوی یکی از کلمات فیلترشده باشه حذف می‌شه
        if db.get_setting(owner_id, "word_filter_active") == "1" and event.is_private and text:
            words = _get_filtered_words(owner_id)
            if any(w and w in text for w in words):
                try:
                    await msg.delete()
                except Exception:
                    pass

        # نگهبان چت: ذخیره عکس‌های تایمی (view-once/self-destruct) قبل از پاک‌شدن
        if db.get_setting(owner_id, "guard_view_once_active") == "1" and sender_id != owner_id:
            ttl = getattr(getattr(msg, "media", None), "ttl_seconds", None)
            if ttl and msg.photo:
                try:
                    path = await cl.download_media(msg)
                    if path:
                        await cl.send_file("me", path, caption="عکس تایمی ذخیره‌شده")
                        os.remove(path)
                except Exception:
                    pass

        # پاسخ کلیدی: اگه توی متن پیام یکی از کلمه‌های تنظیم‌شده باشه، پاسخ اختصاصی
        # همون کلمه ارسال میشه (مستقل از پاسخ ثابت پایین) — با بات‌ها کاری نداشته باش
        if event.is_private and sender_id != owner_id and not is_bot_sender and text.strip():
            keyword_rules = _get_keyword_replies(owner_id)
            if keyword_rules:
                matched_reply = None
                lower_text = text.lower()
                for rule in keyword_rules:
                    kw = rule.get("keyword", "")
                    if kw and kw.lower() in lower_text:
                        matched_reply = rule.get("reply")
                        break
                if matched_reply:
                    now = time.time()
                    last = _last_auto_reply.get(chat_id, 0)
                    if now - last >= AUTO_REPLY_COOLDOWN:
                        try:
                            await event.reply(matched_reply)
                            _last_auto_reply[chat_id] = now
                        except Exception:
                            pass
                    return

        # پاسخ خودکار ثابت به همه‌ی پیام‌ها (با کول‌داون مستقل از منشی/هوش‌مصنوعی) — با بات‌ها کاری نداشته باش
        # فقط وقتی کار می‌کنه که کاربر خودش یه متن برای پاسخ خودکار تنظیم کرده باشه؛
        # اگه متنی تنظیم نشده باشه، حتی اگه روشن باشه، هیچ پیامی فرستاده نمی‌شه.
        if db.get_setting(owner_id, "auto_reply_active") == "1" and sender_id != owner_id and not is_bot_sender and text.strip():
            msg_text = db.get_setting(owner_id, "auto_reply_message", "").strip()
            if msg_text:
                now = time.time()
                last = _last_auto_reply.get(chat_id, 0)
                if now - last >= AUTO_REPLY_COOLDOWN:
                    try:
                        await event.reply(msg_text)
                        _last_auto_reply[chat_id] = now
                    except Exception:
                        pass

    @cl.on(events.MessageEdited())
    async def on_edited(event):
        """نگهبان چت: اگه پیامِ یک نفر دیگه ویرایش شد، نسخه‌ی قبل/بعد رو برای خودت (Saved Messages) بفرست."""
        try:
            if event.out:
                return
            if db.get_setting(owner_id, "guard_edit_active") != "1":
                return
            key = (event.chat_id, event.id)
            old_text = _msg_cache.get(key)
            new_text = event.raw_text
            if old_text is not None and old_text != new_text:
                who = _msg_sender_cache.get(key)
                if not who:
                    sender = await event.get_sender()
                    who = getattr(sender, "first_name", None) or getattr(sender, "username", None) or event.sender_id
                try:
                    from telethon.tl.types import MessageEntityBlockquote
                    header = f"پیام ویرایش شده\nاز طرف: {who}\nپیام قبلی\n"
                    mid = f"\n\nپیام جدید\n"
                    full = header + old_text + mid + new_text
                    old_start = _u16len(header)
                    new_start = _u16len(header) + _u16len(old_text) + _u16len(mid)
                    await cl.send_message(
                        "me", full,
                        formatting_entities=[
                            MessageEntityBlockquote(old_start, _u16len(old_text), collapsed=False),
                            MessageEntityBlockquote(new_start, _u16len(new_text), collapsed=False),
                        ]
                    )
                except Exception:
                    pass
                _msg_sender_cache[key] = who
            _msg_cache[key] = new_text
        except Exception:
            pass

    @cl.on(events.MessageDeleted())
    async def on_deleted(event):
        """نگهبان چت: اگه پیامِ یک نفر دیگه حذف شد و توی کش بود، برای خودت بفرست (فقط پیوی)."""
        try:
            if db.get_setting(owner_id, "guard_delete_active") != "1":
                return
            # طبق درخواست: این قابلیت فقط تویِ پیوی کار کنه، نه گروه/سوپرگروه/کانال.
            if not event.is_private:
                return
            chat_id_del = event.chat_id
            for msg_id in event.deleted_ids:
                key = (chat_id_del, msg_id)
                # قبلاً اینجا `if cached:` بود که چون رشته‌ی خالی هم False حساب
                # می‌شه، پیام‌های رسانه‌ایِ بدون کپشن (که متنشون "" کش شده بود)
                # اصلاً گزارش نمی‌شدن و به نظر می‌رسید نگهبان چت ناقص کار می‌کنه.
                if key not in _msg_cache:
                    continue
                cached = _msg_cache.pop(key, None) or ""
                who = _msg_sender_cache.pop(key, None) or "نامشخص"
                media_path = _msg_media_cache.pop(key, None)
                try:
                    from telethon.tl.types import MessageEntityBlockquote
                    header = f"پیام حذف شده\nاز طرف: {who}\n"
                    body = cached if cached else "(بدون متن — فقط رسانه)"
                    header += "پیام قبلی\n"
                    full = header + body
                    entity_start = _u16len(header)
                    if media_path and os.path.exists(media_path):
                        await cl.send_file(
                            "me", media_path, caption=full,
                            formatting_entities=[MessageEntityBlockquote(entity_start, _u16len(body), collapsed=False)]
                        )
                        try:
                            os.remove(media_path)
                        except Exception:
                            pass
                    else:
                        await cl.send_message(
                            "me", full,
                            formatting_entities=[MessageEntityBlockquote(entity_start, _u16len(body), collapsed=False)]
                        )
                except Exception:
                    pass
        except Exception:
            pass

    @cl.on(events.NewMessage(outgoing=True))
    async def on_outgoing(event):
        raw = event.raw_text.strip()
        had_dot = raw.startswith(".") and len(raw) > 1
        # قبول کردن پیشوند نقطه («.دستور» هم مثل «دستور» کار کند)
        text = raw[1:].strip() if had_dot else raw

        # ثبت آخرین فعالیتِ خودِ کاربر — برای تشخیص «غایب/آفلاین» دستیار هوش مصنوعی
        _last_outgoing_activity[owner_id] = time.time()

        # دستورات همیشه فعال
        if text == "سلف روشن":
            db.set_setting(owner_id, "self_bot_active", "1")
            await _safe_edit(event, owner_id, "✅ سلف‌بات روشن شد.")
            return
        if text == "سلف خاموش":
            db.set_setting(owner_id, "self_bot_active", "0")
            await _safe_edit(event, owner_id, "❌ سلف‌بات خاموش شد.")
            return

        # اگه پلن منقضی شده، فقط دستور وضعیت رو اجرا کن — بدون دست‌کاری بقیه‌ی پیام‌های خروجی
        if entry.get("paused"):
            if text in ("وضعیت", "راهنما", "help"):
                pass  # اجازه بده ادامه پیدا کنه
            else:
                # پیام معمولیه (نه دستور سلف) → اصلاً دست نمی‌زنیم، ادیت نمی‌کنیم
                return

        # لیست دستورات تنظیماتی که همیشه فعال هستند
        config_commands = [
            "منشی روشن", "منشی خاموش", "پیام منشی",
            "ضد حذف روشن", "ضد حذف خاموش",
            "ضد لینک روشن", "ضد لینک خاموش",
            "قفل پیوی روشن", "قفل پیوی خاموش",
            "سین خودکار روشن", "سین خودکار خاموش",
            "ری‌اکشن روشن", "ری‌اکشن خاموش",
            "ذخیره مدیا روشن", "ذخیره مدیا خاموش",
            "ساعت نام روشن", "ساعت نام خاموش",
            "ساعت بیو روشن", "ساعت بیو خاموش",
            "پاسخ دشمن روشن", "پاسخ دشمن خاموش",
            "تنظیم دشمن", "حذف دشمن", "نمایش لیست دشمن", "پاک کردن لیست دشمن",
            "تنظیم دوست", "حذف دوست", "نمایش لیست دوست", "پاک کردن لیست دوست",
            "سایلنت چت روشن", "سایلنت چت خاموش", "سایلنت کاربر", "لغو سایلنت کاربر",
            "سکوت", "لغو سکوت", "لیست سکوت",
            "فونت ", "لیست فونت", "فونت متن روشن", "فونت متن خاموش",
            "بولد ", "ایتالیک ", "مونو ", "اسپویلر ", "کوت ", "خط‌خورده ", "زیرخط ",
            "ذخیره ", "ارسال ذخیره ",
            "ترجمه ", "هوا ", "قیمت دلار", "ارز",
            "وضعیت", "راهنما", "help",
            "حذف بعد ",
            "سیو کانال", "توقف سیو",
            "افزودن کانال ", "حذف کانال ", "جوین اجباری روشن", "جوین اجباری خاموش",
            "پیام جوین ", "پاک کردن کانال‌های اجباری", "لیست کانال‌های اجباری",
            "پنل", "panel",
        ]

        is_config_command = any(text.startswith(cmd) or text == cmd for cmd in config_commands)

        # اگر دستور تنظیماتی نیست و سلف خاموش است، اجرا نکن
        if not is_config_command and db.get_setting(owner_id, "self_bot_active") != "1":
            return

        await _handle_command(cl, event, text, owner_id, entry, had_dot=had_dot)



    # ─── تاس (send_dice) ─────────────────────────────────────────────────────────
    async def send_dice(ev, dice_type, target=None, max_tries=80):
        reply_to = ev.reply_to_msg_id
        tries = 0
        while True:
            tries += 1
            try:
                if reply_to:
                    msg = await ev.reply(file=InputMediaDice(dice_type))
                else:
                    msg = await ev.respond(file=InputMediaDice(dice_type))
            except FloodWaitError as e:
                await asyncio.sleep(e.seconds + 1)
                continue

            if target is None or (msg.media and msg.media.value == target) or tries >= max_tries:
                break

            await asyncio.sleep(0.5)
            try:
                await msg.delete()
            except Exception:
                pass

    @cl.on(events.MessageEdited(outgoing=True, pattern=r"(?i)^\.(?:تاس|roll)(?:\s+(\d))?$"))
    @cl.on(events.NewMessage(outgoing=True, pattern=r"(?i)^\.(?:تاس|roll)(?:\s+(\d))?$"))
    async def dice(event):
        if entry.get("paused"):
            return
        await event.delete()
        g = event.pattern_match.group(1)
        target = int(g) if g else 6  # پیش‌فرض بهترین نتیجه (۶)
        await send_dice(event, "🎲", target=target)

    @cl.on(events.MessageEdited(outgoing=True, pattern=r"(?i)^\.دارت(?:\s+(\d))?$"))
    @cl.on(events.NewMessage(outgoing=True, pattern=r"(?i)^\.دارت(?:\s+(\d))?$"))
    async def dart(event):
        if entry.get("paused"):
            return
        await event.delete()
        g = event.pattern_match.group(1)
        target = int(g) if g else 6  # ۶ = وسط دقیقِ دارت (بولزآی)
        await send_dice(event, "🎯", target=target)

    @cl.on(events.MessageEdited(outgoing=True, pattern=r"(?i)^\.فوتبال(?:\s+(\d))?$"))
    @cl.on(events.NewMessage(outgoing=True, pattern=r"(?i)^\.فوتبال(?:\s+(\d))?$"))
    async def football(event):
        if entry.get("paused"):
            return
        await event.delete()
        g = event.pattern_match.group(1)
        target = int(g) if g else 5  # ۳،۴،۵ = گل؛ ۵ تمیزترین حالت
        await send_dice(event, "⚽", target=target)

    @cl.on(events.MessageEdited(outgoing=True, pattern=r"(?i)^\.بسکتبال(?:\s+(\d))?$"))
    @cl.on(events.NewMessage(outgoing=True, pattern=r"(?i)^\.بسکتبال(?:\s+(\d))?$"))
    async def basketball(event):
        if entry.get("paused"):
            return
        await event.delete()
        g = event.pattern_match.group(1)
        target = int(g) if g else 5  # ۴،۵ = توپ توی سبد
        await send_dice(event, "🏀", target=target)

    # ─── کازینو (اسلات ماشین) — با اسم میوه/نماد، مثل «کازینو انگور» ──────────
    # فرمول رسمی تلگرام برای مقدار اسلات: value = 1 + r1 + r2*4 + r3*16
    # (r1,r2,r3 اندیس هر رول از چپ به راست هستن؛ ۰=میله، ۱=انگور، ۲=لیمو، ۳=هفت)
    _SLOT_SYMBOLS = {
        "انگور": 1,
        "لیمو": 2,
        "هفت": 3, "سون": 3, "جکپات": 3,
    }

    @cl.on(events.MessageEdited(outgoing=True, pattern=r"(?i)^\.کازینو(?:\s+(.+))?$"))
    @cl.on(events.NewMessage(outgoing=True, pattern=r"(?i)^\.کازینو(?:\s+(.+))?$"))
    async def casino(event):
        if entry.get("paused"):
            return
        await event.delete()
        raw = event.pattern_match.group(1)
        names = raw.split() if raw else []
        if not names:
            await event.respond("فرمت: .کازینو [نماد] — مثال: .کازینو انگور\nنمادها: انگور، لیمو، هفت")
            return
        if len(names) == 1:
            names = names * 3
        elif len(names) == 2:
            names = names + [names[-1]]
        indices = []
        for n in names[:3]:
            if n not in _SLOT_SYMBOLS:
                await event.respond(f"نماد «{n}» شناخته‌شده نیست. نمادها: انگور، لیمو، هفت")
                return
            indices.append(_SLOT_SYMBOLS[n])
        target = 1 + indices[0] + indices[1] * 4 + indices[2] * 16
        await send_dice(event, "🎰", target=target)


# ─── فینگلیش (تبدیل متن فارسی به حروف لاتین) ───────────────────────────────────
# یک دیکشنری برای رایج‌ترین کلمه‌های محاوره‌ای (دقت بالا) + یک الگوریتم حرف‌به‌حرف
# برای بقیه‌ی متن (چون فارسی بدون اعراب نوشته می‌شه، این روش برای بعضی کلمه‌ها
# ممکنه صددرصد دقیق نباشه، ولی برای استفاده‌ی روزمره کاملاً قابل‌قبوله).
_FINGLISH_WORDS = {
    "سلام": "salam", "سلامم": "salamam", "خوبی": "khobi", "خوبم": "khobam",
    "خوبید": "khobid", "ممنون": "mamnoon", "ممنونم": "mamnoonam", "مرسی": "merci",
    "چطوری": "chetori", "چطورید": "chetorid", "چطورین": "chetorin",
    "خداحافظ": "khodahafez", "بله": "bale", "آره": "are", "اره": "are",
    "نه": "na", "باشه": "bashe", "چی": "chi", "چیه": "chie", "چرا": "chera",
    "کجا": "koja", "کجایی": "kojayi", "کی": "ki", "کیه": "kie",
    "چیکار": "chikar", "چیکارا": "chikara", "میخوام": "mikham", "میخوای": "mikhay",
    "میخواد": "mikhad", "نمیخوام": "nemikham", "میدونم": "midoonam",
    "نمیدونم": "nemidoonam", "میدونی": "midooni", "دوست": "doost",
    "دارم": "daram", "داری": "dari", "داره": "dare", "عزیزم": "azizam",
    "جانم": "janam", "خیلی": "kheyli", "لطفا": "lotfan", "لطفاً": "lotfan",
    "متشکرم": "motshakeram", "خوش": "khosh", "اومدی": "oomadi",
    "خوشحالم": "khoshhalam", "تولدت": "tavalodet", "مبارک": "mobarak",
    "صبح": "sobh", "بخیر": "bekheir", "شب": "shab", "روز": "rooz",
    "خداروشکر": "khodaroshokr", "قربونت": "ghorboonet", "برم": "beram",
    "میام": "miam", "میری": "miri", "میره": "mire", "کارت": "karet",
    "چیزی": "chizi", "هیچی": "hichi", "همه": "hame", "همش": "hamash",
    "الان": "alan", "بعدا": "bada", "بعداً": "bada", "امروز": "emrooz",
    "فردا": "farda", "دیروز": "dirooz", "خانه": "khune", "خونه": "khune",
    "کار": "kar", "درس": "dars", "پول": "pool", "وقت": "vaght",
    "حالا": "hala", "حالت": "halet", "حالتون": "haletoon", "کمک": "komak",
    "مشکل": "moshkel", "درست": "dorost", "غلط": "ghalat", "خوب": "khoob",
    "بد": "bad", "زیاد": "ziad", "کم": "kam", "تند": "tond", "یواش": "yavash",
}


def _finglish_letter(word: str, i: int) -> str:
    """معادل لاتینِ یک حرف فارسی در جایگاه i از کلمه، با توجه به موقعیتش
    (شروع/میان/پایان کلمه) برای حرف‌های چندمعنایی مثل ا، و، ی، ه."""
    ch = word[i]
    is_first = (i == 0)

    fixed = {
        "ب": "b", "پ": "p", "ت": "t", "ث": "s", "ج": "j", "چ": "ch",
        "ح": "h", "خ": "kh", "د": "d", "ذ": "z", "ر": "r", "ز": "z",
        "ژ": "zh", "س": "s", "ش": "sh", "ص": "s", "ض": "z", "ط": "t",
        "ظ": "z", "غ": "gh", "ف": "f", "ق": "gh", "ک": "k", "گ": "g",
        "ل": "l", "م": "m", "ن": "n", "ع": "a",
    }
    if ch in fixed:
        return fixed[ch]

    if ch in ("ا", "آ"):
        return "a"

    if ch == "و":
        if is_first:
            return "v"
        return "o"

    if ch == "ی":
        return "y" if is_first else "i"

    if ch == "ه":
        return "h"

    # هر کاراکتر غیرفارسی (فاصله، عدد، ایموجی، حروف لاتین و ...) دست‌نخورده می‌مونه
    return ch


def to_finglish(text: str) -> str:
    """تبدیل متن فارسی به فینگلیش (حروف لاتین)، با اولویت دادن به دیکشنریِ
    کلمات پرکاربرد و بازگشت به تبدیل حرف‌به‌حرف برای بقیه‌ی کلمات."""
    if not text:
        return text

    _persian_word_re = re.compile(r"[آ-یءئؤ]+")

    def _convert_word(m: "re.Match") -> str:
        w = m.group(0)
        w_norm = w.replace("ي", "ی").replace("ك", "ک")
        mapped = _FINGLISH_WORDS.get(w_norm)
        if mapped:
            return mapped
        return "".join(_finglish_letter(w_norm, i) for i in range(len(w_norm)))

    return _persian_word_re.sub(_convert_word, text)


# ─── پردازش دستورات ────────────────────────────────────────────────────────────
# ─── دستورهای روشن/خاموش جدید پنل (قفل‌ها، ساعت پرمیوم، حالت‌های متن) ─────────
# اینا فقط یک تنظیم ساده در دیتابیس رو ست/ری‌ست می‌کنن (بدون منطق اجراییِ
# جداگانه)، برای این‌که دکمه‌های پنل واقعاً وضعیت روشن/خاموش رو نگه دارن.
_EXTRA_TOGGLE_COMMANDS = {
    "قفل یوزرنیم روشن": ("lock_username_active", "1"),
    "قفل یوزرنیم خاموش": ("lock_username_active", "0"),
    "قفل ریپلای روشن": ("lock_reply_active", "1"),
    "قفل ریپلای خاموش": ("lock_reply_active", "0"),
    "قفل گیف روشن": ("lock_gif_active", "1"),
    "قفل گیف خاموش": ("lock_gif_active", "0"),
    "قفل عکس روشن": ("lock_photo_active", "1"),
    "قفل عکس خاموش": ("lock_photo_active", "0"),
    "قفل استیکر روشن": ("lock_sticker_active", "1"),
    "قفل استیکر خاموش": ("lock_sticker_active", "0"),
    "قفل فوروارد روشن": ("lock_forward_active", "1"),
    "قفل فوروارد خاموش": ("lock_forward_active", "0"),
    "قفل لاگین روشن": ("login_lock_active", "1"),
    "قفل لاگین خاموش": ("login_lock_active", "0"),
    "ساعت پرمیوم روشن": ("clock_premium_active", "1"),
    "ساعت پرمیوم خاموش": ("clock_premium_active", "0"),
    "حالت بولد روشن": ("text_style_bold_active", "1"),
    "حالت بولد خاموش": ("text_style_bold_active", "0"),
    "حالت ایتالیک روشن": ("text_style_italic_active", "1"),
    "حالت ایتالیک خاموش": ("text_style_italic_active", "0"),
    "حالت نقل قول روشن": ("text_style_quote_active", "1"),
    "حالت نقل قول خاموش": ("text_style_quote_active", "0"),
    "حالت زیرخط روشن": ("text_style_underline_active", "1"),
    "حالت زیرخط خاموش": ("text_style_underline_active", "0"),
    "حالت اسپویلر روشن": ("text_style_spoiler_active", "1"),
    "حالت اسپویلر خاموش": ("text_style_spoiler_active", "0"),
    "حالت خط‌خورده روشن": ("text_style_strike_active", "1"),
    "حالت خط‌خورده خاموش": ("text_style_strike_active", "0"),
    "حالت تدریجی روشن": ("text_style_gradual_active", "1"),
    "حالت تدریجی خاموش": ("text_style_gradual_active", "0"),
    "حالت تک‌فاصله روشن": ("text_style_single_space_active", "1"),
    "حالت تک‌فاصله خاموش": ("text_style_single_space_active", "0"),
    "حالت فینگلیش روشن": ("text_style_finglish_active", "1"),
    "حالت فینگلیش خاموش": ("text_style_finglish_active", "0"),
}

# ─── گروه‌هایی که باید «انحصاری» (رادیویی) رفتار کنن: با روشن شدن یکی، بقیه
# همون گروه خاموش می‌شن ──────────────────────────────────────────────────────
_TEXT_STYLE_GROUP = [
    "text_style_bold_active", "text_style_italic_active", "text_style_quote_active",
    "text_style_underline_active", "text_style_spoiler_active", "text_style_strike_active",
    "text_style_gradual_active", "text_style_single_space_active",
    "text_style_finglish_active",
]
_ACTION_GROUP = [
    "typing_action_active", "gaming_action_active",
    "voice_action_active", "video_action_active",
]


def _enforce_exclusive_group(owner_id: int, group: list, active_key: str):
    """توی یک گروه انحصاری، فقط active_key روشن می‌مونه و بقیه خاموش می‌شن."""
    for k in group:
        if k != active_key:
            db.set_setting(owner_id, k, "0")


async def _handle_command(cl, event, text, owner_id, entry, had_dot=True):
    msg = event.message

    def gs(key, default=None):
        return db.get_setting(owner_id, key, default)

    def ss(key, value):
        db.set_setting(owner_id, key, value)

    async def edit(t):
        await _safe_edit(event, owner_id, t)

    # ─── پنل دکمه‌ای مدیریت سلف ─────────────────────────────────────────────────
    # وقتی کاربر در خودِ چت فقط «پنل» یا «panel» می‌نویسه، پیامش پاک می‌شه و
    # به‌جاش همون پیام اینلاینِ واقعی (via @helper_bot) با دکمه‌های رنگی فعال
    # جایگزینش می‌شه - دقیقاً انگار با نوشتن «پنل» پنل شیشه‌ای باز شده.
    if text in ("پنل", "panel"):
        try:
            await event.delete()
        except Exception:
            pass

        if not config.HELPER_BOT_TOKEN:
            await cl.send_message(event.chat_id, "❗ پنل دکمه‌ای فعال نیست (بات کمکی تنظیم نشده).")
            return

        from helper_bot import get_helper_client
        helper = get_helper_client()
        uname = None
        if helper:
            try:
                me = await helper.get_me()
                uname = me.username
            except Exception:
                uname = None

        if not uname:
            await cl.send_message(event.chat_id, "❗ بات کمکی هنوز آماده نیست، کمی بعد دوباره امتحان کن.")
            return

        try:
            results = await cl.inline_query(uname, "پنل")
            if results:
                sent = await results[0].click(event.chat_id)
                if sent is not None:
                    try:
                        from helper_bot import schedule_panel_timeout
                        schedule_panel_timeout(sent.chat_id, sent.id)
                    except Exception:
                        pass
            else:
                await cl.send_message(event.chat_id, "❗ نتیجه‌ای از بات کمکی دریافت نشد.")
        except Exception as e:
            await cl.send_message(event.chat_id, f"❗ خطا در باز کردن پنل: {e}")

    # ─── دستورهای روشن/خاموش جدید پنل (قفل‌ها، ساعت پرمیوم، حالت‌های متن) ─────
    elif text in _EXTRA_TOGGLE_COMMANDS:
        key, val = _EXTRA_TOGGLE_COMMANDS[text]
        ss(key, val)
        if val == "1" and key in _TEXT_STYLE_GROUP:
            _enforce_exclusive_group(owner_id, _TEXT_STYLE_GROUP, key)
        label = text.rsplit(" ", 1)[0]
        state = "روشن" if val == "1" else "خاموش"
        await edit(f"{label} {state} شد.")

    # ─── ماشین حساب ──────────────────────────────────────────────────────────
    elif text.startswith("محاسبه "):
        expr = text[len("محاسبه "):].strip()
        try:
            import ast, operator as _op
            _ops = {
                ast.Add: _op.add, ast.Sub: _op.sub, ast.Mult: _op.mul,
                ast.Div: _op.truediv, ast.Pow: _op.pow, ast.Mod: _op.mod,
                ast.USub: _op.neg, ast.UAdd: _op.pos,
            }

            def _safe_eval(node):
                if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
                    return node.value
                if isinstance(node, ast.BinOp) and type(node.op) in _ops:
                    return _ops[type(node.op)](_safe_eval(node.left), _safe_eval(node.right))
                if isinstance(node, ast.UnaryOp) and type(node.op) in _ops:
                    return _ops[type(node.op)](_safe_eval(node.operand))
                raise ValueError("عبارت نامعتبر")

            result = _safe_eval(ast.parse(expr, mode="eval").body)
            await edit(f"نتیجه: {result}")
        except Exception:
            await edit("❗ عبارت ریاضی نامعتبر است.\nفرمت درست: `محاسبه 2+2*3`")

    # ─── دستیار هوش مصنوعی (دیپ‌سیک) ───────────────────────────────────────
    # ─── پینگ ────────────────────────────────────────────────────────────────
    elif text == "پینگ":
        t0 = time.time()
        await edit("در حال محاسبه پینگ...")
        ms = int((time.time() - t0) * 1000)
        await edit(f"پینگ: {ms} میلی‌ثانیه")

    # ─── حذف (پیامی که روش ریپلای شده رو حذف می‌کنه) ────────────────────────
    elif text == "حذف":
        if not event.is_reply:
            await event.delete()
        else:
            reply = await event.get_reply_message()
            try:
                await reply.delete()
                await event.delete()
            except Exception as e:
                await edit(f"نمی‌توانم این پیام را حذف کنم: {e}")

    # ─── تگ (منشن همه اعضای گروه) ────────────────────────────────────────────
    elif text.startswith("تگ") and text != "لغو تگ":
        raw_part = text[len("تگ"):].strip()
        if not raw_part:
            # «تگ» خالی (بدون متن/عدد بعدش) — عمداً هیچ کاری نمی‌کنیم
            pass
        elif event.is_private:
            await edit("این دستور فقط توی گروه کار می‌کند.")
        else:
            # اگر بعد از «تگ» فقط عدد بود، یعنی تعداد نفراتی که باید تگ بشن، نه متن پیام
            tag_limit = None
            if raw_part.isdigit():
                tag_limit = int(raw_part)
                msg_part = ""
            else:
                msg_part = raw_part
            entry["cancel_tag"] = False
            await edit("در حال تگ کردن اعضا... (برای توقف: لغو تگ)")
            mentions = []
            try:
                async for user in cl.iter_participants(event.chat_id):
                    if user.bot or user.deleted:
                        continue
                    mentions.append(user)
                    if tag_limit is not None and len(mentions) >= tag_limit:
                        break
            except Exception as e:
                await edit(f"خطا در دریافت اعضا: {e}")
                mentions = []
            chunk = []
            cancelled = False
            for user in mentions:
                if entry.get("cancel_tag"):
                    cancelled = True
                    break
                chunk.append(user)
                if len(chunk) == 5:
                    text_line = " ".join(f"[‌](tg://user?id={u.id})" for u in chunk)
                    try:
                        await cl.send_message(event.chat_id, f"{msg_part} {text_line}")
                    except Exception:
                        pass
                    chunk = []
                    await asyncio.sleep(1)
            if chunk and not entry.get("cancel_tag"):
                text_line = " ".join(f"[‌](tg://user?id={u.id})" for u in chunk)
                try:
                    await cl.send_message(event.chat_id, f"{msg_part} {text_line}")
                except Exception:
                    pass
            if cancelled or entry.get("cancel_tag"):
                try:
                    await cl.send_message(event.chat_id, "⛔ تگ متوقف شد.")
                except Exception:
                    pass
            entry["cancel_tag"] = False
            try:
                await event.delete()
            except Exception:
                pass

    elif text == "لغو تگ":
        entry["cancel_tag"] = True
        await edit("درخواست توقف تگ ثبت شد.")

    # ─── لوگو (ارسال بنر تزئینی سلف) ─────────────────────────────────────────
    elif text == "لوگو":
        try:
            me = await cl.get_me()
            photo_bytes = await cl.download_profile_photo(me, file=bytes)
            if not photo_bytes:
                await edit("عکس پروفایل پیدا نشد.")
            else:
                from banner import generate_banner
                banner_bytes = generate_banner(
                    photo_bytes,
                    bottom_text="self panel",
                    bottom_sub=f"@{me.username}" if me.username else "",
                )
                await cl.send_file(event.chat_id, banner_bytes, force_document=False)
                await event.delete()
        except Exception as e:
            await edit(f"خطا در ساخت لوگو: {e}")

    # ─── اسکرین (ساخت استیکر از یک پیام، همراه با پروفایلِ فرستنده) ────────────
    elif text == "اسکرین" or text.startswith("اسکرین "):
        link_part = text[len("اسکرین"):].strip()
        try:
            target_msg = None
            sender_name = None
            profile_bytes = None

            if link_part:
                # حالت لینک — مخصوصِ کانال‌ها: «اسکرین [لینک پیام]»
                post_match = _POST_LINK_RE.match(link_part)
                private_match = _PRIVATE_POST_LINK_RE.match(link_part)
                if not (post_match or private_match):
                    await edit(
                        "❗ لینک پیام معتبر نیست.\n"
                        "مثال: اسکرین https://t.me/channel/123\n"
                        "یا (کانال خصوصی): اسکرین https://t.me/c/123456789/123"
                    )
                else:
                    if private_match:
                        from telethon.tl.types import PeerChannel
                        raw_channel_id, post_id = int(private_match.group(1)), int(private_match.group(2))
                        channel_entity = await cl.get_entity(PeerChannel(raw_channel_id))
                    else:
                        channel_username, post_id = post_match.group(1), int(post_match.group(2))
                        channel_entity = await cl.get_entity(channel_username)

                    target_msg = await cl.get_messages(channel_entity, ids=post_id)
                    if not target_msg:
                        await edit("❌ پیام پیدا نشد.")
                    else:
                        sender_name = getattr(channel_entity, "title", None) or "کانال"
                        try:
                            profile_bytes = await cl.download_profile_photo(channel_entity, file=bytes)
                        except Exception:
                            profile_bytes = None
            else:
                # حالت ریپلای — روی یه پیام ریپلای بزن و بنویس: اسکرین
                if not event.is_reply:
                    await edit("❗ روی یه پیام ریپلای بزن و بنویس: اسکرین")
                else:
                    target_msg = await event.get_reply_message()
                    if not target_msg:
                        await edit("❌ پیامِ ریپلای‌شده پیدا نشد.")
                    else:
                        sender = await target_msg.get_sender()
                        sender_name = (
                            " ".join(filter(None, [
                                getattr(sender, "first_name", None),
                                getattr(sender, "last_name", None),
                            ])).strip()
                            or getattr(sender, "title", None)
                            or getattr(sender, "username", None)
                            or "کاربر"
                        )
                        try:
                            profile_bytes = await cl.download_profile_photo(sender, file=bytes)
                        except Exception:
                            profile_bytes = None

            if target_msg is not None:
                message_text = target_msg.text or target_msg.raw_text or ""
                if not message_text and not target_msg.media:
                    await edit("❗ این پیام متنی برای اسکرین کردن نداره.")
                else:
                    if not message_text:
                        message_text = "🖼 (رسانه بدون متن)"
                    date_str = target_msg.date.strftime("%H:%M") if target_msg.date else None

                    from screenshot import generate_message_sticker
                    sticker_bytes = generate_message_sticker(profile_bytes, sender_name, message_text, date_str)

                    from telethon.tl.types import DocumentAttributeSticker, InputStickerSetEmpty
                    buf = io.BytesIO(sticker_bytes)
                    buf.name = "screen.webp"
                    await cl.send_file(
                        event.chat_id, buf,
                        attributes=[DocumentAttributeSticker(alt="🖼", stickerset=InputStickerSetEmpty())],
                        force_document=False,
                    )
                    try:
                        await event.delete()
                    except Exception:
                        pass
        except Exception as e:
            await edit(f"❌ خطا در ساخت اسکرین: {e}")

    # ─── فیلتر کلمات ─────────────────────────────────────────────────────────
    elif text.startswith("فیلتر کلمه "):
        word = text[len("فیلتر کلمه "):].strip()
        if not word:
            await edit("فرمت: فیلتر کلمه [کلمه]")
        else:
            words = _get_filtered_words(owner_id)
            if word not in words:
                words.append(word)
                _save_filtered_words(owner_id, words)
            await edit(f"کلمه «{word}» به فیلتر اضافه شد.")

    elif text.startswith("حذف فیلتر کلمه "):
        word = text[len("حذف فیلتر کلمه "):].strip()
        words = _get_filtered_words(owner_id)
        if word in words:
            words.remove(word)
            _save_filtered_words(owner_id, words)
            await edit(f"کلمه «{word}» از فیلتر حذف شد.")
        else:
            await edit("این کلمه توی لیست فیلتر نیست.")

    elif text == "لیست فیلتر کلمات":
        words = _get_filtered_words(owner_id)
        await edit("لیست فیلتر کلمات:\n" + "\n".join(words) if words else "لیست فیلتر کلمات خالی است.")

    elif text == "فیلترکلمات روشن":
        ss("word_filter_active", "1")
        await edit("فیلتر کلمات روشن شد. پیام‌های پیویِ حاوی کلمات فیلترشده حذف می‌شوند.")
    elif text == "فیلترکلمات خاموش":
        ss("word_filter_active", "0")
        await edit("فیلتر کلمات خاموش شد.")

    # ─── تبچی (مدیریت بنرها) ────────────────────────────────────────────────
    elif text == "تبچی روشن":
        ss("tabchi_active", "1")
        await edit("تبچی روشن شد؛ بنرهای فعال طبق مقصدشان ارسال می‌شوند.")
    elif text == "تبچی خاموش":
        ss("tabchi_active", "0")
        await edit("تبچی خاموش شد.")

    elif text == "ثبت بنر با ریپلای" or re.match(r"^تنظیم بنر (\d+) با ریپلای$", text):
        if not event.is_reply:
            await edit("روی پیامِ بنر (متن/عکس/ویدیو) ریپلای کن و دوباره تایپ کن.")
        else:
            data = _get_tabchi(owner_id)
            m = re.match(r"^تنظیم بنر (\d+) با ریپلای$", text)
            if m:
                slot = m.group(1)
                if slot not in _TABCHI_SLOTS:
                    await edit("شماره بنر باید بین ۱ تا ۱۰ باشد.")
                    return
            else:
                slot = _tabchi_next_free_slot(data)
                if not slot:
                    await edit("همه‌ی ۱۰ اسلات بنر پر است. اول یکی را حذف کن.")
                    return
            reply = await event.get_reply_message()
            existing = data.get(slot, {})
            data[slot] = {
                "chat_id": reply.chat_id,
                "msg_id": reply.id,
                "target_mode": existing.get("target_mode"),
                "target_chat_id": existing.get("target_chat_id"),
                "active": existing.get("active", False),
            }
            _save_tabchi(owner_id, data)
            await edit(f"بنر {slot} ثبت شد.")

    elif re.match(r"^تنظیم بنر (\d+) در این چت$", text):
        m = re.match(r"^تنظیم بنر (\d+) در این چت$", text)
        slot = m.group(1)
        data = _get_tabchi(owner_id)
        if slot not in data:
            await edit(f"اول باید بنر {slot} را با ریپلای ثبت کنی.")
        else:
            data[slot]["target_mode"] = "this_chat"
            data[slot]["target_chat_id"] = event.chat_id
            _save_tabchi(owner_id, data)
            await edit(f"مقصد بنر {slot} روی این چت تنظیم شد.")

    elif re.match(r"^تنظیم بنر (\d+) در همه گروه‌ها$", text):
        m = re.match(r"^تنظیم بنر (\d+) در همه گروه‌ها$", text)
        slot = m.group(1)
        data = _get_tabchi(owner_id)
        if slot not in data:
            await edit(f"اول باید بنر {slot} را با ریپلای ثبت کنی.")
        else:
            data[slot]["target_mode"] = "all_groups"
            data[slot]["target_chat_id"] = None
            _save_tabchi(owner_id, data)
            await edit(f"مقصد بنر {slot} روی همه گروه‌ها تنظیم شد.")

    elif re.match(r"^فعال کردن بنر (\d+)$", text):
        m = re.match(r"^فعال کردن بنر (\d+)$", text)
        slot = m.group(1)
        data = _get_tabchi(owner_id)
        if slot not in data:
            await edit(f"اول باید بنر {slot} را با ریپلای ثبت کنی.")
        elif not data[slot].get("target_mode"):
            await edit(f"اول مقصد بنر {slot} را تنظیم کن (در این چت / در همه گروه‌ها).")
        else:
            data[slot]["active"] = True
            _save_tabchi(owner_id, data)
            await edit(f"بنر {slot} فعال شد.")

    elif re.match(r"^غیرفعال کردن بنر (\d+)$", text):
        m = re.match(r"^غیرفعال کردن بنر (\d+)$", text)
        slot = m.group(1)
        data = _get_tabchi(owner_id)
        if slot in data:
            data[slot]["active"] = False
            _save_tabchi(owner_id, data)
        await edit(f"بنر {slot} غیرفعال شد.")

    elif text == "لیست بنرها":
        data = _get_tabchi(owner_id)
        if not data:
            await edit("هیچ بنری ثبت نشده است.")
        else:
            lines = ["لیست بنرها:\n"]
            for slot in _TABCHI_SLOTS:
                b = data.get(slot)
                if not b:
                    continue
                mode = b.get("target_mode") or "تنظیم‌نشده"
                mode_fa = {"this_chat": "این چت", "all_groups": "همه گروه‌ها"}.get(mode, mode)
                state = "فعال" if b.get("active") else "غیرفعال"
                lines.append(f"بنر {slot} — مقصد: {mode_fa} — وضعیت: {state}")
            await edit("\n".join(lines))

    elif text == "پاکسازی لیست بنر":
        _save_tabchi(owner_id, {})
        await edit("همه‌ی بنرها حذف شدند.")

    elif text == "پاکسازی بنر در این چت":
        data = _get_tabchi(owner_id)
        changed = False
        for slot, b in data.items():
            if b.get("target_mode") == "this_chat" and b.get("target_chat_id") == event.chat_id:
                b["target_mode"] = None
                b["target_chat_id"] = None
                b["active"] = False
                changed = True
        if changed:
            _save_tabchi(owner_id, data)
            await edit("بنرهای مربوط به این چت پاکسازی شدند.")
        else:
            await edit("هیچ بنری برای این چت تنظیم نشده بود.")

    elif re.match(r"^حذف بنر (\d+) در این چت$", text):
        m = re.match(r"^حذف بنر (\d+) در این چت$", text)
        slot = m.group(1)
        data = _get_tabchi(owner_id)
        if slot in data and data[slot].get("target_mode") == "this_chat":
            data[slot]["target_mode"] = None
            data[slot]["target_chat_id"] = None
            data[slot]["active"] = False
            _save_tabchi(owner_id, data)
            await edit(f"بنر {slot} از این چت حذف شد.")
        else:
            await edit(f"بنر {slot} برای این چت تنظیم نشده بود.")

    elif re.match(r"^حذف بنر (\d+) از همه گروه‌ها$", text):
        m = re.match(r"^حذف بنر (\d+) از همه گروه‌ها$", text)
        slot = m.group(1)
        data = _get_tabchi(owner_id)
        if slot in data and data[slot].get("target_mode") == "all_groups":
            data[slot]["target_mode"] = None
            data[slot]["active"] = False
            _save_tabchi(owner_id, data)
            await edit(f"بنر {slot} از حالت «همه گروه‌ها» خارج شد.")
        else:
            await edit(f"بنر {slot} روی «همه گروه‌ها» تنظیم نشده بود.")

    elif text == "حذف بنرها از همه گروه‌ها":
        data = _get_tabchi(owner_id)
        changed = False
        for slot, b in data.items():
            if b.get("target_mode") == "all_groups":
                b["target_mode"] = None
                b["active"] = False
                changed = True
        if changed:
            _save_tabchi(owner_id, data)
        await edit("همه‌ی بنرهایی که روی «همه گروه‌ها» بودند غیرفعال شدند.")

    elif re.match(r"^حذف بنر (\d+)$", text):
        m = re.match(r"^حذف بنر (\d+)$", text)
        slot = m.group(1)
        data = _get_tabchi(owner_id)
        if slot in data:
            del data[slot]
            _save_tabchi(owner_id, data)
            await edit(f"بنر {slot} کاملاً حذف شد.")
        else:
            await edit(f"بنر {slot} وجود نداشت.")

    elif re.match(r"^فور بنر (\d+) در (50|100) گروه اخیر$", text):
        m = re.match(r"^فور بنر (\d+) در (50|100) گروه اخیر$", text)
        slot, count = m.group(1), int(m.group(2))
        data = _get_tabchi(owner_id)
        if slot not in data:
            await edit(f"اول باید بنر {slot} را با ریپلای ثبت کنی.")
        else:
            await edit(f"در حال ارسال فوری بنر {slot} به {count} گروه اخیر...")
            banner = data[slot]
            sent = 0
            n = 0
            async for dialog in cl.iter_dialogs():
                if n >= count:
                    break
                if not dialog.is_group and not dialog.is_channel:
                    continue
                n += 1
                ok = await _tabchi_deliver(cl, dialog.id, banner)
                if ok:
                    sent += 1
                await asyncio.sleep(1.5)
            await edit(f"بنر {slot} به {sent} گروه ارسال شد.")

    elif re.match(r"^تایم بنرها (\d+)$", text):
        m = re.match(r"^تایم بنرها (\d+)$", text)
        minutes = m.group(1)
        ss("tabchi_interval", minutes)
        await edit(f"فاصله‌ی ارسال خودکار بنرها روی {minutes} دقیقه تنظیم شد.")

    # ─── پاسخ خودکار به همه (پیام ثابت برای هر پیامی که بیاد) ────────────────
    elif text == "پاسخ خودکار روشن":
        ss("auto_reply_active", "1")
        await edit("پاسخ خودکار روشن شد.")
    elif text == "پاسخ خودکار خاموش":
        ss("auto_reply_active", "0")
        await edit("پاسخ خودکار خاموش شد.")
    elif text.startswith("متن پاسخ خودکار "):
        msg = text[len("متن پاسخ خودکار "):].strip()
        if not msg:
            await edit("فرمت: متن پاسخ خودکار [متن دلخواه]")
        else:
            ss("auto_reply_message", msg)
            await edit("متن پاسخ خودکار ذخیره شد.")

    # ─── پاسخ کلیدی (اگه توی پیام یه کلمه‌ی خاص بود، پاسخ اختصاصی همون بره) ──
    elif text.startswith("پاسخ کلیدی "):
        body = text[len("پاسخ کلیدی "):].strip()
        if "=" not in body:
            await edit("فرمت درست: پاسخ کلیدی [کلمه] = [پاسخ]\nمثال: پاسخ کلیدی قیمت = قیمت‌ها توی کانال هست.")
        else:
            keyword, reply_text = body.split("=", 1)
            keyword = keyword.strip()
            reply_text = reply_text.strip()
            if not keyword or not reply_text:
                await edit("فرمت درست: پاسخ کلیدی [کلمه] = [پاسخ]")
            else:
                rules = _get_keyword_replies(owner_id)
                rules = [r for r in rules if r["keyword"].lower() != keyword.lower()]
                rules.append({"keyword": keyword, "reply": reply_text})
                _save_keyword_replies(owner_id, rules)
                await edit(f"✅ پاسخ کلیدی ثبت شد.\nکلمه: {keyword}\nپاسخ: {reply_text}")

    elif text.startswith("حذف پاسخ کلیدی "):
        keyword = text[len("حذف پاسخ کلیدی "):].strip()
        rules = _get_keyword_replies(owner_id)
        new_rules = [r for r in rules if r["keyword"].lower() != keyword.lower()]
        if len(new_rules) == len(rules):
            await edit(f"کلمه‌ی «{keyword}» توی لیست پاسخ‌های کلیدی نبود.")
        else:
            _save_keyword_replies(owner_id, new_rules)
            await edit(f"❌ پاسخ کلیدی «{keyword}» حذف شد.")

    elif text == "لیست پاسخ کلیدی":
        rules = _get_keyword_replies(owner_id)
        if not rules:
            await edit("هنوز هیچ پاسخ کلیدی‌ای ثبت نشده.")
        else:
            lines = [f"📋 پاسخ‌های کلیدی ({len(rules)} مورد):\n"]
            for r in rules:
                lines.append(f"• {r['keyword']} ← {r['reply']}")
            await edit("\n".join(lines))

    elif text == "پاک کردن پاسخ کلیدی":
        _save_keyword_replies(owner_id, [])
        await edit("همه‌ی پاسخ‌های کلیدی پاک شدند.")

    # ─── نگهبان چت (ذخیره پیام حذف‌شده/ویرایش‌شده/عکس تایمی) ─────────────────
    elif text == "ذخیره پیام حذف‌شده روشن":
        ss("guard_delete_active", "1")
        await edit("ذخیره پیام حذف‌شده روشن شد؛ پیام‌های حذف‌شده به پیام‌های ذخیره‌شده فرستاده می‌شوند.")
    elif text == "ذخیره پیام حذف‌شده خاموش":
        ss("guard_delete_active", "0")
        await edit("ذخیره پیام حذف‌شده خاموش شد.")
    elif text == "ذخیره پیام ویرایش‌شده روشن":
        ss("guard_edit_active", "1")
        await edit("ذخیره پیام ویرایش‌شده روشن شد.")
    elif text == "ذخیره پیام ویرایش‌شده خاموش":
        ss("guard_edit_active", "0")
        await edit("ذخیره پیام ویرایش‌شده خاموش شد.")
    elif text == "ذخیره عکس تایمی روشن":
        ss("guard_view_once_active", "1")
        await edit("ذخیره عکس تایمی روشن شد.")
    elif text == "ذخیره عکس تایمی خاموش":
        ss("guard_view_once_active", "0")
        await edit("ذخیره عکس تایمی خاموش شد.")

    elif text == "دیپ سیک روشن":
        if not getattr(config, "DEEPSEEK_API_KEY", ""):
            await edit("کلید API دیپ‌سیک تنظیم نشده است.")
        else:
            ss("ai_assistant_active", "1")
            await edit(
                "دستیار هوش مصنوعی روشن شد.\n"
                f"وقتی {AI_AWAY_SECONDS // 60} دقیقه پیامی نفرستی، به‌جای تو به پیام‌های پیوی جواب می‌ده."
            )
    elif text == "دیپ سیک خاموش":
        ss("ai_assistant_active", "0")
        await edit("دستیار هوش مصنوعی خاموش شد.")

    elif text == "هوش مصنوعی پاسخ همه روشن":
        ss("ai_reply_always_active", "1")
        await edit("از این به بعد هوش مصنوعی به همه پیام‌های پیوی جواب می‌ده (نه فقط وقتی غایبی)، با همون اطلاعاتی که آموزش داده‌ای.")
    elif text == "هوش مصنوعی پاسخ همه خاموش":
        ss("ai_reply_always_active", "0")
        await edit("هوش مصنوعی دوباره فقط وقتی غایب باشی جواب می‌ده.")

    elif text.startswith("آموزش هوش مصنوعی "):
        info = text[len("آموزش هوش مصنوعی "):].strip()
        if not info:
            await edit("فرمت: آموزش هوش مصنوعی [متن]")
        else:
            existing = gs("ai_knowledge_base", "")
            merged = f"{existing}\n{info}".strip() if existing else info
            ss("ai_knowledge_base", merged)
            await edit("اطلاعات به دانش هوش مصنوعی اضافه شد.")

    elif text == "نمایش دانش هوش مصنوعی":
        info = gs("ai_knowledge_base", "")
        await edit(info if info else "هنوز چیزی به هوش مصنوعی آموزش نداده‌ای.")

    elif text == "پاک کردن دانش هوش مصنوعی":
        ss("ai_knowledge_base", "")
        await edit("دانش هوش مصنوعی پاک شد.")

    elif text == "تایپینگ روشن":
        ss("typing_action_active", "1")
        _enforce_exclusive_group(owner_id, _ACTION_GROUP, "typing_action_active")
        await edit("اکشن تایپینگ ۲۴ ساعته روشن شد.")
    elif text == "تایپینگ خاموش":
        ss("typing_action_active", "0")
        await edit("اکشن تایپینگ ۲۴ ساعته خاموش شد.")

    elif text == "گیمینگ روشن":
        ss("gaming_action_active", "1")
        _enforce_exclusive_group(owner_id, _ACTION_GROUP, "gaming_action_active")
        await edit("اکشن گیمینگ ۲۴ ساعته روشن شد.")
    elif text == "گیمینگ خاموش":
        ss("gaming_action_active", "0")
        await edit("اکشن گیمینگ ۲۴ ساعته خاموش شد.")

    elif text == "ویس روشن":
        ss("voice_action_active", "1")
        _enforce_exclusive_group(owner_id, _ACTION_GROUP, "voice_action_active")
        await edit("اکشن ویس ۲۴ ساعته روشن شد.")
    elif text == "ویس خاموش":
        ss("voice_action_active", "0")
        await edit("اکشن ویس ۲۴ ساعته خاموش شد.")

    elif text == "ارسال ویدیو روشن":
        ss("video_action_active", "1")
        _enforce_exclusive_group(owner_id, _ACTION_GROUP, "video_action_active")
        await edit("اکشن ارسال ویدیو ۲۴ ساعته روشن شد.")
    elif text == "ارسال ویدیو خاموش":
        ss("video_action_active", "0")
        await edit("اکشن ارسال ویدیو ۲۴ ساعته خاموش شد.")

    # ─── بلاک / آنبلاک کاربر ────────────────────────────────────────────────
    elif text in ("بلاک کاربر", "انبلاک کاربر"):
        target = await _resolve_target_or_username(cl, event, text.split())
        if not target:
            await edit("روی پیام کاربر ریپلای کن یا آیدی عددی/یوزرنیمش رو بنویس.")
        else:
            from telethon.tl.functions.contacts import BlockRequest, UnblockRequest
            blocked = _get_block_list(owner_id)
            try:
                # برای این‌که BlockRequest/UnblockRequest حتی روی آیدی‌های عددیِ
                # کش‌نشده هم کار کنه، اول entity واقعی رو از تلگرام می‌گیریم
                try:
                    entity = await cl.get_entity(target["id"])
                except Exception:
                    entity = target["id"]
                if text == "بلاک کاربر":
                    await cl(BlockRequest(id=entity))
                    if not any(u["id"] == target["id"] for u in blocked):
                        blocked.append(target)
                        _save_block_list(owner_id, blocked)
                    await edit(f"کاربر {target.get('name') or target['id']} بلاک شد.")
                else:
                    await cl(UnblockRequest(id=entity))
                    blocked = [u for u in blocked if u["id"] != target["id"]]
                    _save_block_list(owner_id, blocked)
                    await edit(f"کاربر {target.get('name') or target['id']} آنبلاک شد.")
            except Exception as e:
                await edit(f"خطا: {e}")

    elif text == "لیست بلاک":
        blocked = _get_block_list(owner_id)
        if not blocked:
            await edit("لیست بلاک خالی است.")
        else:
            lines = [f"لیست بلاک ({len(blocked)} نفر):\n"]
            for u in blocked:
                lines.append(f"- {u.get('name') or u.get('username') or u['id']} — `{u['id']}`")
            await edit("\n".join(lines))

    elif text == "پاکسازی لیست بلاک":
        from telethon.tl.functions.contacts import UnblockRequest
        for u in _get_block_list(owner_id):
            try:
                try:
                    entity = await cl.get_entity(u["id"])
                except Exception:
                    entity = u["id"]
                await cl(UnblockRequest(id=entity))
            except Exception:
                pass
        _save_block_list(owner_id, [])
        await edit("لیست بلاک پاکسازی شد و همه آنبلاک شدند.")

    # ─── ری‌اکت اختصاصی برای یک کاربر خاص ───────────────────────────────────
    elif text.startswith("تنظیم ری‌اکت "):
        emoji = text[len("تنظیم ری‌اکت "):].strip()
        target = await _resolve_target(event, text.split())
        if not emoji or not target:
            await edit("فرمت: روی پیام کاربر ریپلای کن و بنویس «تنظیم ری‌اکت [ایموجی]»")
        else:
            mapping = _get_react_map(owner_id)
            mapping[str(target["id"])] = emoji
            _save_react_map(owner_id, mapping)
            await edit(f"از این به بعد پیام‌های {target.get('name') or target['id']} با {emoji} ری‌اکت می‌شود.")

    elif text == "حذف ری‌اکت":
        target = await _resolve_target(event, text.split())
        if not target:
            await edit("روی پیام کاربر ریپلای کن.")
        else:
            mapping = _get_react_map(owner_id)
            mapping.pop(str(target["id"]), None)
            _save_react_map(owner_id, mapping)
            await edit("ری‌اکت اختصاصی این کاربر حذف شد.")

    # ─── ترک همگانی گروه/کانال ──────────────────────────────────────────────
    elif text == "ترک همگانی گروه":
        from telethon.tl.functions.channels import LeaveChannelRequest
        await edit("در حال ترک همه گروه‌ها...")
        count = 0
        async for dialog in cl.iter_dialogs():
            if dialog.is_group:
                try:
                    await cl(LeaveChannelRequest(dialog.entity))
                    count += 1
                except Exception:
                    pass
        await edit(f"ترک همگانی گروه انجام شد. تعداد: {count}")

    elif text == "ترک همگانی کانال":
        from telethon.tl.functions.channels import LeaveChannelRequest
        await edit("در حال ترک همه کانال‌ها...")
        count = 0
        async for dialog in cl.iter_dialogs():
            if dialog.is_channel and not dialog.is_group:
                try:
                    await cl(LeaveChannelRequest(dialog.entity))
                    count += 1
                except Exception:
                    pass
        await edit(f"ترک همگانی کانال انجام شد. تعداد: {count}")

    # ─── تبدیل ویدیوی ریپلای‌شده به گیف ──────────────────────────────────────
    elif text == "تبدیل به گیف":
        if not event.is_reply:
            await edit("لطفا روی یک ویدیو ریپلای کن.")
        else:
            reply = await event.get_reply_message()
            if not reply.video and not reply.document:
                await edit("پیام ریپلای‌شده ویدیو نیست.")
            else:
                await edit("در حال تبدیل...")
                path = await cl.download_media(reply)
                gif_path = os.path.splitext(path)[0] + ".gif"
                try:
                    os.rename(path, gif_path)
                    await cl.send_file(event.chat_id, gif_path)
                    await event.delete()
                except Exception as e:
                    await edit(f"خطا در تبدیل به گیف: {e}")
                finally:
                    try:
                        os.remove(gif_path)
                    except Exception:
                        pass

    # ─── ترجمه‌ی متن ریپلای‌شده ──────────────────────────────────────────────
    elif text == "ترجمه متن":
        if not event.is_reply:
            await edit("لطفا روی یک پیام متنی ریپلای کن.")
        else:
            reply = await event.get_reply_message()
            raw = reply.raw_text
            if not raw:
                await edit("پیام ریپلای‌شده متن ندارد.")
            else:
                result = await _translate(raw)
                await edit(f"ترجمه:\n{result}")

    # ─── دشمن ────────────────────────────────────────────────────────────────
    elif text.startswith("تنظیم دشمن"):
        target = await _resolve_target(event, text.split())
        if target:
            db.add_enemy(owner_id, target["id"], target.get("username"), target.get("name"))
            await edit(f"🔴 {target.get('name', target['id'])} به لیست دشمن اضافه شد.")
        else:
            await edit("❗ روی پیام کاربر ریپلای کن یا آیدی عددی بنویس.")

    elif text.startswith("حذف دشمن"):
        target = await _resolve_target(event, text.split())
        if target:
            removed = db.remove_enemy(owner_id, target["id"])
            await edit("✅ از لیست دشمن حذف شد." if removed else "❗ در لیست نبود.")
        else:
            await edit("❗ روی پیام کاربر ریپلای کن یا آیدی عددی بنویس.")

    elif text == "نمایش لیست دشمن":
        enemies = db.get_enemies(owner_id)
        if not enemies:
            await edit("📋 لیست دشمن خالی است.")
        else:
            lines = [f"🔴 لیست دشمن ({len(enemies)} نفر):\n"]
            for e in enemies:
                lines.append(f"• {e['name'] or e['username'] or e['user_id']} — `{e['user_id']}`")
            await edit("\n".join(lines))

    elif text == "پاک کردن لیست دشمن":
        db.clear_enemies(owner_id)
        await edit("🗑️ لیست دشمن پاک شد.")

    # ─── دوست ────────────────────────────────────────────────────────────────
    elif text.startswith("تنظیم دوست"):
        target = await _resolve_target(event, text.split())
        if target:
            db.add_friend(owner_id, target["id"], target.get("username"), target.get("name"))
            await edit(f"💚 {target.get('name', target['id'])} به لیست دوست اضافه شد.")
        else:
            await edit("❗ روی پیام کاربر ریپلای کن یا آیدی عددی بنویس.")

    elif text.startswith("حذف دوست"):
        target = await _resolve_target(event, text.split())
        if target:
            removed = db.remove_friend(owner_id, target["id"])
            await edit("✅ از لیست دوست حذف شد." if removed else "❗ در لیست نبود.")
        else:
            await edit("❗ روی پیام کاربر ریپلای کن یا آیدی عددی بنویس.")

    elif text == "نمایش لیست دوست":
        friends = db.get_friends(owner_id)
        if not friends:
            await edit("📋 لیست دوست خالی است.")
        else:
            lines = [f"💚 لیست دوست ({len(friends)} نفر):\n"]
            for f in friends:
                lines.append(f"• {f['name'] or f['username'] or f['user_id']} — `{f['user_id']}`")
            await edit("\n".join(lines))

    elif text == "پاک کردن لیست دوست":
        db.clear_friends(owner_id)
        await edit("🗑️ لیست دوست پاک شد.")

    # ─── منشی ────────────────────────────────────────────────────────────────
    elif text == "منشی روشن":
        ss("secretary_active", "1"); await edit("🤖 منشی خودکار روشن شد.\n💡 هر کاربر فقط هر 24 ساعت یک بار پاسخ می‌گیرد.")
    elif text == "منشی خاموش":
        ss("secretary_active", "0"); await edit("🤖 منشی خودکار خاموش شد.")
    elif text.startswith("پیام منشی "):
        ss("secretary_message", text[len("پیام منشی "):].strip())
        await edit("✅ پیام منشی تنظیم شد.")

    # ─── ضد حذف ──────────────────────────────────────────────────────────────
    elif text == "ضد حذف روشن":
        ss("anti_delete_active", "1"); await edit("🛡️ ضد حذف روشن شد.")
    elif text == "ضد حذف خاموش":
        ss("anti_delete_active", "0"); await edit("🛡️ ضد حذف خاموش شد.")

    # ─── بازی میویی ──────────────────────────────────────────────────────────
    elif (_mw := meowie_game.handle_panel_command(text, owner_id, ss, gs, edit))[0]:
        await _mw[1]

    # ─── ضد لینک ─────────────────────────────────────────────────────────────
    elif text == "ضد لینک روشن":
        ss("anti_link_active", "1"); await edit("🔗 ضد لینک روشن شد.")
    elif text == "ضد لینک خاموش":
        ss("anti_link_active", "0"); await edit("🔗 ضد لینک خاموش شد.")

    # ─── قفل پیوی ────────────────────────────────────────────────────────────
    elif text == "قفل پیوی روشن":
        ss("private_lock_active", "1"); await edit("🔒 قفل پیوی روشن شد.")
    elif text == "قفل پیوی خاموش":
        ss("private_lock_active", "0"); await edit("🔓 قفل پیوی خاموش شد.")

    # ─── سین خودکار ──────────────────────────────────────────────────────────
    elif text == "سین خودکار روشن":
        ss("auto_seen_active", "1"); await edit("👁️ سین خودکار روشن شد.")
    elif text == "سین خودکار خاموش":
        ss("auto_seen_active", "0"); await edit("👁️ سین خودکار خاموش شد.")

    # ─── ری‌اکشن ─────────────────────────────────────────────────────────────
    elif text == "ری‌اکشن روشن":
        ss("auto_reaction_active", "1"); await edit("❤️ ری‌اکشن خودکار روشن شد.")
    elif text == "ری‌اکشن خاموش":
        ss("auto_reaction_active", "0"); await edit("❤️ ری‌اکشن خودکار خاموش شد.")
    elif text.startswith("ری‌اکشن "):
        emoji = text[len("ری‌اکشن "):].strip()
        ss("auto_reaction_emoji", emoji); await edit(f"✅ ری‌اکشن پیش‌فرض: {emoji}")

    # ─── ذخیره مدیا ──────────────────────────────────────────────────────────
    elif text == "ذخیره مدیا روشن":
        os.makedirs(f"saved_media/{owner_id}", exist_ok=True)
        ss("auto_save_media", "1"); await edit("💾 ذخیره خودکار مدیا روشن شد.")
    elif text == "ذخیره مدیا خاموش":
        ss("auto_save_media", "0"); await edit("💾 ذخیره خودکار مدیا خاموش شد.")

    # ─── سیو کانال ───────────────────────────────────────────────────────────
    elif text == "سیو کانال" or text.startswith("سیو کانال "):
        parts = text.split()
        channel_input = parts[2] if len(parts) >= 3 else None
        if not channel_input:
            await edit(
                "❗ فرمت درست یکی از این حالت‌هاست:\n"
                "• سیو کانال [لینک یک پست خاص]\n"
                "  مثال: سیو کانال https://t.me/channel/123\n"
                "• سیو کانال [لینک پست کانال خصوصی]\n"
                "  مثال: سیو کانال https://t.me/c/3807322753/674\n"
                "• سیو کانال [@یوزرنیم یا لینک کانال] [تعداد]\n"
                "  مثال: سیو کانال @channel 50"
            )
        elif _POST_LINK_RE.match(channel_input) or _PRIVATE_POST_LINK_RE.match(channel_input):
            await edit("⏳ در حال ذخیره این پست...")
            asyncio.ensure_future(_save_channel_media(cl, channel_input, None, owner_id))
        else:
            limit = int(parts[3]) if len(parts) >= 4 and parts[3].isdigit() else 100
            await edit(f"⏳ در حال پردازش کانال، تا {limit} مدیا ذخیره می‌شود...")
            asyncio.ensure_future(_save_channel_media(cl, channel_input, limit, owner_id))

    elif text == "توقف سیو":
        ss("channel_save_active", "0"); await edit("🛑 سیو کانال متوقف شد.")

    # ─── سایلنت ──────────────────────────────────────────────────────────────
    elif text == "سایلنت چت روشن":
        chat = await event.get_chat()
        db.add_silent_chat(owner_id, chat.id); await edit("🔇 این چت سایلنت شد.")
    elif text == "سایلنت چت خاموش":
        chat = await event.get_chat()
        db.remove_silent_chat(owner_id, chat.id); await edit("🔔 سایلنت این چت برداشته شد.")
    elif text.startswith("سایلنت کاربر "):
        uid = int(text.split()[-1])
        db.add_silent_user(owner_id, uid); await edit(f"🔇 کاربر {uid} سایلنت شد.")
    elif text.startswith("لغو سایلنت کاربر "):
        uid = int(text.split()[-1])
        db.remove_silent_user(owner_id, uid); await edit(f"🔔 سایلنت کاربر {uid} برداشته شد.")

    # ─── سکوت (حذف خودکار دوطرفه‌ی پیام‌های یک کاربر در پیوی) ─────────────────
    elif text.startswith("سکوت"):
        parts = text.split()
        target = await _resolve_target_or_username(cl, event, parts)
        if target:
            added = _add_silence_user(owner_id, target["id"], target.get("username"), target.get("name"))
            if added:
                await edit(f"🔇 سکوت برای {target.get('name') or target['id']} فعال شد؛ پیام‌های پیوی این کاربر از این به بعد دوطرفه پاک می‌شود.")
            else:
                await edit("❗ این کاربر از قبل توی لیست سکوت بود.")
        else:
            await edit("❗ روی پیام کاربر ریپلای کن یا آیدی عددی/یوزرنیمش رو بنویس. مثال: سکوت 123456789")

    elif text.startswith("لغو سکوت"):
        parts = text.split()
        target = await _resolve_target_or_username(cl, event, parts)
        if target:
            removed = _remove_silence_user(owner_id, target["id"])
            await edit("🔔 سکوت این کاربر برداشته شد." if removed else "❗ این کاربر توی لیست سکوت نبود.")
        else:
            await edit("❗ روی پیام کاربر ریپلای کن یا آیدی عددی/یوزرنیمش رو بنویس. مثال: لغو سکوت 123456789")

    elif text == "لیست سکوت":
        users = _get_silence_users(owner_id)
        if not users:
            await edit("📋 لیست سکوت خالی است.")
        else:
            lines = [f"🔇 لیست سکوت ({len(users)} نفر):\n"]
            for u in users:
                lines.append(f"• {u.get('name') or u.get('username') or u['id']} — `{u['id']}`")
            await edit("\n".join(lines))

    # ─── پاسخ دشمن ───────────────────────────────────────────────────────────
    elif text == "پاسخ دشمن روشن":
        ss("enemy_reply_active", "1"); await edit("⚔️ پاسخ خودکار به دشمن روشن شد.")
    elif text == "پاسخ دشمن خاموش":
        ss("enemy_reply_active", "0"); await edit("⚔️ پاسخ خودکار به دشمن خاموش شد.")

    # ─── فونت متن (حالت خودکار) ──────────────────────────────────────────────
    elif text == "فونت متن روشن" and had_dot:
        ss("text_font_auto", "1")
        font_id = gs("selected_font", "0")
        fn = FONTS.get(font_id, FONTS["0"])
        sample = fn("Hello World")
        await edit(f"✅ فونت متن خودکار روشن شد.\n✏️ از این به بعد هر پیامی که بنویسی با فونت {font_id} ادیت می‌شه.\nنمونه: `{sample}`")

    elif text == "فونت متن خاموش" and had_dot:
        ss("text_font_auto", "0")
        await edit("❌ فونت متن خودکار خاموش شد.\nپیام‌ها دیگه ادیت نمی‌شن.")

    # ─── قالب‌بندی تلگرام (entities) — کار با فارسی هم دارد ────────────────────
    elif text.startswith("بولد "):
        raw = text[len("بولد "):].strip()
        if raw:
            from telethon.tl.types import MessageEntityBold
            await event.edit(raw, formatting_entities=[MessageEntityBold(0, len(raw))])
        else:
            await edit("❗ فرمت: بولد [متن]")

    elif text.startswith("ایتالیک "):
        raw = text[len("ایتالیک "):].strip()
        if raw:
            from telethon.tl.types import MessageEntityItalic
            await event.edit(raw, formatting_entities=[MessageEntityItalic(0, len(raw))])
        else:
            await edit("❗ فرمت: ایتالیک [متن]")

    elif text.startswith("مونو "):
        raw = text[len("مونو "):].strip()
        if raw:
            from telethon.tl.types import MessageEntityCode
            await event.edit(raw, formatting_entities=[MessageEntityCode(0, len(raw))])
        else:
            await edit("❗ فرمت: مونو [متن]")

    elif text.startswith("اسپویلر "):
        raw = text[len("اسپویلر "):].strip()
        if raw:
            from telethon.tl.types import MessageEntitySpoiler
            await event.edit(raw, formatting_entities=[MessageEntitySpoiler(0, _u16len(raw))])
        else:
            await edit("❗ فرمت: اسپویلر [متن]")

    elif text.startswith("کوت "):
        raw = text[len("کوت "):].strip()
        if raw:
            try:
                from telethon.tl.types import MessageEntityBlockquote
                await event.edit(raw, formatting_entities=[MessageEntityBlockquote(0, _u16len(raw), collapsed=False)])
            except Exception:
                # fallback برای نسخه‌های قدیمی‌تر telethon
                await event.edit(f"❝ {raw} ❞")
        else:
            await edit("❗ فرمت: کوت [متن]")

    elif text.startswith("خط‌خورده "):
        raw = text[len("خط‌خورده "):].strip()
        if raw:
            from telethon.tl.types import MessageEntityStrike
            await event.edit(raw, formatting_entities=[MessageEntityStrike(0, len(raw))])
        else:
            await edit("❗ فرمت: خط‌خورده [متن]")

    elif text.startswith("زیرخط "):
        raw = text[len("زیرخط "):].strip()
        if raw:
            from telethon.tl.types import MessageEntityUnderline
            await event.edit(raw, formatting_entities=[MessageEntityUnderline(0, len(raw))])
        else:
            await edit("❗ فرمت: زیرخط [متن]")

    # ─── فونت ساعت ──────────────────────────────────────────────────────────
    elif text.startswith("فونت ساعت ") and had_dot:
        font_id = text.split()[-1]
        if font_id in CLOCK_FONTS:
            ss("selected_clock_font", font_id)
            digits = CLOCK_FONTS[font_id]
            sample = _apply_clock_font(owner_id, "12:34")
            await edit(f"⏰ فونت ساعت {font_id} انتخاب شد:\n`{sample}`")
        else:
            await edit("❗ شماره فونت ساعت باید بین ۰ تا ۹ باشد.")
    elif text == "لیست فونت ساعت" and had_dot:
        lines = ["⏰ **فونت‌های ساعت موجود:**\n"]
        for k, digits in CLOCK_FONTS.items():
            sample = "".join(digits[int(ch)] for ch in "1234567890")
            lines.append(f"`.فونت ساعت {k}` — `{sample}`")
        lines.append("\n💡 برای انتخاب: `.فونت ساعت [شماره]`")
        await edit("\n".join(lines))

    # ─── فونت ────────────────────────────────────────────────────────────────
    elif text.startswith("فونت ") and had_dot:
        parts = text.split()
        # ".فونت 4" یا ".فونت amel 4"
        font_id = parts[-1]
        preview_words = parts[1:-1]  # کلمات بین "فونت" و شماره
        if font_id in FONTS:
            ss("selected_font", font_id)
            fn = FONTS[font_id]
            if preview_words:
                preview = fn(" ".join(preview_words))
                await edit(f"✅ فونت {font_id} انتخاب شد:\n`{preview}`")
            else:
                sample = fn("Hello World")
                await edit(f"✅ فونت {font_id} انتخاب شد.\nنمونه: `{sample}`")
        else:
            await edit("❗ شماره فونت باید بین ۰ تا ۸ باشد.")

    elif text == "لیست فونت" and had_dot:
        lines = ["🔤 **فونت‌های موجود:**\n"]
        for k in FONTS:
            fn = FONTS[k]
            sample = fn("Hello World")
            lines.append(f"`.فونت {k}` — `{sample}`")
        lines.append("\n💡 برای انتخاب: `.فونت [شماره]`")
        await edit("\n".join(lines))

    # ─── ساعت نام/بیو ─────────────────────────────────────────────────────────
    elif text == "ساعت نام روشن":
        ss("clock_name_active", "1"); await edit("⏰ ساعت نام روشن شد.")
    elif text == "ساعت نام خاموش":
        ss("clock_name_active", "0"); await edit("⏰ ساعت نام خاموش شد.")
    elif text == "ساعت بیو روشن":
        ss("clock_bio_active", "1"); await edit("⏰ ساعت بیو روشن شد.")
    elif text == "ساعت بیو خاموش":
        ss("clock_bio_active", "0"); await edit("⏰ ساعت بیو خاموش شد.")

    # ─── اسپم ────────────────────────────────────────────────────────────────
    elif text.startswith("اسپم "):
        parts = text.split(maxsplit=2)
        if len(parts) >= 3 and parts[1].isdigit():
            count = int(parts[1])
            spam_text = parts[2]
            chat = await event.get_chat()
            ss("spam_active", "1")
            await msg.delete()
            asyncio.ensure_future(_do_spam(cl, owner_id, chat.id, spam_text, count))
        # اگه فرمت درست نیست → هیچ کاری نکن (بی‌صدا)
    elif text == "توقف اسپم":
        ss("spam_active", "0"); await edit("🛑 اسپم متوقف شد.")

    # ─── حذف خودکار ──────────────────────────────────────────────────────────
    elif text.startswith("حذف بعد "):
        parts = text.split()
        if len(parts) >= 3 and parts[2].isdigit():
            secs = int(parts[2])
            await edit(f"⏱️ پیام بعد از {secs} ثانیه حذف می‌شود.")
            await asyncio.sleep(secs)
            try:
                await msg.delete()
            except Exception:
                pass

    # ─── ذخیره پیام ──────────────────────────────────────────────────────────
    elif text.startswith("ذخیره "):
        parts = text.split()
        if len(parts) >= 2 and parts[1].isdigit():
            slot = int(parts[1])
            if 1 <= slot <= 10:
                replied = await event.get_reply_message()
                if replied:
                    db.save_message_slot(owner_id, slot, replied.text or "")
                    await edit(f"💾 پیام در اسلات {slot} ذخیره شد.")
                else:
                    await edit("❗ روی پیام مورد نظر ریپلای کن.")
            else:
                await edit("❗ اسلات باید بین ۱ تا ۱۰ باشد.")

    elif text.startswith("ارسال ذخیره "):
        parts = text.split()
        if len(parts) >= 3 and parts[2].isdigit():
            slot = int(parts[2])
            saved = db.get_message_slot(owner_id, slot)
            if saved:
                chat = await event.get_chat()
                await cl.send_message(chat.id, saved["content"])
                await msg.delete()
            else:
                await edit(f"❗ اسلات {slot} خالی است.")

    # ─── ترجمه ───────────────────────────────────────────────────────────────
    elif text.startswith("ترجمه "):
        to_tr = text[len("ترجمه "):].strip()
        if not to_tr:
            replied = await event.get_reply_message()
            if replied:
                to_tr = replied.text or ""
        if to_tr:
            await edit(f"🌐 ترجمه:\n{await _translate(to_tr)}")
        else:
            await edit("❗ متن یا ریپلای لازم است.")

    # ─── هواشناسی ────────────────────────────────────────────────────────────
    elif text.startswith("هوا ") and had_dot:
        await edit(await _get_weather(text[len("هوا "):].strip()))

    # ─── قیمت ارز ────────────────────────────────────────────────────────────
    elif text == "ارز" or text == "قیمت دلار" or text.startswith("ارز "):
        sub = text[len("ارز"):].strip() if text != "قیمت دلار" else "دلار"
        sub = sub.replace("‌", " ").replace("‏", "")  # حذف نیم‌فاصله/کاراکترهای نامرئی
        if any(k in sub for k in ("بیت کوین", "بیتکوین", "bitcoin", "btc")):
            target = "btc"
        elif any(k in sub for k in ("تتر", "tether", "usdt")):
            target = "usdt"
        elif any(k in sub for k in ("یورو", "eur")):
            target = "eur"
        elif any(k in sub for k in ("پوند", "gbp")):
            target = "gbp"
        elif any(k in sub for k in ("دلار", "usd")):
            target = "usd"
        else:
            target = None  # بدون نام ارز خاص → نمایش لیست ارزهای مهم
        await edit(await _get_currency_text(target))

    # ─── جوین اجباری (چند کاناله، لیست توی دیتابیس دائمی ذخیره می‌شه) ─────────
    elif text.startswith("افزودن کانال "):
        raw = text[len("افزودن کانال "):].strip()
        parts = raw.split()
        if len(parts) < 2:
            await edit("❗ فرمت: افزودن کانال [آیدی یا @یوزرنیم] [لینک]\nمثال: افزودن کانال @mychannel https://t.me/mychannel")
        else:
            channel_input, link = parts[0], parts[1]
            if not link.startswith("http"):
                link = "https://t.me/" + link.lstrip("@")
            try:
                entity = await cl.get_entity(
                    int(channel_input.lstrip("-")) * (-1 if channel_input.startswith("-") else 1)
                    if channel_input.lstrip("-").isdigit() else channel_input
                )
                real_id = str(entity.id)
                title = getattr(entity, "title", channel_input)
                channels = _get_force_join_channels(owner_id)
                channels = [c for c in channels if c.get("id") != real_id]
                channels.append({"id": real_id, "title": title, "link": link})
                _save_force_join_channels(owner_id, channels)
                ss("force_join_active", "1")
                await edit(
                    f"✅ کانال به لیست جوین اجباری اضافه شد:\n"
                    f"📢 {title} (ID: {real_id})\n"
                    f"🔗 {link}\n\n"
                    f"در حال حاضر {len(channels)} کانال توی لیست هست.\n\n"
                    f"💡 دستورات:\n"
                    f"> `لیست کانال‌های اجباری`\n"
                    f"> `حذف کانال [آیدی/یوزرنیم]`\n"
                    f"> `جوین اجباری روشن` / `جوین اجباری خاموش`\n"
                    f"> `پیام جوین [متن]` — تغییر پیام هشدار"
                )
            except Exception as e:
                await edit(f"❌ کانال پیدا نشد: {e}\n\n💡 مطمئن شو سلف عضو کانال/گروه هست.")

    elif text.startswith("حذف کانال "):
        raw = text[len("حذف کانال "):].strip()
        if not raw:
            await edit("❗ فرمت: حذف کانال [آیدی یا @یوزرنیم]")
        else:
            channels = _get_force_join_channels(owner_id)
            target = raw.lstrip("@").lower()
            new_channels = [
                c for c in channels
                if raw != c.get("id")
                and target not in (c.get("title", "").lower())
                and target not in (c.get("link", "").lower())
            ]
            if len(new_channels) == len(channels):
                await edit("❗ همچین کانالی توی لیست جوین اجباری نیست.")
            else:
                _save_force_join_channels(owner_id, new_channels)
                if not new_channels:
                    ss("force_join_active", "0")
                await edit(f"🗑️ کانال حذف شد. {len(new_channels)} کانال باقی مونده.")

    elif text == "پاک کردن کانال‌های اجباری":
        _save_force_join_channels(owner_id, [])
        ss("force_join_active", "0")
        await edit("🗑️ همه‌ی کانال‌های جوین اجباری پاک شدند.")

    elif text == "لیست کانال‌های اجباری":
        channels = _get_force_join_channels(owner_id)
        if not channels:
            await edit("❗ هیچ کانالی توی لیست جوین اجباری نیست.\nبرای افزودن: `افزودن کانال [آیدی/یوزرنیم] [لینک]`")
        else:
            lines = [f"📋 کانال‌های جوین اجباری ({len(channels)} کانال):\n"]
            for c in channels:
                lines.append(f"📢 {c.get('title', '؟')} — {c.get('link', '')}")
            await edit("\n".join(lines))

    elif text == "جوین اجباری روشن":
        channels = _get_force_join_channels(owner_id)
        if not channels:
            await edit("❗ اول حداقل یه کانال اضافه کن: `افزودن کانال [آیدی/یوزرنیم] [لینک]`")
        else:
            ss("force_join_active", "1")
            await edit("✅ جوین اجباری روشن شد.")

    elif text == "جوین اجباری خاموش":
        ss("force_join_active", "0")
        await edit("❌ جوین اجباری خاموش شد.")

    elif text.startswith("پیام جوین "):
        new_msg = text[len("پیام جوین "):].strip()
        if not new_msg:
            await edit("❗ فرمت: پیام جوین [متن پیام]")
        else:
            ss("force_join_message", new_msg)
            await edit(f"✅ پیام جوین اجباری تنظیم شد:\n\n{new_msg}")

    # ─── وضعیت ───────────────────────────────────────────────────────────────
    elif text == "وضعیت":
        status_map = {
            "self_bot_active": "سلف‌بات", "secretary_active": "منشی",
            "anti_delete_active": "ضد حذف", "anti_link_active": "ضد لینک",
            "auto_seen_active": "سین خودکار", "auto_reaction_active": "ری‌اکشن",
            "private_lock_active": "قفل پیوی", "enemy_reply_active": "پاسخ دشمن",
            "auto_save_media": "ذخیره مدیا", "clock_name_active": "ساعت نام",
            "clock_bio_active": "ساعت بیو", "force_join_active": "جوین اجباری",
        }
        lines = [f"📊 وضعیت {config.BOT_NAME} v{config.BOT_VERSION}\n"]
        for key, label in status_map.items():
            icon = "✅" if gs(key) == "1" else "❌"
            lines.append(f"{icon} {label}")
        lines.append(f"\n🔤 فونت: {gs('selected_font', '0')}")
        lines.append(f"✏️ فونت متن خودکار: {'✅ روشن' if gs('text_font_auto','0')=='1' else '❌ خاموش'}")
        lines.append(f"⏰ فونت ساعت: {gs('selected_clock_font', '0')}")
        fj_channels = _get_force_join_channels(owner_id)
        if fj_channels:
            lines.append(f"📢 کانال‌های جوین اجباری: {len(fj_channels)} کانال")
        lines.append(f"👥 دشمن: {len(db.get_enemies(owner_id))} نفر")
        lines.append(f"💚 دوست: {len(db.get_friends(owner_id))} نفر")
        await edit("\n".join(lines))

    # ─── راهنما ───────────────────────────────────────────────────────────────
    elif text in ("راهنما", "help"):
        await edit(_help_text())

    # ─── ارسال زمان‌بندی شده ─────────────────────────────────────────────────
    elif text.startswith("ارسال زمان‌بندی "):
        m = re.match(r"^ارسال زمان‌بندی (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) (.+)$", text, re.DOTALL)
        if m:
            chat = await event.get_chat()
            db.add_scheduled_message(owner_id, chat.id, m.group(2), m.group(1) + ":00")
            await edit(f"📅 پیام در {m.group(1)} ارسال خواهد شد.")
        else:
            await edit("❗ فرمت: ارسال زمان‌بندی [YYYY-MM-DD HH:MM] متن")

    # ─── ایدی: اطلاعات کامل کاربر/چت (با ریپلای = اطلاعات همون کاربر) ──────────
    elif text == "ایدی":
        try:
            replied = await event.get_reply_message()
            if replied:
                sender = await replied.get_sender()
                chat = await event.get_chat()
                username = f"@{sender.username}" if getattr(sender, "username", None) else "ندارد"
                premium = "فعال" if getattr(sender, "premium", False) else "غیرفعال"
                info = (
                    "• اطلاعات کاربر\n\n"
                    f"آیدی عددی: {sender.id}\n"
                    f"یوزرنیم: {username}\n"
                    f"نام: {getattr(sender, 'first_name', None) or 'ندارد'}\n"
                    f"نام خانوادگی: {getattr(sender, 'last_name', None) or 'ندارد'}\n"
                    f"پریمیوم: {premium}\n\n"
                    "• اطلاعات چت\n"
                    f"آیدی چت: {chat.id}\n"
                    f"عنوان چت: {getattr(chat, 'title', None) or 'ندارد'}\n"
                )
                try:
                    common = await cl(GetCommonChatsRequest(user_id=await cl.get_input_entity(sender.id), max_id=0, limit=20))
                    if common.chats:
                        info += f"\n• گروه‌های مشترک: {len(common.chats)}\n"
                        for i, c in enumerate(common.chats, 1):
                            uname = f"@{c.username}" if getattr(c, "username", None) else "بدون یوزرنیم"
                            members = getattr(c, "participants_count", None)
                            info += f"{i}. {getattr(c, 'title', '')} | {uname} | {members if members else 'نامشخص'} عضو | {c.id}\n"
                except Exception:
                    pass
                await edit(info)
            else:
                me = await cl.get_me()
                chat = await event.get_chat()
                username = f"@{me.username}" if getattr(me, "username", None) else "ندارد"
                info = (
                    "• اطلاعات شما\n\n"
                    f"آیدی عددی: {me.id}\n"
                    f"یوزرنیم: {username}\n"
                    f"نام: {getattr(me, 'first_name', None) or 'ندارد'}\n"
                    f"نام خانوادگی: {getattr(me, 'last_name', None) or 'ندارد'}\n"
                    f"پریمیوم: {'فعال' if getattr(me, 'premium', False) else 'غیرفعال'}\n\n"
                    "• اطلاعات چت فعلی\n"
                    f"آیدی چت: {chat.id}\n"
                    f"عنوان چت: {getattr(chat, 'title', None) or 'ندارد'}\n"
                    f"تعداد اعضا: {getattr(chat, 'participants_count', None) or 'نامشخص'}\n"
                )
                await edit(info)
        except Exception as e:
            await edit(f"❌ خطا در دریافت اطلاعات: {e}")

    # ─── دانلود: کپی یک پست از لینک تلگرام به «پیام‌های ذخیره‌شده» ─────────────
    elif text.startswith("دانلود "):
        link = text[len("دانلود "):].strip()
        m = re.match(r"^https?://t\.me/(?:c/)?([^/]+)/(\d+)$", link)
        if not m:
            await edit("❗ فرمت: دانلود https://t.me/channel/123")
        else:
            username_or_id, post_id = m.group(1), int(m.group(2))
            try:
                await edit("🔄 در حال دریافت پست...")
                if username_or_id.isdigit():
                    entity = int(f"-100{username_or_id}")
                else:
                    entity = username_or_id
                post = await cl.get_messages(entity, ids=post_id)
                if not post:
                    await edit("❌ پست یافت نشد.")
                else:
                    try:
                        await cl.forward_messages("me", post)
                    except Exception:
                        if post.media:
                            await cl.send_file("me", post.media, caption=post.text or "")
                        elif post.text:
                            await cl.send_message("me", post.text)
                    await edit("✅ پست به پیام‌های ذخیره‌شده کپی شد.")
            except Exception as e:
                await edit(f"❌ خطا: {e}")

    # ─── اینستا: دانلود پست/ریل اینستاگرام از طریق یک API شخص‌ثالث ─────────────
    # ⚠️ این قابلیت به یک سرویس/کلید خارجی (fast-creat.ir) وابسته‌ست که تحت
    # کنترل ما نیست؛ در صورت از کار افتادن یا محدودیت اون سرویس، این دستور
    # هم کار نخواهد کرد. اگه بخوای می‌تونیم بعداً با یه API قابل‌اعتمادتر یا
    # سلف‌هاستد جایگزینش کنیم.
    elif text.startswith("اینستا "):
        url = text[len("اینستا "):].strip()
        if not url.startswith(("https://www.instagram.com/", "https://instagram.com/")):
            await edit("❗ فرمت: اینستا [لینک پست یا ریل اینستاگرام]")
        elif "/stories/" in url or "/story/" in url:
            await edit("❌ این دستور فقط برای پست‌ها و ریل‌ها کار می‌کند؛ استوری پشتیبانی نمی‌شود.")
        else:
            await edit("🔄 در حال دریافت اطلاعات از اینستاگرام...")
            try:
                import urllib.parse
                api_key = "8000978149:uJC3mxBncq9ELPN@Api_ManagerRoBot"
                encoded_url = urllib.parse.quote(url, safe="")
                api_url = f"https://api.fast-creat.ir/instagram?apikey={api_key}&type=post&url={encoded_url}"
                resp = await asyncio.to_thread(requests.get, api_url, timeout=45)
                if resp.status_code != 200:
                    await edit(f"❌ خطا در اتصال به سرور (کد {resp.status_code})")
                else:
                    data = resp.json()
                    result = (data or {}).get("result", {})
                    posts = result.get("result", []) if result.get("status") == "success" else []
                    if not posts:
                        await edit("❌ محتوایی یافت نشد یا لینک نامعتبر است.")
                    else:
                        post = posts[0]
                        media_url = post.get("video_url") or post.get("url") or post.get("thumbnail_url")
                        caption = (post.get("caption") or "")[:500]
                        cap_text = f"👤 @{post.get('username','نامشخص')}\n\n{caption}"
                        if media_url:
                            await cl.send_file(event.chat_id if False else "me", media_url, caption=cap_text)
                        else:
                            await cl.send_message("me", cap_text)
                        await edit("✅ محتوا در پیام‌های ذخیره‌شده ارسال شد.")
            except Exception as e:
                await edit(f"❌ خطا: {e}")

    # ─── پیام عادی (دستور نیست) — اعمال فونت اگه حالت خودکار روشنه ─────────────
    else:
        font_id = gs("selected_font", "0")
        auto_active = gs("text_font_auto", "0") == "1"
        # فونت خودکار: فقط وقتی "فونت متن روشن" باشه، همه پیام‌ها ادیت می‌شن
        if auto_active and font_id != "0" and text:
            fn = FONTS.get(font_id, FONTS["0"])
            styled = fn(text)
            if styled != text:
                try:
                    await event.edit(styled)
                except FloodWaitError as e:
                    await asyncio.sleep(e.seconds + 1)
                except Exception:
                    pass

        # ─── حالت متن (سطح ۲ پنل): اعمال خودکار سبک‌های نوشتاری روی پیام ────
        style_on = {
            "quote": gs("text_style_quote_active", "0") == "1",
            "underline": gs("text_style_underline_active", "0") == "1",
            "spoiler": gs("text_style_spoiler_active", "0") == "1",
            "bold": gs("text_style_bold_active", "0") == "1",
            "italic": gs("text_style_italic_active", "0") == "1",
            "strike": gs("text_style_strike_active", "0") == "1",
            "single_space": gs("text_style_single_space_active", "0") == "1",
            "gradual": gs("text_style_gradual_active", "0") == "1",
            "finglish": gs("text_style_finglish_active", "0") == "1",
        }
        if text and any(style_on.values()):
            import html as _html_mod

            if style_on["finglish"]:
                body = to_finglish(text)
            elif style_on["single_space"]:
                body = " ".join(list(text.replace(" ", "")))
            else:
                body = text

            # ─── به‌جای محاسبه‌ی دستیِ آفست/طول entity ها (که خطاپذیر بود و
            # روی برخی پیام‌ها بی‌صدا شکست می‌خورد)، از تگ‌های HTML استفاده
            # می‌کنیم — دقیقاً همون روشی که در تست عملی درست کار کرده.
            def _wrap_html(s: str) -> str:
                escaped = _html_mod.escape(s, quote=False)
                if style_on["bold"]:
                    escaped = f"<b>{escaped}</b>"
                if style_on["italic"]:
                    escaped = f"<i>{escaped}</i>"
                if style_on["underline"]:
                    escaped = f"<u>{escaped}</u>"
                if style_on["strike"]:
                    escaped = f"<s>{escaped}</s>"
                if style_on["spoiler"]:
                    escaped = f"<spoiler>{escaped}</spoiler>"
                if style_on["quote"]:
                    escaped = f"<blockquote>{escaped}</blockquote>"
                return escaped

            try:
                if style_on["gradual"]:
                    # افکت تایپ تدریجی: پیام رو در چند مرحله کامل نشون می‌ده
                    steps = 5
                    n = len(body)
                    for i in range(1, steps + 1):
                        cut = max(1, (n * i) // steps)
                        partial = body[:cut]
                        try:
                            await event.edit(_wrap_html(partial), parse_mode="html")
                        except FloodWaitError as e:
                            await asyncio.sleep(e.seconds + 1)
                        except Exception as e:
                            print(f"❌ خطا در افکت تدریجی: {e!r}")
                            break
                        if cut < n:
                            await asyncio.sleep(0.35)
                else:
                    if body != text or any(
                        style_on[k] for k in ("bold", "italic", "underline", "strike", "spoiler", "quote")
                    ):
                        await event.edit(_wrap_html(body), parse_mode="html")
            except FloodWaitError as e:
                await asyncio.sleep(e.seconds + 1)
            except Exception as e:
                print(f"❌ خطا در اعمال حالت متن (بولد/ایتالیک/...) روی پیام: {e!r}")


# ─── توابع کمکی ────────────────────────────────────────────────────────────────
async def _safe_edit(event, owner_id, text):
    try:
        fn = FONTS.get(db.get_setting(owner_id, "selected_font", "0"), FONTS["0"])
        await event.edit(fn(text))
    except FloodWaitError as e:
        await asyncio.sleep(e.seconds + 1)
    except Exception:
        pass


async def _resolve_target(event, parts):
    replied = await event.get_reply_message()
    if replied:
        sender = await replied.get_sender()
        if sender:
            return {
                "id": sender.id,
                "username": getattr(sender, "username", None),
                "name": getattr(sender, "first_name", str(sender.id)),
            }
    for p in parts[1:]:
        if p.lstrip("-").isdigit():
            return {"id": int(p), "username": None, "name": p}
    return None


async def _resolve_target_or_username(cl, event, parts):
    """
    مثل _resolve_target ولی علاوه بر ریپلای و آیدی عددی، یوزرنیم (@user یا user) را
    هم با کوئری گرفتن از تلگرام به آیدی عددی تبدیل می‌کند. برای دستور «سکوت» استفاده می‌شود.
    """
    target = await _resolve_target(event, parts)
    if target:
        return target
    for p in parts[1:]:
        candidate = p.lstrip("@")
        if not candidate:
            continue
        try:
            entity = await cl.get_entity(candidate)
            return {
                "id": entity.id,
                "username": getattr(entity, "username", None),
                "name": getattr(entity, "first_name", None) or candidate,
            }
        except Exception:
            continue
    return None


# ─── سکوت: حذف خودکار و دوطرفه‌ی پیام‌های یک کاربر خاص در پیوی ────────────────
_SILENCE_KEY = "silence_users"


def _get_silence_users(owner_id: int) -> list:
    raw = db.get_setting(owner_id, _SILENCE_KEY, "")
    if not raw:
        return []
    try:
        return json.loads(raw)
    except Exception:
        return []


def _save_silence_users(owner_id: int, users: list):
    db.set_setting(owner_id, _SILENCE_KEY, json.dumps(users))


def _add_silence_user(owner_id: int, user_id: int, username=None, name=None):
    users = _get_silence_users(owner_id)
    if any(u["id"] == user_id for u in users):
        return False
    users.append({"id": user_id, "username": username, "name": name})
    _save_silence_users(owner_id, users)
    return True


def _remove_silence_user(owner_id: int, user_id: int) -> bool:
    users = _get_silence_users(owner_id)
    new_users = [u for u in users if u["id"] != user_id]
    if len(new_users) == len(users):
        return False
    _save_silence_users(owner_id, new_users)
    return True


def _is_silence_user(owner_id: int, user_id: int) -> bool:
    return any(u["id"] == user_id for u in _get_silence_users(owner_id))


# ─── بلاک: لیست کاربرانی که با «بلاک کاربر» بلاک شدن ──────────────────────────
_BLOCK_KEY = "blocked_users"


def _get_block_list(owner_id: int) -> list:
    raw = db.get_setting(owner_id, _BLOCK_KEY, "")
    if not raw:
        return []
    try:
        return json.loads(raw)
    except Exception:
        return []


def _save_block_list(owner_id: int, users: list):
    db.set_setting(owner_id, _BLOCK_KEY, json.dumps(users))


# ─── ری‌اکت اختصاصی: یک ایموجی ثابت که فقط برای پیام‌های یک کاربر خاص زده می‌شه ─
_REACT_MAP_KEY = "user_react_map"


def _get_react_map(owner_id: int) -> dict:
    raw = db.get_setting(owner_id, _REACT_MAP_KEY, "")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _save_react_map(owner_id: int, mapping: dict):
    db.set_setting(owner_id, _REACT_MAP_KEY, json.dumps(mapping))


# ─── پاسخ کلیدی: لیستی از {کلمه ← پاسخ} که با پیدا شدن کلمه توی پیام فعال میشه ──
_KEYWORD_REPLY_KEY = "keyword_replies"


def _get_keyword_replies(owner_id: int) -> list:
    raw = db.get_setting(owner_id, _KEYWORD_REPLY_KEY, "")
    if not raw:
        return []
    try:
        return json.loads(raw)
    except Exception:
        return []


def _save_keyword_replies(owner_id: int, rules: list):
    db.set_setting(owner_id, _KEYWORD_REPLY_KEY, json.dumps(rules))


# ─── جوین اجباری: لیست کانال‌هایی که کاربر باید عضوشون باشه (توی دیتابیس دائمی
# ذخیره می‌شه، چون db.get_setting/set_setting از جدول Supabase استفاده می‌کنن) ──
_FORCE_JOIN_KEY = "force_join_channels"


def _get_force_join_channels(owner_id: int) -> list:
    raw = db.get_setting(owner_id, _FORCE_JOIN_KEY, "")
    if not raw:
        return []
    try:
        return json.loads(raw)
    except Exception:
        return []


def _save_force_join_channels(owner_id: int, channels: list):
    db.set_setting(owner_id, _FORCE_JOIN_KEY, json.dumps(channels))


# ─── فیلتر کلمات: لیست کلماتی که پیام حاوی‌شون در پیوی حذف می‌شه ───────────────
_FILTER_WORDS_KEY = "filtered_words"


def _get_filtered_words(owner_id: int) -> list:
    raw = db.get_setting(owner_id, _FILTER_WORDS_KEY, "")
    if not raw:
        return []
    try:
        return json.loads(raw)
    except Exception:
        return []


def _save_filtered_words(owner_id: int, words: list):
    db.set_setting(owner_id, _FILTER_WORDS_KEY, json.dumps(words))


# ─── تبچی: مدیریت بنرها (ثبت با ریپلای، فعال‌سازی، ارسال چرخشی/فوری) ──────────
_TABCHI_KEY = "tabchi_banners"
_TABCHI_SLOTS = [str(i) for i in range(1, 11)]  # شماره بنرها از ۱ تا ۱۰


def _get_tabchi(owner_id: int) -> dict:
    raw = db.get_setting(owner_id, _TABCHI_KEY, "")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _save_tabchi(owner_id: int, data: dict):
    db.set_setting(owner_id, _TABCHI_KEY, json.dumps(data))


def _tabchi_next_free_slot(data: dict) -> str:
    for slot in _TABCHI_SLOTS:
        if slot not in data:
            return slot
    return None


async def _tabchi_deliver(cl, target_chat_id, banner: dict):
    """محتوای یک بنر رو (بدون تگ Forwarded from) توی یک چت می‌فرسته."""
    try:
        src_msg = await cl.get_messages(banner["chat_id"], ids=banner["msg_id"])
        if not src_msg:
            return False
        if src_msg.media:
            await cl.send_file(target_chat_id, src_msg.media, caption=src_msg.text or "")
        elif src_msg.text:
            await cl.send_message(target_chat_id, src_msg.text)
        else:
            return False
        return True
    except Exception:
        return False


async def _tabchi_loop(cl, owner_id):
    """
    چرخه‌ی پس‌زمینه‌ی ارسال خودکار بنرها: تا وقتی «تبچی روشن» باشه، هر بنرِ
    فعال رو طبق مقصدش (این چت / همه گروه‌ها) با فاصله‌ی تنظیم‌شده می‌فرسته.
    """
    while True:
        try:
            if db.get_setting(owner_id, "tabchi_active") != "1":
                await asyncio.sleep(5)
                continue

            data = _get_tabchi(owner_id)
            active_banners = {k: v for k, v in data.items() if v.get("active")}
            if not active_banners:
                await asyncio.sleep(10)
                continue

            for slot, banner in active_banners.items():
                if db.get_setting(owner_id, "tabchi_active") != "1":
                    break
                mode = banner.get("target_mode")
                if mode == "this_chat" and banner.get("target_chat_id"):
                    await _tabchi_deliver(cl, banner["target_chat_id"], banner)
                    await asyncio.sleep(2)
                elif mode == "all_groups":
                    try:
                        async for dialog in cl.iter_dialogs():
                            if not dialog.is_group and not dialog.is_channel:
                                continue
                            if db.get_setting(owner_id, "tabchi_active") != "1":
                                break
                            await _tabchi_deliver(cl, dialog.id, banner)
                            await asyncio.sleep(2)
                    except Exception:
                        pass

            interval_min = int(db.get_setting(owner_id, "tabchi_interval", "30") or "30")
            await asyncio.sleep(max(1, interval_min) * 60)
        except Exception as e:
            print(f"خطا در _tabchi_loop: {e}")
            await asyncio.sleep(15)


async def _do_spam(cl, owner_id, chat_id, text, count):
    # delay پیش‌فرض ۱ ثانیه (دو برابر سرعت نسبت به قبل که ۲ بود)
    delay = float(db.get_setting(owner_id, "spam_delay", "1"))
    sent = 0
    while True:
        if db.get_setting(owner_id, "spam_active") != "1":
            break
        if sent >= count:
            break
        try:
            await cl.send_message(chat_id, text)
            sent += 1
            await asyncio.sleep(delay)
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds + 1)
        except Exception:
            break
    db.set_setting(owner_id, "spam_active", "0")


async def _save_channel_media(cl, channel_input, limit, owner_id):
    db.set_setting(owner_id, "channel_save_active", "1")
    media_dir = f"saved_media/{owner_id}"
    os.makedirs(media_dir, exist_ok=True)
    try:
        me = await cl.get_me()

        # ─── حالت ۱: لینک یک پست خاص (کانال عمومی یا خصوصی) ──────────────
        post_match = _POST_LINK_RE.match(channel_input)
        private_match = _PRIVATE_POST_LINK_RE.match(channel_input)
        if post_match or private_match:
            try:
                if private_match:
                    # لینک کانال خصوصی: t.me/c/CHANNEL_ID/MSG_ID — این CHANNEL_ID
                    # همون آیدیِ خامِ کانال (بدون پیشوند -100) هست
                    from telethon.tl.types import PeerChannel
                    raw_channel_id, post_id = int(private_match.group(1)), int(private_match.group(2))
                    entity = await cl.get_entity(PeerChannel(raw_channel_id))
                else:
                    entity, post_id = post_match.group(1), int(post_match.group(2))
                target_msg = await cl.get_messages(entity, ids=post_id)
            except Exception as e:
                await cl.send_message(me.id, f"❌ پست پیدا نشد: {e}\n\n💡 مطمئن شو سلف عضو این کانال هست.")
                db.set_setting(owner_id, "channel_save_active", "0")
                return

            if not target_msg or not target_msg.media:
                await cl.send_message(me.id, "❗ این پست مدیا ندارد یا پیدا نشد.")
            else:
                try:
                    path = await cl.download_media(target_msg, file=media_dir + "/")
                    caption = f"📥 سیو پست\n📌 پیام #{target_msg.id}"
                    if target_msg.text:
                        caption += f"\n📝 {target_msg.text[:100]}"
                    await cl.send_file(me.id, path, caption=caption)
                    await cl.send_message(me.id, "✅ پست با موفقیت ذخیره شد.")
                except Exception as e:
                    await cl.send_message(me.id, f"❌ خطا در ذخیره پست: {e}")
            db.set_setting(owner_id, "channel_save_active", "0")
            return

        # ─── حالت ۲: کانال + تعداد ──────────────────────────────────────
        limit = limit or 100
        if channel_input.startswith("https://t.me/"):
            channel_input = channel_input.replace("https://t.me/", "")
        if channel_input.startswith("@"):
            channel_input = channel_input[1:]

        saved = skipped = 0
        async for msg in cl.iter_messages(channel_input, limit=limit):
            if db.get_setting(owner_id, "channel_save_active") != "1":
                break
            if msg.media:
                try:
                    path = await cl.download_media(msg, file=media_dir + "/")
                    if path:
                        caption = f"📥 سیو کانال\n📌 پیام #{msg.id}"
                        if msg.text:
                            caption += f"\n📝 {msg.text[:100]}"
                        await cl.send_file(me.id, path, caption=caption)
                        saved += 1
                        await asyncio.sleep(1.5)
                except FloodWaitError as e:
                    await asyncio.sleep(e.seconds + 2)
                except Exception:
                    skipped += 1
            else:
                skipped += 1

        db.set_setting(owner_id, "channel_save_active", "0")
        await cl.send_message(me.id,
            f"✅ سیو کانال تموم شد\n💾 ذخیره شد: {saved}\n⏭ رد شد: {skipped}")
    except Exception as e:
        db.set_setting(owner_id, "channel_save_active", "0")
        try:
            me = await cl.get_me()
            await cl.send_message(me.id, f"❌ خطا در سیو کانال: {e}")
        except Exception:
            pass


async def _ask_deepseek(knowledge_base: str, question: str) -> str:
    """
    یک سوال از طرف کسی که به سلف پیام داده رو به مدل دیپ‌سیک می‌ده، به‌همراه
    اطلاعاتی که خودِ کاربر قبلاً به هوش مصنوعی آموزش داده (مثل لیست قیمت‌ها)،
    و پاسخ متنی رو برمی‌گردونه. اگه کلید API تنظیم نشده باشه یا خطایی رخ بده،
    None برمی‌گردونه (یعنی پاسخی ارسال نشه).
    """
    api_key = getattr(config, "DEEPSEEK_API_KEY", "")
    if not api_key:
        return None
    try:
        import urllib.request
        system_prompt = (
            "تو دستیار پاسخ‌گویی خودکار یک اکانت تلگرام هستی. صاحب اکانت الان "
            "در دسترس نیست. فقط بر اساس اطلاعاتی که صاحب اکانت زیر آورده شده "
            "به پیام‌های افراد پاسخ بده. اگه سوال ربطی به این اطلاعات نداشت یا "
            "اطلاعات کافی نبود، مختصر و محترمانه بگو که صاحب اکانت به‌زودی خودش "
            "جواب می‌ده. پاسخ باید کوتاه، مستقیم و طبیعی باشه، بدون ایموجی.\n\n"
            f"اطلاعاتی که صاحب اکانت داده:\n{knowledge_base or '(چیزی ثبت نشده)'}"
        )
        payload = json.dumps({
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": question},
            ],
            "max_tokens": 300,
            "temperature": 0.4,
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://api.deepseek.com/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )

        def _do_request():
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode())

        data = await asyncio.get_event_loop().run_in_executor(None, _do_request)
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"خطا در ارتباط با دیپ‌سیک: {e}")
        return None


async def _translate(text):
    try:
        import urllib.request, urllib.parse, json
        url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=fa&dt=t&q={urllib.parse.quote(text)}"
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            return data[0][0][0]
    except Exception:
        return "⚠️ خطا در ترجمه"


async def _get_weather(city):
    try:
        import urllib.request, urllib.parse, json
        api_key = config.WEATHER_API_KEY
        if not api_key:
            return "⚠️ کلید API هواشناسی تنظیم نشده."
        url = f"https://api.openweathermap.org/data/2.5/weather?q={urllib.parse.quote(city)}&appid={api_key}&units=metric&lang=fa"
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            return (f"🌤️ هوای {city}:\n"
                    f"وضعیت: {data['weather'][0]['description']}\n"
                    f"دما: {data['main']['temp']}°C\n"
                    f"رطوبت: {data['main']['humidity']}%")
    except Exception:
        return "⚠️ خطا در دریافت اطلاعات هوا"


_CURRENCY_LABELS = {
    "usd":  "💵 دلار آمریکا",
    "eur":  "💶 یورو",
    "gbp":  "💷 پوند انگلیس",
    "usdt": "💎 تتر (USDT)",
    "btc":  "₿ بیت‌کوین",
    "eth":  "⟠ اتریوم",
}
_CURRENCY_DEFAULT_LIST = ("usd", "eur", "gbp", "usdt", "btc", "eth")

_currency_cache = {"data": {}, "ts": 0.0}
_CURRENCY_CACHE_TTL = 60  # ثانیه

async def _fetch_currency_prices() -> dict:
    """
    دریافت قیمت ارزها به تومان:
    - دلار آزاد → Nobitex (usdt-rls) که برابر نرخ آزاد است
    - یورو/پوند → open.er-api.com (رایگان) × نرخ دلار
    - بیت‌کوین/اتریوم → CoinGecko × نرخ دلار
    - کش ۶۰ ثانیه‌ای
    """
    now = time.time()
    if now - _currency_cache["ts"] < _CURRENCY_CACHE_TTL and _currency_cache["data"]:
        return _currency_cache["data"]

    loop = asyncio.get_event_loop()
    result = {}

    # ─── مرحله ۱: نرخ دلار آزاد از Nobitex ──────────────────────────────────
    usd_toman = 0
    for src, pair in [("usdt", "usdt-rls"), ("btc", "btc-rls"), ("eth", "eth-rls")]:
        try:
            nb = await loop.run_in_executor(
                None, lambda s=src: _fetch_json_sync(
                    "https://api.nobitex.ir/market/stats",
                    json_body={"srcCurrency": s, "dstCurrency": "rls"}, timeout=8
                )
            )
            rial = float(nb["stats"][f"{s}-rls"]["latest"])
            val = int(rial / 10)
            result[src] = val
            if src == "usdt":
                usd_toman = val
                result["usd"] = val
        except Exception as e:
            print(f"⚠️ Nobitex {src}: {e}")

    # ─── مرحله ۲: نرخ یورو/پوند از exchangerate-api ─────────────────────────
    if usd_toman:
        try:
            fx = await loop.run_in_executor(
                None, lambda: _fetch_json_sync(
                    "https://open.er-api.com/v6/latest/USD", timeout=8
                )
            )
            rates = fx.get("rates", {})
            if rates.get("EUR"):
                result["eur"] = int(usd_toman * rates["EUR"])
            if rates.get("GBP"):
                result["gbp"] = int(usd_toman * rates["GBP"])
            if rates.get("AED"):
                result["aed"] = int(usd_toman * rates["AED"])
        except Exception as e:
            print(f"⚠️ exchangerate EUR/GBP: {e}")
            result.setdefault("eur", int(usd_toman * 1.08))
            result.setdefault("gbp", int(usd_toman * 1.27))

    # ─── مرحله ۳: BTC/ETH دقیق‌تر از CoinGecko ──────────────────────────────
    if usd_toman and ("btc" not in result or "eth" not in result):
        try:
            cg = await loop.run_in_executor(
                None, lambda: _fetch_json_sync(
                    "https://api.coingecko.com/api/v3/simple/price"
                    "?ids=bitcoin,ethereum&vs_currencies=usd", timeout=10
                )
            )
            btc_usd = cg.get("bitcoin", {}).get("usd", 0)
            eth_usd = cg.get("ethereum", {}).get("usd", 0)
            if btc_usd:
                result["btc"] = int(btc_usd * usd_toman)
            if eth_usd:
                result["eth"] = int(eth_usd * usd_toman)
        except Exception as e:
            print(f"⚠️ CoinGecko: {e}")

    if not result:
        return _currency_cache.get("data") or {}

    _currency_cache["data"] = result
    _currency_cache["ts"] = now
    return result


def _fetch_json_sync(url, json_body=None, timeout=6, retries=3):
    """درخواست HTTP همگام (در executor اجرا می‌شود تا event loop بلاک نشود)"""
    import urllib.request, json as _json, time as _time
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    req = urllib.request.Request(url, headers=headers)
    if json_body is not None:
        req.data = _json.dumps(json_body).encode()
        req.add_header("Content-Type", "application/json")
    last_err = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return _json.loads(resp.read().decode())
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                _time.sleep(2 ** attempt)  # 1s, 2s
    raise last_err


async def _get_currency_text(target: str = None) -> str:
    """
    target=None → نمایش لیست ارزهای مهم (دلار، تتر، یورو، پوند)
    target='usd'/'eur'/'gbp'/'usdt'/'btc' → فقط همان یک ارز
    """
    prices = await _fetch_currency_prices()
    if not prices:
        return "❌ دریافت قیمت ممکن نیست"

    if target:
        if target not in prices:
            return "❌ دریافت قیمت ممکن نیست"
        return f"- {_CURRENCY_LABELS[target]}: {prices[target]:,} تومان"

    lines = [
        f"- {_CURRENCY_LABELS[c]}: {prices[c]:,} تومان"
        for c in _CURRENCY_DEFAULT_LIST if c in prices
    ]
    return "\n".join(lines) if lines else "❌ دریافت قیمت ممکن نیست"


def _help_text():
    # هر دستور در یک بلوک quote + mono جداگانه
    sections = [
        ("🔹 اصلی", [
            "سلف روشن",
            "سلف خاموش",
            "وضعیت",
            "راهنما",
        ]),
        ("🔹 لیست‌ها", [
            "تنظیم دشمن  ← ریپلای روی پیام",
            "حذف دشمن  ← ریپلای یا آیدی",
            "نمایش لیست دشمن",
            "پاک کردن لیست دشمن",
            "تنظیم دوست  ← ریپلای روی پیام",
            "حذف دوست  ← ریپلای یا آیدی",
            "نمایش لیست دوست",
            "پاک کردن لیست دوست",
        ]),
        ("🔹 منشی", [
            "منشی روشن",
            "منشی خاموش",
            "پیام منشی [متن]",
            "💡 هر کاربر هر ۲۴ ساعت یک بار پاسخ می‌گیرد",
        ]),
        ("🔹 امنیت", [
            "ضد حذف روشن",
            "ضد حذف خاموش",
            "ضد لینک روشن",
            "ضد لینک خاموش",
            "قفل پیوی روشن",
            "قفل پیوی خاموش",
            "پاسخ دشمن روشن",
            "پاسخ دشمن خاموش",
            "سکوت [آیدی یا یوزرنیم]  ← ریپلای یا آیدی/یوزرنیم",
            "لغو سکوت [آیدی یا یوزرنیم]",
            "لیست سکوت",
            "💡 پیام‌های پیوی کاربر سکوت‌شده به‌صورت خودکار و دوطرفه پاک می‌شود",
        ]),
        ("🔹 جوین اجباری", [
            "افزودن کانال [آیدی یا @یوزرنیم] [لینک]  ← افزودن کانال به لیست",
            "حذف کانال [آیدی یا @یوزرنیم]  ← حذف یک کانال از لیست",
            "لیست کانال‌های اجباری  ← نمایش همه کانال‌ها",
            "پاک کردن کانال‌های اجباری  ← حذف همه کانال‌ها",
            "پیام جوین [متن]  ← تغییر متن پیام هشدار",
            "جوین اجباری روشن / خاموش",
            "💡 می‌تونی چند کانال اضافه کنی؛ لیست توی دیتابیس دائمی ذخیره می‌شه",
            "💡 پیام عضو‌نشده حذف + هشدار با دکمه‌های رنگی همه‌ی کانال‌ها میفرسته",
        ]),
        ("🔹 اتوماسیون", [
            "سین خودکار روشن",
            "سین خودکار خاموش",
            "ری‌اکشن روشن",
            "ری‌اکشن خاموش",
            "ری‌اکشن [ایموجی]  ← تغییر ایموجی",
            "ذخیره مدیا روشن",
            "ذخیره مدیا خاموش",
            "ساعت نام روشن",
            "ساعت نام خاموش",
            "ساعت بیو روشن",
            "ساعت بیو خاموش",
        ]),
        ("🔹 ابزار", [
            "ترجمه [متن]",
            ".هوا [شهر]",
            "ارز  ← دلار، تتر، یورو، پوند",
            "ارز دلار / ارز تتر / ارز یورو / ارز پوند / ارز بیت کوین",
        ]),
        ("🔹 اسپم", [
            "اسپم [تعداد] [متن]  ← مثال: اسپم 100 سلام",
            "توقف اسپم",
            "💡 تعداد نامحدود — فرمت باید دقیق باشه",
        ]),
        ("🔹 پیام", [
            "ذخیره [1-10]  ← ریپلای",
            "ارسال ذخیره [1-10]",
            "حذف بعد [ثانیه]",
            "ارسال زمان‌بندی [YYYY-MM-DD HH:MM] متن",
        ]),
        ("🔹 سیو مدیا", [
            "سیو کانال [لینک پست]  ← ذخیره یک پست (عمومی یا خصوصی، مثل t.me/c/.../...)",
            "سیو کانال [@کانال] [تعداد]  ← ذخیره چند پست",
            "توقف سیو",
        ]),
        ("🔹 قالب‌بندی (فارسی/انگلیسی)", [
            "بولد [متن]  ← متن ضخیم",
            "ایتالیک [متن]  ← متن کج",
            "مونو [متن]  ← متن کد",
            "اسپویلر [متن]  ← متن مخفی",
            "کوت [متن]  ← نقل قول",
            "خط‌خورده [متن]  ← متن خط‌خورده",
            "زیرخط [متن]  ← متن زیرخط",
            "💡 روی متن فارسی هم کار می‌کند",
        ]),
        ("🔹 فونت", [
            ".فونت [0-8]  ← انتخاب فونت",
            ".فونت [متن] [0-8]  ← نوشتن یه کلمه با فونت",
            ".لیست فونت  ← نمایش همه فونت‌ها",
            "──────────────────",
            ".فونت متن روشن  ← هر پیامی که بنویسی ادیت می‌شه",
            ".فونت متن خاموش  ← خاموش کردن حالت خودکار",
            "──────────────────",
            ".فونت ساعت [0-9]  ← فونت ساعت نام/بیو",
            ".لیست فونت ساعت  ← نمایش فونت‌های ساعت",
        ]),
        ("🎲 تاس", [
            ".تاس [1-6]  ← ارسال تاس با عدد دلخواه 🎲",
            ".roll [1-6]  ← همان دستور به انگلیسی",
        ]),
        ("💡 نکات", [
            "در گروه‌ها فقط وقتی تگ شوید پاسخ می‌دهد",
            "پاسخ به دوستان هر ۱ ساعت یک بار",
        ]),
    ]
    parts = ["📖 **راهنمای NexoSelf**\n"]
    for title, cmds in sections:
        parts.append(f"\n{title}")
        for cmd in cmds:
            parts.append(f"> `{cmd}`")
    return "\n".join(parts)


# ─── پنل دکمه‌ای مدیریت سلف — دسته‌بندی‌شده (برای بات کمکی / helper_bot.py) ─────
# هر دسته یک "title" داره، یک لیست "toggles" (سوییچ‌های روشن/خاموش، رنگشون از
# طریق style واقعی دکمه مشخص می‌شه نه ایموجی)، یک لیست "actions" (دکمه‌های
# ساده‌ی اجرایی یا فقط اطلاع‌رسانی) و به‌صورت اختیاری:
#   "children": [(برچسب دکمه, کلید زیرمنو), ...] → دکمه‌هایی که به یک دسته‌ی
#                دیگه (زیرمنو) می‌رن، مثلاً «فونت ساعت» یا «دوست»/«دشمن».
#   "parent": کلید دسته‌ی والد → دکمه‌ی «بازگشت» این دسته به‌جای منوی اصلی،
#             به همون دسته‌ی والد برمی‌گرده.
# actions: (برچسب دکمه، متن دستور، ...) — اگه متن دستور با "INFO::" شروع بشه،
# یعنی این دکمه فقط یک پیام کوتاه (toast) نشون می‌ده و هیچ دستوری روی سلف
# اجرا نمی‌شه (برای مواردی مثل ماشین‌حساب/ترجمه که نیاز به ورودی متنی دارن).
PANEL_CATEGORIES = {
    # ─── سطح ۱: منوی اصلی (چیدمانِ شبکه‌ای، مطابق طرح درخواستی) ───────────────
    "text_mode": {
        "title": "حالت متن",
        "menu_style": "primary",
        "toggles": [
            ("text_style_quote_active", "نقل قول", "حالت نقل قول روشن", "حالت نقل قول خاموش"),
            ("text_style_underline_active", "زیر خط", "حالت زیرخط روشن", "حالت زیرخط خاموش"),
            ("text_style_spoiler_active", "اسپویلر", "حالت اسپویلر روشن", "حالت اسپویلر خاموش"),
            ("text_style_gradual_active", "تدریجی", "حالت تدریجی روشن", "حالت تدریجی خاموش"),
            ("text_style_bold_active", "بولد", "حالت بولد روشن", "حالت بولد خاموش"),
            ("text_style_italic_active", "ایتالیک", "حالت ایتالیک روشن", "حالت ایتالیک خاموش"),
            ("text_style_strike_active", "خط خورده", "حالت خط‌خورده روشن", "حالت خط‌خورده خاموش"),
            ("text_style_single_space_active", "تک فاصله", "حالت تک‌فاصله روشن", "حالت تک‌فاصله خاموش"),
            ("text_style_finglish_active", "فینگلیش", "حالت فینگلیش روشن", "حالت فینگلیش خاموش"),
        ],
        "actions": [],
    },
    "clock": {
        "title": "ساعت",
        "menu_style": "primary",
        "toggles": [
            ("clock_name_active", "ساعت نام", "ساعت نام روشن", "ساعت نام خاموش"),
            ("clock_bio_active", "ساعت بیو", "ساعت بیو روشن", "ساعت بیو خاموش"),
            ("clock_premium_active", "ساعت پرمیوم", "ساعت پرمیوم روشن", "ساعت پرمیوم خاموش"),
        ],
        "actions": [],
        "children": [("فونت ساعت", "clock_font")],
    },
    "chat_guard": {
        "title": "نگهبان چت",
        "menu_style": "primary",
        "toggles": [
            ("guard_delete_active", "ذخیره پیام حذف‌شده", "ذخیره پیام حذف‌شده روشن", "ذخیره پیام حذف‌شده خاموش"),
            ("guard_edit_active", "ذخیره پیام ویرایش‌شده", "ذخیره پیام ویرایش‌شده روشن", "ذخیره پیام ویرایش‌شده خاموش"),
            ("guard_view_once_active", "ذخیره عکس تایمی", "ذخیره عکس تایمی روشن", "ذخیره عکس تایمی خاموش"),
        ],
        "actions": [],
    },
    "ping": {
        "title": "پینگ",
        "menu_style": "primary",
        "direct_command": "پینگ",
    },
    "logo": {
        "title": "لوگو",
        "menu_style": "primary",
        "direct_command": "لوگو",
    },
    "locks": {
        "title": "قفل ها",
        "menu_style": "primary",
        "toggles": [
            ("lock_username_active", "قفل یوزرنیم", "قفل یوزرنیم روشن", "قفل یوزرنیم خاموش"),
            ("lock_reply_active", "قفل ریپلای", "قفل ریپلای روشن", "قفل ریپلای خاموش"),
            ("lock_gif_active", "قفل گیف", "قفل گیف روشن", "قفل گیف خاموش"),
            ("private_lock_active", "قفل پیوی", "قفل پیوی روشن", "قفل پیوی خاموش"),
            ("anti_link_active", "قفل لینک", "ضد لینک روشن", "ضد لینک خاموش"),
            ("lock_photo_active", "قفل عکس", "قفل عکس روشن", "قفل عکس خاموش"),
            ("lock_sticker_active", "قفل استیکر", "قفل استیکر روشن", "قفل استیکر خاموش"),
            ("lock_forward_active", "قفل فوروارد", "قفل فوروارد روشن", "قفل فوروارد خاموش"),
            ("anti_delete_active", "قفل ضد حذف", "ضد حذف روشن", "ضد حذف خاموش"),
            ("login_lock_active", "قفل لاگین", "قفل لاگین روشن", "قفل لاگین خاموش"),
        ],
        "actions": [],
    },
    "actions": {
        "title": "اکشن",
        "menu_style": "primary",
        "toggles": [
            ("typing_action_active", "اکشن تایپینگ 24 ساعته", "تایپینگ روشن", "تایپینگ خاموش"),
            ("gaming_action_active", "اکشن گیمینگ 24 ساعته", "گیمینگ روشن", "گیمینگ خاموش"),
            ("voice_action_active", "اکشن ویس 24 ساعته", "ویس روشن", "ویس خاموش"),
            ("video_action_active", "اکشن ارسال ویدیو 24 ساعته", "ارسال ویدیو روشن", "ارسال ویدیو خاموش"),
        ],
        "actions": [],
    },
    "friend_enemy": {
        "title": "دوست و دشمن",
        "menu_style": "success",
        "toggles": [],
        "actions": [],
        "children": [("دوست", "friend_enemy_friend"), ("دشمن", "friend_enemy_enemy")],
    },
    "secretary": {
        "title": "منشی",
        "menu_style": "success",
        "toggles": [
            ("secretary_active", "منشی", "منشی روشن", "منشی خاموش"),
        ],
        "actions": [
            ("نمایش متن دستورات منشی", "INFO::دستورات منشی:\nمنشی روشن / منشی خاموش\nپیام منشی [متن دلخواه]"),
        ],
    },
    "word_filter": {
        "title": "فیلترکلمات",
        "menu_style": "success",
        "toggles": [
            ("word_filter_active", "فیلتر کلمات", "فیلترکلمات روشن", "فیلترکلمات خاموش"),
        ],
        "actions": [
            ("افزودن کلمه", "INFO::برای افزودن تایپ کن: فیلتر کلمه [کلمه]"),
            ("حذف کلمه", "INFO::برای حذف تایپ کن: حذف فیلتر کلمه [کلمه]"),
            ("لیست فیلتر کلمات", "لیست فیلتر کلمات"),
        ],
    },
    "auto_reply": {
        "title": "پاسخ خودکار",
        "menu_style": "success",
        "toggles": [
            ("auto_reply_active", "پاسخ خودکار", "پاسخ خودکار روشن", "پاسخ خودکار خاموش"),
        ],
        "actions": [
            ("تنظیم متن پاسخ", "INFO::برای تنظیم متن تایپ کن: متن پاسخ خودکار [متن دلخواه]"),
            ("افزودن پاسخ کلیدی", "INFO::برای ثبت تایپ کن: پاسخ کلیدی [کلمه] = [پاسخ]\nمثال: پاسخ کلیدی قیمت = قیمت‌ها توی کانال هست."),
            ("حذف پاسخ کلیدی", "INFO::برای حذف تایپ کن: حذف پاسخ کلیدی [کلمه]"),
            ("لیست پاسخ کلیدی", "لیست پاسخ کلیدی"),
            ("پاک کردن همه‌ی پاسخ کلیدی", "پاک کردن پاسخ کلیدی"),
        ],
    },
    "forced_join": {
        "title": "عضویت اجباری پیوی",
        "menu_style": "success",
        "toggles": [
            ("force_join_active", "عضویت اجباری", "جوین اجباری روشن", "جوین اجباری خاموش"),
        ],
        "actions": [
            ("نمایش متن دستورات", "INFO::دستورات عضویت اجباری:\nافزودن کانال [آیدی/یوزرنیم] [لینک]\nحذف کانال [آیدی/یوزرنیم]\nلیست کانال‌های اجباری\nپاک کردن کانال‌های اجباری\nپیام جوین [متن]\nجوین اجباری روشن / جوین اجباری خاموش"),
            ("لیست کانال‌های اجباری", "لیست کانال‌های اجباری"),
            ("پاک کردن کانال‌های اجباری", "پاک کردن کانال‌های اجباری"),
        ],
    },
    "downloader": {
        "title": "دانلودر",
        "menu_style": "primary",
        "direct_command": "INFO::روی یک عکس/ویدیو/فایل ریپلای کن و تایپ کن: تبدیل به گیف (برای ویدیو) یا مستقیم فوروارد کن به پیام‌های ذخیره‌شده",
    },
    "user_react": {
        "title": "ریکت",
        "menu_style": "primary",
        "direct_command": "INFO::روی پیام کاربر ریپلای کن و تایپ کن: تنظیم ری‌اکت [ایموجی] — برای حذف: حذف ری‌اکت",
    },
    "spam": {
        "title": "اسپم",
        "menu_style": "primary",
        "direct_command": "INFO::برای شروع تایپ کن: اسپم [تعداد] [متن] — برای توقف: توقف اسپم",
    },
    "pm_silence": {
        "title": "سکوت",
        "menu_style": "primary",
        "direct_command": "INFO::روی پیام کاربر ریپلای کن و تایپ کن: سکوت — برای لغو: لغو سکوت — برای دیدن لیست: لیست سکوت",
    },
    "user_info": {
        "title": "اطلاعات",
        "menu_style": "primary",
        "direct_command": "وضعیت",
    },
    "tag_all": {
        "title": "تگ",
        "menu_style": "primary",
        "direct_command": "INFO::این دستور فقط توی گروه کار می‌کند. تایپ کن: تگ [متن دلخواه]",
    },
    "block_user": {
        "title": "بلاک",
        "menu_style": "primary",
        "direct_command": "INFO::روی پیام کاربر ریپلای کن و تایپ کن: بلاک کاربر — لیست: لیست بلاک",
    },
    "delete_msg": {
        "title": "حذف",
        "menu_style": "primary",
        "direct_command": "INFO::روی پیامی که می‌خوای حذف شه ریپلای کن و تایپ کن: حذف",
    },
    "ai_assistant": {
        "title": "هوش مصنوعی",
        "menu_style": "success",
        "toggles": [
            ("ai_assistant_active", "دیپ سیک", "دیپ سیک روشن", "دیپ سیک خاموش"),
            ("ai_reply_always_active", "پاسخ به همه پیام‌ها", "هوش مصنوعی پاسخ همه روشن", "هوش مصنوعی پاسخ همه خاموش"),
        ],
        "actions": [
            ("افزودن اطلاعات", "INFO::برای اضافه‌کردن اطلاعات تایپ کن: آموزش هوش مصنوعی [متن] — مثال: آموزش هوش مصنوعی قیمت گوشی X ۱۰ میلیون تومان است"),
            ("نمایش دانش هوش مصنوعی", "نمایش دانش هوش مصنوعی"),
            ("پاک کردن دانش هوش مصنوعی", "پاک کردن دانش هوش مصنوعی"),
        ],
    },
    "translate_tool": {
        "title": "ترجمه",
        "menu_style": "primary",
        "direct_command": "INFO::برای استفاده تایپ کن: ترجمه [متن] — یا روی پیام ریپلای کن و بنویس: ترجمه متن",
    },
    "animation": {
        "title": "انیمیشن",
        "menu_style": "primary",
        "direct_command": "INFO::این بخش هنوز آماده نیست.",
    },
    "cheat": {
        "title": "تقلب",
        "menu_style": "success",
        "direct_command": (
            "INFO::تقلب تاس/دارت/فوتبال/بسکتبال/کازینو — همیشه بهترین نتیجه می‌گیری:\n"
            ".تاس یا .تاس 6\n"
            ".دارت یا .دارت 6\n"
            ".فوتبال یا .فوتبال 5\n"
            ".بسکتبال یا .بسکتبال 5\n"
            ".کازینو انگور  (یا: .کازینو لیمو/هفت)"
        ),
    },
    "calculator": {
        "title": "× ÷",
        "menu_style": "primary",
        "direct_command": "INFO::برای استفاده تایپ کن: محاسبه [عبارت] — مثال: محاسبه 2+2*3",
    },
    "text_to_voice": {
        "title": "تبدیل متن به ویس",
        "menu_style": "primary",
        "direct_command": "INFO::این بخش هنوز آماده نیست.",
    },
    "voice_search": {
        "title": "سرچ ویس آماده",
        "menu_style": "primary",
        "direct_command": "INFO::این بخش هنوز آماده نیست.",
    },
    "music_search": {
        "title": "سرچ آهنگ",
        "menu_style": "primary",
        "direct_command": "INFO::این بخش هنوز آماده نیست.",
    },
    "tabchi": {
        "title": "تبچی",
        "menu_style": "primary",
        "toggles": [
            ("tabchi_active", "تبچی", "تبچی روشن", "تبچی خاموش"),
        ],
        "actions": [
            ("راهنمای دستورات تبچی", "INFO::"
             "ثبت بنر با ریپلای  (یا: تنظیم بنر N با ریپلای)\n"
             "تنظیم بنر N در این چت\n"
             "تنظیم بنر N در همه گروه‌ها\n"
             "فعال کردن بنر N  /  غیرفعال کردن بنر N\n"
             "لیست بنرها\n"
             "پاکسازی لیست بنر  /  پاکسازی بنر در این چت\n"
             "حذف بنر N  /  حذف بنر N در این چت  /  حذف بنر N از همه گروه‌ها\n"
             "حذف بنرها از همه گروه‌ها\n"
             "فور بنر N در 50 گروه اخیر  /  فور بنر N در 100 گروه اخیر\n"
             "تایم بنرها N   (فاصله‌ی ارسال خودکار به دقیقه)"
             ),
            ("لیست بنرها", "لیست بنرها"),
            ("پاکسازی لیست بنر", "پاکسازی لیست بنر"),
        ],
    },
    "profile_snoop": {
        "title": "فضول پروفایل",
        "menu_style": "primary",
        "direct_command": "INFO::این بخش هنوز آماده نیست.",
    },
    "first_comment": {
        "title": "کامنت اول",
        "menu_style": "danger",
        "direct_command": "INFO::این بخش هنوز آماده نیست.",
    },
    "currency": {
        "title": "قیمت ارز",
        "menu_style": "danger",
        "direct_command": "ارز",
    },
    "screen_guard": {
        "title": "اسکرین",
        "menu_style": "danger",
        "direct_command": "INFO::دستورات اسکرین:\nروی یه پیام ریپلای بزن و بنویس: اسکرین\nپیام به‌صورت استیکر (همراه با پروفایل فرستنده) ساخته می‌شه.\n\nبرای پست‌های کانال:\nاسکرین [لینک پیام]\nمثال: اسکرین https://t.me/channel/123\nیا (کانال خصوصی): اسکرین https://t.me/c/123456789/123",
    },
    "tools": {
        "title": "ابزار بیشتر",
        "menu_style": "primary",
        "toggles": [
            ("auto_seen_active", "سین خودکار", "سین خودکار روشن", "سین خودکار خاموش"),
            ("auto_reaction_active", "ری‌اکشن خودکار", "ری‌اکشن روشن", "ری‌اکشن خاموش"),
            ("auto_save_media", "ذخیره مدیا", "ذخیره مدیا روشن", "ذخیره مدیا خاموش"),
        ],
        "actions": [
            ("آب و هوا", "INFO::برای استفاده تایپ کن: هوا [نام شهر]"),
            ("راهنما", "راهنما"),
            ("پاکسازی لیست بلاک", "پاکسازی لیست بلاک"),
            ("ترک همگانی گروه", "ترک همگانی گروه"),
            ("ترک همگانی کانال", "ترک همگانی کانال"),
            ("تبدیل به گیف", "INFO::روی یک ویدیو ریپلای کن و تایپ کن: تبدیل به گیف"),
            ("توقف سیو کانال", "توقف سیو"),
            ("اطلاعات کاربر (ایدی)", "INFO::توی یه چت یا گروه تایپ کن: ایدی\nاگه روی پیام یه کاربر ریپلای کنی و «ایدی» رو بفرستی، اطلاعات همون کاربر رو نشون می‌ده."),
            ("دانلود پست تلگرام", "INFO::برای استفاده تایپ کن: دانلود [لینک پست]\nمثال: دانلود https://t.me/channel/123\nبرای کانال خصوصی: دانلود https://t.me/c/123456789/123"),
            ("دانلود اینستاگرام", "INFO::برای استفاده تایپ کن: اینستا [لینک پست یا ریل]\nمثال: اینستا https://www.instagram.com/reel/xxxxx/"),
        ],
    },
    "premium_emoji": {
        "title": "ایموجی پرمیوم",
        "menu_style": "danger",
        "toggles": [],
        "actions": [],
        "stub_message": "این بخش هنوز در دسترس نیست",
    },

    "meowie_game": meowie_game.PANEL_CATEGORY,

    # ─── زیرمنوها (توی منوی اصلی نشون داده نمی‌شن، فقط از طریق children) ────
    "clock_font": {
        "title": "فونت ساعت",
        "toggles": [],
        "actions": [(f"فونت {k}", f"فونت ساعت {k}") for k in "0123456789"],
        "parent": "clock",
    },
    "friend_enemy_friend": {
        "title": "دوست",
        "toggles": [],
        "actions": [
            ("نمایش لیست دوست", "نمایش لیست دوست"),
            ("پاک کردن لیست دوست", "پاک کردن لیست دوست"),
        ],
        "parent": "friend_enemy",
    },
    "friend_enemy_enemy": {
        "title": "دشمن",
        "toggles": [
            ("enemy_reply_active", "پاسخ دشمن", "پاسخ دشمن روشن", "پاسخ دشمن خاموش"),
        ],
        "actions": [
            ("نمایش لیست دشمن", "نمایش لیست دشمن"),
            ("پاک کردن لیست دشمن", "پاک کردن لیست دشمن"),
        ],
        "parent": "friend_enemy",
    },
    "meowie_settings": meowie_game.SETTINGS_PANEL_CATEGORY,
}

# ترتیب نمایش دسته‌ها در منوی اصلی پنل (فقط سطح ۱، زیرمنوها اینجا نیستن)
# این ترتیب دقیقاً مطابق چیدمانِ درخواستی (شبکه‌ای، رنگی) هست.
PANEL_CATEGORY_ORDER = [
    "text_mode", "clock", "chat_guard",
    "ping", "logo", "locks",
    "actions", "friend_enemy", "secretary",
    "word_filter", "auto_reply", "forced_join",
    "downloader", "user_react", "spam",
    "pm_silence", "user_info", "tag_all",
    "block_user", "delete_msg", "tools",
    "ai_assistant", "translate_tool", "animation",
    "cheat", "calculator", "text_to_voice",
    "voice_search", "music_search", "tabchi",
    "profile_snoop", "first_comment", "currency",
    "screen_guard", "premium_emoji", "meowie_game",
]




def build_category_commands(owner_id: int, category_key: str):
    """
    برای یک دسته‌ی مشخص، آیتم‌های toggle (بر اساس وضعیت لحظه‌ای owner)
    و آیتم‌های action رو با هم به‌صورت یک لیست واحد
    (key, label, command_text, style) برمی‌گردونه - دقیقاً فرمتی که
    get_all_commands_buttons نیاز داره. style رنگ واقعیِ دکمه رو مشخص
    می‌کنه (success/danger/primary)، بدون هیچ ایموجی‌ای توی متن دکمه.
    """
    cat = PANEL_CATEGORIES.get(category_key)
    if not cat:
        return []

    items = []
    for key, label, on_cmd, off_cmd in cat["toggles"]:
        is_on = db.get_setting(owner_id, key) == "1"
        if is_on:
            items.append((key, f"{label}: روشن", off_cmd, "success"))
        else:
            items.append((key, f"{label}: خاموش", on_cmd, "danger"))

    for label, cmd in cat["actions"]:
        items.append((label, label, cmd, "primary"))

    return items


def build_category_menu():
    """لیست دکمه‌های منوی اصلی پنل: (کلید، عنوان، رنگ)."""
    return [
        (key, PANEL_CATEGORIES[key]["title"], PANEL_CATEGORIES[key].get("menu_style", "primary"))
        for key in PANEL_CATEGORY_ORDER
    ]




class _FakePanelEvent:
    """
    یک شبیه‌ساز سبک از رویداد پیام Telethon تا بشه دستورات متنی موجود در
    _handle_command رو از طریق کلیک روی دکمه‌ی پنل (به‌جای تایپ واقعی توسط
    کاربر) اجرا کرد. به‌جای ادیت یک پیام واقعی، نتیجه رو به «پیام‌های ذخیره‌شده»
    (Saved Messages) خود کاربر می‌فرسته تا لاگ اجرای دستور رو داشته باشه.
    """

    def __init__(self, client):
        self._client = client
        self.message = None

    async def edit(self, text, **kwargs):
        try:
            await self._client.send_message("me", text)
        except Exception:
            pass

    async def get_reply_message(self):
        return None


async def _execute_panel_command(cl, owner_id: int, command_text: str):
    """دستور متنیِ متناظر با دکمه‌ی کلیک‌شده در پنل رو روی کلاینتِ سلفِ کاربر اجرا می‌کنه."""
    entry = bot_manager._bots.get(owner_id) or {}
    fake_event = _FakePanelEvent(cl)
    try:
        await _handle_command(cl, fake_event, command_text, owner_id, entry, had_dot=True)
    except Exception as e:
        print(f"❌ خطا در اجرای دستور پنل ({command_text}): {e}")


# ─── حلقه‌های پس‌زمینه ──────────────────────────────────────────────────────────
async def _clock_loop(cl, owner_id):
    """به‌روزرسانی ساعت نام/بیو با دقت بالا - بدون تاخیر"""
    last_minute = -1
    
    while True:
        try:
            # ✅ زمان ایران
            iran_tz = datetime.timezone(datetime.timedelta(hours=3, minutes=30))
            now = datetime.datetime.now(iran_tz)
            current_minute = now.minute
            
            # ✅ فقط در دقیقه‌های جدید به‌روزرسانی کن
            if current_minute != last_minute:
                last_minute = current_minute
                time_str = f"{now.hour:02d}:{now.minute:02d}"
                
                # اعمال فونت مخصوص ساعت
                styled_time = _apply_clock_font(owner_id, time_str)

                # ساعت پرمیوم: یک ایموجی ساعتِ آنالوگ (مطابق ساعت لحظه‌ای) جلوی زمان
                if db.get_setting(owner_id, "clock_premium_active") == "1":
                    clock_face = _CLOCK_FACE_EMOJIS[now.hour % 12]
                    styled_time = f"{clock_face} {styled_time}"
                
                # به‌روزرسانی نام
                if db.get_setting(owner_id, "clock_name_active") == "1":
                    try:
                        await cl(UpdateProfileRequest(last_name=styled_time[:64]))
                        print(f"⏰ [{owner_id}] ساعت نام به‌روز شد: {styled_time}")
                    except Exception as e:
                        print(f"❌ خطا در به‌روزرسانی نام: {e}")
                
                # به‌روزرسانی بیو
                if db.get_setting(owner_id, "clock_bio_active") == "1":
                    try:
                        await cl(UpdateProfileRequest(about=f"⏰ {styled_time}"[:70]))
                        print(f"⏰ [{owner_id}] ساعت بیو به‌روز شد: {styled_time}")
                    except Exception as e:
                        print(f"❌ خطا در به‌روزرسانی بیو: {e}")
            
            # ✅ چک کردن هر 5 ثانیه برای دقت بالا
            await asyncio.sleep(5)
            
        except Exception as e:
            print(f"❌ خطا در _clock_loop: {e}")
            await asyncio.sleep(10)


async def _typing_loop(cl, owner_id):
    """
    اکشن‌های ۲۴ ساعته: تا وقتی هرکدوم روشن باشن، به‌صورت مداوم وضعیت مربوطه
    («در حال تایپ»، «در حال بازی»، «در حال ضبط ویس»، «در حال ارسال ویدیو»)
    رو در پیوی‌های اخیر کاربر نشون می‌ده. هر کدوم مستقل از بقیه روشن/خاموش می‌شن.
    """
    ACTIONS = [
        ("typing_action_active", "typing"),
        ("gaming_action_active", "game"),
        ("voice_action_active", "record-audio"),
        ("video_action_active", "video"),
    ]
    while True:
        try:
            active_actions = [action for key, action in ACTIONS if db.get_setting(owner_id, key) == "1"]
            if active_actions:
                try:
                    async for dialog in cl.iter_dialogs(limit=30):
                        if not dialog.is_user:
                            continue
                        for key, action in ACTIONS:
                            if db.get_setting(owner_id, key) != "1":
                                continue
                            try:
                                await cl.send_chat_action(dialog.entity, action)
                            except Exception as e:
                                print(f"⚠️ خطا در ارسال اکشن ({action}) به {dialog.id}: {e}")
                            await asyncio.sleep(1)
                except Exception as e:
                    print(f"خطا در اکشن‌های ۲۴ ساعته: {e}")
                await asyncio.sleep(3)
            else:
                await asyncio.sleep(5)
        except Exception as e:
            print(f"خطا در _typing_loop: {e}")
            await asyncio.sleep(10)


async def _scheduler_loop(cl, owner_id):
    while True:
        try:
            for p in db.get_pending_scheduled(owner_id):
                try:
                    await cl.send_message(p["chat_id"], p["message"])
                    db.mark_scheduled_sent(p["id"])
                except Exception:
                    pass
        except Exception:
            pass
        await asyncio.sleep(30)

