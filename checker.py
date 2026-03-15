import csv
import requests
from typing import Any

from database import (
    init_db,
    is_new_deal,
    save_deal,
    delete_seen_deal,
    is_game_blocked,
    get_cached_translation,
)
from bot import send_to_moderation
from steam_store_parser import get_sale_end_text
from deep_translator import GoogleTranslator

STEAM_URL = "https://store.steampowered.com/api/appdetails"
REVIEWS_URL = "https://store.steampowered.com/appreviews"

COUNTRY_CODE = "ua"

DISCOUNT_THRESHOLD = 50
MIN_TOTAL_REVIEWS = 1000
MIN_REVIEW_PERCENT = 80.0


def load_games(csv_path: str = "games.csv") -> list[dict[str, str]]:
    games: list[dict[str, str]] = []

    with open(csv_path, "r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)

        for row in reader:
            appid = (row.get("appid") or "").strip()
            title = (row.get("title") or "").strip()

            if not appid:
                continue

            games.append({
                "appid": appid,
                "title": title,
            })

    return games


def translate_description(appid: str, text: str) -> str:
    if not text:
        return ""

    cached = get_cached_translation(appid, text)
    if cached:
        return cached

    try:
        return GoogleTranslator(source="auto", target="uk").translate(text)
    except Exception:
        return text


def fetch_app_details(appid: str) -> dict[str, Any] | None:
    url = f"{STEAM_URL}?appids={appid}&cc={COUNTRY_CODE}"

    try:
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as error:
        print(f"[ERROR] appid={appid}: {error}")
        return None
    except ValueError as error:
        print(f"[ERROR] appid={appid}: invalid JSON: {error}")
        return None

    app_block = payload.get(str(appid))
    if not app_block or not app_block.get("success"):
        return None

    return app_block.get("data")


def fetch_reviews_summary(appid: str) -> dict[str, Any] | None:
    url = f"{REVIEWS_URL}/{appid}?json=1&filter=all&language=all&purchase_type=all&num_per_page=0"

    try:
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as error:
        print(f"[ERROR] reviews appid={appid}: {error}")
        return None
    except ValueError as error:
        print(f"[ERROR] reviews appid={appid}: invalid JSON: {error}")
        return None

    if payload.get("success") != 1:
        return None

    summary = payload.get("query_summary", {})
    total_positive = int(summary.get("total_positive", 0))
    total_negative = int(summary.get("total_negative", 0))
    total_reviews = int(summary.get("total_reviews", 0))

    review_percent = 0.0
    if total_positive + total_negative > 0:
        review_percent = round(
            (total_positive / (total_positive + total_negative)) * 100,
            2,
        )

    return {
        "review_percent": review_percent,
        "total_reviews": total_reviews,
        "review_score": summary.get("review_score"),
        "review_score_desc": summary.get("review_score_desc", ""),
        "total_positive": total_positive,
        "total_negative": total_negative,
    }


def extract_deal(
    appid: str,
    data: dict[str, Any],
    reviews: dict[str, Any] | None,
    sale_end_text: str | None,
) -> dict[str, Any] | None:
    price = data.get("price_overview")
    if not price:
        return None

    discount_percent = int(price.get("discount_percent", 0))
    final_price = price.get("final", 0) / 100
    initial_price = price.get("initial", 0) / 100

    review_percent = 0.0
    total_reviews = 0
    review_score_desc = ""

    if reviews:
        review_percent = float(reviews.get("review_percent", 0.0))
        total_reviews = int(reviews.get("total_reviews", 0))
        review_score_desc = reviews.get("review_score_desc", "")

    original_description = data.get("short_description", "") or ""
    translated_description = translate_description(appid, original_description)

    return {
        "appid": appid,
        "title": data.get("name", "Unknown"),
        "type": data.get("type", ""),
        "discount_percent": discount_percent,
        "final_price": final_price,
        "initial_price": initial_price,
        "currency": price.get("currency", ""),
        "header_image": data.get("header_image", ""),
        "short_description": translated_description,
        "original_description": original_description,
        "translated_description": translated_description,
        "review_percent": review_percent,
        "total_reviews": total_reviews,
        "review_score_desc": review_score_desc,
        "sale_end_text": sale_end_text or "",
    }


def check_games() -> list[dict[str, Any]]:
    games = load_games()
    new_deals: list[dict[str, Any]] = []

    for game in games:
        appid = game["appid"]

        if is_game_blocked(appid):
            print(f"[BLOCKED] appid={appid}")
            continue

        data = fetch_app_details(appid)
        if not data:
            print(f"[SKIP] appid={appid}: no data")
            delete_seen_deal(appid)
            continue

        reviews = fetch_reviews_summary(appid)

        sale_end_text = None
        try:
            sale_end_text = get_sale_end_text(appid)
        except Exception as error:
            print(f"[WARN] sale_end appid={appid}: {error}")

        deal = extract_deal(appid, data, reviews, sale_end_text)

        if not deal:
            delete_seen_deal(appid)
            print(f"[SKIP] {data.get('name', appid)}: no price (removed from seen_deals)")
            continue

        if deal["type"] != "game":
            delete_seen_deal(appid)
            print(f"[SKIP] {deal['title']}: type={deal['type']} (removed from seen_deals)")
            continue

        if deal["discount_percent"] < DISCOUNT_THRESHOLD:
            delete_seen_deal(appid)
            print(
                f"[SKIP] {deal['title']}: discount too low "
                f"({deal['discount_percent']}%) (removed from seen_deals)"
            )
            continue

        if deal["total_reviews"] < MIN_TOTAL_REVIEWS:
            delete_seen_deal(appid)
            print(
                f"[SKIP] {deal['title']}: too few reviews "
                f"({deal['total_reviews']}) (removed from seen_deals)"
            )
            continue

        if deal["review_percent"] < MIN_REVIEW_PERCENT:
            delete_seen_deal(appid)
            print(
                f"[SKIP] {deal['title']}: review percent too low "
                f"({deal['review_percent']}%) (removed from seen_deals)"
            )
            continue

        if is_new_deal(deal):
            new_deals.append(deal)
            save_deal(deal)
            send_to_moderation(deal)

            print(
                f"[NEW] {deal['title']} | "
                f"-{deal['discount_percent']}% | "
                f"{deal['final_price']} {deal['currency']} | "
                f"rating={deal['review_percent']}% | "
                f"reviews={deal['total_reviews']} | "
                f"sale_end={deal['sale_end_text'] or 'N/A'}"
            )
        else:
            print(f"[OLD] {deal['title']}")

    return new_deals


def print_deals(deals: list[dict[str, Any]]) -> None:
    if not deals:
        print("\nНових знижок не знайдено.")
        return

    print(f"\nЗнайдено нових знижок: {len(deals)}\n")

    for deal in deals:
        print(f"🎮 {deal['title']}")
        print(f"   appid: {deal['appid']}")
        print(f"   discount: -{deal['discount_percent']}%")
        print(f"   final price: {deal['final_price']} {deal['currency']}")
        print(f"   initial price: {deal['initial_price']} {deal['currency']}")
        print(f"   review percent: {deal['review_percent']}%")
        print(f"   total reviews: {deal['total_reviews']}")
        print(f"   sale end: {deal['sale_end_text'] or 'N/A'}")
        print()


if __name__ == "__main__":
    init_db()
    deals = check_games()
    print_deals(deals)
