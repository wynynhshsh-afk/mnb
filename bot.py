"""
╔══════════════════════════════════════════════════╗
║         ربات فورواردر دوطرفه  🤖                ║
║  python-telegram-bot 22.7+  |  Python 3.14+      ║
║  حالت اجرا: Webhook (مخصوص Render Web Service)   ║
╚══════════════════════════════════════════════════╝

متغیرهای محیطی اجباری (در Render تنظیم کنید):
  BOT_TOKEN     ← توکن از @BotFather
  ADMINS        ← آیدی عددی ادمین‌ها (با کاما جدا کنید اگر چند نفرند)
  WEBHOOK_URL   ← آدرس سرویس Render شما (مثل https://my-bot.onrender.com)
"""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
import threading
from dataclasses import dataclass

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
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

_admins_raw = os.environ.get("ADMINS", "").strip()
ADMINS: list[int] = [
    int(part) for part in _admins_raw.split(",") if part.strip().lstrip("-").isdigit()
]

WEBHOOK_URL: str = os.environ.get("WEBHOOK_URL", "").strip().rstrip("/")

# Render پورت واقعی را از طریق متغیر PORT تزریق می‌کند.
PORT: int = int(os.environ.get("PORT", "8443"))

DB_PATH: str = "settings.db"

if not BOT_TOKEN:
    log.critical("متغیر محیطی BOT_TOKEN تنظیم نشده است.")
    sys.exit(1)
if not ADMINS:
    log.critical("متغیر محیطی ADMINS تنظیم نشده یا نامعتبر است.")
    sys.exit(1)
if not WEBHOOK_URL:
    log.critical("متغیر محیطی WEBHOOK_URL تنظیم نشده است.")
    sys.exit(1)

# ════════════════════════════════════════════════
#  حالت‌های مکالمه
# ════════════════════════════════════════════════

(
    ST_MENU,        # انتخاب حالت فورواد
    ST_PANEL,       # پنل مدیریت
    ST_SRC,         # انتظار یوزرنیم منبع
    ST_TGT,         # انتظار یوزرنیم مقصد
) = range(4)

# ════════════════════════════════════════════════
#  مدل داده
# ════════════════════════════════════════════════

@dataclass(slots=True, frozen=True)
class Config:
    mode:   str         # "gtc" = گروه→چنل  |  "ctg" = چنل→گروه
    source: int | None
    target: int | None
    active: bool

    @property
    def ready(self) -> bool:
        return self.source is not None and self.target is not None

    @property
    def mode_label(self) -> str:
        return "گروه  →  چنل 📤" if self.mode == "gtc" else "چنل  →  گروه 📥"

# ════════════════════════════════════════════════
#  دیتابیس (thread-safe)
# ════════════════════════════════════════════════

_lock = threading.Lock()


def init_db() -> None:
    with _lock, sqlite3.connect(DB_PATH) as cx:
        cx.execute(
            """
            CREATE TABLE IF NOT EXISTS configs (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                mode    TEXT    NOT NULL UNIQUE,
                source  INTEGER,
                target  INTEGER,
                active  INTEGER DEFAULT 0
            )
            """
        )
        for m in ("gtc", "ctg"):
            cx.execute(
                "INSERT OR IGNORE INTO configs (mode, source, target, active) VALUES (?,NULL,NULL,0)",
                (m,),
            )
        cx.commit()


def db_get(mode: str) -> Config:
    with _lock, sqlite3.connect(DB_PATH) as cx:
        row = cx.execute(
            "SELECT source, target, active FROM configs WHERE mode=?", (mode,)
        ).fetchone()
    if row:
        return Config(mode=mode, source=row[0], target=row[1], active=bool(row[2]))
    return Config(mode=mode, source=None, target=None, active=False)


def db_set(mode: str, field: str, value: int | None) -> None:
    match field:
        case "source" | "target" | "active":
            pass
        case _:
            raise ValueError(f"Unknown field: {field!r}")
    with _lock, sqlite3.connect(DB_PATH) as cx:
        cx.execute(
            f"UPDATE configs SET {field}=? WHERE mode=?", (value, mode)
        )
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

# ════════════════════════════════════════════════
#  کیبورد و متن
#  نکته: style فقط سه مقدار معتبر دارد: primary (آبی)، success (سبز)،
#  danger (قرمز). از Bot API 9.4 (۹ فوریه ۲۰۲۶) و python-telegram-bot
#  نسخه 22.7+ پشتیبانی می‌شود. مقدار "secondary" معتبر نیست و حذف شده.
# ════════════════════════════════════════════════

