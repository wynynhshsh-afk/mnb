"""
╔══════════════════════════════════════════════════╗
║      ربات فورواردر چندمسیره (نامحدود) 🤖         ║
║  python-telegram-bot 22.7+  |  Python 3.14+      ║
║  حالت اجرا: Webhook (مخصوص Render Web Service)   ║
║  پشتیبانی از حداکثر ۶ ربات هم‌زمان، یک دیتابیس مشترک ║
╚══════════════════════════════════════════════════╝

متغیرهای محیطی اجباری (در Render تنظیم کنید):
  BOT_TOKEN_1   ← توکن ربات #۱ (همان که پنل مدیریت کامل را دارد) — الزامی
  BOT_TOKEN_2   ← توکن ربات #۲ (اختیاری — فقط فوروارد می‌کند، بدون پنل)
  BOT_TOKEN_3   ← ...  تا BOT_TOKEN_6 (اختیاری، هرکدام تنظیم شود فعال می‌شود)
  ADMINS        ← آیدی عددی مالکان اصلی ربات (با کاما جدا کنید اگر چند نفرند)
  WEBHOOK_URL   ← آدرس سرویس Render شما (مثل https://my-bot.onrender.com)
  SUPABASE_URL  ← از Project Settings → General → Project URL
  SUPABASE_KEY  ← از Project Settings → API Keys → Secret keys
                  (کلیدی که با sb_secret_ شروع می‌شود، نه publishable/anon)

نکته مهم: این نسخه روی حالت webhook کار می‌کند، پس باید به‌عنوان «Web Service»
روی Render دیپلوی بشه (نه Background Worker). همه‌ی رباتها روی همین یک پورت
مشترک (که Render از طریق PORT تزریق می‌کند) جواب می‌دهند؛ هرکدام مسیر
وبهوکِ مخصوص به خودش را دارد (بر پایه‌ی توکنش، پس حدس‌زدنی نیست).

⚠️ اگر از پلن رایگان (Free) رندر استفاده می‌کنی، این سرویس بعد از چند دقیقه
بی‌فعالیتی HTTP می‌خوابد و وقتی پیام جدیدی برسد، تلگرام باید دوباره تلاش کند
تا سرویس بیدار شود — یعنی همان تاخیرِ چند دقیقه‌ای که قبلاً باهاش مواجه بودی
می‌تواند دوباره برگردد. برای رفعش یا پلن رو آپگرید کن، یا یه سرویس ping (مثل
UptimeRobot) رو هر چند دقیقه به آدرس سرویس بزن تا نخوابه.

نکته‌ی دیگر: تا ۶ ربات مختلف می‌توانند هم‌زمان اجرا شوند و همه به همین یک
دیتابیس وصل می‌شوند. هر مسیر فوروارد مشخص می‌کند کدام ربات (۱ تا ۶) مسئول
آن است — پس همان ربات باید از قبل در چت منبع و مقصدِ آن مسیر عضو/ادمین شده
باشد. فقط ربات #۱ منوی مدیریت (افزودن مسیر، لیست مسیرها، ادمین‌ها) را دارد؛
بقیه فقط بی‌صدا فوروارد می‌کنند. برای اضافه‌کردن ستون لازم به دیتابیس، فایل
supabase_schema.sql را ببینید (ستون bot_slot).

نکته: افرادی که در ADMINS قرار می‌گیرند «مالک» ربات هستند و همیشه دسترسی کامل
دارند. مالک‌ها می‌توانند از داخل ربات ادمین‌های دیگر اضافه کنند و برای هرکدام
دسترسی دلخواه (مثلاً فقط فوروارد گروه‌به‌گروه) تعیین کنند.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from dataclasses import dataclass, field

from aiohttp import web
from postgrest.exceptions import APIError
from supabase import create_client, Client

from telegram import (
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.error import RetryAfter, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ════════════════════════════════════════════════
#  لاگ
# ════════════════════════════════════════════════

logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

# ════════════════════════════════════════════════
#  تنظیمات  ← از متغیرهای محیطی خوانده می‌شود
# ════════════════════════════════════════════════

BOT_TOKEN: str = os.environ.get("BOT_TOKEN", "").strip()  # سازگاری با نسخه‌ی قبلی (ربات #۱)

# متغیرهای BOT_TOKEN_1 تا BOT_TOKEN_6 ← تا ۶ ربات مختلف که همه به یک دیتابیس
# وصل می‌شوند. فقط ربات #۱ پنل مدیریت را دارد؛ بقیه فقط فوروارد می‌کنند.
_bot_tokens: list[str] = []
for _i in range(1, 7):
    _tok = os.environ.get(f"BOT_TOKEN_{_i}", "").strip()
    if _tok:
        _bot_tokens.append(_tok)
if not _bot_tokens and BOT_TOKEN:
    _bot_tokens = [BOT_TOKEN]
BOT_TOKENS: list[str] = _bot_tokens
BOT_TOKEN = BOT_TOKENS[0] if BOT_TOKENS else ""  # ربات #۱ (همان که پنل مدیریت را دارد)

_owners_raw = os.environ.get("ADMINS", "").strip()
OWNERS: list[int] = [
    int(part) for part in _owners_raw.split(",") if part.strip().lstrip("-").isdigit()
]

WEBHOOK_URL: str = os.environ.get("WEBHOOK_URL", "").strip().rstrip("/")

# Render پورت واقعی را از طریق متغیر PORT تزریق می‌کند.
PORT: int = int(os.environ.get("PORT", "8443"))

SUPABASE_URL: str = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY: str = os.environ.get("SUPABASE_KEY", "").strip()

if not BOT_TOKENS:
    log.critical("هیچ‌کدام از متغیرهای BOT_TOKEN_1..BOT_TOKEN_6 (یا BOT_TOKEN) تنظیم نشده‌اند.")
    sys.exit(1)
if not OWNERS:
    log.critical("متغیر محیطی ADMINS تنظیم نشده یا نامعتبر است.")
    sys.exit(1)
if not WEBHOOK_URL:
    log.critical("متغیر محیطی WEBHOOK_URL تنظیم نشده است.")
    sys.exit(1)
if not SUPABASE_URL or not SUPABASE_KEY:
    log.critical(
        "متغیرهای محیطی SUPABASE_URL و/یا SUPABASE_KEY تنظیم نشده‌اند. "
        "SUPABASE_URL را از Project Settings → General → Project URL بردارید، "
        "و SUPABASE_KEY را از Project Settings → API Keys → بخش Secret keys "
        "(کلیدی که با sb_secret_ شروع می‌شود، نه publishable) بردارید."
    )
    sys.exit(1)

# ════════════════════════════════════════════════
#  حالت‌های مکالمه
# ════════════════════════════════════════════════

(
    ST_MAIN,        # منوی اصلی / همه‌ی ناوبری با دکمه (callback)
    ST_ADD_SRC,     # انتظار یوزرنیم منبع مسیر جدید
    ST_ADD_TGT,     # انتظار یوزرنیم مقصد مسیر جدید
    ST_ADD_BOT,     # انتظار انتخاب اینکه کدام ربات (۱ تا ۶) مسئول این مسیر باشد
    ST_ADMIN_ADD,   # انتظار آیدی عددی ادمین جدید
) = range(5)

# ════════════════════════════════════════════════
#  دسترسی‌ها (Permissions)
# ════════════════════════════════════════════════

# هر دسترسی مربوط به یک نوعِ مسیر فوروارد است، به‌علاوه‌ی مدیریت ادمین‌ها.
ROUTE_PERMS: list[str] = ["gtc", "ctg", "gtg", "ctc"]
ALL_PERMS: list[str] = ROUTE_PERMS + ["manage_admins"]

PERM_LABELS: dict[str, str] = {
    "gtc": "📤 فوروارد گروه → چنل",
    "ctg": "📥 فوروارد چنل → گروه",
    "gtg": "🔁 فوروارد گروه → گروه",
    "ctc": "🔁 فوروارد چنل → چنل",
    "manage_admins": "👥 مدیریت ادمین‌ها",
}

KIND_LABELS = {"group": "گروه/سوپرگروه", "channel": "چنل"}

# ترکیب (نوعِ منبع، نوعِ مقصد) → کلید جهت
DIRECTIONS: dict[tuple[str, str], str] = {
    ("group", "channel"): "gtc",
    ("channel", "group"): "ctg",
    ("group", "group"): "gtg",
    ("channel", "channel"): "ctc",
}


def kind_of(chat_type: str) -> str | None:
    if chat_type in ("group", "supergroup"):
        return "group"
    if chat_type == "channel":
        return "channel"
    return None


# ════════════════════════════════════════════════
#  مدل داده
# ════════════════════════════════════════════════

@dataclass(slots=True, frozen=True)
class Rule:
    id: int
    source_id: int
    source_title: str
    source_kind: str
    target_id: int
    target_title: str
    target_kind: str
    direction: str
    active: bool
    created_by: int | None
    bot_slot: int = 1

    @property
    def direction_label(self) -> str:
        return PERM_LABELS.get(self.direction, self.direction)


@dataclass(slots=True, frozen=True)
class AdminInfo:
    user_id: int
    is_owner: bool
    permissions: set[str] = field(default_factory=set)
    added_by: int | None = None

    def has(self, perm: str) -> bool:
        return self.is_owner or perm in self.permissions

# ════════════════════════════════════════════════
#  دیتابیس (Supabase — دائمی، از طریق REST API)
# ════════════════════════════════════════════════
#
# نکته‌ی مهم: این لایه با REST API سوپابیس (کتابخانه‌ی supabase-py) کار می‌کند
# نه اتصال مستقیم Postgres، پس مشکلات شبکه‌ای IPv6/pooler که سرویس‌هایی مثل
# Render با اتصال مستقیم دارند اینجا مطرح نیست.
#
# جدول‌ها را REST API نمی‌تواند خودش بسازد (DDL ندارد)، پس باید یک‌بار از
# طریق SQL Editor پنل سوپابیس ساخته شوند. اسکریپت لازم را در فایل
# supabase_schema.sql کنار همین پروژه قرار داده‌ایم.

sb: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def init_db() -> None:
    """
    مالک‌های تعریف‌شده در ENV (ADMINS) را با دسترسی کامل در دیتابیس ثبت/به‌روزرسانی می‌کند.
    (ساخت جدول‌ها به‌عهده‌ی supabase_schema.sql است — یک‌بار در SQL Editor اجرا شود.)
    """
    all_perms_csv = ",".join(ALL_PERMS)
    for uid in OWNERS:
        # مالک‌های ENV همیشه دسترسی کامل دارند، حتی اگر قبلاً به شکل دیگری
        # در دیتابیس ثبت شده باشند؛ upsert بدون ignore_duplicates یعنی
        # اگر از قبل هست، رکورد آپدیت می‌شود.
        sb.table("admins").upsert(
            {"user_id": uid, "is_owner": True, "permissions": all_perms_csv, "added_by": None},
            on_conflict="user_id",
        ).execute()

# ── ادمین‌ها ──────────────────────────────────────

def get_admin(uid: int) -> AdminInfo | None:
    res = sb.table("admins").select("*").eq("user_id", uid).limit(1).execute()
    rows = res.data
    if not rows:
        return None
    row = rows[0]
    perms = {p for p in (row.get("permissions") or "").split(",") if p}
    return AdminInfo(
        user_id=row["user_id"], is_owner=bool(row.get("is_owner")),
        permissions=perms, added_by=row.get("added_by"),
    )


def is_admin(uid: int) -> bool:
    return get_admin(uid) is not None


def has_perm(uid: int, perm: str) -> bool:
    info = get_admin(uid)
    return bool(info and info.has(perm))


def list_admins() -> list[AdminInfo]:
    res = (
        sb.table("admins").select("*")
        .order("is_owner", desc=True).order("user_id")
        .execute()
    )
    out = []
    for r in res.data:
        perms = {p for p in (r.get("permissions") or "").split(",") if p}
        out.append(AdminInfo(
            user_id=r["user_id"], is_owner=bool(r.get("is_owner")),
            permissions=perms, added_by=r.get("added_by"),
        ))
    return out


def add_admin(uid: int, added_by: int) -> None:
    # ignore_duplicates=True یعنی اگر از قبل وجود دارد دست‌نخورده می‌ماند
    # (معادل رفتار قبلیِ INSERT OR IGNORE).
    sb.table("admins").upsert(
        {"user_id": uid, "is_owner": False, "permissions": "", "added_by": added_by},
        on_conflict="user_id",
        ignore_duplicates=True,
    ).execute()


def remove_admin(uid: int) -> None:
    sb.table("admins").delete().eq("user_id", uid).eq("is_owner", False).execute()


def toggle_admin_perm(uid: int, perm: str) -> None:
    info = get_admin(uid)
    if info is None or info.is_owner:
        return
    perms = set(info.permissions)
    if perm in perms:
        perms.discard(perm)
    else:
        perms.add(perm)
    sb.table("admins").update({"permissions": ",".join(sorted(perms))}).eq("user_id", uid).execute()

# ── مسیرهای فوروارد ───────────────────────────────

def _row_to_rule(row: dict) -> Rule:
    return Rule(
        id=row["id"], source_id=row["source_id"], source_title=row.get("source_title") or "—",
        source_kind=row["source_kind"],
        target_id=row["target_id"], target_title=row.get("target_title") or "—",
        target_kind=row["target_kind"],
        direction=row["direction"], active=bool(row.get("active")), created_by=row.get("created_by"),
        bot_slot=int(row.get("bot_slot") or 1),
    )


def add_rule(source_id: int, source_title: str, source_kind: str,
             target_id: int, target_title: str, target_kind: str,
             direction: str, created_by: int, bot_slot: int = 1) -> int | None:
    try:
        res = sb.table("rules").insert({
            "source_id": source_id, "source_title": source_title, "source_kind": source_kind,
            "target_id": target_id, "target_title": target_title, "target_kind": target_kind,
            "direction": direction, "active": False, "created_by": created_by,
            "bot_slot": bot_slot,
        }).execute()
        return res.data[0]["id"] if res.data else None
    except APIError as e:
        if getattr(e, "code", None) == "23505" or "duplicate key" in str(e).lower():
            return None  # این مسیر (همین منبع و مقصد) از قبل وجود دارد
        raise


def get_rule(rule_id: int) -> Rule | None:
    res = sb.table("rules").select("*").eq("id", rule_id).limit(1).execute()
    return _row_to_rule(res.data[0]) if res.data else None


def list_rules() -> list[Rule]:
    res = sb.table("rules").select("*").order("id").execute()
    return [_row_to_rule(r) for r in res.data]


def rules_for_source(source_id: int, bot_slot: int) -> list[Rule]:
    res = (
        sb.table("rules").select("*")
        .eq("active", True).eq("source_id", source_id).eq("bot_slot", bot_slot)
        .execute()
    )
    return [_row_to_rule(r) for r in res.data]


def set_rule_active(rule_id: int, active: bool) -> None:
    sb.table("rules").update({"active": active}).eq("id", rule_id).execute()


def delete_rule(rule_id: int) -> None:
    sb.table("rules").delete().eq("id", rule_id).execute()

# ════════════════════════════════════════════════
#  ابزار: نرمال‌سازی یوزرنیم
# ════════════════════════════════════════════════

def normalize(text: str) -> str:
    """
    قبول می‌کند:
      @username
      username
      t.me/username
      https://t.me/username
      https://telegram.me/username
    """
    t = text.strip()
    for prefix in (
        "https://telegram.me/",
        "https://t.me/",
        "http://t.me/",
        "telegram.me/",
        "t.me/",
    ):
        if t.lower().startswith(prefix):
            t = t[len(prefix):]
            break
    t = t.lstrip("@").split("/")[0].split("?")[0]
    return f"@{t}" if t else ""


def parse_chat_ref(text: str) -> str | int | None:
    """
    ورودی چت را برای get_chat آماده می‌کند. قبول می‌کند:
      @username
      username
      t.me/username
      https://t.me/username
      آیدی عددی (مثل -1001234567890) ← برای گروه/چنل خصوصی بدون یوزرنیم
    """
    t = text.strip()
    if t.lstrip("-").isdigit():
        return int(t)
    username = normalize(t)
    if not username or username == "@":
        return None
    return username


def is_invite_link(text: str) -> bool:
    """
    تشخیص لینک‌های دعوت خصوصی مثل:
      t.me/+AbCdEfGhIjK
      https://t.me/+AbCdEfGhIjK
      t.me/joinchat/AbCdEfGhIjK
    این‌ها هشِ دعوت هستند نه یوزرنیم و Bot API نمی‌تواند مستقیماً
    از روی آن‌ها چت را پیدا کند (getChat این فرمت را پشتیبانی نمی‌کند).
    """
    t = text.strip().lower()
    for prefix in (
        "https://telegram.me/",
        "https://t.me/",
        "http://t.me/",
        "telegram.me/",
        "t.me/",
    ):
        if t.startswith(prefix):
            t = t[len(prefix):]
            break
    return t.startswith("+") or t.startswith("joinchat/")


def chat_from_forward(update: Update):
    """
    اگر کاربر پیامی را از چنل/گروهِ موردنظر همینجا فوروارد کند، این تابع
    خودِ چتِ مبدأ فوروارد را برمی‌گرداند — حتی اگر آن چنل/گروه خصوصی و
    بدون یوزرنیم باشد. این بهترین راه برای چنل‌های خصوصی است چون کاربر
    مجبور نیست آیدی عددی را دستی پیدا کند.
    نکته: اگر چنل «مخفی‌سازی نام فوروارد» را فعال کرده باشد، این روش کار
    نمی‌کند و کاربر باید آیدی عددی را مستقیم بفرستد.
    """
    msg = update.message
    origin = getattr(msg, "forward_origin", None)
    if origin is None:
        return None
    return getattr(origin, "chat", None) or getattr(origin, "sender_chat", None)


NAV_KEYWORDS = ("شروع", "توقف", "تنظیم", "وضعیت", "بازگشت", "مسیر", "ادمین")

# ════════════════════════════════════════════════
#  کیبورد و متن
#  نکته: style فقط سه مقدار معتبر دارد: primary (آبی)، success (سبز)،
#  danger (قرمز). از Bot API 9.4 (۹ فوریه ۲۰۲۶) و python-telegram-bot
#  نسخه 22.7+ پشتیبانی می‌شود. مقدار "secondary" معتبر نیست و حذف شده.
# ════════════════════════════════════════════════

def main_menu_text() -> str:
    return "🤖 *ربات فورواردر چندمسیره*\n\nیکی از گزینه‌ها را انتخاب کن:"


def main_menu_kb(info: AdminInfo) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if info.is_owner or (info.permissions & set(ROUTE_PERMS)):
        rows.append([InlineKeyboardButton("➕ افزودن مسیر جدید", style="success", callback_data="add_route")])
    rows.append([InlineKeyboardButton("📋 لیست مسیرهای فوروارد", style="primary", callback_data="list_rules:0")])
    if info.has("manage_admins"):
        rows.append([InlineKeyboardButton("👥 مدیریت ادمین‌ها", style="primary", callback_data="admins")])
    return InlineKeyboardMarkup(rows)


def rule_row_label(r: Rule) -> str:
    status = "✅" if r.active else "🔴"
    return f"{status} #{r.id} · 🤖{r.bot_slot} · {r.source_title} → {r.target_title}"


PAGE_SIZE = 6


def rules_list_kb(page: int) -> tuple[str, InlineKeyboardMarkup]:
    rules = list_rules()
    total = len(rules)
    start = page * PAGE_SIZE
    chunk = rules[start:start + PAGE_SIZE]

    if not rules:
        text = "📋 *لیست مسیرها*\n\nهنوز هیچ مسیر فورواردی ساخته نشده.\nاز «➕ افزودن مسیر جدید» شروع کن."
    else:
        text = f"📋 *لیست مسیرها* ({total} مسیر)\n\nروی هر مسیر بزن تا جزئیاتش را ببینی:"

    rows = [[InlineKeyboardButton(rule_row_label(r), callback_data=f"rule:{r.id}")] for r in chunk]

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"list_rules:{page-1}"))
    if start + PAGE_SIZE < total:
        nav.append(InlineKeyboardButton("➡️ بعدی", callback_data=f"list_rules:{page+1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="menu")])
    return text, InlineKeyboardMarkup(rows)


def rule_panel_text(r: Rule) -> str:
    status = "✅ فعال" if r.active else "🔴 غیرفعال"
    return (
        f"╔══════════════════════╗\n"
        f"║   🔀 مسیر شماره #{r.id:<5}║\n"
        f"╚══════════════════════╝\n\n"
        f"🧭 نوع مسیر:   {r.direction_label}\n"
        f"🤖 ربات مسئول: ربات #{r.bot_slot}\n"
        f"📥 منبع:        {r.source_title}  (`{r.source_id}`)\n"
        f"📤 مقصد:        {r.target_title}  (`{r.target_id}`)\n"
        f"📡 وضعیت:      {status}"
    )


def rule_panel_kb(r: Rule, info: AdminInfo) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if info.has(r.direction):
        if r.active:
            rows.append([InlineKeyboardButton("⏹ توقف این مسیر", style="danger", callback_data=f"rule:{r.id}:stop")])
        else:
            rows.append([InlineKeyboardButton("▶️ شروع این مسیر", style="success", callback_data=f"rule:{r.id}:start")])
        rows.append([InlineKeyboardButton("🗑 حذف این مسیر", style="danger", callback_data=f"rule:{r.id}:del")])
    rows.append([InlineKeyboardButton("🔙 بازگشت به لیست", callback_data="list_rules:0")])
    return InlineKeyboardMarkup(rows)


def rule_del_confirm_kb(r: Rule) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ بله، حذف کن", style="danger", callback_data=f"rule:{r.id}:delok"),
            InlineKeyboardButton("❌ انصراف", style="primary", callback_data=f"rule:{r.id}"),
        ],
    ])


def admins_menu_text(admins: list[AdminInfo]) -> str:
    lines = ["👥 *مدیریت ادمین‌ها*\n"]
    for a in admins:
        if a.is_owner:
            lines.append(f"👑 `{a.user_id}` — مالک (دسترسی کامل)")
        else:
            perm_txt = "، ".join(PERM_LABELS[p] for p in ROUTE_PERMS if p in a.permissions)
            if "manage_admins" in a.permissions:
                perm_txt = (perm_txt + "، " if perm_txt else "") + PERM_LABELS["manage_admins"]
            lines.append(f"👤 `{a.user_id}` — {perm_txt or 'بدون دسترسی'}")
    return "\n".join(lines)


def admins_menu_kb(admins: list[AdminInfo]) -> InlineKeyboardMarkup:
    rows = []
    for a in admins:
        if a.is_owner:
            continue
        rows.append([InlineKeyboardButton(f"✏️ ویرایش دسترسی {a.user_id}", callback_data=f"admins:edit:{a.user_id}")])
    rows.append([InlineKeyboardButton("➕ افزودن ادمین جدید", style="success", callback_data="admins:add")])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="menu")])
    return InlineKeyboardMarkup(rows)


def admin_edit_text(a: AdminInfo) -> str:
    return f"✏️ *ویرایش دسترسی ادمین*\n\nآیدی: `{a.user_id}`\n\nروی هر دسترسی بزن تا فعال/غیرفعال بشه:"


def admin_edit_kb(a: AdminInfo) -> InlineKeyboardMarkup:
    rows = []
    for perm in ALL_PERMS:
        mark = "✅" if perm in a.permissions else "◻️"
        rows.append([InlineKeyboardButton(f"{mark} {PERM_LABELS[perm]}", callback_data=f"aperm:{a.user_id}:{perm}")])
    rows.append([InlineKeyboardButton("🗑 حذف این ادمین", style="danger", callback_data=f"admins:rm:{a.user_id}")])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="admins")])
    return InlineKeyboardMarkup(rows)


def admin_rm_confirm_kb(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ بله، حذف کن", style="danger", callback_data=f"admins:rmok:{uid}"),
            InlineKeyboardButton("❌ انصراف", style="primary", callback_data=f"admins:edit:{uid}"),
        ],
    ])


def bot_slot_kb() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for i in range(1, len(BOT_TOKENS) + 1):
        row.append(InlineKeyboardButton(f"🤖 ربات #{i}", callback_data=f"addbot:{i}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("🚫 انصراف", style="danger", callback_data="menu")])
    return InlineKeyboardMarkup(rows)

# ════════════════════════════════════════════════
#  /start
# ════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    uid = update.effective_user.id
    info = get_admin(uid)
    if info is None:
        await update.message.reply_text("❌ شما دسترسی ندارید")
        return ConversationHandler.END

    ctx.user_data.clear()
    await update.message.reply_text(
        main_menu_text(),
        reply_markup=main_menu_kb(info),
        parse_mode="Markdown",
    )
    return ST_MAIN

# ════════════════════════════════════════════════
#  هندلر دکمه‌های Inline
# ════════════════════════════════════════════════

async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    uid = q.from_user.id
    await q.answer()

    info = get_admin(uid)
    if info is None:
        await q.edit_message_text("❌ شما دسترسی ندارید")
        return ConversationHandler.END

    data = q.data

    # ── منوی اصلی ──────────────────────────────
    if data == "menu":
        ctx.user_data.clear()
        await q.edit_message_text(main_menu_text(), reply_markup=main_menu_kb(info), parse_mode="Markdown")
        return ST_MAIN

    # ── افزودن مسیر جدید ───────────────────────
    if data == "add_route":
        if not (info.is_owner or (info.permissions & set(ROUTE_PERMS))):
            await q.answer("⚠️ شما اجازه‌ی ساخت مسیر فوروارد را ندارید", show_alert=True)
            return ST_MAIN
        ctx.user_data["new_rule"] = {}
        await q.edit_message_text(
            "📥 *مرحله ۱ از ۲ — منبع*\n\n"
            "یوزرنیم یا آیدی عددیِ گروه/چنلِ *منبع* را ارسال کن (ربات باید عضو آن باشد)\n\n"
            "فرمت‌های قابل‌قبول:\n`@username`\n`t.me/username`\n`-1001234567890` (آیدی عددی — برای چت‌های خصوصی بدون یوزرنیم)\n\n"
            "🔒 برای چت‌های *خصوصی* (بدون یوزرنیم و بدون لینک عمومی): یک پیام از همان چت را همینجا فوروارد کن، ربات خودش آیدی را تشخیص می‌دهد\n\n"
            "برای انصراف /cancel بزن",
            parse_mode="Markdown",
        )
        return ST_ADD_SRC

    # ── لیست مسیرها ────────────────────────────
    if data.startswith("list_rules:"):
        page = int(data.split(":", 1)[1])
        text, kb = rules_list_kb(page)
        await q.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
        return ST_MAIN

    # ── پنل یک مسیر خاص ────────────────────────
    if data.startswith("rule:"):
        parts = data.split(":")
        rule_id = int(parts[1])
        action = parts[2] if len(parts) > 2 else None
        r = get_rule(rule_id)
        if r is None:
            await q.answer("⚠️ این مسیر دیگر وجود ندارد", show_alert=True)
            text, kb = rules_list_kb(0)
            await q.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
            return ST_MAIN

        if action is None:
            await q.edit_message_text(rule_panel_text(r), reply_markup=rule_panel_kb(r, info), parse_mode="Markdown")
            return ST_MAIN

        if not info.has(r.direction):
            await q.answer("⚠️ شما اجازه‌ی مدیریت این نوع مسیر را ندارید", show_alert=True)
            return ST_MAIN

        match action:
            case "start":
                set_rule_active(rule_id, True)
                r = get_rule(rule_id)
                log.info("▶ Rule #%s STARTED by admin %s", rule_id, uid)
            case "stop":
                set_rule_active(rule_id, False)
                r = get_rule(rule_id)
                log.info("■ Rule #%s STOPPED by admin %s", rule_id, uid)
            case "del":
                await q.edit_message_text(
                    "🗑 *حذف مسیر*\n\n" + rule_panel_text(r) + "\n\nمطمئنی می‌خوای این مسیر حذف بشه؟",
                    reply_markup=rule_del_confirm_kb(r),
                    parse_mode="Markdown",
                )
                return ST_MAIN
            case "delok":
                delete_rule(rule_id)
                log.info("🗑 Rule #%s DELETED by admin %s", rule_id, uid)
                text, kb = rules_list_kb(0)
                await q.edit_message_text("✅ مسیر حذف شد\n\n" + text, reply_markup=kb, parse_mode="Markdown")
                return ST_MAIN

        await q.edit_message_text(rule_panel_text(r), reply_markup=rule_panel_kb(r, info), parse_mode="Markdown")
        return ST_MAIN

    # ── مدیریت ادمین‌ها ────────────────────────
    if data == "admins":
        if not info.has("manage_admins"):
            await q.answer("⚠️ شما اجازه‌ی مدیریت ادمین‌ها را ندارید", show_alert=True)
            return ST_MAIN
        admins = list_admins()
        await q.edit_message_text(admins_menu_text(admins), reply_markup=admins_menu_kb(admins), parse_mode="Markdown")
        return ST_MAIN

    if data == "admins:add":
        if not info.has("manage_admins"):
            await q.answer("⚠️ شما اجازه‌ی مدیریت ادمین‌ها را ندارید", show_alert=True)
            return ST_MAIN
        await q.edit_message_text(
            "➕ *افزودن ادمین جدید*\n\n"
            "آیدی عددی تلگرام کاربر را ارسال کن.\n"
            "(می‌تونی از رباتی مثل @userinfobot آیدی عددی را بگیری)\n\n"
            "برای انصراف /cancel بزن",
            parse_mode="Markdown",
        )
        return ST_ADMIN_ADD

    if data.startswith("admins:edit:"):
        if not info.has("manage_admins"):
            await q.answer("⚠️ شما اجازه‌ی مدیریت ادمین‌ها را ندارید", show_alert=True)
            return ST_MAIN
        target_uid = int(data.split(":")[2])
        target = get_admin(target_uid)
        if target is None or target.is_owner:
            await q.answer("⚠️ این ادمین قابل‌ویرایش نیست", show_alert=True)
            return ST_MAIN
        await q.edit_message_text(admin_edit_text(target), reply_markup=admin_edit_kb(target), parse_mode="Markdown")
        return ST_MAIN

    if data.startswith("admins:rm:"):
        if not info.has("manage_admins"):
            await q.answer("⚠️ شما اجازه‌ی مدیریت ادمین‌ها را ندارید", show_alert=True)
            return ST_MAIN
        target_uid = int(data.split(":")[2])
        await q.edit_message_text(
            f"🗑 آیا از حذف ادمین `{target_uid}` مطمئنی؟",
            reply_markup=admin_rm_confirm_kb(target_uid),
            parse_mode="Markdown",
        )
        return ST_MAIN

    if data.startswith("admins:rmok:"):
        if not info.has("manage_admins"):
            await q.answer("⚠️ شما اجازه‌ی مدیریت ادمین‌ها را ندارید", show_alert=True)
            return ST_MAIN
        target_uid = int(data.split(":")[2])
        remove_admin(target_uid)
        log.info("👤 Admin %s removed by %s", target_uid, uid)
        admins = list_admins()
        await q.edit_message_text(
            "✅ ادمین حذف شد\n\n" + admins_menu_text(admins),
            reply_markup=admins_menu_kb(admins),
            parse_mode="Markdown",
        )
        return ST_MAIN

    # ── انتخاب ربات مسئول برای مسیر جدید ───────
    if data.startswith("addbot:"):
        slot = int(data.split(":", 1)[1])
        new_state = await _finalize_new_rule(q.message, ctx, uid, info, bot_slot=slot)
        return new_state

    if data.startswith("aperm:"):
        if not info.has("manage_admins"):
            await q.answer("⚠️ شما اجازه‌ی مدیریت ادمین‌ها را ندارید", show_alert=True)
            return ST_MAIN
        _, target_uid_s, perm = data.split(":")
        target_uid = int(target_uid_s)
        target = get_admin(target_uid)
        if target is None or target.is_owner:
            await q.answer("⚠️ این ادمین قابل‌ویرایش نیست", show_alert=True)
            return ST_MAIN
        toggle_admin_perm(target_uid, perm)
        target = get_admin(target_uid)
        await q.edit_message_text(admin_edit_text(target), reply_markup=admin_edit_kb(target), parse_mode="Markdown")
        return ST_MAIN

    return ST_MAIN

# ════════════════════════════════════════════════
#  دریافت و اعتبارسنجی منبع مسیر جدید
# ════════════════════════════════════════════════

async def recv_add_src(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    uid = update.effective_user.id
    info = get_admin(uid)
    if info is None:
        return ConversationHandler.END

    fwd_chat = chat_from_forward(update)
    if fwd_chat is not None:
        chat = fwd_chat
    else:
        text = update.message.text or ""
        if is_invite_link(text):
            await update.message.reply_text(
                "❌ لینک‌های دعوت خصوصی (`t.me/+...`) به‌تنهایی برای ربات قابل‌استفاده نیستند\n\n"
                "به‌جایش یکی از این دو راه را انجام بده:\n"
                "۱️⃣ یک پیام از همان گروه/چنل را همینجا فوروارد کن (ربات خودش تشخیص می‌دهد)\n"
                "۲️⃣ ربات را در آن چت عضو/ادمین کن و آیدی عددی چت (مثل `-1001234567890`) را بفرست\n\n"
                "برای انصراف /cancel بزن",
                parse_mode="Markdown",
            )
            return ST_ADD_SRC

        ref = parse_chat_ref(text)
        if ref is None:
            await update.message.reply_text(
                "❌ ورودی نامعتبر است\n\nمثال: `@mygroup`، `t.me/mygroup`، آیدی عددی `-1001234567890` یا فوروارد یک پیام از آن چت\n\nدوباره ارسال کن یا /cancel بزن",
                parse_mode="Markdown",
            )
            return ST_ADD_SRC

        try:
            chat = await ctx.bot.get_chat(ref)
        except Exception as e:
            log.warning("get_chat failed for %r: %s", ref, e)
            await update.message.reply_text(
                "❌ چت پیدا نشد!\n\n• یوزرنیم/آیدی را چک کن\n• مطمئن شو ربات عضو آن است\n"
                "• یا به‌جای تایپ آیدی، یک پیام از آن چت را همینجا فوروارد کن\n\n"
                "دوباره ارسال کن یا /cancel بزن"
            )
            return ST_ADD_SRC

    kind = kind_of(chat.type)
    if kind is None:
        await update.message.reply_text("❌ این باید یک گروه، سوپرگروه یا چنل باشد")
        return ST_ADD_SRC

    try:
        await ctx.bot.get_chat_member(chat.id, ctx.bot.id)
    except Exception:
        await update.message.reply_text(
            "❌ ربات عضو این چت نیست!\n\n۱. ربات را به این چت اضافه کن\n۲. دوباره ارسال کن"
        )
        return ST_ADD_SRC

    ctx.user_data["new_rule"] = {"source_id": chat.id, "source_title": chat.title or str(chat.id), "source_kind": kind}
    await update.message.reply_text(
        f"✅ منبع «*{chat.title}*» ثبت شد\n\n"
        "📤 *مرحله ۲ از ۲ — مقصد*\n\n"
        "یوزرنیم یا آیدی عددیِ گروه/چنلِ *مقصد* را ارسال کن\n"
        "(اگر مقصد چنل است، ربات باید ادمین آن باشد)\n\n"
        "🔒 برای چت‌های *خصوصی*: یک پیام از همان چت را همینجا فوروارد کن، ربات خودش آیدی را تشخیص می‌دهد\n\n"
        "برای انصراف /cancel بزن",
        parse_mode="Markdown",
    )
    return ST_ADD_TGT

# ════════════════════════════════════════════════
#  دریافت و اعتبارسنجی مقصد مسیر جدید
# ════════════════════════════════════════════════

async def recv_add_tgt(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    uid = update.effective_user.id
    info = get_admin(uid)
    if info is None:
        return ConversationHandler.END

    new_rule = ctx.user_data.get("new_rule")
    if not new_rule or "source_id" not in new_rule:
        await update.message.reply_text("⚠️ چیزی اشتباه شد، دوباره /start بزن")
        return ConversationHandler.END

    fwd_chat = chat_from_forward(update)
    if fwd_chat is not None:
        chat = fwd_chat
    else:
        text = update.message.text or ""
        if is_invite_link(text):
            await update.message.reply_text(
                "❌ لینک‌های دعوت خصوصی (`t.me/+...`) به‌تنهایی برای ربات قابل‌استفاده نیستند\n\n"
                "به‌جایش یکی از این دو راه را انجام بده:\n"
                "۱️⃣ یک پیام از همان گروه/چنل را همینجا فوروارد کن (ربات خودش تشخیص می‌دهد)\n"
                "۲️⃣ ربات را در آن چت عضو/ادمین کن و آیدی عددی چت (مثل `-1001234567890`) را بفرست\n\n"
                "برای انصراف /cancel بزن",
                parse_mode="Markdown",
            )
            return ST_ADD_TGT

        ref = parse_chat_ref(text)
        if ref is None:
            await update.message.reply_text(
                "❌ ورودی نامعتبر است\n\nمثال: `@mychannel`، `t.me/mychannel`، آیدی عددی `-1001234567890` یا فوروارد یک پیام از آن چت\n\nدوباره ارسال کن یا /cancel بزن",
                parse_mode="Markdown",
            )
            return ST_ADD_TGT

        try:
            chat = await ctx.bot.get_chat(ref)
        except Exception as e:
            log.warning("get_chat failed for %r: %s", ref, e)
            await update.message.reply_text(
                "❌ چت پیدا نشد!\n\n• یوزرنیم/آیدی را چک کن\n• مطمئن شو ربات عضو/ادمین آن است\n"
                "• یا به‌جای تایپ آیدی، یک پیام از آن چت را همینجا فوروارد کن\n\n"
                "دوباره ارسال کن یا /cancel بزن"
            )
            return ST_ADD_TGT

    tgt_kind = kind_of(chat.type)
    if tgt_kind is None:
        await update.message.reply_text("❌ این باید یک گروه، سوپرگروه یا چنل باشد")
        return ST_ADD_TGT

    if chat.id == new_rule["source_id"]:
        await update.message.reply_text("❌ مقصد نمی‌تواند همان منبع باشد\n\nیوزرنیم دیگری بفرست یا /cancel بزن")
        return ST_ADD_TGT

    direction = DIRECTIONS.get((new_rule["source_kind"], tgt_kind))
    if direction is None:
        await update.message.reply_text("❌ این ترکیب پشتیبانی نمی‌شود\n\nیوزرنیم دیگری بفرست یا /cancel بزن")
        return ST_ADD_TGT

    if not info.has(direction):
        await update.message.reply_text(
            f"⛔ شما اجازه‌ی ساخت مسیر «{PERM_LABELS[direction]}» را نداری\n\n"
            "از یک مالک ربات بخواه این دسترسی را برایت فعال کند، یا /cancel بزن"
        )
        return ST_ADD_TGT

    ctx.user_data["pending_rule"] = {
        "source_id": new_rule["source_id"], "source_title": new_rule["source_title"],
        "source_kind": new_rule["source_kind"],
        "target_id": chat.id, "target_title": chat.title or str(chat.id), "target_kind": tgt_kind,
        "direction": direction,
    }
    ctx.user_data.pop("new_rule", None)

    if len(BOT_TOKENS) == 1:
        # فقط یک ربات تنظیم شده؛ نیازی به پرسیدن نیست، خودکار ربات #۱ انتخاب می‌شود
        return await _finalize_new_rule(update.message, ctx, uid, info, bot_slot=1)

    await update.message.reply_text(
        "🤖 *مرحله ۳ از ۳ — انتخاب ربات مسئول*\n\n"
        "کدام ربات این مسیر را فوروارد کند؟\n"
        "⚠️ حتماً همان ربات را از قبل در هر دو چت (منبع و مقصد) عضو/ادمین کرده باش، "
        "وگرنه فوروارد کار نمی‌کند.",
        reply_markup=bot_slot_kb(),
        parse_mode="Markdown",
    )
    return ST_ADD_BOT

# ════════════════════════════════════════════════
#  نهایی‌سازی ساخت مسیر (بعد از انتخاب ربات مسئول)
# ════════════════════════════════════════════════

async def _finalize_new_rule(reply_target, ctx: ContextTypes.DEFAULT_TYPE, uid: int, info: AdminInfo, bot_slot: int) -> int:
    pending = ctx.user_data.get("pending_rule")
    if not pending:
        await reply_target.reply_text("⚠️ چیزی اشتباه شد، دوباره /start بزن")
        return ConversationHandler.END

    if bot_slot < 1 or bot_slot > len(BOT_TOKENS):
        await reply_target.reply_text("⚠️ شماره‌ی ربات نامعتبر است")
        return ST_ADD_BOT

    # چک عضویت همون ربات انتخاب‌شده (نه لزوماً ربات #۱) در منبع و مقصد
    tmp_bot = Bot(token=BOT_TOKENS[bot_slot - 1])
    try:
        await tmp_bot.initialize()
        try:
            await tmp_bot.get_chat_member(pending["source_id"], tmp_bot.id)
        except Exception:
            await reply_target.reply_text(
                f"❌ ربات #{bot_slot} در چت *منبع* ({pending['source_title']}) عضو نیست\n\n"
                "اول رباتِ انتخابی را در آن چت عضو/ادمین کن، بعد دوباره امتحان کن.",
                reply_markup=bot_slot_kb(),
                parse_mode="Markdown",
            )
            return ST_ADD_BOT

        if pending["target_kind"] == "channel":
            try:
                me = await tmp_bot.get_chat_member(pending["target_id"], tmp_bot.id)
                if me.status not in ("administrator", "creator"):
                    raise PermissionError
            except Exception:
                await reply_target.reply_text(
                    f"❌ ربات #{bot_slot} ادمینِ چنلِ *مقصد* ({pending['target_title']}) نیست\n\n"
                    "اول رباتِ انتخابی را در آن چنل ادمین کن، بعد دوباره امتحان کن.",
                    reply_markup=bot_slot_kb(),
                    parse_mode="Markdown",
                )
                return ST_ADD_BOT
        else:
            try:
                await tmp_bot.get_chat_member(pending["target_id"], tmp_bot.id)
            except Exception:
                await reply_target.reply_text(
                    f"❌ ربات #{bot_slot} در گروهِ *مقصد* ({pending['target_title']}) عضو نیست\n\n"
                    "اول رباتِ انتخابی را در آن گروه عضو کن، بعد دوباره امتحان کن.",
                    reply_markup=bot_slot_kb(),
                    parse_mode="Markdown",
                )
                return ST_ADD_BOT
    finally:
        await tmp_bot.shutdown()

    rule_id = add_rule(
        source_id=pending["source_id"], source_title=pending["source_title"], source_kind=pending["source_kind"],
        target_id=pending["target_id"], target_title=pending["target_title"], target_kind=pending["target_kind"],
        direction=pending["direction"], created_by=uid, bot_slot=bot_slot,
    )
    ctx.user_data.pop("pending_rule", None)

    if rule_id is None:
        await reply_target.reply_text("⚠️ این مسیر (همین منبع و مقصد) از قبل وجود دارد")
        text, kb = rules_list_kb(0)
        await reply_target.reply_text(text, reply_markup=kb, parse_mode="Markdown")
        return ST_MAIN

    r = get_rule(rule_id)
    log.info("🆕 [%s] Rule #%s created: %s → %s (bot #%s)", pending["direction"], rule_id, r.source_id, r.target_id, bot_slot)
    await reply_target.reply_text(
        "🎉 مسیر جدید ساخته شد!\n\n" + rule_panel_text(r),
        reply_markup=rule_panel_kb(r, info),
        parse_mode="Markdown",
    )
    return ST_MAIN

# ════════════════════════════════════════════════
#  دریافت آیدی عددی ادمین جدید
# ════════════════════════════════════════════════

async def recv_admin_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    uid = update.effective_user.id
    info = get_admin(uid)
    if info is None or not info.has("manage_admins"):
        return ConversationHandler.END

    text = update.message.text.strip()
    if not text.lstrip("-").isdigit():
        await update.message.reply_text(
            "❌ این یک آیدی عددی معتبر نیست\n\nفقط عدد آیدی تلگرام را بفرست، یا /cancel بزن"
        )
        return ST_ADMIN_ADD

    new_uid = int(text)
    existing = get_admin(new_uid)
    if existing is not None:
        await update.message.reply_text("⚠️ این کاربر از قبل ادمین است")
        admins = list_admins()
        await update.message.reply_text(admins_menu_text(admins), reply_markup=admins_menu_kb(admins), parse_mode="Markdown")
        return ST_MAIN

    add_admin(new_uid, added_by=uid)
    log.info("👤 New admin %s added by %s", new_uid, uid)
    target = get_admin(new_uid)
    await update.message.reply_text(
        f"✅ ادمین `{new_uid}` اضافه شد (فعلاً بدون دسترسی)\n\n"
        "حالا دسترسی‌هایی که می‌خوای بهش بدی رو انتخاب کن:",
        parse_mode="Markdown",
    )
    await update.message.reply_text(admin_edit_text(target), reply_markup=admin_edit_kb(target), parse_mode="Markdown")
    return ST_MAIN

# ════════════════════════════════════════════════
#  لغو
# ════════════════════════════════════════════════

async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    uid = update.effective_user.id
    info = get_admin(uid)
    if info is None:
        return ConversationHandler.END
    ctx.user_data.clear()
    await update.message.reply_text(
        "🚫 عملیات لغو شد\n\n" + main_menu_text(),
        reply_markup=main_menu_kb(info),
        parse_mode="Markdown",
    )
    return ST_MAIN

# ════════════════════════════════════════════════
#  فورواد پیام‌ها (بین هر تعداد مسیر فعال)
# ════════════════════════════════════════════════

MAX_FLOOD_RETRIES = 3


async def _forward_one(ctx: ContextTypes.DEFAULT_TYPE, r: Rule, chat_id: int, msg_id: int, attempt: int = 1) -> None:
    try:
        await ctx.bot.forward_message(
            chat_id=r.target_id,
            from_chat_id=chat_id,
            message_id=msg_id,
        )
        log.info("📨 [%s] msg#%s  %s → %s (rule #%s)", r.direction, msg_id, r.source_id, r.target_id, r.id)
    except RetryAfter as e:
        wait_s = float(e.retry_after) + 1
        if attempt <= MAX_FLOOD_RETRIES:
            log.warning(
                "⏳ [rule #%s] Flood control on msg#%s, retrying in %.0fs (attempt %s/%s)",
                r.id, msg_id, wait_s, attempt, MAX_FLOOD_RETRIES,
            )
            await asyncio.sleep(wait_s)
            await _forward_one(ctx, r, chat_id, msg_id, attempt + 1)
        else:
            log.error(
                "❌ [rule #%s] Gave up on msg#%s after %s flood-control retries (last wait: %.0fs)",
                r.id, msg_id, MAX_FLOOD_RETRIES, wait_s,
            )
    except TelegramError as e:
        log.error("❌ [rule #%s] Forward failed msg#%s: %s", r.id, msg_id, e)


def make_do_forward(bot_slot: int):
    async def do_forward(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message or update.channel_post
        if msg is None:
            return

        chat_id = msg.chat_id
        for r in rules_for_source(chat_id, bot_slot):
            # هر فوروارد یه تسک جدا و بدون هیچ تاخیری اجرا می‌شه؛
            # اگه یکی به فلود کنترل بخوره، بقیه‌ی قوانین و پیام‌های بعدی معطلش نمی‌مونن.
            ctx.application.create_task(_forward_one(ctx, r, chat_id, msg.message_id))

    return do_forward

# ════════════════════════════════════════════════
#  اجرا (Polling - مخصوص Render Background Worker)
# ════════════════════════════════════════════════

def build_forward_filter() -> filters.BaseFilter:
    return (
        (filters.ChatType.GROUPS | filters.ChatType.CHANNEL)
        & (
            filters.TEXT
            | filters.PHOTO
            | filters.VIDEO
            | filters.Document.ALL
            | filters.AUDIO
            | filters.VOICE
            | filters.VIDEO_NOTE
            | filters.Sticker.ALL
            | filters.ANIMATION
        )
    )


def build_admin_app(token: str) -> Application:
    """ربات #۱ — همان که پنل کامل مدیریت (منو، مسیرها، ادمین‌ها) را دارد."""
    app = Application.builder().token(token).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", cmd_start),
            CallbackQueryHandler(on_button),
        ],
        states={
            ST_MAIN: [
                CallbackQueryHandler(on_button),
            ],
            ST_ADD_SRC: [
                MessageHandler(
                    filters.ChatType.PRIVATE & ~filters.COMMAND & (filters.TEXT | filters.FORWARDED),
                    recv_add_src,
                ),
                CommandHandler("cancel", cmd_cancel),
                CallbackQueryHandler(on_button),
            ],
            ST_ADD_TGT: [
                MessageHandler(
                    filters.ChatType.PRIVATE & ~filters.COMMAND & (filters.TEXT | filters.FORWARDED),
                    recv_add_tgt,
                ),
                CommandHandler("cancel", cmd_cancel),
                CallbackQueryHandler(on_button),
            ],
            ST_ADD_BOT: [
                CallbackQueryHandler(on_button),
                CommandHandler("cancel", cmd_cancel),
            ],
            ST_ADMIN_ADD: [
                MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, recv_admin_add),
                CommandHandler("cancel", cmd_cancel),
                CallbackQueryHandler(on_button),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
            CommandHandler("start", cmd_start),
        ],
        per_chat=True,
        allow_reentry=True,
    )

    app.add_handler(conv, group=0)
    app.add_handler(MessageHandler(build_forward_filter(), make_do_forward(bot_slot=1)), group=1)
    return app


