import asyncio
import os
import threading
import time
from functools import wraps
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    FloodWaitError,
)
import database_supabase as db
import db_cache as cache
import config
from bot import bot_manager

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
app.config["PERMANENT_SESSION_LIFETIME"] = 180  # ۳ دقیقه بی‌کاری → لاگ‌اوت خودکار


def _ensure_helper_bot():
    """
    اگه ربات کمکیِ پنل به هر دلیلی (خطای شبکه‌ای موقت موقع بالا اومدنِ سرور،
    قطعی لحظه‌ای و ...) وصل نباشه، بدون نیاز به ری‌استارتِ هاست دوباره
    وصلش می‌کنه. صدا زدنش امن و بی‌ضرره چون start_helper_bot() اگه از قبل
    سالم باشه فوراً برمی‌گرده. عمداً fire-and-forget هست تا مسیر
    ثبت‌نام/لاگین کاربر رو بلاک نکنه.
    """
    if not config.HELPER_BOT_TOKEN:
        return
    try:
        from helper_bot import start_helper_bot
        asyncio.run_coroutine_threadsafe(start_helper_bot(), get_loop())
    except Exception as e:
        print(f"⚠️ خطا در تلاش برای اتصال ربات کمکی: {e}")


@app.errorhandler(500)
def server_error(e):
    return jsonify({"ok": False, "error": f"خطای داخلی سرور: {str(e)}"}), 500


@app.errorhandler(Exception)
def unhandled_exception(e):
    return jsonify({"ok": False, "error": f"خطای غیرمنتظره: {str(e)}"}), 500


# ─── event loop جداگانه برای Telethon ────────────────────────────────────────
_loop = None
_login_clients = {}   # {owner_id: TelegramClient} برای فرایند لاگین
_phone_hashes = {}    # {owner_id: phone_code_hash}
_phone_numbers = {}   # {owner_id: phone}


def get_loop():
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
        t = threading.Thread(target=_loop.run_forever, daemon=True)
        t.start()
    return _loop


def run_async(coro):
    return asyncio.run_coroutine_threadsafe(coro, get_loop()).result(timeout=30)


# ─── احراز هویت پنل ───────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("owner_id"):
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "وارد نشده‌اید"}), 401
            return redirect(url_for("panel_login_page"))
        return f(*args, **kwargs)
    return decorated


def owner_id() -> int:
    return int(session["owner_id"])


# ─── keep-alive ───────────────────────────────────────────────────────────────
@app.route("/ping")
def ping():
    return "pong", 200


@app.route("/health")
def health():
    return jsonify({"status": "ok", "bot": config.BOT_NAME}), 200


# ─── ثبت‌نام / ورود پنل ───────────────────────────────────────────────────────
@app.route("/")
@login_required
def index():
    oid = owner_id()
    account = db.get_account(oid)
    
    if not account:
        session.pop("owner_id", None)
        return redirect(url_for("panel_login_page"))
        
    if db.get_setting(oid, "logged_in") != "1":
        return redirect(url_for("tg_login_page"))
        
    return render_template(
        "panel.html",
        page="panel",
        username=account["username"],
        owner_id=oid,
    )


@app.route("/register", methods=["GET"])
def register_page():
    if session.get("owner_id"):
        return redirect(url_for("index"))
    return render_template("panel.html", page="register")


@app.route("/panel-login", methods=["GET"])
def panel_login_page():
    if session.get("owner_id"):
        return redirect(url_for("index"))
    has_accounts = db.account_exists()
    return render_template("panel.html", page="panel_login",
                           has_accounts=has_accounts)


@app.route("/api/register", methods=["POST"])
def api_register():
    try:
        data = request.json or {}
        username = data.get("username", "").strip()
        password = data.get("password", "").strip()
        if not username or not password:
            return jsonify({"ok": False, "error": "یوزرنیم و رمز الزامی هستند"}), 400
        if len(username) < 3:
            return jsonify({"ok": False, "error": "یوزرنیم باید حداقل ۳ کاراکتر باشد"}), 400
        if len(password) < 6:
            return jsonify({"ok": False, "error": "رمز باید حداقل ۶ کاراکتر باشد"}), 400
        new_id = db.create_account(username, password)
        if new_id is None:
            existing = db.get_account_by_username(username)
            if existing:
                return jsonify({"ok": False, "error": "این یوزرنیم قبلاً ثبت شده"}), 409
            return jsonify({"ok": False, "error": "خطا در ایجاد حساب — لطفاً مجدداً تلاش کنید"}), 500
        db.init_user_settings(new_id)
        session.permanent = True
        session["owner_id"] = new_id
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": f"خطای سرور: {str(e)}"}), 500


