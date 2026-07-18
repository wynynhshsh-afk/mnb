# health_check.py
# سیستم Health Check برای پایداری در Render

import threading
import time
import json
from typing import Dict, Any
from flask import Flask, jsonify, request
import redis_cache as rc
from heartbeat import get_heartbeat_manager

def create_health_blueprint():
    """ساخت Blueprint برای Health Check"""
    from flask import Blueprint
    
    health_bp = Blueprint('health', __name__)
    hb_manager = get_heartbeat_manager()
    
    @health_bp.route('/health', methods=['GET'])
    def health_check():
        """Health Check اصلی"""
        active_owners = hb_manager.get_all_alive()
        alive_count = len(active_owners)
        queued_tasks = 0
        
        r = rc.get_redis()
        if r:
            try:
                # تعداد تسک‌های در صف
                queue_keys = r.keys("queue:*")
                for key in queue_keys:
                    queued_tasks += r.llen(key)
            except Exception:
                pass
        
        return jsonify({
            "status": "ok",
            "active_bots": alive_count,
            "queued_tasks": queued_tasks,
            "timestamp": time.time()
        }), 200
    
    @health_bp.route('/health/bots', methods=['GET'])
    def bots_status():
        """دریافت وضعیت همه بات‌ها"""
        active = hb_manager.get_all_alive()
        return jsonify({
            "active_bots": active,
            "count": len(active)
        }), 200
    
    @health_bp.route('/health/cleanup', methods=['POST'])
    def cleanup_stale():
        """پاک کردن Heartbeatهای قدیمی"""
        r = rc.get_redis()
        if r:
            try:
                keys = r.keys("hb:*")
                stale = 0
                for key in keys:
                    # اگر TTL باقی‌مانده کمتر از ۱۰ ثانیه بود، حذف کن
                    ttl = r.ttl(key)
                    if ttl < 10:
                        r.delete(key)
                        stale += 1
                return jsonify({"cleaned": stale}), 200
            except Exception:
                pass
        return jsonify({"error": "Redis unavailable"}), 500
    
    return health_bp
