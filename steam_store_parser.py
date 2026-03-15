import re
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup


KYIV_TZ = ZoneInfo("Europe/Kyiv")


def format_ua_date(dt: datetime) -> str:
    months = {
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
    return f"{dt.day} {months[dt.month]}"


def extract_timestamp_from_html(html: str, span_id: str) -> int | None:
    """
    Шукає JS типу:
    InitDailyDealTimer( $DiscountCountdown, 1773680400 );
    """
    pattern = rf"{re.escape(span_id)}.*?InitDailyDealTimer\(\s*\$DiscountCountdown,\s*(\d+)\s*\)"
    match = re.search(pattern, html, flags=re.DOTALL)
    if match:
        return int(match.group(1))

    # запасний варіант: просто шукаємо біля span_id
    around_pattern = rf"{re.escape(span_id)}.*?(\d{{10}})"
    match = re.search(around_pattern, html, flags=re.DOTALL)
    if match:
        return int(match.group(1))

    return None


def get_sale_end_text(appid: str) -> str | None:
    url = f"https://store.steampowered.com/app/{appid}/"

    headers = {
        "User-Agent": "Mozilla/5.0",
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

    # Варіант 1: "Діє до 19 березня"
    m = re.search(r"Діє до\s+(\d{1,2}\s+[А-Яа-яІіЇїЄєҐґ]+)", full_text, flags=re.IGNORECASE)
    if m:
        return m.group(1).strip()

    # Варіант 2: "Закінчується 19 березня"
    m = re.search(r"Закінчується\s+(\d{1,2}\s+[А-Яа-яІіЇїЄєҐґ]+)", full_text, flags=re.IGNORECASE)
    if m:
        return m.group(1).strip()

    # Варіант 3: "Закінчиться через 31:28:52" — беремо timestamp із JS
    if "Закінчиться через" in full_text or "Закінчується через" in full_text:
        span = countdown.find("span")
        if span and span.get("id"):
            timestamp = extract_timestamp_from_html(html, span["id"])
            if timestamp:
                dt = datetime.fromtimestamp(timestamp, tz=KYIV_TZ)
                return format_ua_date(dt)

    # Варіант 4: іноді дата є в html, але не спіймалась попередніми патернами
    m = re.search(r"(\d{1,2}\s+[А-Яа-яІіЇїЄєҐґ]+)", full_text)
    if m:
        return m.group(1).strip()

    return None