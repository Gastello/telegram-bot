import os
import re
from io import BytesIO
from typing import Optional

import requests
from PIL import Image, ImageDraw, ImageFont

CANVAS_WIDTH = 1280
CANVAS_HEIGHT = 1280
BACKGROUND_HEIGHT = 1100

COLOR_TEXT = "#B8ED15"

DATE_FONT_SIZE = 48
DATE_LEFT = 20
DATE_TOP = 44

OLD_PRICE_FONT_SIZE = 48
OLD_PRICE_LEFT = 20
OLD_PRICE_BOTTOM = 20

NEW_PRICE_FONT_SIZE = 96
NEW_PRICE_BOTTOM = 84

ASSETS_DIR = "assets"
LAYOUT_PATH = os.path.join(ASSETS_DIR, "layout.png")
FONT_PATH = os.path.join(ASSETS_DIR, "PressStart2P-Regular.ttf")

OUTPUT_DIR = "generated"
os.makedirs(OUTPUT_DIR, exist_ok=True)

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}

VARIANT_SOURCE_MAP = {
    "1": "page_bg_raw",
    "2": "library_hero",
    "3": "header_image",
}

MONTHS_UA_TO_NUM = {
    "січня": "01",
    "лютого": "02",
    "березня": "03",
    "квітня": "04",
    "травня": "05",
    "червня": "06",
    "липня": "07",
    "серпня": "08",
    "вересня": "09",
    "жовтня": "10",
    "листопада": "11",
    "грудня": "12",
}


def get_generated_image_path(moderation_id: int, kind: str) -> str:
    return os.path.join(OUTPUT_DIR, f"{moderation_id}_{kind}.png")


def get_custom_upload_path(moderation_id: int, ext: str = "jpg") -> str:
    return os.path.join(OUTPUT_DIR, f"{moderation_id}_custom_upload.{ext}")


def download_image(url: str) -> Optional[Image.Image]:
    try:
        response = requests.get(url, timeout=30, headers=REQUEST_HEADERS)
        response.raise_for_status()
        return Image.open(BytesIO(response.content)).convert("RGBA")
    except Exception as error:
        print(f"[IMG ERROR] Failed to download {url}: {error}")
        return None


def cover_crop(image: Image.Image, target_width: int, target_height: int) -> Image.Image:
    src_w, src_h = image.size
    src_ratio = src_w / src_h
    target_ratio = target_width / target_height

    if src_ratio > target_ratio:
        scale = target_height / src_h
    else:
        scale = target_width / src_w

    new_w = int(src_w * scale)
    new_h = int(src_h * scale)

    resized = image.resize((new_w, new_h), Image.Resampling.LANCZOS)

    left = (new_w - target_width) // 2
    top = (new_h - target_height) // 2
    right = left + target_width
    bottom = top + target_height

    return resized.crop((left, top, right, bottom))


def get_background_candidates(appid: str, header_image_url: str = "") -> list[str]:
    return [
        f"https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/{appid}/page_bg_raw.jpg",
        f"https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/{appid}/library_hero.jpg",
        header_image_url,
        f"https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/{appid}/capsule_616x353.jpg",
        f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg",
    ]


def get_background_url_by_source(appid: str, source_type: str, header_image_url: str = "") -> str:
    if source_type == "page_bg_raw":
        return f"https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/{appid}/page_bg_raw.jpg"
    if source_type == "library_hero":
        return f"https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/{appid}/library_hero.jpg"
    if source_type == "header_image":
        return header_image_url or f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg"

    candidates = [u for u in get_background_candidates(appid, header_image_url) if u and u.strip()]
    if not candidates:
        raise ValueError(f"No background candidates for appid={appid}")
    return candidates[0]


def load_background(
    appid: str,
    header_image_url: str = "",
    source_type: str = "auto",
    custom_background_path: str | None = None,
) -> Image.Image:
    if custom_background_path:
        return Image.open(custom_background_path).convert("RGBA")

    if source_type == "auto":
        candidates = [u for u in get_background_candidates(appid, header_image_url) if u and u.strip()]
        for url in candidates:
            image = download_image(url)
            if image is not None:
                print(f"[IMG] Using background: {url}")
                return image
        raise RuntimeError(f"Could not load background for appid={appid}")

    url = get_background_url_by_source(appid, source_type, header_image_url)
    image = download_image(url)
    if image is None:
        raise RuntimeError(f"Could not load {source_type} background for appid={appid}")
    print(f"[IMG] Using background: {url}")
    return image


