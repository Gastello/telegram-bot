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

MONTHS_UA_TO_NUM = {
    "січня": 1,
    "лютого": 2,
    "березня": 3,
    "квітня": 4,
    "травня": 5,
    "червня": 6,
    "липня": 7,
    "серпня": 8,
    "вересня": 9,
    "жовтня": 10,
    "листопада": 11,
    "грудня": 12,
}

DATE_FONT_SIZE = 48
DATE_LEFT = 20
DATE_TOP = 44

OLD_PRICE_FONT_SIZE = 48
OLD_PRICE_LEFT = 20
OLD_PRICE_BOTTOM = 20

NEW_PRICE_FONT_SIZE = 96
NEW_PRICE_BOTTOM = 84

# TikTok image constants
TIKTOK_CANVAS_WIDTH = 1280
TIKTOK_CANVAS_HEIGHT = 1600
TIKTOK_GAME_IMAGE_WIDTH = 1280
TIKTOK_GAME_IMAGE_HEIGHT = 1280
TIKTOK_GAME_IMAGE_TOP_OFFSET = 200

TIKTOK_DISCOUNT_PRICE_FONT_SIZE = 48
TIKTOK_DISCOUNT_PRICE_BOTTOM = 135

TIKTOK_OLD_PRICE_FONT_SIZE = 32
TIKTOK_OLD_PRICE_LEFT = 160
TIKTOK_OLD_PRICE_BOTTOM = 80

TIKTOK_SALE_END_FONT_SIZE = 32
TIKTOK_SALE_END_RIGHT = 160
TIKTOK_SALE_END_BOTTOM = 80

TIKTOK_TITLE_FONT_SIZE = 40
TIKTOK_TITLE_SIDE_PADDING = 160
TIKTOK_TITLE_SHADOW_BLUR = 10
TIKTOK_TITLE_SHADOW_OFFSET = (0, 0)  # Will be applied as filter

ASSETS_DIR = "assets"
LAYOUT_PATH = os.path.join(ASSETS_DIR, "layout.png")
FONT_PATH = os.path.join(ASSETS_DIR, "PressStart2P-Regular.ttf")
TIKTOK_TEMPLATE_PATH = os.path.join(ASSETS_DIR, "tiktok.png")

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
    "4": "capsule_616x353",
    "5": "header_fallback",

    # screenshots
    "6": "screenshot_0",
    "7": "screenshot_1",
    "8": "screenshot_2",
    "9": "screenshot_3",
    "10": "screenshot_4",
}

TIKTOK_VARIANT_SOURCE_MAP = {
    "1": "page_bg_raw",
    "2": "library_hero",
    "3": "header_image",
    "4": "capsule_616x353",
    "5": "header_fallback",

    # screenshots
    "6": "screenshot_0",
    "7": "screenshot_1",
    "8": "screenshot_2",
    "9": "screenshot_3",
    "10": "screenshot_4",
}


def slugify(text: str) -> str:
    text = text.lower()
    text = text.replace("ё", "е")
    text = text.replace("®", "")
    text = text.replace("™", "")
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"_+", "_", text)
    return text.strip("_") or "game"


def build_file_stem(title: str, appid: str | int) -> str:
    return f"{slugify(title)}_{appid}"


def get_generated_image_path(title: str, appid: str | int, kind: str) -> str:
    stem = build_file_stem(title, appid)
    return os.path.join(OUTPUT_DIR, f"{stem}_{kind}.png")


def get_custom_upload_path(title: str, appid: str | int, ext: str = "jpg") -> str:
    stem = build_file_stem(title, appid)
    return os.path.join(OUTPUT_DIR, f"{stem}_custom_upload.{ext}")


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


def get_background_url_by_source(appid: str, source_type: str, header_image_url: str = "", screenshots: list[str] | None = None) -> str:
    if source_type == "page_bg_raw":
        return f"https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/{appid}/page_bg_raw.jpg"
    if source_type == "library_hero":
        return f"https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/{appid}/library_hero.jpg"
    if source_type == "header_image":
        return header_image_url or f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg"
    if source_type == "capsule_616x353":
        return f"https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/{appid}/capsule_616x353.jpg"
    if source_type == "header_fallback":
        return f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg"
    if source_type.startswith("screenshot_"):
        idx = int(source_type.split("_")[1])
        if screenshots and idx < len(screenshots):
            return screenshots[idx]
        return f"https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/{appid}/page_bg_raw.jpg"

    candidates = [u for u in get_background_candidates(appid, header_image_url) if u and u.strip()]
    if not candidates:
        raise ValueError(f"No background candidates for appid={appid}")
    return candidates[0]


