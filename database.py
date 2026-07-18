# database.py - Bridge بین دیتابیس‌ها
import hashlib
import datetime
from typing import Optional, Dict, List, Any

# ─── ایمپورت از دیتابیس اصلی (Supabase) ──────────────────────────────────────
from database_supabase import (
    create_account as supa_create_account,
    verify_account as supa_verify_account,
    get_account as supa_get_account,
    get_account_by_username as supa_get_account_by_username,
    get_account_by_tg_id as supa_get_account_by_tg_id,
    get_all_accounts as supa_get_all_accounts,
    account_exists as supa_account_exists,
    save_telegram_user_id as supa_save_telegram_user_id,
    get_telegram_id_by_owner as supa_get_telegram_id_by_owner,
    get_setting as supa_get_setting,
    set_setting as supa_set_setting,
    toggle_setting as supa_toggle_setting,
    get_all_logged_in_users as supa_get_all_logged_in_users,
    init_user_settings as supa_init_user_settings,
    get_token_balance as supa_get_token_balance,
    add_tokens as supa_add_tokens,
    deduct_tokens as supa_deduct_tokens,
    transfer_diamonds as supa_transfer_diamonds,
    claim_daily_token as supa_claim_daily_token,
    get_token_stats as supa_get_token_stats,
    process_referral as supa_process_referral,
    get_referral_count as supa_get_referral_count,
    save_message_slot as supa_save_message_slot,
    get_message_slot as supa_get_message_slot,
    add_scheduled_message as supa_add_scheduled_message,
    get_pending_scheduled as supa_get_pending_scheduled,
    mark_scheduled_sent as supa_mark_scheduled_sent,
    log_deleted_message as supa_log_deleted_message,
    get_deleted_messages as supa_get_deleted_messages,
    get_forced_channels as supa_get_forced_channels,
    add_forced_channel as supa_add_forced_channel,
    remove_forced_channel as supa_remove_forced_channel,
    create_world_cup_challenge as supa_create_world_cup_challenge,
    update_challenge_message as supa_update_challenge_message,
    join_world_cup_challenge as supa_join_world_cup_challenge,
    finish_world_cup_challenge as supa_finish_world_cup_challenge,
    create_wc_challenge as supa_create_wc_challenge,
    wc_challenge_exists as supa_wc_challenge_exists,
    set_wc_channel_msg as supa_set_wc_channel_msg,
    get_wc_challenge as supa_get_wc_challenge,
    get_pending_wc_challenges as supa_get_pending_wc_challenges,
    join_wc_challenge as supa_join_wc_challenge,
    finish_wc_challenge as supa_finish_wc_challenge,
    cancel_bet as supa_cancel_bet,
    create_bet as supa_create_bet,
    get_bet as supa_get_bet,
    update_bet_message as supa_update_bet_message,
    join_bet as supa_join_bet,
    finish_bet as supa_finish_bet,
    get_global_setting as supa_get_global_setting,
    set_global_setting as supa_set_global_setting,
    get_subscription as supa_get_subscription,
    set_subscription as supa_set_subscription,
    is_subscribed as supa_is_subscribed,
    transfer_subscription as supa_transfer_subscription,
    create_payment as supa_create_payment,
    update_payment as supa_update_payment,
    get_payment as supa_get_payment,
    get_pending_payments as supa_get_pending_payments,
    SETTING_DEFAULTS,
    _hash_pw,
)

# ─── ایمپورت از دیتابیس کش (SQLite) ──────────────────────────────────────────
import db_cache as cache

# ─── توابع دیتابیس پایدار ──────────────────────────────────────────────────────
def create_account(username: str, password: str) -> Optional[int]:
    return supa_create_account(username, password)

def verify_account(username: str, password: str) -> Optional[int]:
    return supa_verify_account(username, password)

def get_account_password_hash(owner_id: int) -> Optional[str]:
    """دریافت هش رمز عبور برای تأیید هویت از طریق کیپد"""
    from database_supabase import execute_query
    try:
        row = execute_query(
            "SELECT password_hash FROM amel_accounts WHERE id = %s",
            (owner_id,), fetch_one=True
        )
        return row["password_hash"] if row else None
    except Exception:
        return None

def get_account(owner_id: int) -> Optional[Dict]:
    return supa_get_account(owner_id)

def get_account_by_username(username: str) -> Optional[Dict]:
    return supa_get_account_by_username(username)

def get_account_by_tg_id(tg_id: int) -> Optional[Dict]:
    return supa_get_account_by_tg_id(tg_id)