def format_price_for_image(value: float, currency: str) -> str:
    if currency == "UAH":
        if float(value).is_integer():
            return f"{int(value)} грн"
        return f"{value:.2f} грн"

    if float(value).is_integer():
        return f"{int(value)} {currency}"
    return f"{value:.2f} {currency}"


def format_sale_end_for_image(sale_end_text: str | None) -> str:
    if not sale_end_text:
        return ""

    text = sale_end_text.strip().lower()

    # already numeric-like
    match_numeric = re.search(r"(\d{1,2})[./-](\d{1,2})", text)
    if match_numeric:
        day = int(match_numeric.group(1))
        month = int(match_numeric.group(2))
        return f"до {day:02d}.{month:02d}"

    # ukrainian month text, e.g. "19 березня"
    match_words = re.search(r"(\d{1,2})\s+([а-щьюяєіїґ]+)", text)
    if match_words:
        day = int(match_words.group(1))
        month_word = match_words.group(2)
        month_num = MONTHS_UA_TO_NUM.get(month_word)
        if month_num:
            return f"до {day:02d}.{month_num}"

    return ""


def load_font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_PATH, size)


def draw_sale_end(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> None:
    if not text:
        return

    draw.text(
        (DATE_LEFT, DATE_TOP),
        text,
        font=font,
        fill=COLOR_TEXT,
    )


def draw_old_price(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    x = OLD_PRICE_LEFT
    y = CANVAS_HEIGHT - OLD_PRICE_BOTTOM - text_h

    draw.text((x, y), text, font=font, fill=COLOR_TEXT)

    line_y = y + text_h * 0.52
    draw.line(
        [(x, line_y), (x + text_w, line_y)],
        fill=COLOR_TEXT,
        width=3,
    )


def draw_new_price(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    text_h = bbox[3] - bbox[1]

    x = CANVAS_WIDTH // 2
    y = CANVAS_HEIGHT - NEW_PRICE_BOTTOM - text_h // 2

    draw.text((x, y), text, font=font, fill=COLOR_TEXT, anchor="mm")


def generate_post_image(
    appid: str,
    final_price: float,
    initial_price: float,
    currency: str,
    header_image_url: str = "",
    source_type: str = "auto",
    custom_background_path: str | None = None,
    sale_end_text: str | None = None,
    output_path: Optional[str] = None,
) -> str:
    background = load_background(
        appid=appid,
        header_image_url=header_image_url,
        source_type=source_type,
        custom_background_path=custom_background_path,
    )
    background = cover_crop(background, CANVAS_WIDTH, BACKGROUND_HEIGHT)

    canvas = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 255))
    canvas.alpha_composite(background, (0, 0))

    layout = Image.open(LAYOUT_PATH).convert("RGBA")
    if layout.size != (CANVAS_WIDTH, CANVAS_HEIGHT):
        layout = layout.resize((CANVAS_WIDTH, CANVAS_HEIGHT), Image.Resampling.LANCZOS)

    canvas.alpha_composite(layout, (0, 0))

    draw = ImageDraw.Draw(canvas)

    date_font = load_font(DATE_FONT_SIZE)
    old_price_font = load_font(OLD_PRICE_FONT_SIZE)
    new_price_font = load_font(NEW_PRICE_FONT_SIZE)

    sale_end_image_text = format_sale_end_for_image(sale_end_text)
    old_price_text = format_price_for_image(initial_price, currency)
    new_price_text = format_price_for_image(final_price, currency)

    draw_sale_end(draw, sale_end_image_text, date_font)
    draw_old_price(draw, old_price_text, old_price_font)
    draw_new_price(draw, new_price_text, new_price_font)

    if output_path is None:
        output_path = os.path.join(OUTPUT_DIR, f"{appid}.png")

    canvas.save(output_path)
    return output_path