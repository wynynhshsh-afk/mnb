# panel_api.py
# ─────────────────────────────────────────────────────────────────────────────
# API داخلیِ پنلِ مدیریتِ سلف — پُلِ ارتباطی بینِ «سلف» (این پروژه) و «ربات
# کمکی» (helper_bot) که حالا روی یک هاستِ کاملاً جدا اجرا می‌شه.
#
# چرا این فایل لازم شد؟
# ربات کمکی قبلاً مستقیماً (توی همون پروسس) به کلاینتِ زنده‌ی سلفِ هر کاربر
# و به توابع bot.py دسترسی داشت. حالا که روی هاستِ دیگه‌ای اجرا می‌شه، دیگه
# به اون حافظه دسترسی نداره؛ پس هر کاری که قبلاً با یک فراخوانیِ مستقیمِ
# پایتون انجام می‌شد (مثلاً «این دستور رو روی سلفِ کاربر Y اجرا کن»)، حالا
# باید از طریقِ یک درخواستِ HTTP به این API انجام بشه.
#
# امنیت: همه‌ی مسیرها به یک هدر مخفیِ مشترک (X-Panel-Secret) نیاز دارن که
# باید دقیقاً با config.PANEL_API_SECRET یکی باشه. این مقدار رو هم توی
# پروژه‌ی سلف و هم توی پروژه‌ی ربات کمکی (به‌عنوان متغیر محیطی) ست کن.
# ─────────────────────────────────────────────────────────────────────────────

import asyncio
import base64
import io

from flask import Blueprint, request, jsonify

import config
import database as db

panel_api_bp = Blueprint("panel_api", __name__, url_prefix="/internal/panel")


def _unauthorized():
    return jsonify({"ok": False, "error": "unauthorized"}), 401


def _check_secret() -> bool:
    if not config.PANEL_API_SECRET:
        # اگه سکرت تنظیم نشده باشه، از روی احتیاط کل API رو غیرفعال می‌کنیم
        # (نه اینکه بدونِ محافظت بازش بذاریم).
        return False
    given = request.headers.get("X-Panel-Secret", "")
    return given == config.PANEL_API_SECRET


@panel_api_bp.before_request
def _guard():
    if not _check_secret():
        return _unauthorized()


def _run_coro(coro):
    """
    یک کوروتینِ async رو روی همون event loopِ اصلیِ برنامه (که bot.py و
    self-clientها روش اجرا می‌شن) اجرا می‌کنه و منتظرِ نتیجه می‌مونه.
    چون خودِ Flask توی یک ترد معمولیِ sync اجرا می‌شه، نمی‌تونیم مستقیم
    await کنیم؛ باید از طریقِ run_coroutine_threadsafe به لوپِ اصلی بفرستیمش.
    """
    from app import get_loop  # import دیرهنگام تا از circular import جلوگیری بشه
    loop = get_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=30)


@panel_api_bp.route("/owner_by_tg", methods=["POST"])
def owner_by_tg():
    """
    از روی آیدیِ عددیِ تلگرامِ کسی که به ربات کمکی وصل شده، owner_id و اینکه
    آیا سلفش الان واقعاً در حال اجراست رو برمی‌گردونه.
    """
    from bot import bot_manager

    data = request.get_json(force=True) or {}
    tg_id = data.get("tg_id")
    if tg_id is None:
        return jsonify({"ok": False, "error": "tg_id لازم است"}), 400

    owner_id, entry = bot_manager.get_owner_by_tg_id(int(tg_id))
    if owner_id is None:
        return jsonify({"ok": True, "found": False})

    return jsonify({"ok": True, "found": True, "owner_id": owner_id})