@app.route("/api/panel-login", methods=["POST"])
def api_panel_login():
    data = request.json or {}
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    if not username or not password:
        return jsonify({"ok": False, "error": "یوزرنیم و رمز الزامی هستند"}), 400
    uid = db.verify_account(username, password)
    if uid is None:
        return jsonify({"ok": False, "error": "یوزرنیم یا رمز اشتباه است"}), 401
    session["owner_id"] = uid
    if db.get_setting(uid, "logged_in") == "1":
        bot_manager.start(uid, get_loop(), check_tokens=False, is_restart=True)
    return jsonify({"ok": True})


@app.route("/api/panel-logout", methods=["POST"])
@login_required
def api_panel_logout():
    session.pop("owner_id", None)
    return jsonify({"ok": True})


# ─── لاگین تلگرام ────────────────────────────────────────────────────────────
@app.route("/tg-login")
@login_required
def tg_login_page():
    oid = owner_id()
    account = db.get_account(oid)
    
    if not account:
        session.pop("owner_id", None)
        return redirect(url_for("panel_login_page"))
        
    return render_template("panel.html", page="tg_login", username=account["username"])


@app.route("/api/login/send_code", methods=["POST"])
@login_required
def send_code():
    oid = owner_id()
    data = request.json or {}
    phone = data.get("phone", "").strip()
    if not phone:
        return jsonify({"ok": False, "error": "شماره تلفن الزامی است"}), 400
    if not config.API_ID or not config.API_HASH:
        return jsonify({"ok": False, "error": "API_ID و API_HASH تنظیم نشده‌اند"}), 400

    async def _send():
        cl = TelegramClient(StringSession(), config.API_ID, config.API_HASH)
        await cl.connect()
        result = await cl.send_code_request(phone)
        partial_sess = cl.session.save()
        await cl.disconnect()
        db.set_setting(oid, "_login_phone", phone)
        db.set_setting(oid, "_login_phone_hash", result.phone_code_hash)
        db.set_setting(oid, "_login_partial_session", partial_sess)
        _phone_hashes[oid] = result.phone_code_hash
        _phone_numbers[oid] = phone
        return {"ok": True}

    try:
        return jsonify(run_async(_send()))
    except FloodWaitError as e:
        return jsonify({"ok": False, "error": f"محدودیت: {e.seconds} ثانیه صبر کنید"}), 429
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/login/verify_code", methods=["POST"])
@login_required
def verify_code():
    oid = owner_id()
    data = request.json or {}
    code = data.get("code", "").strip()
    if not code:
        return jsonify({"ok": False, "error": "کد الزامی است"}), 400

    phone = _phone_numbers.get(oid) or db.get_setting(oid, "_login_phone")
    ph = _phone_hashes.get(oid) or db.get_setting(oid, "_login_phone_hash")
    partial_sess = db.get_setting(oid, "_login_partial_session")

    if not phone or not ph or not partial_sess:
        return jsonify({"ok": False, "error": "ابتدا کد ارسال کنید (مجدداً شماره خود را وارد کنید)"}), 400

    async def _verify():
        cl = TelegramClient(StringSession(partial_sess), config.API_ID, config.API_HASH)
        await cl.connect()
        await cl.sign_in(phone=phone, code=code, phone_code_hash=ph)
        me = await cl.get_me()
        sess = cl.session.save()
        await cl.disconnect()
        _login_clients.pop(oid, None)
        _phone_hashes.pop(oid, None)
        _phone_numbers.pop(oid, None)
        db.set_setting(oid, "_login_phone", "")
        db.set_setting(oid, "_login_phone_hash", "")
        db.set_setting(oid, "_login_partial_session", "")
        db.set_setting(oid, "session_data", sess)
        db.set_setting(oid, "logged_in", "1")
        db.save_telegram_user_id(oid, me.id)
        return {"ok": True}

    try:
        result = run_async(_verify())
        bot_manager.start(oid, get_loop(), check_tokens=False)
        _ensure_helper_bot()
        return jsonify(result)
    except SessionPasswordNeededError:
        return jsonify({"ok": False, "need_2fa": True}), 200
    except (PhoneCodeInvalidError, PhoneCodeExpiredError):
        return jsonify({"ok": False, "error": "کد اشتباه یا منقضی شده"}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/login/verify_2fa", methods=["POST"])
@login_required
def verify_2fa():
    oid = owner_id()
    data = request.json or {}
    password = data.get("password", "").strip()
    if not password:
        return jsonify({"ok": False, "error": "رمز دو مرحله‌ای الزامی است"}), 400

    phone = _phone_numbers.get(oid) or db.get_setting(oid, "_login_phone")
    ph = _phone_hashes.get(oid) or db.get_setting(oid, "_login_phone_hash")
    partial_sess = db.get_setting(oid, "_login_partial_session")
    code = data.get("_code", "")

    if not partial_sess:
        return jsonify({"ok": False, "error": "ابتدا کد تأیید را وارد کنید"}), 400

    async def _verify():
        cl = TelegramClient(StringSession(partial_sess), config.API_ID, config.API_HASH)
        await cl.connect()
        await cl.sign_in(password=password)
        me = await cl.get_me()
        sess = cl.session.save()
        await cl.disconnect()
        _login_clients.pop(oid, None)
        _phone_hashes.pop(oid, None)
        _phone_numbers.pop(oid, None)
        db.set_setting(oid, "_login_phone", "")
        db.set_setting(oid, "_login_phone_hash", "")
        db.set_setting(oid, "_login_partial_session", "")
        db.set_setting(oid, "session_data", sess)
        db.set_setting(oid, "logged_in", "1")
        db.save_telegram_user_id(oid, me.id)
        return {"ok": True}

    try:
        result = run_async(_verify())
        bot_manager.start(oid, get_loop(), check_tokens=False)
        _ensure_helper_bot()
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/logout", methods=["POST"])
@login_required
def tg_logout():
    oid = owner_id()
    bot_manager.stop(oid)
    db.set_setting(oid, "logged_in", "0")
    db.set_setting(oid, "session_data", "")
    return jsonify({"ok": True})


# ─── روشن / خاموش کردن سلف ───────────────────────────────────────────────────
@app.route("/api/start", methods=["POST"])
@login_required
def start_bot_api():
    oid = owner_id()
    ok = bot_manager.start(oid, get_loop(), check_tokens=True)
    if ok:
        _ensure_helper_bot()
        db.set_setting(oid, "self_bot_active", "1")
        tg_id = db.get_telegram_id_by_owner(oid)
        if tg_id == config.OWNER_TG_ID:
            msg = "✅ سلف روشن شد — دسترسی رایگان مالک ♾️"
        else:
            hours = config.SESSION_HOURS
            tokens = config.TOKENS_PER_SESSION
            msg = f"✅ سلف روشن شد — {tokens} توکن کسر شد — {hours} ساعت فعال است"
        return jsonify({"ok": True, "message": msg})
    else:
        balance = db.get_token_balance(oid)
        return jsonify({
            "ok": False,
            "error": f"توکن کافی ندارید! موجودی: {balance} — برای روشن کردن {config.TOKENS_PER_SESSION} توکن لازم است.",
        })


@app.route("/api/stop", methods=["POST"])
@login_required
def stop_bot_api():
    oid = owner_id()
    bot_manager.stop(oid)
    db.set_setting(oid, "self_bot_active", "0")
    return jsonify({"ok": True})


# ─── API تنظیمات ─────────────────────────────────────────────────────────────
@app.route("/api/settings", methods=["GET"])
@login_required
def get_settings():
    oid = owner_id()
    keys = [
        "self_bot_active", "secretary_active", "anti_delete_active",
        "anti_link_active", "auto_seen_active", "auto_reaction_active",
        "private_lock_active", "enemy_reply_active", "auto_save_media",
        "clock_name_active", "clock_bio_active", "selected_font",
        "secretary_message", "auto_reaction_emoji", "spam_delay",
    ]
    return jsonify({k: db.get_setting(oid, k) for k in keys})


@app.route("/api/settings", methods=["POST"])
@login_required
def update_settings():
    oid = owner_id()
    data = request.json or {}
    allowed = ["secretary_message", "auto_reaction_emoji", "selected_font", "spam_delay", "spam_count"]
    for k in allowed:
        if k in data:
            db.set_setting(oid, k, data[k])
    return jsonify({"ok": True})


@app.route("/api/toggle/<key>", methods=["POST"])
@login_required
def toggle(key):
    allowed = [
        "self_bot_active", "secretary_active", "anti_delete_active",
        "anti_link_active", "auto_seen_active", "auto_reaction_active",
        "private_lock_active", "enemy_reply_active", "auto_save_media",
        "clock_name_active", "clock_bio_active",
    ]
    if key not in allowed:
        return jsonify({"ok": False, "error": "کلید مجاز نیست"}), 400
    new_state = db.toggle_setting(owner_id(), key)
    return jsonify({"ok": True, "active": new_state})


# ─── API توکن ─────────────────────────────────────────────────────────────────
@app.route("/api/tokens", methods=["GET"])
@login_required
def get_tokens():
    import telegram_bot as tb
    oid = owner_id()
    stats = db.get_token_stats(oid)
    stats["ref_count"] = db.get_referral_count(oid)
    stats["bot_username"] = tb.BOT_USERNAME or ""
    stats["token_system_active"] = bool(config.BOT_TOKEN)
    stats["tokens_per_session"] = config.TOKENS_PER_SESSION
    stats["session_hours"] = config.SESSION_HOURS
    stats["daily_gift"] = config.DAILY_TOKEN_GIFT
    stats["referral_tokens"] = config.REFERRAL_TOKENS
    return jsonify(stats)


@app.route("/api/tokens/daily", methods=["POST"])
@login_required
def claim_daily():
    oid = owner_id()
    success, message = db.claim_daily_token(oid)
    return jsonify({"ok": success, "message": message})


# ─── API لیست‌ها ──────────────────────────────────────────────────────────────
@app.route("/api/enemies", methods=["GET"])
@login_required
def get_enemies():
    return jsonify(db.get_enemies(owner_id()))


@app.route("/api/enemies", methods=["POST"])
@login_required
def add_enemy():
    oid = owner_id()
    data = request.json or {}
    uid = data.get("user_id")
    if not uid:
        return jsonify({"ok": False, "error": "آیدی کاربر الزامی است"}), 400
    db.add_enemy(oid, int(uid), data.get("username"), data.get("name"))
    return jsonify({"ok": True})


@app.route("/api/enemies/<int:uid>", methods=["DELETE"])
@login_required
def del_enemy(uid):
    db.remove_enemy(owner_id(), uid)
    return jsonify({"ok": True})


@app.route("/api/enemies/clear", methods=["POST"])
@login_required
def clear_enemies_api():
    db.clear_enemies(owner_id())
    return jsonify({"ok": True})


@app.route("/api/friends", methods=["GET"])
@login_required
def get_friends():
    return jsonify(db.get_friends(owner_id()))


@app.route("/api/friends", methods=["POST"])
@login_required
def add_friend():
    oid = owner_id()
    data = request.json or {}
    uid = data.get("user_id")
    if not uid:
        return jsonify({"ok": False, "error": "آیدی کاربر الزامی است"}), 400
    db.add_friend(oid, int(uid), data.get("username"), data.get("name"))
    return jsonify({"ok": True})


@app.route("/api/friends/<int:uid>", methods=["DELETE"])
@login_required
def del_friend(uid):
    db.remove_friend(owner_id(), uid)
    return jsonify({"ok": True})


@app.route("/api/friends/clear", methods=["POST"])
@login_required
def clear_friends_api():
    db.clear_friends(owner_id())
    return jsonify({"ok": True})


@app.route("/api/deleted_messages", methods=["GET"])
@login_required
def deleted_messages():
    return jsonify(db.get_deleted_messages(owner_id(), 50))


@app.route("/api/bot_status", methods=["GET"])
@login_required
def bot_status():
    oid = owner_id()
    running = bot_manager.is_running(oid)
    logged_in = db.get_setting(oid, "logged_in") == "1"
    return jsonify({"running": running, "logged_in": logged_in})


# ─── API چنل‌های اجباری (با دیتابیس کش) ──────────────────────────────────────
@app.route("/api/forced_channels", methods=["GET"])
@login_required
def get_forced_channels():
    return jsonify(cache.get_forced_channels())


@app.route("/api/forced_channels", methods=["POST"])
@login_required
def add_forced_channel():
    data = request.json or {}
    username = data.get("username", "").strip()
    if not username:
        return jsonify({"ok": False, "error": "یوزرنیم کانال الزامی است"}), 400
    if cache.add_forced_channel(username):
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "خطا یا کانال تکراری است"})


