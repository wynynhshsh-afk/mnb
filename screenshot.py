"""
screenshot.py - ساخت تصویرِ «اسکرین» (شبیهِ حباب پیامِ تلگرام، همراه با پروفایل و اسمِ
فرستنده) برای دستورِ «اسکرین». خروجی به فرمت WEBP هست تا بشه به‌عنوانِ استیکر فرستاد.

استفاده:
    from screenshot import generate_message_sticker
    webp_bytes = generate_message_sticker(profile_photo_bytes, "نام فرستنده", "متن پیام")
"""

import io
from PIL import Image, ImageDraw, ImageFont

# ─── تنظیمات ظاهری ──────────────────────────────────────────────────────────
W = 640
PADDING = 28
AVATAR_SIZE = 56
BUBBLE_RADIUS = 26
BUBBLE_COLOR = (37, 44, 60)
BG_COLOR = (18, 20, 26)
NAME_COLOR = (124, 173, 255)
TEXT_COLOR = (235, 235, 240)
TIME_COLOR = (150, 155, 165)

_AVATAR_COLORS = [
    (155, 89, 182), (52, 152, 219), (46, 204, 113),
    (230, 126, 34), (231, 76, 60), (26, 188, 156),
]

# چند مسیر رایج فونت که ممکنه فارسی رو (حداقل نسبتاً) پشتیبانی کنن؛
# اگه هیچ‌کدوم پیدا نشه، می‌ره سراغ DejaVuSans و در نهایت فونت پیش‌فرض PIL.
_FONT_CANDIDATES_BOLD = [
    "/usr/share/fonts/truetype/vazir/Vazir-Bold.ttf",
    "/usr/share/fonts/truetype/vazirmatn/Vazirmatn-Bold.ttf",
    "/usr/share/fonts/opentype/noto/NotoNaskhArabic-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]
_FONT_CANDIDATES_REGULAR = [
    "/usr/share/fonts/truetype/vazir/Vazir.ttf",
    "/usr/share/fonts/truetype/vazirmatn/Vazirmatn-Regular.ttf",
    "/usr/share/fonts/opentype/noto/NotoNaskhArabic-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _load_font(candidates, size):
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _shape_text(text: str) -> str:
    """
    شکل‌دهی و راست‌به‌چپ کردنِ حروفِ فارسی/عربی برای نمایشِ درست توی عکس.
    اگه کتابخونه‌های arabic_reshaper و python-bidi نصب نباشن، همون متنِ خام
    برگردونده می‌شه (باز هم قابل‌خوندنه، فقط حروف به هم متصل نمی‌شن).
    """
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        reshaped = arabic_reshaper.reshape(text)
        return get_display(reshaped)
    except Exception:
        return text


def _make_circle_avatar(photo_bytes: bytes, diameter: int) -> Image.Image:
    """عکسِ پروفایل رو به یک دایره تبدیل می‌کنه."""
    img = Image.open(io.BytesIO(photo_bytes)).convert("RGB")
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    img = img.crop((left, top, left + side, top + side))
    img = img.resize((diameter, diameter), Image.LANCZOS)

    mask = Image.new("L", (diameter, diameter), 0)
    mdraw = ImageDraw.Draw(mask)
    mdraw.ellipse((0, 0, diameter, diameter), fill=255)

    out = Image.new("RGBA", (diameter, diameter), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def _placeholder_avatar(name: str, diameter: int) -> Image.Image:
    """اگه پروفایل عکس نداشت، یه دایره‌ی رنگی با حرفِ اولِ اسم می‌سازه."""
    name = (name or "?").strip() or "?"
    color = _AVATAR_COLORS[sum(ord(c) for c in name) % len(_AVATAR_COLORS)]
    img = Image.new("RGBA", (diameter, diameter), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((0, 0, diameter, diameter), fill=color)
    letter = name[0].upper()
    font = _load_font(_FONT_CANDIDATES_BOLD, int(diameter * 0.5))
    bbox = draw.textbbox((0, 0), letter, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((diameter - tw) / 2 - bbox[0], (diameter - th) / 2 - bbox[1]), letter, font=font, fill=(255, 255, 255, 255))
    return img


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list:
    """متن رو بر اساسِ عرضِ حبابِ پیام، خط‌به‌خط می‌کنه (بر اساسِ متنِ منطقی، قبل از شکل‌دهی)."""
    lines = []
    for paragraph in text.split("\n"):
        if not paragraph:
            lines.append("")
            continue
        words = paragraph.split(" ")
        cur = ""
        for w in words:
            test = (cur + " " + w).strip() if cur else w
            bbox = draw.textbbox((0, 0), _shape_text(test), font=font)
            if bbox[2] - bbox[0] <= max_width or not cur:
                cur = test
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
    return lines


def generate_message_sticker(profile_photo_bytes, sender_name: str, message_text: str, date_str: str = None) -> bytes:
    """
    یک عکسِ شبیهِ حبابِ پیامِ تلگرام (آواتار + اسمِ فرستنده + متنِ پیام) می‌سازه
    و خروجی رو به فرمتِ WEBP (مناسبِ استیکر، حداکثر ضلع ۵۱۲ پیکسل) برمی‌گردونه.
    """
    message_text = (message_text or "(بدون متن)").strip() or "(بدون متن)"
    sender_name = (sender_name or "کاربر").strip() or "کاربر"

    name_font = _load_font(_FONT_CANDIDATES_BOLD, 30)
    text_font = _load_font(_FONT_CANDIDATES_REGULAR, 28)
    time_font = _load_font(_FONT_CANDIDATES_REGULAR, 20)

    bubble_x0 = PADDING + AVATAR_SIZE + 16
    bubble_x1 = W - PADDING
    max_text_width = (bubble_x1 - bubble_x0) - 48

    tmp_canvas = Image.new("RGB", (10, 10))
    tmp_draw = ImageDraw.Draw(tmp_canvas)
    lines = _wrap_text(tmp_draw, message_text, text_font, max_text_width)

    line_height = int(text_font.size * 1.5)
    name_height = int(name_font.size * 1.6)
    bubble_height = name_height + line_height * len(lines) + 55
    bubble_top = PADDING
    H = bubble_top + bubble_height + PADDING

    canvas = Image.new("RGB", (W, H), BG_COLOR)
    draw = ImageDraw.Draw(canvas)

    # ─── آواتار ──────────────────────────────────────────────────────────
    avatar = None
    if profile_photo_bytes:
        try:
            avatar = _make_circle_avatar(profile_photo_bytes, AVATAR_SIZE)
        except Exception:
            avatar = None
    if avatar is None:
        avatar = _placeholder_avatar(sender_name, AVATAR_SIZE)
    canvas.paste(avatar, (PADDING, bubble_top), avatar)

    # ─── حباب پیام ───────────────────────────────────────────────────────
    bubble_y0 = bubble_top
    bubble_y1 = bubble_top + bubble_height
    draw.rounded_rectangle([bubble_x0, bubble_y0, bubble_x1, bubble_y1], radius=BUBBLE_RADIUS, fill=BUBBLE_COLOR)

    # اسمِ فرستنده (راست‌چین، چون معمولاً فارسیه)
    name_display = _shape_text(sender_name)
    bbox = draw.textbbox((0, 0), name_display, font=name_font)
    tw = bbox[2] - bbox[0]
    draw.text((bubble_x1 - 24 - tw, bubble_y0 + 16), name_display, font=name_font, fill=NAME_COLOR)

    # متنِ پیام (خط‌به‌خط، راست‌چین)
    y = bubble_y0 + name_height + 20
    for line in lines:
        shaped = _shape_text(line)
        bbox = draw.textbbox((0, 0), shaped, font=text_font)
        tw = bbox[2] - bbox[0]
        draw.text((bubble_x1 - 24 - tw, y), shaped, font=text_font, fill=TEXT_COLOR)
        y += line_height

    # زمان (اختیاری، گوشه‌ی پایین‌چپِ حباب)
    if date_str:
        time_display = _shape_text(date_str)
        draw.text((bubble_x0 + 24, bubble_y1 - 34), time_display, font=time_font, fill=TIME_COLOR)

    # ─── تبدیل به سایزِ مناسبِ استیکر (حداکثر ضلع ۵۱۲ پیکسل) ─────────────
    scale = 512 / max(canvas.size)
    if scale < 1:
        new_size = (max(1, int(canvas.size[0] * scale)), max(1, int(canvas.size[1] * scale)))
        canvas = canvas.resize(new_size, Image.LANCZOS)

    buf = io.BytesIO()
    canvas.save(buf, format="WEBP")
    buf.seek(0)
    return buf.getvalue()