def get_all_accounts() -> List[Dict]:
    return supa_get_all_accounts()

def account_exists() -> bool:
    return supa_account_exists()

def save_telegram_user_id(owner_id: int, tg_user_id: int):
    supa_save_telegram_user_id(owner_id, tg_user_id)

def get_telegram_id_by_owner(owner_id: int) -> Optional[int]:
    return supa_get_telegram_id_by_owner(owner_id)

# ─── توابع تنظیمات ─────────────────────────────────────────────────────────────
def get_setting(owner_id: int, key: str, default=None) -> str:
    return supa_get_setting(owner_id, key, default)

def set_setting(owner_id: int, key: str, value):
    supa_set_setting(owner_id, key, value)

def toggle_setting(owner_id: int, key: str) -> bool:
    return supa_toggle_setting(owner_id, key)

def get_all_logged_in_users() -> List[int]:
    return supa_get_all_logged_in_users()

def init_user_settings(owner_id: int):
    supa_init_user_settings(owner_id)

# ─── توابع توکن ────────────────────────────────────────────────────────────────
def get_token_balance(owner_id: int) -> int:
    return supa_get_token_balance(owner_id)

def add_tokens(owner_id: int, amount: int):
    supa_add_tokens(owner_id, amount)

def deduct_tokens(owner_id: int, amount: int) -> bool:
    return supa_deduct_tokens(owner_id, amount)

def transfer_diamonds(from_owner_id: int, to_owner_id: int, amount: int) -> tuple:
    return supa_transfer_diamonds(from_owner_id, to_owner_id, amount)

def claim_daily_token(owner_id: int):
    return supa_claim_daily_token(owner_id)

def get_token_stats(owner_id: int) -> dict:
    return supa_get_token_stats(owner_id)

def process_referral(referrer_owner_id: int, referred_tg_id: int) -> bool:
    return supa_process_referral(referrer_owner_id, referred_tg_id)

def get_referral_count(owner_id: int) -> int:
    return supa_get_referral_count(owner_id)

# ─── 📋 توابع دشمن (ذخیره در دیتابیس کش) ──────────────────────────────────────
def add_enemy(owner_id: int, user_id: int, username=None, name=None):
    return cache.add_enemy(owner_id, user_id, username, name)

def remove_enemy(owner_id: int, user_id: int) -> bool:
    return cache.remove_enemy(owner_id, user_id)

def get_enemies(owner_id: int) -> List[Dict]:
    return cache.get_enemies(owner_id)

def is_enemy(owner_id: int, user_id: int) -> bool:
    return cache.is_enemy(owner_id, user_id)

def clear_enemies(owner_id: int):
    cache.clear_enemies(owner_id)

def get_enemy_count(owner_id: int) -> int:
    return cache.get_enemy_count(owner_id)

# ─── 📋 توابع دوست (ذخیره در دیتابیس کش) ──────────────────────────────────────
def add_friend(owner_id: int, user_id: int, username=None, name=None):
    return cache.add_friend(owner_id, user_id, username, name)

def remove_friend(owner_id: int, user_id: int) -> bool:
    return cache.remove_friend(owner_id, user_id)

def get_friends(owner_id: int) -> List[Dict]:
    return cache.get_friends(owner_id)

def is_friend(owner_id: int, user_id: int) -> bool:
    return cache.is_friend(owner_id, user_id)

def clear_friends(owner_id: int):
    cache.clear_friends(owner_id)

def get_friend_count(owner_id: int) -> int:
    return cache.get_friend_count(owner_id)

# ─── توابع پیام ────────────────────────────────────────────────────────────────
def save_message_slot(owner_id: int, slot: int, content, media_path=None):
    supa_save_message_slot(owner_id, slot, content, media_path)

def get_message_slot(owner_id: int, slot: int):
    return supa_get_message_slot(owner_id, slot)

def add_scheduled_message(owner_id: int, chat_id, message, send_at):
    return supa_add_scheduled_message(owner_id, chat_id, message, send_at)

def get_pending_scheduled(owner_id: int):
    return supa_get_pending_scheduled(owner_id)

def mark_scheduled_sent(msg_id: int):
    supa_mark_scheduled_sent(msg_id)

def log_deleted_message(owner_id: int, chat_id, sender_id, sender_name, message, media_type=None):
    supa_log_deleted_message(owner_id, chat_id, sender_id, sender_name, message, media_type)

def get_deleted_messages(owner_id: int, limit=50):
    return supa_get_deleted_messages(owner_id, limit)