@app.route("/api/forced_channels/<username>", methods=["DELETE"])
@login_required
def remove_forced_channel(username):
    if cache.remove_forced_channel(username):
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "کانال یافت نشد"})


# ─── اجرا ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # ۱. ایجاد جداول (اگر موجود نیستند)
    db.init_tables()
    print("✅ جداول Supabase بررسی/ایجاد شدند")
    
    # ۲. استارت Heartbeat Manager
    from heartbeat import get_heartbeat_manager
    hb = get_heartbeat_manager()
    hb.start()
    print("✅ Heartbeat Manager استارت شد")
    
    # ۳. استارت ربات توکن
    from telegram_bot import start_token_bot
    start_token_bot()
    
    # ۴. استارت بات برای همه کاربران لاگین‌شده
    loop = get_loop()
    for oid in db.get_all_logged_in_users():
        # ✅ هر کاربر جدا try/except دارد — اگر استارت یک کاربر با خطا مواجه شود
        # (مثلاً یک هیکاپ لحظه‌ای دیتابیس/تلگرام)، دیگر کاربرهای بعدی در این
        # لیست بی‌خبر نمی‌مانند و استارت‌شان متوقف نمی‌شود (قبلاً یک خطا برای
        # یک کاربر، کل حلقه را متوقف می‌کرد و باقی کاربرها هرگز ری‌استارت
        # نمی‌شدند تا خودشان دستی دوباره لاگین کنند)
        try:
            bot_manager.start(oid, loop, check_tokens=False, is_restart=True)
            print(f"🚀 بات کاربر {oid} استارت شد.")
        except Exception as e:
            print(f"❌ خطا در استارت خودکار کاربر {oid}: {e} — کاربر بعدی ادامه می‌یابد")
        # ✅ فاصله‌ی کوچک بین استارت‌ها تا تلگرام همه‌ی این اتصال‌های هم‌زمان
        # را به‌عنوان رفتار مشکوک/فلود نبیند
        time.sleep(0.3)

    # ۵. واچ‌داگ سلامت سلف‌ها — هر چند دقیقه چک می‌کند که آیا سلف هر کاربر
    #    لاگین‌شده واقعاً در حال اجراست؛ اگر نبود (مثلاً به هر دلیلی، حتی
    #    دلایلی که هنوز کشف نشده‌اند، متوقف شده بود) خودش دوباره استارتش
    #    می‌زند — تا کاربر مجبور نشود دستی سلف را حذف و دوباره لاگین کند
    def _self_heal_watchdog():
        WATCHDOG_INTERVAL = 180  # هر ۳ دقیقه
        while True:
            time.sleep(WATCHDOG_INTERVAL)
            try:
                for oid in db.get_all_logged_in_users():
                    try:
                        if not bot_manager.is_running(oid):
                            print(f"🩺 واچ‌داگ: سلف کاربر {oid} روشن نبود — تلاش برای ری‌استارت خودکار")
                            bot_manager.start(oid, get_loop(), check_tokens=False, is_restart=True)
                    except Exception as e:
                        print(f"⚠️ واچ‌داگ: خطا در بررسی/ری‌استارت کاربر {oid}: {e}")
            except Exception as e:
                print(f"⚠️ واچ‌داگ: خطای کلی: {e}")

            # ✅ همون کاری که برای سلف‌ها می‌کنیم رو برای ربات کمکیِ پنل هم
            # انجام می‌دیم: اگه به هر دلیلی (خطای شبکه‌ای موقت و ...) قطع
            # شده باشه، بدون نیاز به ری‌استارتِ هاست دوباره وصلش می‌کنیم.
            # این دقیقاً همون مشکلی رو حل می‌کنه که «کاربر تازه ثبت‌نام کرده
            # ولی پنل ربات کمکی براش کار نمی‌کنه تا هاست ریستارت بشه».
            try:
                from helper_bot import get_helper_client
                helper_cl = get_helper_client()
                if helper_cl is None or not helper_cl.is_connected():
                    print("🩺 واچ‌داگ: ربات کمکی پنل وصل نبود — تلاش برای اتصال مجدد خودکار")
                    _ensure_helper_bot()
            except Exception as e:
                print(f"⚠️ واچ‌داگ: خطا در بررسی/اتصال مجدد ربات کمکی: {e}")

    threading.Thread(target=_self_heal_watchdog, daemon=True).start()
    print("✅ واچ‌داگ سلامت سلف‌ها استارت شد")

    # ۶. استارت بات کمکی پنل دکمه‌ای مدیریت سلف (اختیاری - نیازمند HELPER_BOT_TOKEN)
    if config.HELPER_BOT_TOKEN:
        from helper_bot import start_helper_bot
        try:
            asyncio.run_coroutine_threadsafe(start_helper_bot(), get_loop()).result(timeout=30)
        except Exception as e:
            print(f"❌ خطا در استارت بات کمکی پنل: {e}")
    else:
        print("⚠️ HELPER_BOT_TOKEN تنظیم نشده — پنل دکمه‌ای سلف غیرفعال می‌ماند")

    app.run(host="0.0.0.0", port=config.PORT, debug=False)
