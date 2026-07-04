"""Pillow helpers that render individual Stream Deck key images.

Every key is a square RGB image (default 96x96 for the Stream Deck XL). Helpers
return PIL.Image objects; the device layer converts them to the native format.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

KEY_SIZE = 96

BG = (18, 18, 22)
FG = (235, 235, 240)
MUTED = (140, 140, 150)
ACCENT = (90, 170, 255)
WARN = (255, 180, 70)
DANGER = (255, 90, 90)
OK = (90, 210, 140)

_FONT_CANDIDATES = [
    "/System/Library/Fonts/SFNSMono.ttf",
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


@lru_cache(maxsize=64)
def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, ValueError):
            continue
    return ImageFont.load_default()


def _text_size(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def blank_key(color=BG, size: int = KEY_SIZE) -> Image.Image:
    return Image.new("RGB", (size, size), color)


def _draw_centered(
    draw: ImageDraw.ImageDraw,
    text: str,
    y: int,
    font,
    fill,
    size: int = KEY_SIZE,
) -> None:
    w, h = _text_size(draw, text, font)
    x = (size - w) // 2
    draw.text((x, y - h // 2), text, font=font, fill=fill)


def _fit_font(draw, text: str, max_width: int, start: int, min_size: int = 10):
    size = start
    while size > min_size:
        font = _load_font(size)
        w, _ = _text_size(draw, text, font)
        if w <= max_width:
            return font
        size -= 2
    return _load_font(min_size)


def format_tokens(n: int) -> str:
    """Human-readable token counts: 1.2M, 950K, 320."""
    n = int(n)
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def format_cost(usd: float) -> str:
    if usd >= 1000:
        return f"${usd / 1000:.1f}k"
    return f"${usd:.2f}"


def fraction_color(fraction: float) -> tuple[int, int, int]:
    if fraction >= 1.0:
        return DANGER
    if fraction >= 0.8:
        return WARN
    return OK


def stat_key(
    title: str,
    value: str,
    subtitle: str = "",
    value_color=FG,
    title_color=MUTED,
    size: int = KEY_SIZE,
) -> Image.Image:
    """A key with a small title, a large centered value, and optional subtitle."""
    img = blank_key(size=size)
    draw = ImageDraw.Draw(img)

    title_font = _load_font(14)
    _draw_centered(draw, title.upper(), 14, title_font, title_color, size)

    value_font = _fit_font(draw, value, size - 8, 34)
    _draw_centered(draw, value, size // 2 + 4, value_font, value_color, size)

    if subtitle:
        sub_font = _load_font(13)
        _draw_centered(draw, subtitle, size - 14, sub_font, MUTED, size)
    return img


def countdown_key(
    title: str,
    countdown: str,
    subtitle: str = "",
    accent=ACCENT,
    size: int = KEY_SIZE,
) -> Image.Image:
    """A prominent countdown tile (e.g. session reset)."""
    img = blank_key(size=size)
    draw = ImageDraw.Draw(img)

    title_font = _load_font(14)
    _draw_centered(draw, title.upper(), 13, title_font, MUTED, size)

    value_font = _fit_font(draw, countdown, size - 6, 40)
    _draw_centered(draw, countdown, size // 2 + 2, value_font, accent, size)

    if subtitle:
        sub_font = _load_font(13)
        _draw_centered(draw, subtitle, size - 13, sub_font, MUTED, size)
    return img


def model_name_key(
    name: str,
    fraction: float,
    color: tuple[int, int, int],
    size: int = KEY_SIZE,
) -> Image.Image:
    """Left half of a model tile: name + percentage of weekly limit."""
    img = blank_key(size=size)
    draw = ImageDraw.Draw(img)

    # Accent stripe down the left edge.
    draw.rectangle([0, 0, 5, size], fill=color)

    name_font = _fit_font(draw, name, size - 14, 22)
    _draw_centered(draw, name, 26, name_font, FG, size)

    pct = min(fraction * 100.0, 999.0)
    pct_text = f"{pct:.0f}%"
    pct_font = _load_font(30)
    _draw_centered(draw, pct_text, size // 2 + 20, pct_font, fraction_color(fraction), size)
    return img


def model_bar_key(
    used_text: str,
    limit_text: str,
    fraction: float,
    color: tuple[int, int, int],
    size: int = KEY_SIZE,
) -> Image.Image:
    """Right half of a model tile: usage bar with used/limit numbers."""
    img = blank_key(size=size)
    draw = ImageDraw.Draw(img)

    top_font = _load_font(20)
    _draw_centered(draw, used_text, 20, top_font, FG, size)

    # Progress bar.
    bar_x0, bar_x1 = 8, size - 8
    bar_y0, bar_y1 = size // 2 - 4, size // 2 + 10
    draw.rounded_rectangle([bar_x0, bar_y0, bar_x1, bar_y1], radius=4, fill=(45, 45, 52))
    fill_frac = max(0.0, min(fraction, 1.0))
    fill_w = int((bar_x1 - bar_x0) * fill_frac)
    if fill_w > 0:
        bar_color = DANGER if fraction >= 1.0 else color
        draw.rounded_rectangle(
            [bar_x0, bar_y0, bar_x0 + fill_w, bar_y1], radius=4, fill=bar_color
        )

    limit_font = _load_font(13)
    _draw_centered(draw, f"/ {limit_text}", size - 14, limit_font, MUTED, size)
    return img


def title_key(
    line1: str,
    line2: str = "",
    accent=ACCENT,
    size: int = KEY_SIZE,
) -> Image.Image:
    img = blank_key(size=size)
    draw = ImageDraw.Draw(img)
    f1 = _fit_font(draw, line1, size - 8, 26)
    if line2:
        _draw_centered(draw, line1, size // 2 - 12, f1, accent, size)
        f2 = _load_font(18)
        _draw_centered(draw, line2, size // 2 + 16, f2, FG, size)
    else:
        _draw_centered(draw, line1, size // 2, f1, accent, size)
    return img


def message_key(text: str, color=FG, size: int = KEY_SIZE) -> Image.Image:
    """A single centered word/short message across a key."""
    img = blank_key(size=size)
    draw = ImageDraw.Draw(img)
    font = _fit_font(draw, text, size - 8, 22)
    _draw_centered(draw, text, size // 2, font, color, size)
    return img
