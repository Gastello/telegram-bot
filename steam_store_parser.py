import re
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


UA_TZ = ZoneInfo("Europe/Kyiv")

MONTHS_UA = {
    1: "січня",
    2: "лютого",
    3: "березня",
    4: "квітня",
    5: "травня",
    6: "червня",
    7: "липня",
    8: "серпня",
    9: "вересня",
    10: "жовтня",
    11: "листопада",
    12: "грудня",
}


def format_ua_date(dt: datetime) -> str:
    return f"{dt.day} {MONTHS_UA[dt.month]}"


def get_sale_end_text(appid: str) -> str | None:
    url = f"https://store.steampowered.com/app/{appid}/"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "uk,en;q=0.9",
    }

    cookies = {
        "birthtime": "568022401",
        "lastagecheckage": "1-January-1988",
        "wants_mature_content": "1",
    }

    response = requests.get(
        url,
        headers=headers,
        cookies=cookies,
        timeout=20,
    )
    response.raise_for_status()

    html = response.text
    soup = BeautifulSoup(html, "html.parser")

    countdown = soup.select_one(".game_purchase_discount_countdown")
    if not countdown:
        return None

    full_text = " ".join(countdown.get_text(" ", strip=True).split())

    # 1. Якщо дата вже є прямо в HTML
    match_date = re.search(r"Діє до\s+(.+)$", full_text)
    if match_date:
        return match_date.group(1).strip()

    # 2. Якщо це live countdown, Steam часто ініціалізує його JS-функцією
    #    Наприклад: InitDailyDealTimer( $DiscountCountdown, 1773680400 );
    match_timer = re.search(
        r"InitDailyDealTimer\(\s*\$DiscountCountdown\s*,\s*(\d+)\s*\)",
        html
    )
    if match_timer:
        end_ts = int(match_timer.group(1))
        end_dt = datetime.fromtimestamp(end_ts, tz=UA_TZ)
        return format_ua_date(end_dt)

    # 3. Fallback: якщо згодом знайдуться інші timer init-функції
    generic_timer = re.search(
        r"Init\w*Timer\([^)]*,\s*(\d{10})\s*\)",
        html
    )
    if generic_timer:
        end_ts = int(generic_timer.group(1))
        end_dt = datetime.fromtimestamp(end_ts, tz=UA_TZ)
        return format_ua_date(end_dt)

    return None