# ─── ✅ توابع سایلنت (دیتابیس کش) ──────────────────────────────────────────────
def add_silent_chat(owner_id: int, chat_id: int):
    cache.add_silent_chat(owner_id, chat_id)

def remove_silent_chat(owner_id: int, chat_id: int):
    cache.remove_silent_chat(owner_id, chat_id)

def is_silent_chat(owner_id: int, chat_id: int) -> bool:
    return cache.is_silent_chat(owner_id, chat_id)

def add_silent_user(owner_id: int, user_id: int):
    cache.add_silent_user(owner_id, user_id)

def remove_silent_user(owner_id: int, user_id: int):
    cache.remove_silent_user(owner_id, user_id)

def is_silent_user(owner_id: int, user_id: int) -> bool:
    return cache.is_silent_user(owner_id, user_id)

# ─── ✅ توابع چنل‌های اجباری (دیتابیس دائمی Supabase) ─────────────────────────
def get_forced_channels():
    return supa_get_forced_channels()

def add_forced_channel(username: str) -> bool:
    return supa_add_forced_channel(username)

def remove_forced_channel(username: str) -> bool:
    return supa_remove_forced_channel(username)

def check_user_membership(bot, user_id: int) -> tuple:
    return cache.check_user_membership(bot, user_id)

# ─── ✅ توابع چالش جام جهانی ───────────────────────────────────────────────────
def create_world_cup_challenge(team1, team2, match_time, bet_amount):
    return supa_create_world_cup_challenge(team1, team2, match_time, bet_amount)
def update_challenge_message(challenge_id, message_id, chat_id):
    return supa_update_challenge_message(challenge_id, message_id, chat_id)
def join_world_cup_challenge(challenge_id, user_id, user_tg_id, chosen_team, amount):
    return supa_join_world_cup_challenge(challenge_id, user_id, user_tg_id, chosen_team, amount)
def finish_world_cup_challenge(challenge_id, winner_team):
    return supa_finish_world_cup_challenge(challenge_id, winner_team)
def create_wc_challenge(match_id, team1, team2, match_time):
    return supa_create_wc_challenge(match_id, team1, team2, match_time)
def wc_challenge_exists(match_id):
    return supa_wc_challenge_exists(match_id)
def set_wc_channel_msg(challenge_id, msg_id):
    return supa_set_wc_channel_msg(challenge_id, msg_id)
def get_wc_challenge(challenge_id):
    return supa_get_wc_challenge(challenge_id)
def get_pending_wc_challenges():
    return supa_get_pending_wc_challenges()
def join_wc_challenge(challenge_id, user_id, user_tg_id, selected_option, amount):
    return supa_join_wc_challenge(challenge_id, user_id, user_tg_id, selected_option, amount)
def finish_wc_challenge(challenge_id, winner_option):
    return supa_finish_wc_challenge(challenge_id, winner_option)

# ─── ✅ توابع شرط‌بندی ──────────────────────────────────────────────────────────
def create_bet(creator_id: int, creator_tg_id: int, amount: int, chat_id: int):
    return supa_create_bet(creator_id, creator_tg_id, amount, chat_id)

def get_bet(bet_id: int):
    return supa_get_bet(bet_id)

def update_bet_message(bet_id: int, message_id: int):
    return supa_update_bet_message(bet_id, message_id)

def join_bet(bet_id: int, opponent_id: int, opponent_tg_id: int):
    return supa_join_bet(bet_id, opponent_id, opponent_tg_id)

def finish_bet(bet_id: int):
    return supa_finish_bet(bet_id)

def cancel_bet(bet_id: int):
    return supa_cancel_bet(bet_id)

# ─── ✅ توابع خرید و اشتراک ────────────────────────────────────────────────────
def get_global_setting(key, default=""):
    return supa_get_global_setting(key, default)
def set_global_setting(key, value):
    return supa_set_global_setting(key, value)
def get_subscription(owner_id):
    return supa_get_subscription(owner_id)
def set_subscription(owner_id, plan, days):
    return supa_set_subscription(owner_id, plan, days)
def is_subscribed(owner_id):
    return supa_is_subscribed(owner_id)
def transfer_subscription(from_owner_id, to_owner_id):
    return supa_transfer_subscription(from_owner_id, to_owner_id)
def create_payment(owner_id, tg_id, ptype, plan=None, diamond_amount=None, toman_amount=None):
    return supa_create_payment(owner_id, tg_id, ptype, plan, diamond_amount, toman_amount)
def update_payment(payment_id, **kwargs):
    return supa_update_payment(payment_id, **kwargs)
