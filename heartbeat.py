# heartbeat.py
# سیستم Heartbeat برای مدیریت زنده بودن اکانت‌ها

import time
import threading
import redis
from typing import Optional, Set
import config
import redis_cache as rc

class HeartbeatManager:
    """
    مدیریت Heartbeat اکانت‌ها در Redis
    هر اکانت یک کلید با TTL دارد که هر ۳۰ ثانیه به‌روز می‌شود
    """
    
    def __init__(self):
        self._redis = rc.get_redis()
        self._active_owners: Set[int] = set()
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._check_interval = 30  # هر ۳۰ ثانیه heartbeat ارسال کن
        
    def _get_redis(self):
        """دریافت اتصال Redis (با fallback)"""
        if self._redis is None:
            self._redis = rc.get_redis()
        return self._redis
    
    def register(self, owner_id: int):
        """ثبت اکانت در سیستم heartbeat"""
        with self._lock:
            self._active_owners.add(owner_id)
            # بلافاصله heartbeat ارسال کن
            self._send_heartbeat(owner_id)
            print(f"❤️ [{owner_id}] Heartbeat ثبت شد")
    
    def unregister(self, owner_id: int):
        """حذف اکانت از سیستم heartbeat"""
        with self._lock:
            self._active_owners.discard(owner_id)
            self._clear_heartbeat(owner_id)
            print(f"💔 [{owner_id}] Heartbeat حذف شد")
    
    def _send_heartbeat(self, owner_id: int):
        """ارسال یک heartbeat به Redis"""
        r = self._get_redis()
        if r:
            key = f"hb:{owner_id}"
            try:
                r.setex(key, 60, str(time.time()))  # ۶۰ ثانیه TTL
                return True
            except Exception as e:
                print(f"⚠️ خطا در ارسال heartbeat برای {owner_id}: {e}")
        return False
    
    def _clear_heartbeat(self, owner_id: int):
        """پاک کردن heartbeat از Redis"""
        r = self._get_redis()
        if r:
            try:
                r.delete(f"hb:{owner_id}")
            except Exception:
                pass
    
    def is_alive(self, owner_id: int) -> bool:
        """بررسی زنده بودن یک اکانت — Redis + حافظه با هم چک می‌شن"""
        with self._lock:
            in_memory = owner_id in self._active_owners
        
        # اگر در حافظه ثبت شده، زنده است — بدون نیاز به Redis
        if in_memory:
            return True
        
        # fallback به Redis (برای حالت multi-process)
        r = self._get_redis()
        if r:
            try:
                return r.exists(f"hb:{owner_id}") > 0
            except Exception:
                pass
        
        return False
    
    def get_all_alive(self) -> list:
        """دریافت لیست همه اکانت‌های زنده"""
        r = self._get_redis()
        if r:
            try:
                keys = r.keys("hb:*")
                return [int(k.split(":")[1]) for k in keys]
            except Exception:
                pass
        return list(self._active_owners)
    
    def _heartbeat_loop(self):
        """حلقه اصلی heartbeat - هر ۳۰ ثانیه اجرا می‌شود"""
        while self._running:
            try:
                with self._lock:
                    owners = list(self._active_owners)
                
                for owner_id in owners:
                    self._send_heartbeat(owner_id)
                
                # لاگ تعداد اکانت‌های فعال
                if owners:
                    print(f"❤️ Heartbeat: {len(owners)} اکانت فعال")
                
                time.sleep(self._check_interval)
                
            except Exception as e:
                print(f"⚠️ خطا در حلقه heartbeat: {e}")
                time.sleep(5)
    
    def start(self):
        """شروع سیستم heartbeat در یک thread جداگانه"""
        if self._running:
            return
        
        self._running = True
        self._thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._thread.start()
        print("✅ Heartbeat Manager استارت شد")
    
    def stop(self):
        """متوقف کردن سیستم heartbeat"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        print("⏹ Heartbeat Manager متوقف شد")


# نمونه Singleton
_heartbeat_manager: Optional[HeartbeatManager] = None

def get_heartbeat_manager() -> HeartbeatManager:
    global _heartbeat_manager
    if _heartbeat_manager is None:
        _heartbeat_manager = HeartbeatManager()
    return _heartbeat_manager