def load_background(
    appid: str,
    header_image_url: str = "",
    source_type: str = "auto",
    custom_background_path: str | None = None,
    screenshots: list[str] | None = None,
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

    url = get_background_url_by_source(appid, source_type, header_image_url, screenshots)
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

    match_numeric = re.search(r"(\d{1,2})[./-](\d{1,2})", text)
    if match_numeric:
        day = int(match_numeric.group(1))
        month = int(match_numeric.group(2))
        return f"до {day:02d}.{month:02d}"

    match_words = re.search(r"(\d{1,2})\s+([а-щьюяєіїґ]+)", text)
    if match_words:
        day = int(match_words.group(1))
        month_word = match_words.group(2)
        month_num = MONTHS_UA_TO_NUM.get(month_word)
        if month_num:
            return f"до {day:02d}.{month_num:02d}"

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
    title: str,
    final_price: float,
    initial_price: float,
    currency: str,
    header_image_url: str = "",
    source_type: str = "auto",
    custom_background_path: str | None = None,
    sale_end_text: str | None = None,
    screenshots: list[str] | None = None,
    output_path: Optional[str] = None,
) -> str:
    background = load_background(
        appid=appid,
        header_image_url=header_image_url,
        source_type=source_type,
        custom_background_path=custom_background_path,
        screenshots=screenshots,
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
        output_path = get_generated_image_path(title, appid, "final")

    canvas.save(output_path)
    return output_path


def draw_text_with_strikethrough(
    draw: ImageDraw.ImageDraw,
    position: tuple,
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: str,
    anchor: str = "lm",
) -> tuple:
    """Draw text with strikethrough line"""
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    draw.text(position, text, font=font, fill=fill, anchor=anchor)

    # Calculate strikethrough position
    x, y = position
    
    if anchor == "lm":  # Left middle
        line_start_x = x
        line_end_x = x + text_w
        line_y = y
    elif anchor == "rm":  # Right middle
        line_start_x = x - text_w
        line_end_x = x
        line_y = y
    elif anchor == "mm":  # Center middle
        line_start_x = x - text_w // 2
        line_end_x = x + text_w // 2
        line_y = y
    else:
        line_start_x = x
        line_end_x = x + text_w
        line_y = y + text_h // 2

    draw.line(
        [(line_start_x, line_y), (line_end_x, line_y)],
        fill=fill,
        width=3,
    )

    return (text_w, text_h)


def draw_text_with_shadow(
    draw: ImageDraw.ImageDraw,
    position: tuple,
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: str,
    shadow_color: str = "#000000",
    shadow_blur: int = 10,
    anchor: str = "mm",
) -> None:
    """Draw text with shadow effect"""
    # Draw shadow by drawing the text multiple times with slight offsets
    for offset_x in range(-shadow_blur, shadow_blur + 1):
        for offset_y in range(-shadow_blur, shadow_blur + 1):
            if offset_x == 0 and offset_y == 0:
                continue
            # Calculate distance from center for shadow fade effect
            distance = (offset_x ** 2 + offset_y ** 2) ** 0.5
            if distance > shadow_blur * 0.7:  # Only draw outer shadow
                shadow_pos = (position[0] + offset_x, position[1] + offset_y)
                draw.text(shadow_pos, text, font=font, fill=shadow_color, anchor=anchor)

    # Draw main text on top
    draw.text(position, text, font=font, fill=fill, anchor=anchor)


def generate_tiktok_image(
    appid: str,
    title: str,
    final_price: float,
    initial_price: float,
    currency: str,
    header_image_url: str = "",
    sale_end_text: str | None = None,
    source_type: str = "auto",
    custom_background_path: str | None = None,
    screenshots: list[str] | None = None,
    output_path: Optional[str] = None,
) -> str:
    """Generate TikTok-style vertical image (1080x1920)"""
    
    if output_path is None:
        output_path = get_generated_image_path(title, appid, "tiktok")

    # Load template
    if not os.path.exists(TIKTOK_TEMPLATE_PATH):
        raise RuntimeError(f"TikTok template not found: {TIKTOK_TEMPLATE_PATH}")

    template = Image.open(TIKTOK_TEMPLATE_PATH).convert("RGBA")
    
    # Resize template if needed
    if template.size != (TIKTOK_CANVAS_WIDTH, TIKTOK_CANVAS_HEIGHT):
        template = template.resize((TIKTOK_CANVAS_WIDTH, TIKTOK_CANVAS_HEIGHT), Image.Resampling.LANCZOS)

    canvas = Image.new("RGBA", (TIKTOK_CANVAS_WIDTH, TIKTOK_CANVAS_HEIGHT), (0, 0, 0, 255))

    # Load and position game image FIRST (under the template)
    try:
        game_image = load_background(appid, header_image_url, source_type=source_type, custom_background_path=custom_background_path, screenshots=screenshots)
        game_image = cover_crop(game_image, TIKTOK_GAME_IMAGE_WIDTH, TIKTOK_GAME_IMAGE_HEIGHT)
        
        # Position at specified offset from bottom
        game_image_y = TIKTOK_GAME_IMAGE_TOP_OFFSET
        canvas.alpha_composite(game_image, (0, game_image_y))
    except Exception as error:
        print(f"[TIKTOK IMG] Error loading game image: {error}")
        # Continue without game image

    # Load template and composite it OVER the game image
    canvas.alpha_composite(template, (0, 0))

    # Create drawing context
    draw = ImageDraw.Draw(canvas)

    # Load fonts
    discount_price_font = load_font(TIKTOK_DISCOUNT_PRICE_FONT_SIZE)
    old_price_font = load_font(TIKTOK_OLD_PRICE_FONT_SIZE)
    title_font = load_font(TIKTOK_TITLE_FONT_SIZE)
    sale_end_font = load_font(TIKTOK_SALE_END_FONT_SIZE)

    # Format prices and dates
    discount_price_text = format_price_for_image(final_price, currency)
    old_price_text = format_price_for_image(initial_price, currency)
    sale_end_text_formatted = format_sale_end_for_image(sale_end_text)

    # Draw discount price (centered horizontally, at specified bottom offset)

    # --- NEW PRICE (centered, bottom offset) ---
    bbox = draw.textbbox((0, 0), discount_price_text, font=discount_price_font)
    text_h = bbox[3] - bbox[1]

    discount_x = TIKTOK_CANVAS_WIDTH // 2
    discount_y = TIKTOK_CANVAS_HEIGHT - TIKTOK_DISCOUNT_PRICE_BOTTOM - text_h

    draw.text(
        (discount_x, discount_y),
        discount_price_text,
        font=discount_price_font,
        fill=COLOR_TEXT,
        anchor="mt",  # middle-top
    )

    # --- OLD PRICE (left, bottom offset) ---
    bbox = draw.textbbox((0, 0), old_price_text, font=old_price_font)
    text_h = bbox[3] - bbox[1]

    old_price_x = TIKTOK_OLD_PRICE_LEFT
    old_price_y = TIKTOK_CANVAS_HEIGHT - TIKTOK_OLD_PRICE_BOTTOM - text_h

    draw_text_with_strikethrough(
        draw,
        (old_price_x, old_price_y),
        old_price_text,
        old_price_font,
        COLOR_TEXT,
        anchor="lm",
    )


    # --- SALE END DATE (right, bottom offset) ---
    if sale_end_text_formatted:
        bbox = draw.textbbox((0, 0), sale_end_text_formatted, font=sale_end_font)
        text_h = bbox[3] - bbox[1]

        sale_end_x = TIKTOK_CANVAS_WIDTH - TIKTOK_SALE_END_RIGHT
        sale_end_y = TIKTOK_CANVAS_HEIGHT - TIKTOK_SALE_END_BOTTOM - text_h

        draw.text(
            (sale_end_x, sale_end_y),
            sale_end_text_formatted,
            font=sale_end_font,
            fill=COLOR_TEXT,
            anchor="rm",
        )


    # --- TITLE (centered + padding) ---
    title_x = TIKTOK_CANVAS_WIDTH // 2
    title_y = TIKTOK_CANVAS_HEIGHT // 2

    max_width = TIKTOK_CANVAS_WIDTH - (TIKTOK_TITLE_SIDE_PADDING * 2)

    def wrap_text(draw, text, font, max_width):
        words = text.split()
        lines = []
        current_line = ""

        for word in words:
            test_line = f"{current_line} {word}".strip()
            bbox = draw.textbbox((0, 0), test_line, font=font)
            width = bbox[2] - bbox[0]

            if width <= max_width:
                current_line = test_line
            else:
                if current_line:
                    lines.append(current_line)
                current_line = word

        if current_line:
            lines.append(current_line)

        return lines

    lines = wrap_text(draw, title, title_font, max_width)

    line_height_multiplier = 1.4

    # Висота одного рядка
    bbox = draw.textbbox((0, 0), "Ay", font=title_font)
    line_height = int((bbox[3] - bbox[1]) * line_height_multiplier)

    # Загальна висота блоку
    total_height = line_height * len(lines)

    start_y = title_y - total_height // 2

    for i, line in enumerate(lines):
        y = start_y + i * line_height

        draw_text_with_shadow(
            draw,
            (title_x, y),
            line,
            title_font,
            COLOR_TEXT,
            shadow_color="#000000",
            shadow_blur=TIKTOK_TITLE_SHADOW_BLUR,
            anchor="mm",
        )

    canvas.save(output_path)
    print(f"[TIKTOK IMG] Generated TikTok image: {output_path}")
    return output_path