@panel_api_bp.route("/profile_banner", methods=["POST"])
def profile_banner():
    """
    اطلاعاتِ نمایشیِ سلف (اسم، یوزرنیم) + عکسِ بنرِ آماده (base64) رو برمی‌گردونه.
    عکسِ پروفایل از روی کلاینتِ زنده‌ی سلف دانلود و بنرش همینجا (روی هاستِ
    سلف) ساخته می‌شه، چون فقط این هاست به کلاینتِ زنده و فونت‌ها دسترسی داره.
    """
    from bot import bot_manager

    data = request.get_json(force=True) or {}
    owner_id = data.get("owner_id")
    if owner_id is None:
        return jsonify({"ok": False, "error": "owner_id لازم است"}), 400

    self_client = bot_manager.get_client(int(owner_id))
    if self_client is None:
        return jsonify({"ok": False, "error": "سلف فعالی برای این کاربر پیدا نشد"}), 404

    async def _fetch():
        me = await self_client.get_me()
        full_name = " ".join(p for p in [me.first_name, me.last_name] if p)
        display_name = full_name or "بدون نام"
        username = me.username or ""

        photo_b64 = None
        try:
            raw_buf = io.BytesIO()
            photo = await self_client.download_profile_photo(me, file=raw_buf)
            if photo:
                raw_buf.seek(0)
                from banner import generate_banner
                banner_bytes = generate_banner(
                    raw_buf.read(),
                    bottom_text="self panel",
                    bottom_sub=f"@{username}" if username else "",
                )
                photo_b64 = base64.b64encode(banner_bytes).decode("ascii")
        except Exception:
            photo_b64 = None

        return display_name, username, photo_b64

    display_name, username, photo_b64 = _run_coro(_fetch())
    return jsonify({
        "ok": True,
        "display_name": display_name,
        "username": username,
        "photo_b64": photo_b64,
    })


@panel_api_bp.route("/force_join_info", methods=["POST"])
def force_join_info():
    from bot import _get_force_join_channels

    data = request.get_json(force=True) or {}
    owner_id = data.get("owner_id")
    if owner_id is None:
        return jsonify({"ok": False, "error": "owner_id لازم است"}), 400

    join_msg = db.get_setting(
        int(owner_id), "force_join_message",
        "⛔ برای ارسال پیام ابتدا باید در کانال‌های زیر عضو شوید.",
    )
    channels = _get_force_join_channels(int(owner_id))
    return jsonify({"ok": True, "message": join_msg, "channels": channels})


@panel_api_bp.route("/categories", methods=["GET"])
def categories():
    """
    کلِ ساختارِ ثابتِ PANEL_CATEGORIES + ترتیبِ منوی اصلی رو یک‌جا برمی‌گردونه
    تا ربات کمکی مجبور نباشه این تعریف‌های بزرگ رو دوباره (و به‌صورتِ
    دستی/جدا از منبعِ اصلی) نگه‌داری کنه. ربات کمکی این رو موقعِ استارت
    (و هر چند دقیقه یک‌بار برای رفرش) می‌گیره و کش می‌کنه.
    """
    from bot import PANEL_CATEGORIES, PANEL_CATEGORY_ORDER

    return jsonify({
        "ok": True,
        "order": PANEL_CATEGORY_ORDER,
        "categories": PANEL_CATEGORIES,
    })


@panel_api_bp.route("/category_commands", methods=["POST"])
def category_commands():
    from bot import build_category_commands

    data = request.get_json(force=True) or {}
    owner_id = data.get("owner_id")
    category_key = data.get("category_key")
    if owner_id is None or not category_key:
        return jsonify({"ok": False, "error": "owner_id و category_key لازم است"}), 400

    items = build_category_commands(int(owner_id), category_key)
    return jsonify({"ok": True, "items": items})


@panel_api_bp.route("/execute", methods=["POST"])
def execute():
    """دستورِ متنیِ متناظر با دکمه‌ی کلیک‌شده رو روی کلاینتِ زنده‌ی سلفِ کاربر اجرا می‌کنه."""
    from bot import bot_manager, _execute_panel_command

    data = request.get_json(force=True) or {}
    owner_id = data.get("owner_id")
    command_text = data.get("command_text")
    if owner_id is None or not command_text:
        return jsonify({"ok": False, "error": "owner_id و command_text لازم است"}), 400

    self_client = bot_manager.get_client(int(owner_id))
    if self_client is None:
        return jsonify({"ok": False, "error": "سلف فعالی برای این کاربر پیدا نشد"}), 404

    _run_coro(_execute_panel_command(self_client, int(owner_id), command_text))
    return jsonify({"ok": True})