def mode_select_kb() -> InlineKeyboardMarkup:
    """صفحه اول — انتخاب جهت فورواد"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 فورواد گروه  →  چنل", style="success", callback_data="mode_gtc")],
        [InlineKeyboardButton("📥 فورواد چنل  →  گروه", style="primary", callback_data="mode_ctg")],
    ])


def panel_kb(mode: str) -> InlineKeyboardMarkup:
    """پنل مدیریت هر حالت"""
    p = mode + ":"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("▶️ شروع فورواد",   style="success", callback_data=p + "start"),
            InlineKeyboardButton("⏹ توقف فورواد",    style="danger",  callback_data=p + "stop"),
        ],
        [
            InlineKeyboardButton("📥 تنظیم منبع",    style="primary", callback_data=p + "set_src"),
            InlineKeyboardButton("📤 تنظیم مقصد",    style="primary", callback_data=p + "set_tgt"),
        ],
        [
            InlineKeyboardButton("📊 وضعیت فورواد",                   callback_data=p + "status"),
            InlineKeyboardButton("🔙 بازگشت",                          callback_data="back"),
        ],
    ])


def reply_kb() -> ReplyKeyboardMarkup:
    """دکمه‌های پایین صفحه رنگی"""
    return ReplyKeyboardMarkup(
        [
            [
                KeyboardButton("▶️ شروع فورواد", style="success"),
                KeyboardButton("⏹ توقف فورواد",  style="danger"),
            ],
            [
                KeyboardButton("📥 تنظیم منبع",  style="primary"),
                KeyboardButton("📤 تنظیم مقصد",  style="primary"),
            ],
            [
                KeyboardButton("📊 وضعیت"),
                KeyboardButton("🔙 بازگشت"),
            ],
        ],
        resize_keyboard=True,
    )


def panel_text(cfg: Config) -> str:
    src    = f"`{cfg.source}`" if cfg.source else "─ تنظیم نشده"
    tgt    = f"`{cfg.target}`" if cfg.target else "─ تنظیم نشده"
    status = "✅ فعال"         if cfg.active  else "🔴 غیرفعال"
    src_lbl, tgt_lbl = (
        ("📥 گروه منبع",  "📤 چنل مقصد")  if cfg.mode == "gtc"
        else ("📥 چنل منبع", "📤 گروه مقصد")
    )
    return (
        f"╔══════════════════════╗\n"
        f"║  🎛  {cfg.mode_label:<18}║\n"
        f"╚══════════════════════╝\n\n"
        f"{src_lbl}:  {src}\n"
        f"{tgt_lbl}:   {tgt}\n"
        f"📡 *فورواد:*      {status}"
    )

# ════════════════════════════════════════════════
#  /start
# ════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_user.id not in ADMINS:
        await update.message.reply_text("❌ شما دسترسی ندارید")
        return ConversationHandler.END

    ctx.user_data.clear()
    await update.message.reply_text(
        "🤖 *ربات فورواردر دوطرفه*\n\n"
        "جهت فورواد را انتخاب کن:",
        reply_markup=mode_select_kb(),
        parse_mode="Markdown",
    )
    return ST_MENU

# ════════════════════════════════════════════════
#  هندلر دکمه‌های Inline
# ════════════════════════════════════════════════

async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q   = update.callback_query
    uid = q.from_user.id
    await q.answer()

    if uid not in ADMINS:
        await q.edit_message_text("❌ شما دسترسی ندارید")
        return ConversationHandler.END

    data = q.data

    # ── انتخاب حالت ───────────────────────────
    match data:
        case "mode_gtc" | "mode_ctg":
            mode = data.split("_")[1]
            ctx.user_data["mode"] = mode
            cfg = db_get(mode)
            await q.edit_message_text(
                panel_text(cfg),
                reply_markup=panel_kb(mode),
                parse_mode="Markdown",
            )
            # دکمه‌های پایین صفحه
            await q.message.reply_text(
                "از دکمه‌های پایین هم می‌تونی استفاده کنی:",
                reply_markup=reply_kb(),
            )
            return ST_PANEL

        case "back":
            ctx.user_data.pop("mode", None)
            await q.edit_message_text(
                "🤖 *ربات فورواردر دوطرفه*\n\nجهت فورواد را انتخاب کن:",
                reply_markup=mode_select_kb(),
                parse_mode="Markdown",
            )
            return ST_MENU

    # ── دکمه‌های پنل — فرمت: {mode}:{action} ─
    if ":" not in data:
        return ST_PANEL

    mode, action = data.split(":", 1)
    ctx.user_data["mode"] = mode
    cfg = db_get(mode)

    match action:

        case "status":
            await q.edit_message_text(
                "📊 *وضعیت فعلی*\n\n" + panel_text(cfg),
                reply_markup=panel_kb(mode),
                parse_mode="Markdown",
            )

        case "start":
            match (cfg.source, cfg.target):
                case (None, _):
                    await q.answer("⚠️ ابتدا منبع را تنظیم کنید", show_alert=True)
                    return ST_PANEL
                case (_, None):
                    await q.answer("⚠️ ابتدا مقصد را تنظیم کنید", show_alert=True)
                    return ST_PANEL
            db_set(mode, "active", 1)
            cfg = db_get(mode)
            await q.edit_message_text(
                "✅ *فورواد فعال شد!*\n\n" + panel_text(cfg),
                reply_markup=panel_kb(mode),
                parse_mode="Markdown",
            )
            log.info("▶ [%s] Forwarding STARTED by admin %s", mode, uid)

        case "stop":
            db_set(mode, "active", 0)
            cfg = db_get(mode)
            await q.edit_message_text(
                "⏹ *فورواد متوقف شد*\n\n" + panel_text(cfg),
                reply_markup=panel_kb(mode),
                parse_mode="Markdown",
            )
            log.info("■ [%s] Forwarding STOPPED by admin %s", mode, uid)

        case "set_src":
            src_type = "گروه یا سوپرگروه" if mode == "gtc" else "چنل"
            await q.edit_message_text(
                f"📥 *تنظیم منبع ({src_type})*\n\n"
                f"یوزرنیم {src_type} را ارسال کن\n\n"
                "فرمت‌های قابل قبول:\n"
                "`@username`\n"
                "`t.me/username`\n"
                "`https://t.me/username`\n\n"
                "برای انصراف /cancel بزن",
                parse_mode="Markdown",
            )
            return ST_SRC

        case "set_tgt":
            tgt_type = "چنل" if mode == "gtc" else "گروه یا سوپرگروه"
            await q.edit_message_text(
                f"📤 *تنظیم مقصد ({tgt_type})*\n\n"
                f"یوزرنیم {tgt_type} را ارسال کن\n\n"
                "فرمت‌های قابل قبول:\n"
                "`@username`\n"
                "`t.me/username`\n"
                "`https://t.me/username`\n\n"
                + ("⚠️ ربات باید ادمین چنل باشد\n\n" if mode == "gtc" else "⚠️ ربات باید عضو گروه باشد\n\n")
                + "برای انصراف /cancel بزن",
                parse_mode="Markdown",
            )
            return ST_TGT

    return ST_PANEL

# ════════════════════════════════════════════════
#  هندلر دکمه‌های Reply Keyboard
# ════════════════════════════════════════════════

async def on_reply_kb(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    uid  = update.effective_user.id
    text = update.message.text
    mode = ctx.user_data.get("mode")

    if uid not in ADMINS or not mode:
        return ST_PANEL

    cfg = db_get(mode)

    match True:
        case _ if "شروع فورواد" in text:
            match (cfg.source, cfg.target):
                case (None, _):
                    await update.message.reply_text("⚠️ ابتدا منبع را تنظیم کن")
                    return ST_PANEL
                case (_, None):
                    await update.message.reply_text("⚠️ ابتدا مقصد را تنظیم کن")
                    return ST_PANEL
            db_set(mode, "active", 1)
            cfg = db_get(mode)
            await update.message.reply_text(
                "✅ *فورواد فعال شد!*\n\n" + panel_text(cfg),
                reply_markup=panel_kb(mode),
                parse_mode="Markdown",
            )
            log.info("▶ [%s] STARTED", mode)

        case _ if "توقف فورواد" in text:
            db_set(mode, "active", 0)
            cfg = db_get(mode)
            await update.message.reply_text(
                "⏹ *فورواد متوقف شد*\n\n" + panel_text(cfg),
                reply_markup=panel_kb(mode),
                parse_mode="Markdown",
            )
            log.info("■ [%s] STOPPED", mode)

        case _ if "تنظیم منبع" in text:
            src_type = "گروه یا سوپرگروه" if mode == "gtc" else "چنل"
            await update.message.reply_text(
                f"📥 یوزرنیم {src_type} را ارسال کن\n(مثال: @username یا t.me/username)\n\n/cancel برای انصراف"
            )
            return ST_SRC

        case _ if "تنظیم مقصد" in text:
            tgt_type = "چنل" if mode == "gtc" else "گروه یا سوپرگروه"
            await update.message.reply_text(
                f"📤 یوزرنیم {tgt_type} را ارسال کن\n(مثال: @username یا t.me/username)\n\n/cancel برای انصراف"
            )
            return ST_TGT

        case _ if "وضعیت" in text:
            await update.message.reply_text(
                "📊 *وضعیت فعلی*\n\n" + panel_text(cfg),
                reply_markup=panel_kb(mode),
                parse_mode="Markdown",
            )

        case _ if "بازگشت" in text:
            ctx.user_data.pop("mode", None)
            await update.message.reply_text(
                "🤖 *ربات فورواردر دوطرفه*\n\nجهت فورواد را انتخاب کن:",
                reply_markup=mode_select_kb(),
                parse_mode="Markdown",
            )
            return ST_MENU

    return ST_PANEL

# ════════════════════════════════════════════════
#  دریافت و اعتبارسنجی منبع
# ════════════════════════════════════════════════

async def recv_src(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    mode = ctx.user_data.get("mode")
    if not mode:
        return ConversationHandler.END

    # اگر دکمه منو زده شد
    if any(kw in update.message.text for kw in ["شروع", "توقف", "تنظیم", "وضعیت", "بازگشت"]):
        return await on_reply_kb(update, ctx)

    username = normalize(update.message.text)
    if not username or username == "@":
        await update.message.reply_text(
            "❌ یوزرنیم نامعتبر است\n\n"
            "مثال: `@mygroup` یا `t.me/mygroup`\n\n"
            "دوباره ارسال کن یا /cancel بزن",
            parse_mode="Markdown",
        )
        return ST_SRC

    try:
        chat = await ctx.bot.get_chat(username)
    except Exception as e:
        log.warning("get_chat failed for %r: %s", username, e)
        await update.message.reply_text(
            "❌ چت پیدا نشد!\n\n"
            "• یوزرنیم را چک کن\n"
            "• مطمئن شو ربات عضو آن است\n\n"
            "دوباره ارسال کن یا /cancel بزن"
        )
        return ST_SRC

    # اعتبارسنجی نوع برای منبع
    match mode:
        case "gtc":  # منبع باید گروه باشد
            match chat.type:
                case "group" | "supergroup":
                    pass
                case _:
                    await update.message.reply_text("❌ این گروه نیست! یوزرنیم یک گروه یا سوپرگروه وارد کن")
                    return ST_SRC
        case "ctg":  # منبع باید چنل باشد
            if chat.type != "channel":
                await update.message.reply_text("❌ این چنل نیست! یوزرنیم یک چنل وارد کن")
                return ST_SRC

    db_set(mode, "source", chat.id)
    cfg = db_get(mode)
    log.info("[%s] Source → %s (%s)", mode, chat.title, chat.id)
    await update.message.reply_text(
        f"✅ منبع «*{chat.title}*» با موفقیت وصل شد 🎉\n\n" + panel_text(cfg),
        reply_markup=panel_kb(mode),
        parse_mode="Markdown",
    )
    return ST_PANEL

# ════════════════════════════════════════════════
#  دریافت و اعتبارسنجی مقصد
# ════════════════════════════════════════════════

async def recv_tgt(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    mode = ctx.user_data.get("mode")
    if not mode:
        return ConversationHandler.END

    # اگر دکمه منو زده شد
    if any(kw in update.message.text for kw in ["شروع", "توقف", "تنظیم", "وضعیت", "بازگشت"]):
        return await on_reply_kb(update, ctx)

    username = normalize(update.message.text)
    if not username or username == "@":
        await update.message.reply_text(
            "❌ یوزرنیم نامعتبر است\n\n"
            "مثال: `@mychannel` یا `t.me/mychannel`\n\n"
            "دوباره ارسال کن یا /cancel بزن",
            parse_mode="Markdown",
        )
        return ST_TGT

    try:
        chat = await ctx.bot.get_chat(username)
    except Exception as e:
        log.warning("get_chat failed for %r: %s", username, e)
        await update.message.reply_text(
            "❌ چت پیدا نشد!\n\n"
            "• یوزرنیم را چک کن\n"
            "• مطمئن شو ربات عضو/ادمین آن است\n\n"
            "دوباره ارسال کن یا /cancel بزن"
        )
        return ST_TGT

    # اعتبارسنجی نوع برای مقصد
    match mode:
        case "gtc":  # مقصد باید چنل باشد
            if chat.type != "channel":
                await update.message.reply_text("❌ این چنل نیست! یوزرنیم یک چنل وارد کن")
                return ST_TGT
            # چک ادمین بودن ربات در چنل
            try:
                me = await ctx.bot.get_chat_member(chat.id, ctx.bot.id)
                match me.status:
                    case "administrator" | "creator":
                        pass
                    case _:
                        raise PermissionError
            except Exception:
                await update.message.reply_text(
                    "❌ ربات ادمین چنل نیست!\n\n"
                    "۱. ربات را به چنل اضافه کن\n"
                    "۲. دسترسی ادمین بده\n"
                    "۳. دوباره یوزرنیم را ارسال کن"
                )
                return ST_TGT

        case "ctg":  # مقصد باید گروه باشد
            match chat.type:
                case "group" | "supergroup":
                    pass
                case _:
                    await update.message.reply_text("❌ این گروه نیست! یوزرنیم یک گروه یا سوپرگروه وارد کن")
                    return ST_TGT

    db_set(mode, "target", chat.id)
    cfg = db_get(mode)
    log.info("[%s] Target → %s (%s)", mode, chat.title, chat.id)
    await update.message.reply_text(
        f"✅ مقصد «*{chat.title}*» با موفقیت وصل شد 🎉\n\n" + panel_text(cfg),
        reply_markup=panel_kb(mode),
        parse_mode="Markdown",
    )
    return ST_PANEL

# ════════════════════════════════════════════════
#  لغو
# ════════════════════════════════════════════════

async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_user.id not in ADMINS:
        return ConversationHandler.END
    mode = ctx.user_data.get("mode")
    if mode:
        cfg = db_get(mode)
        await update.message.reply_text(
            "🚫 عملیات لغو شد\n\n" + panel_text(cfg),
            reply_markup=panel_kb(mode),
            parse_mode="Markdown",
        )
        return ST_PANEL
    await update.message.reply_text(
        "🚫 عملیات لغو شد\n\nجهت فورواد را انتخاب کن:",
        reply_markup=mode_select_kb(),
        parse_mode="Markdown",
    )
    return ST_MENU

# ════════════════════════════════════════════════
#  فورواد پیام‌ها (هر دو جهت)
# ════════════════════════════════════════════════

async def do_forward(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message or update.channel_post
    if msg is None:
        return

    chat_id = msg.chat_id

    for mode in ("gtc", "ctg"):
        cfg = db_get(mode)
        if not cfg.active or not cfg.ready:
            continue
        if chat_id != cfg.source:
            continue
        try:
            await ctx.bot.forward_message(
                chat_id=cfg.target,
                from_chat_id=chat_id,
                message_id=msg.message_id,
            )
            log.info("📨 [%s] msg#%s  %s → %s", mode, msg.message_id, cfg.source, cfg.target)
        except Exception as e:
            log.error("❌ [%s] Forward failed msg#%s: %s", mode, msg.message_id, e)

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
            ST_MENU: [
                CallbackQueryHandler(on_button),
            ],
            ST_PANEL: [
                CallbackQueryHandler(on_button),
                MessageHandler(
                    filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
                    on_reply_kb,
                ),
            ],
            ST_SRC: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_src),
                CommandHandler("cancel", cmd_cancel),
                CallbackQueryHandler(on_button),
            ],
            ST_TGT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_tgt),
                CommandHandler("cancel", cmd_cancel),
                CallbackQueryHandler(on_button),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
            CommandHandler("start",  cmd_start),
        ],
        per_chat=True,
        allow_reentry=True,
    )

    app.add_handler(conv,                                    group=0)
    app.add_handler(MessageHandler(fwd_filter, do_forward),  group=1)

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
