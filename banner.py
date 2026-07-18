"""
banner.py - تولید عکس بنر تزئینی برای پنل سلف (مثل طرح Self Vtr)
استفاده:
    from banner import generate_banner
    img_bytes = generate_banner(profile_photo_bytes, bottom_text="self vtr", bottom_sub="@selflvipbot")
"""

import io
import math
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps

# ─── تنظیمات بوم ────────────────────────────────────────────────────────────
W, H = 1000, 560
BG_COLOR = (10, 10, 12)
FRAME_COLOR = (235, 235, 235)
PURPLE = (124, 58, 196)

FONT_PATH_BOLD = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
FONT_PATH_REGULAR = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"


def _load_font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def _make_circle_avatar(photo_bytes: bytes, diameter: int) -> Image.Image:
    """عکس پروفایل کاربر رو به یک دایره با حاشیه تبدیل می‌کنه."""
    img = Image.open(io.BytesIO(photo_bytes)).convert("RGB")
    # crop مربعی از وسط
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


def _draw_corner_flourish(draw: ImageDraw.ImageDraw, x, y, size, flip_x=False, flip_y=False, color=FRAME_COLOR):
    """یه طرح تزئینی ساده‌ی گوشه (مثل اسکرول/پیچک) با چندتا قوس می‌کشه."""
    sx = -1 if flip_x else 1
    sy = -1 if flip_y else 1

    def pt(dx, dy):
        return (x + sx * dx, y + sy * dy)

    # خط افقی و عمودی اصلی قاب
    draw.line([pt(0, 0), pt(size, 0)], fill=color, width=2)
    draw.line([pt(0, 0), pt(0, size)], fill=color, width=2)

    # یک قوس کوچیک تزئینی نزدیک گوشه
    bbox = [pt(0, 0)[0] - sx * 18, pt(0, 0)[1] - sy * 18, pt(0, 0)[0] + sx * 18, pt(0, 0)[1] + sy * 18]
    bbox = [min(bbox[0], bbox[2]), min(bbox[1], bbox[3]), max(bbox[0], bbox[2]), max(bbox[1], bbox[3])]
    start_angle = 180 if (not flip_x and not flip_y) else 0
    try:
        draw.arc(bbox, start=0, end=360, fill=color, width=1)
    except Exception:
        pass

    # چند نقطه کوچیک تزئینی روی خط
    for i in range(1, 4):
        px, py = pt(size * i / 4, 0)
        draw.ellipse((px - 2, py - 2, px + 2, py + 2), fill=color)
    for i in range(1, 4):
        px, py = pt(0, size * i / 4)
        draw.ellipse((px - 2, py - 2, px + 2, py + 2), fill=color)


def generate_banner(
    profile_photo_bytes: bytes,
    bottom_text: str = "self nexo",
    bottom_sub: str = "",
) -> bytes:
    """
    یک بنر تزئینی شبیه طرح Self Vtr تولید می‌کنه:
    - پس‌زمینه تیره با گرادینت ملایم
    - قاب تزئینی با خطوط و فلش‌های گوشه
    - عکس پروفایل کاربر داخل دایره با حاشیه
    - متن بزرگ پایین (bottom_text)
    - زیرنویس کوچک (bottom_sub) اختیاری

    خروجی: بایت‌های PNG
    """
    # ── پس‌زمینه با گرادینت شعاعی تیره ──────────────────────────────────────
    img = Image.new("RGB", (W, H), BG_COLOR)
    grad = Image.new("L", (W, H), 0)
    gdraw = ImageDraw.Draw(grad)
    cx, cy = W // 2, int(H * 0.42)
    max_r = int(math.hypot(W, H) / 1.4)
    for r in range(max_r, 0, -4):
        val = int(60 * (1 - r / max_r))
        gdraw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=val)
    grad = grad.filter(ImageFilter.GaussianBlur(40))
    glow = Image.new("RGB", (W, H), (40, 20, 55))
    img = Image.composite(glow, img, grad)

    draw = ImageDraw.Draw(img)

    # ── قاب بیرونی نازک ──────────────────────────────────────────────────
    margin = 28
    draw.rectangle(
        [margin, margin, W - margin, H - margin],
        outline=FRAME_COLOR, width=1
    )

    # ── فلش‌های تزئینی گوشه ──────────────────────────────────────────────
    fl_size = 70
    inset = margin + 14
    _draw_corner_flourish(draw, inset, inset, fl_size, flip_x=False, flip_y=False)
    _draw_corner_flourish(draw, W - inset, inset, fl_size, flip_x=True, flip_y=False)
    _draw_corner_flourish(draw, inset, H - inset, fl_size, flip_x=False, flip_y=True)
    _draw_corner_flourish(draw, W - inset, H - inset, fl_size, flip_x=True, flip_y=True)

    # ── خط افقی بالا (مثل طرح اصلی) ──────────────────────────────────────
    line_y = int(H * 0.16)
    draw.line([(margin + 60, line_y), (W - margin - 60, line_y)], fill=FRAME_COLOR, width=1)

    # ── دایره عکس پروفایل با حاشیه دوتایی ──────────────────────────────
    diameter = 230
    avatar_cx, avatar_cy = W // 2, int(H * 0.40)

    # حلقه بیرونی
    ring_r = diameter // 2 + 14
    draw.ellipse(
        (avatar_cx - ring_r, avatar_cy - ring_r, avatar_cx + ring_r, avatar_cy + ring_r),
        outline=FRAME_COLOR, width=2
    )
    ring_r2 = diameter // 2 + 8
    draw.ellipse(
        (avatar_cx - ring_r2, avatar_cy - ring_r2, avatar_cx + ring_r2, avatar_cy + ring_r2),
        outline=FRAME_COLOR, width=1
    )

    avatar = _make_circle_avatar(profile_photo_bytes, diameter)
    img.paste(
        avatar,
        (avatar_cx - diameter // 2, avatar_cy - diameter // 2),
        avatar
    )
    draw = ImageDraw.Draw(img)  # رفرش بعد از paste

    # ── متن بزرگ پایین ──────────────────────────────────────────────────
    big_font = _load_font(FONT_PATH_BOLD, 54)
    text_y = int(H * 0.70)
    bbox = draw.textbbox((0, 0), bottom_text, font=big_font)
    tw = bbox[2] - bbox[0]
    draw.text((W // 2 - tw // 2, text_y), bottom_text, font=big_font, fill=(255, 255, 255))

    # ── زیرنویس کوچک (اختیاری) ──────────────────────────────────────────
    if bottom_sub:
        sub_font = _load_font(FONT_PATH_REGULAR, 22)
        sbbox = draw.textbbox((0, 0), bottom_sub, font=sub_font)
        sw = sbbox[2] - sbbox[0]
        draw.text((W // 2 - sw // 2, text_y + 64), bottom_sub, font=sub_font, fill=(190, 190, 190))

    # ── خروجی PNG ────────────────────────────────────────────────────────
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

