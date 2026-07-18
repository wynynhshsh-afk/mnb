"""
مدیریت پیشرفته بات‌ها با سیستم Heartbeat و Auto Reconnect

این ماژول مسئولیت مدیریت چرخه حیات بات‌های تلگرام را بر عهده دارد:
- Heartbeat برای بررسی زنده بودن
- Auto Reconnect با Exponential Backoff
- Duplicate Protection
- Queue برای تسک‌ها (Redis-backed)
- مدیریت خطاهای کامل با Fallback
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from enum import Enum, auto
from typing import Any, Dict, List, Optional

import database as db
import config
import redis_cache as rc
from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    RPCError,
    UnauthorizedError,
)
from telethon.sessions import StringSession

# ─── Logger ───────────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)


# ─── Enums ────────────────────────────────────────────────────────────────────
class BotState(Enum):
    """وضعیت‌های ممکن یک بات."""
    STOPPED = auto()
    STARTING = auto()
    RUNNING = auto()
    PAUSED = auto()
    RECONNECTING = auto()


# ─── Fallback Classes ─────────────────────────────────────────────────────────
class _FallbackHeartbeatManager:
    """
    Heartbeat Manager ساده در صورت عدم دسترسی به heartbeat.py.
    Thread-Safe و بدون وابستگی خارجی.
    """

    def __init__(self) -> None:
        self._alive: Dict[int, float] = {}
        self._lock = threading.Lock()

    def register(self, owner_id: int) -> None:
        with self._lock:
            self._alive[owner_id] = time.time()

    def unregister(self, owner_id: int) -> None:
        with self._lock:
            self._alive.pop(owner_id, None)

    def is_alive(self, owner_id: int) -> bool:
        with self._lock:
            return owner_id in self._alive

    def get_all_alive(self) -> List[int]:
        with self._lock:
            return list(self._alive.keys())

    def stop(self) -> None:
        with self._lock:
            self._alive.clear()


class _FallbackRedis:
    """
    Redis Queue ساده در حافظه در صورت عدم دسترسی به Redis.
    Thread-Safe و فقط برای توسعه/تست.
    """

    def __init__(self) -> None:
        self._queues: Dict[str, List[bytes]] = {}
        self._lock = threading.Lock()

    def rpush(self, key: str, value: Any) -> int:
        with self._lock:
            self._queues.setdefault(key, []).append(value)
            return len(self._queues[key])

    def lpop(self, key: str) -> Optional[bytes]:
        with self._lock:
            q = self._queues.get(key)
            if not q:
                return None
            return q.pop(0)

    def llen(self, key: str) -> int:
        with self._lock:
            return len(self._queues.get(key, []))

    def delete(self, key: str) -> int:
        with self._lock:
            return 1 if self._queues.pop(key, None) is not None else 0


# ─── Owner Detection Helper ───────────────────────────────────────────────────
def is_owner_account(me: Any) -> bool:
    """
    تشخیص اینکه آیا یک کاربر تلگرام، مالک اصلی ربات است یا خیر.

    Args:
        me: شیء کاربر تلگرام (از client.get_me())

    Returns:
        bool: True اگر مالک باشد
    """
    try:
        me_phone = (getattr(me, "phone", None) or "").lstrip("+")
        owner_phone = getattr(config, "OWNER_PHONE", "").lstrip("+")

        return (
            getattr(me, "id", None) == getattr(config, "OWNER_TG_ID", None)
            or (bool(owner_phone) and me_phone == owner_phone)
            or getattr(me, "username", None) == getattr(config, "OWNER_USERNAME", "")
        )
    except Exception as e:
        logger.warning("خطا در تشخیص مالک: %s", e)
        return False


# ─── Main Manager ─────────────────────────────────────────────────────────────
class AdvancedBotManager:
    """
    مدیریت پیشرفته بات‌ها با:
    - Heartbeat برای بررسی زنده بودن
    - Auto Reconnect با Exponential Backoff
    - Duplicate Protection
    - Queue برای تسک‌ها (Redis-backed با Fallback)
    - مدیریت خطاهای کامل و Thread-Safe
    """

    def __init__(self) -> None:
        self._bots: Dict[int, Dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._hb_manager: Optional[Any] = None
        self._task_queue: Optional[Any] = None
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None
        self._timer_starts: Dict[int, float] = {}
        self._sub_watchers: Dict[int, threading.Timer] = {}
        self._started: bool = False

        # مقادیر از config (با fallback)
        self._heartbeat_interval: int = getattr(config, "HEARTBEAT_INTERVAL", 30)
        self._max_retries: int = getattr(config, "MAX_RECONNECT_RETRIES", 10)
        self._base_retry_delay: float = getattr(config, "BASE_RETRY_DELAY", 3.0)
        self._max_retry_delay: float = getattr(config, "MAX_RETRY_DELAY", 120.0)
        self._sub_check_interval: int = getattr(config, "SUBSCRIPTION_CHECK_INTERVAL", 300)

    # ─── Lazy Getters with Fallback ───────────────────────────────────────────
    def _get_hb_manager(self) -> Any:
        """دریافت Heartbeat Manager با Fallback در صورت خطا."""
        if self._hb_manager is None:
            try:
                from heartbeat import get_heartbeat_manager
                self._hb_manager = get_heartbeat_manager()
                logger.debug("Heartbeat Manager واقعی بارگذاری شد")
            except Exception as e:
                logger.warning("Heartbeat Manager در دسترس نیست، استفاده از Fallback: %s", e)
                self._hb_manager = _FallbackHeartbeatManager()
        return self._hb_manager

    def _get_redis(self) -> Any:
        """دریافت اتصال Redis با Fallback در صورت خطا."""
        if self._task_queue is None:
            try:
                self._task_queue = rc.get_redis()
                if self._task_queue is None:
                    raise RuntimeError("Redis returned None")
                logger.debug("اتصال Redis برقرار شد")
            except Exception as e:
                logger.warning("Redis در دسترس نیست، استفاده از Fallback: %s", e)
                self._task_queue = _FallbackRedis()
        return self._task_queue

    # ─── Validation ───────────────────────────────────────────────────────────
    @staticmethod
    def _validate_owner_id(owner_id: int) -> None:
        """اعتبارسنجی owner_id."""
        if not isinstance(owner_id, int) or owner_id <= 0:
            raise ValueError(f"owner_id نامعتبر: {owner_id}")

    # ─── State Queries ────────────────────────────────────────────────────────
    def is_running(self, owner_id: int) -> bool:
        """بررسی آیا یک اکانت در حال اجراست."""
        self._validate_owner_id(owner_id)
        with self._lock:
            entry = self._bots.get(owner_id)
            if not entry:
                return False

            state = entry.get("state", BotState.STOPPED)
            if state == BotState.STOPPED:
                return False

            hb = self._get_hb_manager()
            if not hb.is_alive(owner_id):
                logger.warning("[%s] Heartbeat مرده است، پاکسازی...", owner_id)
                self._cleanup_bot_locked(owner_id)
                return False

            return True

    def get_client(self, owner_id: int) -> Optional[TelegramClient]:
        """دریافت کلاینت یک اکانت."""
        self._validate_owner_id(owner_id)
        with self._lock:
            entry = self._bots.get(owner_id)
            return entry.get("client") if entry else None

    def get_all_running(self) -> List[int]:
        """دریافت لیست همه اکانت‌های در حال اجرا."""
        hb = self._get_hb_manager()
        return hb.get_all_alive()

    def get_state(self, owner_id: int) -> BotState:
        """دریافت وضعیت فعلی یک بات."""
        self._validate_owner_id(owner_id)
        with self._lock:
            entry = self._bots.get(owner_id)
            return entry.get("state", BotState.STOPPED) if entry else BotState.STOPPED

    def get_remaining_time(self, owner_id: int) -> Optional[float]:
        """دریافت زمان باقی‌مانده تا انقضای سشن (ثانیه)."""
        self._validate_owner_id(owner_id)
        with self._lock:
            start = self._timer_starts.get(owner_id)
            if start is None:
                return None
            elapsed = time.time() - start
            remaining = (config.SESSION_HOURS * 3600) - elapsed
            return max(0.0, remaining)

    # ─── Start ────────────────────────────────────────────────────────────────
    def start(
        self,
        owner_id: int,
        loop: asyncio.AbstractEventLoop,
        check_tokens: bool = True,
        is_restart: bool = False,
    ) -> bool:
        """
        شروع یک اکانت با بررسی‌های کامل.

        Args:
            owner_id: شناسه مالک اکانت
            loop: Event Loop اصلی برنامه
            check_tokens: آیا توکن‌ها بررسی شوند؟
            is_restart: آیا این یک ریستارت است؟

        Returns:
            bool: True در صورت موفقیت
        """
        self._validate_owner_id(owner_id)
        logger.info(
            "🚀 [%s] شروع فرآیند استارت%s...",
            owner_id, "(ریستارت)" if is_restart else "",
        )

        # ذخیره event loop اصلی
        if self._main_loop is None:
            self._main_loop = loop

        # ─── ۱. Duplicate Protection ─────────────────────────────────────
        if self.is_running(owner_id):
            logger.warning("[%s] اکانت در حال اجراست، متوقف می‌شود...", owner_id)
            self.stop(owner_id)
            # صبر غیربلاک‌کننده در thread جداگانه
            time.sleep(0.3)

        # ─── ۲. بررسی اشتراک ─────────────────────────────────────────────
        try:
            tg_id = db.get_telegram_id_by_owner(owner_id)
        except Exception as e:
            logger.error("[%s] خطا در دریافت telegram_id: %s", owner_id, e)
            return False

        is_owner = (tg_id is not None and tg_id == getattr(config, "OWNER_TG_ID", None))

        if not is_owner:
            try:
                subscribed = db.is_subscribed(owner_id)
            except Exception as e:
                logger.error("[%s] خطا در بررسی اشتراک: %s", owner_id, e)
                return False
            if not subscribed:
                logger.warning("[%s] اشتراک منقضی شده", owner_id)
                return False

        # ─── ۳. بررسی توکن ───────────────────────────────────────────────
        tokens_deducted = 0
        if getattr(config, "BOT_TOKEN", None) and check_tokens and not is_owner:
            try:
                balance = db.get_token_balance(owner_id)
                required = getattr(config, "TOKENS_PER_SESSION", 0)
                if balance < required:
                    logger.error("[%s] توکن کافی نیست: %s < %s", owner_id, balance, required)
                    return False
                db.deduct_tokens(owner_id, required)
                tokens_deducted = required
                logger.info("💰 [%s] %s توکن کسر شد", owner_id, tokens_deducted)
            except Exception as e:
                logger.error("[%s] خطا در کسر توکن: %s", owner_id, e)
                return False

        # ─── ۴. ساخت entry جدید ──────────────────────────────────────────
        entry: Dict[str, Any] = {
            "client": None,
            "task": None,
            "state": BotState.STARTING,
            "is_owner": is_owner,
            "tokens_deducted": tokens_deducted,
            "owner_refunded": False,
            "retry_count": 0,
            "start_time": time.time(),
            "last_heartbeat": time.time(),
            "loop": loop,
        }

        with self._lock:
            self._bots[owner_id] = entry

        # ─── ۵. استارت بات در event loop ─────────────────────────────────
        try:
            task = asyncio.run_coroutine_threadsafe(
                self._run_bot_advanced(owner_id),
                loop,
            )
            with self._lock:
                if owner_id in self._bots:
                    self._bots[owner_id]["task"] = task
        except Exception as e:
            logger.error("[%s] خطا در استارت تسک: %s", owner_id, e)
            with self._lock:
                self._bots.pop(owner_id, None)
            return False

        # ─── ۶. ثبت در Heartbeat ─────────────────────────────────────────
        hb = self._get_hb_manager()
        hb.register(owner_id)

        # ─── ۷. تایمر انقضا (برای کاربران غیرمالک) ──────────────────────
        if getattr(config, "BOT_TOKEN", None) and not is_owner:
            self._cancel_timer(owner_id)
            timer = threading.Timer(
                config.SESSION_HOURS * 3600,
                self.stop,
                args=[owner_id],
            )
            timer.daemon = True
            timer.start()
            with self._lock:
                self._timer_starts[owner_id] = time.time()
                # ذخیره timer در entry برای لغو آسان
                if owner_id in self._bots:
                    self._bots[owner_id]["timer"] = timer
            logger.info("⏱️ [%s] تایمر %s ساعته تنظیم شد", owner_id, config.SESSION_HOURS)

        # ─── ۸. تایمر چک اشتراک (برای کاربران غیرمالک) ──────────────────
        if not is_owner:
            self._start_subscription_watcher(owner_id)

        logger.info("✅ [%s] بات با موفقیت استارت شد", owner_id)
        return True

    # ─── Timer Management ─────────────────────────────────────────────────────
    def _cancel_timer(self, owner_id: int) -> None:
        """لغو تایمر انقضا."""
        with self._lock:
            timer = None
            entry = self._bots.get(owner_id)
            if entry:
                timer = entry.pop("timer", None)
            self._timer_starts.pop(owner_id, None)

        if timer is not None:
            try:
                timer.cancel()
                logger.debug("⏹️ [%s] تایمر لغو شد", owner_id)
            except Exception as e:
                logger.warning("[%s] خطا در لغو تایمر: %s", owner_id, e)

    # ─── Stop ─────────────────────────────────────────────────────────────────
    def stop(self, owner_id: int) -> None:
        """متوقف کردن یک اکانت به صورت کامل."""
        self._validate_owner_id(owner_id)
        logger.info("⏹ [%s] در حال توقف...", owner_id)

        # ─── ۱. لغو تایمرها ──────────────────────────────────────────────
        self._cancel_timer(owner_id)

        # ─── ۲. لغو watcher اشتراک ───────────────────────────────────────
        with self._lock:
            watcher = self._sub_watchers.pop(owner_id, None)
        if watcher is not None:
            try:
                watcher.cancel()
            except Exception:
                pass

        # ─── ۳. حذف از Heartbeat ─────────────────────────────────────────
        hb = self._get_hb_manager()
        hb.unregister(owner_id)

        # ─── ۴. متوقف کردن تسک و کلاینت ──────────────────────────────────
        with self._lock:
            entry = self._bots.get(owner_id)
            if not entry:
                return
            entry["state"] = BotState.STOPPED
            client: Optional[TelegramClient] = entry.get("client")
            task = entry.get("task")
            loop: Optional[asyncio.AbstractEventLoop] = entry.get("loop") or self._main_loop

        # لغو task اصلی
        if task is not None:
            try:
                task.cancel()
            except Exception as e:
                logger.warning("[%s] خطا در cancel task: %s", owner_id, e)

        # disconnect کلاینت با استفاده از event loop اصلی
        if client is not None and loop is not None:
            try:
                if client.is_connected():
                    future = asyncio.run_coroutine_threadsafe(
                        client.disconnect(), loop
                    )
                    # منتظر نتیجه با timeout کوتاه
                    try:
                        future.result(timeout=5.0)
                    except Exception as e:
                        logger.warning("[%s] خطا در disconnect: %s", owner_id, e)
            except Exception as e:
                logger.warning("[%s] خطا در ارسال disconnect: %s", owner_id, e)

        # ─── ۵. حذف از لیست ──────────────────────────────────────────────
        with self._lock:
            self._bots.pop(owner_id, None)

        logger.info("✅ [%s] بات متوقف شد", owner_id)

    def stop_all(self) -> None:
        """متوقف کردن همه اکانت‌ها."""
        logger.info("🛑 توقف همه اکانت‌ها...")
        with self._lock:
            owners = list(self._bots.keys())

        for oid in owners:
            try:
                self.stop(oid)
            except Exception as e:
                logger.error("[%s] خطا در توقف: %s", oid, e)

        hb = self._get_hb_manager()
        hb.stop()
        logger.info("✅ همه اکانت‌ها متوقف شدند")

    # ─── Pause / Resume ───────────────────────────────────────────────────────
    def pause(self, owner_id: int) -> None:
        """متوقف کردن عملیات سلف (اتصال تلگرام نگه داشته می‌شود)."""
        self._validate_owner_id(owner_id)
        with self._lock:
            entry = self._bots.get(owner_id)
            if entry and not entry.get("is_owner"):
                entry["state"] = BotState.PAUSED
                logger.info("⏸️ [%s] سلف موقتاً متوقف شد", owner_id)

    def resume(self, owner_id: int) -> None:
        """از سرگیری عملیات سلف."""
        self._validate_owner_id(owner_id)
        with self._lock:
            entry = self._bots.get(owner_id)
            if entry and entry.get("state") == BotState.PAUSED:
                entry["state"] = BotState.RUNNING
                logger.info("▶️ [%s] سلف دوباره فعال شد", owner_id)

    def is_paused(self, owner_id: int) -> bool:
        """بررسی آیا سلف متوقف شده."""
        self._validate_owner_id(owner_id)
        with self._lock:
            entry = self._bots.get(owner_id)
            return bool(entry and entry.get("state") == BotState.PAUSED)

    # ─── Cleanup ──────────────────────────────────────────────────────────────
    def _cleanup_bot_locked(self, owner_id: int) -> None:
        """پاک کردن یک اکانت از حافظه (باید با lock فراخوانی شود)."""
        entry = self._bots.pop(owner_id, None)
        if entry:
            logger.debug("🧹 [%s] پاک شد", owner_id)

    # ─── Subscription Watcher ─────────────────────────────────────────────────
    def _start_subscription_watcher(self, owner_id: int) -> None:
        """تایمر دوره‌ای برای چک اشتراک."""
        with self._lock:
            old = self._sub_watchers.pop(owner_id, None)
        if old is not None:
            try:
                old.cancel()
            except Exception:
                pass

        timer = threading.Timer(
            self._sub_check_interval,
            self._check_subscription,
            args=[owner_id],
        )
        timer.daemon = True
        timer.start()
        with self._lock:
            self._sub_watchers[owner_id] = timer

    def _check_subscription(self, owner_id: int) -> None:
        """بررسی اشتراک و pause/resume."""
        if not self.is_running(owner_id):
            return

        with self._lock:
            entry = self._bots.get(owner_id)
            if not entry or entry.get("is_owner"):
                return

        try:
            subscribed = db.is_subscribed(owner_id)
        except Exception as e:
            logger.error("[%s] خطا در بررسی اشتراک: %s", owner_id, e)
            subscribed = False

        if not subscribed:
            self.pause(owner_id)
        else:
            self.resume(owner_id)

        # چک بعدی
        self._start_subscription_watcher(owner_id)

    # ─── Core Bot Runner (Broken Down) ────────────────────────────────────────
    async def _run_bot_advanced(self, owner_id: int) -> None:
        """اجرای بات با سیستم Auto Reconnect."""
        entry = self._bots.get(owner_id)
        if not entry:
            return

        retry_delay = self._base_retry_delay
        retry_count = 0

        while True:
            with self._lock:
                if entry.get("state") == BotState.STOPPED:
                    break

            try:
                # ─── ۱. دریافت Session ───────────────────────────────────
                session_data = await self._fetch_session_data(owner_id)
                if not session_data:
                    retry_count += 1
                    if retry_count > self._max_retries:
                        logger.error("[%s] session یافت نشد پس از %s تلاش", owner_id, retry_count)
                        break
                    await asyncio.sleep(min(retry_delay * (2 ** min(retry_count, 3)), 30))
                    continue

                # ─── ۲. ساخت کلاینت ──────────────────────────────────────
                client = self._create_telegram_client(session_data)
                with self._lock:
                    entry["client"] = client

                # ─── ۳. ثبت هندلرها ─────────────────────────────────────
                handlers_ok = await self._register_bot_handlers(client, owner_id, entry)
                if not handlers_ok:
                    retry_count += 1
                    if retry_count > 3:
                        logger.error("[%s] ثبت هندلرها ناموفق بود", owner_id)
                        break
                    await asyncio.sleep(5)
                    continue

                # ─── ۴. اتصال و تشخیص هویت ───────────────────────────────
                connected = await self._connect_and_identify(client, owner_id, entry)
                if not connected:
                    break  # خطای بحرانی (session باطل)

                # ─── ۵. استارت تسک‌های پس‌زمینه ─────────────────────────
                bg_tasks = await self._start_background_loops(client, owner_id)

                # ─── ۶. Reset retry و state ──────────────────────────────
                retry_delay = self._base_retry_delay
                retry_count = 0
                with self._lock:
                    entry["state"] = BotState.RUNNING
                    entry["retry_count"] = 0

                # ─── ۷. منتظر قطع شدن ────────────────────────────────────
                await self._wait_for_disconnect(client, owner_id, entry)

                # ─── ۸. لغو تسک‌های پس‌زمینه ─────────────────────────────
                await self._cancel_background_loops(bg_tasks)

                with self._lock:
                    if entry.get("state") == BotState.STOPPED:
                        break

                # ─── ۹. چک session ───────────────────────────────────────
                session_data = await self._fetch_session_data(owner_id)
                if not session_data:
                    logger.warning("[%s] session حذف شده — توقف کامل", owner_id)
                    break

                logger.warning("[%s] اتصال قطع شد، تلاش مجدد...", owner_id)
                with self._lock:
                    entry["state"] = BotState.RECONNECTING

            except asyncio.CancelledError:
                logger.warning("[%s] تسک لغو شد", owner_id)
                break
            except Exception as e:
                logger.error("[%s] خطای ناشناخته: %s", owner_id, e, exc_info=True)
                retry_count += 1
                if retry_count > self._max_retries:
                    logger.error("[%s] بیش از حد مجاز تلاش (%s) — توقف", owner_id, self._max_retries)
                    break

            # ─── Auto Reconnect ──────────────────────────────────────────
            with self._lock:
                if entry.get("state") == BotState.STOPPED:
                    break

            wait = min(retry_delay * (2 ** min(retry_count, 3)), self._max_retry_delay)
            logger.info("🔄 [%s] تلاش مجدد در %.1f ثانیه...", owner_id, wait)
            await asyncio.sleep(wait)
            retry_delay = min(retry_delay * 2, self._max_retry_delay)

        logger.info("🛑 [%s] بات متوقف شد", owner_id)

        # Cleanup
        with self._lock:
            self._bots.pop(owner_id, None)
        hb = self._get_hb_manager()
        hb.unregister(owner_id)

    # ─── Helper Methods for Bot Runner ────────────────────────────────────────
    async def _fetch_session_data(self, owner_id: int) -> Optional[str]:
        """دریافت session_data از دیتابیس."""
        try:
            data = db.get_setting(owner_id, "session_data", "")
            return data if data else None
        except Exception as e:
            logger.error("[%s] خطا در دریافت session: %s", owner_id, e)
            return None

    def _create_telegram_client(self, session_data: str) -> TelegramClient:
        """ساخت کلاینت تلگرام."""
        return TelegramClient(
            StringSession(session_data),
            config.API_ID,
            config.API_HASH,
            connection_retries=5,
            retry_delay=2,
            auto_reconnect=True,
        )

    async def _register_bot_handlers(
        self, client: TelegramClient, owner_id: int, entry: Dict[str, Any]
    ) -> bool:
        """ثبت هندلرهای بات (با lazy import برای جلوگیری از circular import)."""
        try:
            # Lazy import برای جلوگیری از circular import
            from bot import _register_handlers
            _register_handlers(client, owner_id, entry)
            return True
        except ImportError as e:
            logger.error("[%s] خطا در import هندلرها: %s", owner_id, e)
            return False
        except Exception as e:
            logger.error("[%s] خطا در ثبت هندلرها: %s", owner_id, e)
            return False

    async def _connect_and_identify(
        self, client: TelegramClient, owner_id: int, entry: Dict[str, Any]
    ) -> bool:
        """
        اتصال به تلگرام و تشخیص مالک.

        Returns:
            bool: True اگر اتصال موفق بود، False برای خطای بحرانی (توقف کامل)
        """
        try:
            await client.start()
            me = await client.get_me()
            logger.info(
                "✅ [%s] بات متصل شد — @%s",
                owner_id, me.username or me.first_name,
            )
        except UnauthorizedError:
            logger.error("[%s] Session نامعتبر — نیاز به لاگین مجدد", owner_id)
            self._invalidate_session(owner_id)
            return False
        except Exception as e:
            err_str = str(e)
            fatal_errors = (
                "AUTH_KEY_UNREGISTERED",
                "SESSION_REVOKED",
                "USER_DEACTIVATED",
                "AUTH_KEY_DUPLICATED",
            )
            if any(k in err_str for k in fatal_errors):
                logger.error("[%s] Session باطل شده (%s) — توقف کامل", owner_id, e)
                self._invalidate_session(owner_id)
                return False
            logger.error("[%s] خطا در اتصال: %s", owner_id, e)
            raise  # برای مدیریت در حلقه اصلی

        # ذخیره Telegram ID
        try:
            db.save_telegram_user_id(owner_id, me.id)
        except Exception as e:
            logger.warning("[%s] خطا در ذخیره telegram_id: %s", owner_id, e)

        # تشخیص مالک
        if is_owner_account(me):
            with self._lock:
                entry["is_owner"] = True
            self._cancel_timer(owner_id)

            with self._lock:
                refunded = entry.get("owner_refunded", False)
                deducted = entry.get("tokens_deducted", 0)

            if not refunded and deducted > 0:
                try:
                    db.add_tokens(owner_id, deducted)
                    with self._lock:
                        entry["owner_refunded"] = True
                    logger.info("👑 [%s] مالک — %s توکن برگشت", owner_id, deducted)
                except Exception as e:
                    logger.error("[%s] خطا در برگشت توکن: %s", owner_id, e)

            logger.info("👑 [%s] مالک: @%s (ID: %s)", owner_id, me.username, me.id)

        return True

    def _invalidate_session(self, owner_id: int) -> None:
        """باطل کردن session در دیتابیس."""
        try:
            db.set_setting(owner_id, "logged_in", "0")
            db.set_setting(owner_id, "session_data", "")
        except Exception as e:
            logger.error("[%s] خطا در باطل کردن session: %s", owner_id, e)

    async def _start_background_loops(
        self, client: TelegramClient, owner_id: int
    ) -> List[asyncio.Task]:
        """استارت تسک‌های پس‌زمینه (clock و scheduler)."""
        tasks: List[asyncio.Task] = []
        try:
            from bot import _clock_loop, _scheduler_loop
            tasks.append(asyncio.create_task(_clock_loop(client, owner_id)))
            tasks.append(asyncio.create_task(_scheduler_loop(client, owner_id)))
        except ImportError as e:
            logger.warning("[%s] تسک‌های پس‌زمینه در دسترس نیست: %s", owner_id, e)
        except Exception as e:
            logger.error("[%s] خطا در استارت تسک‌های پس‌زمینه: %s", owner_id, e)
        return tasks

    async def _wait_for_disconnect(
        self, client: TelegramClient, owner_id: int, entry: Dict[str, Any]
    ) -> None:
        """منتظر قطع شدن اتصال کلاینت."""
        try:
            await client.run_until_disconnected()
        except asyncio.CancelledError:
            logger.debug("[%s] run_until_disconnected لغو شد", owner_id)
        except Exception as e:
            logger.error("[%s] خطا در run_until_disconnected: %s", owner_id, e)

    async def _cancel_background_loops(self, tasks: List[asyncio.Task]) -> None:
        """لغو و پاکسازی تسک‌های پس‌زمینه."""
        for task in tasks:
            if task and not task.done():
                task.cancel()
        if tasks:
            try:
                await asyncio.gather(*tasks, return_exceptions=True)
            except Exception as e:
                logger.warning("خطا در gather تسک‌های پس‌زمینه: %s", e)

    # ─── Task Queue (Redis-backed) ────────────────────────────────────────────
    def enqueue_task(self, owner_id: int, task_type: str, data: Dict[str, Any]) -> bool:
        """افزودن تسک به Redis Queue."""
        self._validate_owner_id(owner_id)
        r = self._get_redis()
        if not r:
            logger.warning("[%s] Redis در دسترس نیست", owner_id)
            return False

        try:
            task = {
                "owner_id": owner_id,
                "type": task_type,
                "data": data,
                "timestamp": time.time(),
            }
            r.rpush(f"queue:{owner_id}", json.dumps(task))
            logger.info("📋 [%s] تسک %s به صف اضافه شد", owner_id, task_type)
            return True
        except Exception as e:
            logger.error("[%s] خطا در enqueue: %s", owner_id, e)
            return False

    def dequeue_task(self, owner_id: int) -> Optional[Dict[str, Any]]:
        """دریافت تسک از Redis Queue."""
        self._validate_owner_id(owner_id)
        r = self._get_redis()
        if not r:
            return None

        try:
            raw = r.lpop(f"queue:{owner_id}")
            if raw:
                task = json.loads(raw)
                logger.info("📤 [%s] تسک %s از صف خارج شد", owner_id, task.get("type"))
                return task
        except Exception as e:
            logger.error("[%s] خطا در dequeue: %s", owner_id, e)
        return None

    def get_queue_length(self, owner_id: int) -> int:
        """دریافت تعداد تسک‌های در صف."""
        self._validate_owner_id(owner_id)
        r = self._get_redis()
        if not r:
            return 0

        try:
            return r.llen(f"queue:{owner_id}")
        except Exception as e:
            logger.error("[%s] خطا در get_queue_length: %s", owner_id, e)
            return 0

    def clear_queue(self, owner_id: int) -> bool:
        """پاک کردن تمام تسک‌های یک اکانت از صف."""
        self._validate_owner_id(owner_id)
        r = self._get_redis()
        if not r:
            return False

        try:
            r.delete(f"queue:{owner_id}")
            logger.info("🧹 [%s] صف تسک‌ها پاک شد", owner_id)
            return True
        except Exception as e:
            logger.error("[%s] خطا در clear_queue: %s", owner_id, e)
            return False


# ─── Singleton ────────────────────────────────────────────────────────────────
_bot_manager: Optional[AdvancedBotManager] = None


def get_bot_manager() -> AdvancedBotManager:
    """دریافت instance مدیریت بات‌ها (Singleton)."""
    global _bot_manager
    if _bot_manager is None:
        _bot_manager = AdvancedBotManager()
        logger.info("✅ AdvancedBotManager ایجاد شد")
    return _bot_manager


# برای سازگاری با کد قدیمی
bot_manager = get_bot_manager()