def get_payment(payment_id):
    return supa_get_payment(payment_id)
def get_pending_payments():
    return supa_get_pending_payments()

# ─── صادرات ────────────────────────────────────────────────────────────────────
__all__ = [
    # حساب‌ها
    'create_account', 'verify_account', 'get_account',
    'get_account_by_username', 'get_account_by_tg_id',
    'get_all_accounts', 'account_exists', 'save_telegram_user_id',
    'get_telegram_id_by_owner',
    
    # تنظیمات
    'get_setting', 'set_setting', 'toggle_setting',
    'get_all_logged_in_users', 'init_user_settings',
    
    # توکن
    'get_token_balance', 'add_tokens', 'deduct_tokens', 'transfer_diamonds',
    'transfer_subscription',
    'claim_daily_token', 'get_token_stats',
    'process_referral', 'get_referral_count',
    
    # دشمن
    'add_enemy', 'remove_enemy', 'get_enemies', 'is_enemy', 'clear_enemies', 'get_enemy_count',
    
    # دوست
    'add_friend', 'remove_friend', 'get_friends', 'is_friend', 'clear_friends', 'get_friend_count',
    
    # پیام
    'save_message_slot', 'get_message_slot',
    'add_scheduled_message', 'get_pending_scheduled', 'mark_scheduled_sent',
    'log_deleted_message', 'get_deleted_messages',
    
    # سایلنت
    'add_silent_chat', 'remove_silent_chat', 'is_silent_chat',
    'add_silent_user', 'remove_silent_user', 'is_silent_user',
    
    # چنل‌های اجباری
    'get_forced_channels', 'add_forced_channel', 'remove_forced_channel', 'check_user_membership',

    # شرط‌بندی
    'create_bet', 'get_bet', 'update_bet_message', 'join_bet', 'finish_bet', 'cancel_bet',
]


# ─── سیستم ماموریت‌ها ─────────────────────────────────────────────────────────
from database_supabase import (
    get_active_missions as supa_get_active_missions,
    add_mission as supa_add_mission,
    remove_mission as supa_remove_mission,
    get_completed_mission_ids as supa_get_completed_mission_ids,
    complete_mission as supa_complete_mission,
    get_all_telegram_ids as supa_get_all_telegram_ids,
    get_wc_participants as supa_get_wc_participants,
)

def get_active_missions() -> list:
    return supa_get_active_missions()

def add_mission(channel_username: str, reward: int) -> bool:
    return supa_add_mission(channel_username, reward)

def remove_mission(mission_id: int) -> bool:
    return supa_remove_mission(mission_id)

def get_completed_mission_ids(owner_id: int) -> list:
    return supa_get_completed_mission_ids(owner_id)

def complete_mission(owner_id: int, mission_id: int, reward: int) -> bool:
    return supa_complete_mission(owner_id, mission_id, reward)

def get_all_telegram_ids() -> list:
    return supa_get_all_telegram_ids()

def get_wc_participants() -> list:
    return supa_get_wc_participants()


# ─── سیستم ادمین‌های فرعی ────────────────────────────────────────────────────
from database_supabase import (
    add_sub_admin as supa_add_sub_admin,
    remove_sub_admin as supa_remove_sub_admin,
    get_sub_admins as supa_get_sub_admins,
    is_sub_admin as supa_is_sub_admin,
)

def add_sub_admin(telegram_id: int, name: str = "") -> bool:
    return supa_add_sub_admin(telegram_id, name)

def remove_sub_admin(telegram_id: int) -> bool:
    return supa_remove_sub_admin(telegram_id)

def get_sub_admins() -> list:
    return supa_get_sub_admins()

def is_sub_admin(telegram_id: int) -> bool:
    return supa_is_sub_admin(telegram_id)

# ─── توابع جدید دسترسی ادمین‌های فرعی ────────────────────────────────────────
from database_supabase import (
    get_sub_admin as supa_get_sub_admin,
    update_sub_admin_permissions as supa_update_sub_admin_permissions,
    sub_admin_has_permission as supa_sub_admin_has_permission,
    ADMIN_PERMISSIONS,
)

def get_sub_admin(telegram_id: int) -> dict:
    return supa_get_sub_admin(telegram_id)

def update_sub_admin_permissions(telegram_id: int, permissions: str) -> bool:
    return supa_update_sub_admin_permissions(telegram_id, permissions)

def sub_admin_has_permission(telegram_id: int, perm: str) -> bool:
    return supa_sub_admin_has_permission(telegram_id, perm)
