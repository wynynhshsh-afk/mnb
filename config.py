import os
from dotenv import load_dotenv
import re

load_dotenv()

# ─── تلگرام ──────────────────────────────────────────────────────────────────
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
# ربات کمکی پنل دکمه‌ای سلف (اختیاری - اگر خالی باشد پنل دکمه‌ای غیرفعال می‌ماند)
HELPER_BOT_TOKEN = os.environ.get("HELPER_BOT_TOKEN", "")

# ─── سرور ──────────────────────────────────────────────────────────────────
SECRET_KEY = os.environ.get("SECRET_KEY", "nexoself_secret_key_change_me")
PORT = int(os.environ.get("PORT", 5000))
SITE_URL = os.environ.get("SITE_URL", "")

# ─── مالک ──────────────────────────────────────────────────────────────────
OWNER_TG_ID = int(os.environ.get("OWNER_TG_ID", "8540004957"))
OWNER_USERNAME = os.environ.get("OWNER_USERNAME", "n_boy55")
OWNER_PHONE = os.environ.get("OWNER_PHONE", "").lstrip("+")

# ─── دیتابیس پایدار (Supabase PostgreSQL) ──────────────────────────────────
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# ✅ استخراج صحیح SUPABASE_URL از DATABASE_URL
if DATABASE_URL:
    # postgresql://postgres.vijfkltyashuzhqcecff:Amirabas00v89%40@aws-0-eu-west-1.pooler.supabase.com:6543/postgres
    match = re.search(r'postgresql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)', DATABASE_URL)
    if match:
        user, password, host, port, dbname = match.groups()
        # استخراج Project ID از host
        # host: aws-0-eu-west-1.pooler.supabase.com
        # Project ID: vijfkltyashuzhqcecff (از user)
        project_id = user.split('.')[-1] if '.' in user else user
        SUPABASE_URL = f"https://{project_id}.supabase.co"
        print(f"✅ استخراج SUPABASE_URL: {SUPABASE_URL}")
    else:
        SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
else:
    SUPABASE_URL = os.environ.get("SUPABASE_URL", "")

SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
SUPABASE_TABLE_PREFIX = os.environ.get("SUPABASE_TABLE_PREFIX", "amel_")

# ─── Upstash Redis ─────────────────────────────────────────────────────────
UPSTASH_REDIS_URL = os.environ.get("UPSTASH_REDIS_URL", "")

# ─── دیتابیس موقت ──────────────────────────────────────────────────────────
CACHE_DB_PATH = os.environ.get("CACHE_DB_PATH", "cache.db")

# ─── سیستم ──────────────────────────────────────────────────────────────────
BOT_NAME = "NexoSelf"
BOT_VERSION = "1.2.0"
WEATHER_API_KEY = os.environ.get("WEATHER_API_KEY", "")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")

# ─── سیستم توکن ──────────────────────────────────────────────────────────────
TOKENS_PER_SESSION = 2
SESSION_HOURS = 2
DAILY_TOKEN_GIFT = 10
REFERRAL_TOKENS = 12
WELCOME_TOKENS = 10
TOKEN_PRICE_TOMAN = 200

# ─── اسپانسرها ───────────────────────────────────────────────────────────────
SPONSORS = [
    {"username": "pesar777", "name": "اسپانسر اول"},
    {"username": "ISOLODEVIL", "name": "اسپانسر دوم"},
]

# ─── کش تنظیمات ──────────────────────────────────────────────────────────────
CACHE_TTL = 60

# ─── سیستم جام جهانی ──────────────────────────────────────────────────────────
FOOTBALL_API_KEY   = os.environ.get("FOOTBALL_API_KEY", "")   # کلید API از football-data.org
WC_CHANNEL_ID      = os.environ.get("WC_CHANNEL_ID", "")      # آیدی کانال (مثال: @mychannel یا -1001234567)
WC_MIN_BET         = int(os.environ.get("WC_MIN_BET", "5"))  # حداقل مبلغ شرط
WC_MAX_BET         = int(os.environ.get("WC_MAX_BET", "9999999")) # حداکثر مبلغ شرط
WC_POLL_INTERVAL   = int(os.environ.get("WC_POLL_INTERVAL", "600"))  # هر چند ثانیه چک شود (پیش‌فرض: 10 دقیقه)
WC_COMPETITION     = os.environ.get("WC_COMPETITION", "WC")   # کد مسابقه (WC = FIFA World Cup)
