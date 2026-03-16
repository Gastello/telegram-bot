from deep_translator import GoogleTranslator
from database import get_cached_translation


def format_price(value: float, currency: str) -> str:
    if currency == "UAH":
        return f"{int(value)}₴" if float(value).is_integer() else f"{value:.2f}₴"
    return f"{value:.2f} {currency}"


def escape_html(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def format_review_percent(value: float) -> str:
    return f"{float(value):.2f}%"


def translate_text(appid: str, text: str) -> str:
    if not text:
        return ""

    cached = get_cached_translation(appid, text)
    if cached:
        return cached

    try:
        return GoogleTranslator(source="auto", target="uk").translate(text)
    except Exception:
        return text


def build_post_text(item: dict) -> str:
    if item.get("custom_text"):
        return item["custom_text"]

    title = escape_html(item["title"])

    original_description = item.get("original_description") or item.get("short_description") or ""
    translated_description = item.get("translated_description") or translate_text(item["appid"], original_description)
    description = escape_html(translated_description)

    steam_url = f"https://store.steampowered.com/app/{item['appid']}"
    final_price = format_price(item["final_price"], item["currency"])
    rating_text = format_review_percent(item.get("review_percent", 0))
    sale_end_text = (item.get("sale_end_text") or "").strip()

    text = (
        f"🎮 <b>{title}</b>\n\n"
        f"💸 Знижка: <b>-{item['discount_percent']}%</b>\n"
        f"💰 Ціна: <b>{final_price}</b>\n"
        f"⭐️ Рейтинг: <b>{rating_text}</b>\n"
    )

    if sale_end_text:
        text += f"⏳ Діє до: <b>{escape_html(sale_end_text)}</b>\n"

    text += "\n"

    if description:
        text += f"<blockquote>{description}</blockquote>\n\n"

    text += (
        f'🔗 <a href="{steam_url}">Купити у Steam</a>\n\n'
        f"@SteamNavigator"
    )

    return text
