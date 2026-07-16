"""
╔══════════════════════════════════════════════════╗
║      ربات فورواردر چندمسیره (نامحدود) 🤖         ║
║  python-telegram-bot 22.7+  |  Python 3.14+      ║
║  حالت اجرا: Webhook (مخصوص Render Web Service)   ║
╚══════════════════════════════════════════════════╝

متغیرهای محیطی اجباری (در Render تنظیم کنید):
  BOT_TOKEN     ← توکن از @BotFather
  ADMINS        ← آیدی عددی مالکان اصلی ربات (با کاما جدا کنید اگر چند نفرند)
  WEBHOOK_URL   ← آدرس سرویس Render شما (مثل https://my-bot.onrender.com)
  DATABASE_URL  ← کانکشن‌استرینگ دیتابیس Postgres سوپابیس (دائمی)
                  از Supabase: Project Settings → Database → Connection string → URI
                  (پیشنهاد: از حالت "Connection pooling" با پورت 6543 استفاده کنید،
                  چون Render معمولاً IPv6 مستقیم به دیتابیس را پشتیبانی نمی‌کند)

نکته: افرادی که در ADMINS قرار می‌گیرند «مالک» ربات هستند و همیشه دسترسی کامل
دارند. مالک‌ها می‌توانند از داخل ربات ادمین‌های دیگر اضافه کنند و برای هرکدام
دسترسی دلخواه (مثلاً فقط فوروارد گروه‌به‌گروه) تعیین کنند.
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field

import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
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

BOT_TOKEN: str = os.environ.get("BOT_TOKEN", "").strip()

_owners_raw = os.environ.get("ADMINS", "").strip()
OWNERS: list[int] = [
    int(part) for part in _owners_raw.split(",") if part.strip().lstrip("-").isdigit()
]

WEBHOOK_URL: str = os.environ.get("WEBHOOK_URL", "").strip().rstrip("/")

# Render پورت واقعی را از طریق متغیر PORT تزریق می‌کند.
PORT: int = int(os.environ.get("PORT", "8443"))

DATABASE_URL: str = os.environ.get("DATABASE_URL", "").strip()

if not BOT_TOKEN:
    log.critical("متغیر محیطی BOT_TOKEN تنظیم نشده است.")
    sys.exit(1)
if not OWNERS:
    log.critical("متغیر محیطی ADMINS تنظیم نشده یا نامعتبر است.")
    sys.exit(1)
if not WEBHOOK_URL:
    log.critical("متغیر محیطی WEBHOOK_URL تنظیم نشده است.")
    sys.exit(1)
if not DATABASE_URL:
    log.critical(
        "متغیر محیطی DATABASE_URL تنظیم نشده است. "
        "این باید Connection String دیتابیس Postgres سوپابیس شما باشد "
        "(Project Settings → Database → Connection string → URI، حالت Session/Transaction pooler توصیه می‌شود)."
    )
    sys.exit(1)

# ════════════════════════════════════════════════
#  حالت‌های مکالمه
# ════════════════════════════════════════════════

(
    ST_MAIN,        # منوی اصلی / همه‌ی ناوبری با دکمه (callback)
    ST_ADD_SRC,     # انتظار یوزرنیم منبع مسیر جدید
    ST_ADD_TGT,     # انتظار یوزرنیم مقصد مسیر جدید
    ST_ADMIN_ADD,   # انتظار آیدی عددی ادمین جدید
) = range(4)

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
#  دیتابیس (thread-safe)
# ════════════════════════════════════════════════

# ════════════════════════════════════════════════
#  دیتابیس (Supabase Postgres، دائمی — thread-safe)
# ════════════════════════════════════════════════

_pool = ThreadedConnectionPool(minconn=1, maxconn=10, dsn=DATABASE_URL)


class _CompatCursor:
    """
    یک لایه‌ی نازک روی cursor پستگرس که همان الگوی «cx.execute(query, params)»ی
    که در sqlite3 استفاده می‌شد را حفظ می‌کند (از جمله علامت‌سوال به‌عنوان
    جای‌خالی پارامتر) تا بقیه‌ی کد بدون تغییرِ زیاد کار کند.
    """

    def __init__(self, conn):
        self._conn = conn
        self._cur = conn.cursor()

    def execute(self, query: str, params: tuple = ()):
        self._cur.execute(query.replace("?", "%s"), params)
        return self

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def commit(self):
        # کامیت واقعی توسط خودِ context manager انجام می‌شود؛ این متد فقط
        # برای سازگاری با کدی نگه داشته شده که صریحاً cx.commit() صدا می‌زند.
        self._conn.commit()


@contextmanager
def get_conn():
    raw = _pool.getconn()
    try:
        wrapper = _CompatCursor(raw)
        yield wrapper
        raw.commit()
    except Exception:
        raw.rollback()
        raise
    finally:
        _pool.putconn(raw)


def init_db() -> None:
    with get_conn() as cx:
        cx.execute(
            """
            CREATE TABLE IF NOT EXISTS admins (
                user_id     BIGINT PRIMARY KEY,
                is_owner    INTEGER DEFAULT 0,
                permissions TEXT    DEFAULT '',
                added_by    BIGINT
            )
            """
        )
        cx.execute(
            """
            CREATE TABLE IF NOT EXISTS rules (
                id            BIGSERIAL PRIMARY KEY,
                source_id     BIGINT NOT NULL,
                source_title  TEXT,
                source_kind   TEXT NOT NULL,
                target_id     BIGINT NOT NULL,
                target_title  TEXT,
                target_kind   TEXT NOT NULL,
                direction     TEXT NOT NULL,
                active        INTEGER DEFAULT 0,
                created_by    BIGINT,
                UNIQUE(source_id, target_id)
            )
            """
        )
        all_perms_csv = ",".join(ALL_PERMS)
        for uid in OWNERS:
            cx.execute(
                "INSERT INTO admins (user_id, is_owner, permissions, added_by) "
                "VALUES (?, 1, ?, NULL) ON CONFLICT (user_id) DO NOTHING",
                (uid, all_perms_csv),
            )
            # مالک‌های تعریف‌شده در ENV همیشه دسترسی کامل دارند، حتی اگر قبلاً
            # به شکل دیگری در دیتابیس ثبت شده باشند.
            cx.execute(
                "UPDATE admins SET is_owner=1, permissions=? WHERE user_id=?",
                (all_perms_csv, uid),
            )
        cx.commit()

# ── ادمین‌ها ──────────────────────────────────────

def get_admin(uid: int) -> AdminInfo | None:
    with get_conn() as cx:
        row = cx.execute(
            "SELECT user_id, is_owner, permissions, added_by FROM admins WHERE user_id=?",
            (uid,),
        ).fetchone()
    if not row:
        return None
    perms = {p for p in row[2].split(",") if p}
    return AdminInfo(user_id=row[0], is_owner=bool(row[1]), permissions=perms, added_by=row[3])


def is_admin(uid: int) -> bool:
    return get_admin(uid) is not None


def has_perm(uid: int, perm: str) -> bool:
    info = get_admin(uid)
    return bool(info and info.has(perm))


def list_admins() -> list[AdminInfo]:
    with get_conn() as cx:
        rows = cx.execute(
            "SELECT user_id, is_owner, permissions, added_by FROM admins "
            "ORDER BY is_owner DESC, user_id ASC"
        ).fetchall()
    out = []
    for r in rows:
        perms = {p for p in r[2].split(",") if p}
        out.append(AdminInfo(user_id=r[0], is_owner=bool(r[1]), permissions=perms, added_by=r[3]))
    return out


def add_admin(uid: int, added_by: int) -> None:
    with get_conn() as cx:
        cx.execute(
            "INSERT INTO admins (user_id, is_owner, permissions, added_by) "
            "VALUES (?, 0, '', ?) ON CONFLICT (user_id) DO NOTHING",
            (uid, added_by),
        )
        cx.commit()


def remove_admin(uid: int) -> None:
    with get_conn() as cx:
        cx.execute("DELETE FROM admins WHERE user_id=? AND is_owner=0", (uid,))
        cx.commit()


def toggle_admin_perm(uid: int, perm: str) -> None:
    info = get_admin(uid)
    if info is None or info.is_owner:
        return
    perms = set(info.permissions)
    if perm in perms:
        perms.discard(perm)
    else:
        perms.add(perm)
    with get_conn() as cx:
        cx.execute(
            "UPDATE admins SET permissions=? WHERE user_id=?",
            (",".join(sorted(perms)), uid),
        )
        cx.commit()

# ── مسیرهای فوروارد ───────────────────────────────

def _row_to_rule(row) -> Rule:
    return Rule(
        id=row[0], source_id=row[1], source_title=row[2] or "—", source_kind=row[3],
        target_id=row[4], target_title=row[5] or "—", target_kind=row[6],
        direction=row[7], active=bool(row[8]), created_by=row[9],
    )


_RULE_COLS = "id, source_id, source_title, source_kind, target_id, target_title, target_kind, direction, active, created_by"


def add_rule(source_id: int, source_title: str, source_kind: str,
             target_id: int, target_title: str, target_kind: str,
             direction: str, created_by: int) -> int | None:
    try:
        with get_conn() as cx:
            cur = cx.execute(
                "INSERT INTO rules (source_id, source_title, source_kind, target_id, "
                "target_title, target_kind, direction, active, created_by) "
                "VALUES (?,?,?,?,?,?,?,0,?) RETURNING id",
                (source_id, source_title, source_kind, target_id, target_title, target_kind,
                 direction, created_by),
            )
            row = cur.fetchone()
            cx.commit()
            return row[0] if row else None
    except psycopg2.IntegrityError:
        return None  # این مسیر از قبل وجود دارد


def get_rule(rule_id: int) -> Rule | None:
    with get_conn() as cx:
        row = cx.execute(f"SELECT {_RULE_COLS} FROM rules WHERE id=?", (rule_id,)).fetchone()
    return _row_to_rule(row) if row else None


def list_rules() -> list[Rule]:
    with get_conn() as cx:
        rows = cx.execute(f"SELECT {_RULE_COLS} FROM rules ORDER BY id ASC").fetchall()
    return [_row_to_rule(r) for r in rows]


def rules_for_source(source_id: int) -> list[Rule]:
    with get_conn() as cx:
        rows = cx.execute(
            f"SELECT {_RULE_COLS} FROM rules WHERE active=1 AND source_id=?", (source_id,)
        ).fetchall()
    return [_row_to_rule(r) for r in rows]


def set_rule_active(rule_id: int, active: bool) -> None:
    with get_conn() as cx:
        cx.execute("UPDATE rules SET active=? WHERE id=?", (1 if active else 0, rule_id))
        cx.commit()


def delete_rule(rule_id: int) -> None:
    with get_conn() as cx:
        cx.execute("DELETE FROM rules WHERE id=?", (rule_id,))
        cx.commit()

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
    return f"{status} #{r.id} · {r.source_title} → {r.target_title}"


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

    # چک ادمین بودن ربات در مقصد در صورتی که چنل باشد
    if tgt_kind == "channel":
        try:
            me = await ctx.bot.get_chat_member(chat.id, ctx.bot.id)
            if me.status not in ("administrator", "creator"):
                raise PermissionError
        except Exception:
            await update.message.reply_text(
                "❌ ربات ادمین این چنل نیست!\n\n۱. ربات را به چنل اضافه کن\n۲. دسترسی ادمین بده\n۳. دوباره یوزرنیم را ارسال کن"
            )
            return ST_ADD_TGT
    else:
        try:
            await ctx.bot.get_chat_member(chat.id, ctx.bot.id)
        except Exception:
            await update.message.reply_text(
                "❌ ربات عضو این گروه نیست!\n\n۱. ربات را به گروه اضافه کن\n۲. دوباره یوزرنیم را ارسال کن"
            )
            return ST_ADD_TGT

    rule_id = add_rule(
        source_id=new_rule["source_id"], source_title=new_rule["source_title"], source_kind=new_rule["source_kind"],
        target_id=chat.id, target_title=chat.title or str(chat.id), target_kind=tgt_kind,
        direction=direction, created_by=uid,
    )
    ctx.user_data.pop("new_rule", None)

    if rule_id is None:
        await update.message.reply_text("⚠️ این مسیر (همین منبع و مقصد) از قبل وجود دارد")
        text, kb = rules_list_kb(0)
        await update.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")
        return ST_MAIN

    r = get_rule(rule_id)
    log.info("🆕 [%s] Rule #%s created: %s → %s", direction, rule_id, r.source_id, r.target_id)
    await update.message.reply_text(
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

async def do_forward(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message or update.channel_post
    if msg is None:
        return

    chat_id = msg.chat_id
    for r in rules_for_source(chat_id):
        try:
            await ctx.bot.forward_message(
                chat_id=r.target_id,
                from_chat_id=chat_id,
                message_id=msg.message_id,
            )
            log.info("📨 [%s] msg#%s  %s → %s (rule #%s)", r.direction, msg.message_id, r.source_id, r.target_id, r.id)
        except Exception as e:
            log.error("❌ [rule #%s] Forward failed msg#%s: %s", r.id, msg.message_id, e)

# ════════════════════════════════════════════════
#  اجرا (Webhook - مخصوص Render Web Service)
# ════════════════════════════════════════════════

def main() -> None:
    init_db()
    log.info("🚀 Bot starting (Python 3.14 | PTB 22.7+ | Webhook mode)...")

    app = Application.builder().token(BOT_TOKEN).build()

    fwd_filter = (
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
    app.add_handler(MessageHandler(fwd_filter, do_forward), group=1)

    webhook_path = BOT_TOKEN
    full_webhook_url = f"{WEBHOOK_URL}/{webhook_path}"

    log.info("✅ Bot is running (webhook on port %s)...", PORT)
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=webhook_path,
        webhook_url=full_webhook_url,
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