def build_worker_app(token: str, bot_slot: int) -> Application:
    """ربات‌های #۲ تا #۶ — بدون پنل مدیریت، فقط فوروارد مسیرهای متعلق به همین ربات."""
    app = Application.builder().token(token).build()
    app.add_handler(MessageHandler(build_forward_filter(), make_do_forward(bot_slot=bot_slot)), group=1)
    return app


async def run_all_bots() -> None:
    apps: list[Application] = [build_admin_app(BOT_TOKENS[0])]
    for i, token in enumerate(BOT_TOKENS[1:], start=2):
        apps.append(build_worker_app(token, bot_slot=i))

    # هر ربات مسیر وبهوکِ خودش را دارد (بر پایه‌ی توکن خودش، که تصادفی و
    # حدس‌نزدنی است) و همه روی یک پورت مشترک (همان که Render می‌دهد) جواب
    # می‌دهند؛ یک وب‌سرور aiohttp این آپدیت‌ها را به Application درست هدایت می‌کند.
    web_app = web.Application()

    def make_handler(app: Application):
        async def handle(request: web.Request) -> web.Response:
            data = await request.json()
            update = Update.de_json(data, app.bot)
            await app.update_queue.put(update)
            return web.Response()
        return handle

    for i, app in enumerate(apps, start=1):
        await app.initialize()
        await app.start()
        path = f"/{app.bot.token}"
        web_app.router.add_post(path, make_handler(app))
        webhook_url = f"{WEBHOOK_URL}{path}"
        await app.bot.set_webhook(
            url=webhook_url,
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )
        log.info("✅ ربات #%s وبهوکش تنظیم شد و آماده‌ی دریافت است...", i)

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    log.info(
        "🚀 همه‌ی %s ربات با موفقیت اجرا شدند روی پورت %s (webhook mode | فقط ربات #۱ پنل مدیریت دارد)",
        len(apps), PORT,
    )

    try:
        await asyncio.Event().wait()  # تا وقتی سرویس زنده است، همینجا منتظر می‌مانیم
    finally:
        await runner.cleanup()
        for app in apps:
            try:
                await app.stop()
                await app.shutdown()
            except Exception as e:
                log.warning("خطا هنگام خاموش‌کردن یکی از ربات‌ها: %s", e)


def main() -> None:
    init_db()
    log.info(
        "🚀 Bot starting (Python 3.14 | PTB 22.7+ | Webhook mode | %s ربات پیکربندی‌شده)...",
        len(BOT_TOKENS),
    )
    asyncio.run(run_all_bots())


if __name__ == "__main__":
    main()
