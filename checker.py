import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests
from deep_translator import GoogleTranslator

from bot import send_to_moderation
from config import STEAM_API_KEY
from database import (
    delete_seen_deal,
    get_cached_translation,
    get_store_catalog_entries,
    get_store_sync_since,
    init_db,
    is_game_blocked,
    is_new_deal,
    save_deal,
    save_translation,
    set_store_sync_since,
    store_catalog_is_empty,
    upsert_store_catalog_entries,
)
from steam_store_parser import get_sale_end_text

STORE_CHANGES_URL = "https://api.steampowered.com/IStoreService/GetAppList/v1/"
STEAM_URL = "https://store.steampowered.com/api/appdetails"
REVIEWS_URL = "https://store.steampowered.com/appreviews"

COUNTRY_CODE = "ua"

DISCOUNT_THRESHOLD = 50
MIN_TOTAL_REVIEWS = 1000
MIN_REVIEW_PERCENT = 70.0

MAX_WORKERS = 1
MAX_RETRIES = 2

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(REQUEST_HEADERS)
    return session


def translate_description(appid: str, text: str) -> str:
    if not text:
        return ""

    cached = get_cached_translation(appid, text)
    if cached:
        return cached

    try:
        translated = GoogleTranslator(source="auto", target="uk").translate(text)
        save_translation(appid, text, translated)
        return translated
    except Exception as error:
        print(f"[WARN] translation appid={appid}: {error}")
        return text


def get_json_with_retry(
    session: requests.Session,
    url: str,
    label: str,
    params: dict | None = None,
) -> dict[str, Any] | None:
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = session.get(url, params=params, timeout=15)

            if response.status_code == 403:
                return None

            if response.status_code == 429:
                if attempt < MAX_RETRIES:
                    sleep_time = 2 * (attempt + 1)
                    print(f"[RATE LIMIT] {label}: retry in {sleep_time}s")
                    time.sleep(sleep_time)
                    continue
                return None

            response.raise_for_status()
            return response.json()

        except requests.RequestException as error:
            if attempt < MAX_RETRIES:
                sleep_time = 1.5 * (attempt + 1)
                print(f"[RETRY] {label}: {error} | retry in {sleep_time}s")
                time.sleep(sleep_time)
                continue

            print(f"[ERROR] {label}: {error}")
            return None
        except ValueError as error:
            print(f"[ERROR] {label}: invalid JSON: {error}")
            return None

    return None


def fetch_changed_store_apps(if_modified_since: int) -> list[dict]:
    session = make_session()

    changed_apps = []
    last_appid = 0

    while True:
        params = {
            "key": STEAM_API_KEY,
            "include_games": "true",
            "include_dlc": "false",
            "include_software": "false",
            "include_videos": "false",
            "include_hardware": "false",
            "max_results": 50000,
            "last_appid": last_appid,
        }

        if if_modified_since > 0:
            params["if_modified_since"] = int(if_modified_since)

        payload = get_json_with_retry(
            session,
            STORE_CHANGES_URL,
            label=f"store changes since={if_modified_since} last_appid={last_appid}",
            params=params,
        )

        if not payload:
            break

        response = payload.get("response", {})
        apps = response.get("apps", [])
        if not apps:
            break

        for app in apps:
            appid = str(app.get("appid", "")).strip()
            title = str(app.get("name", "")).strip()

            if not appid or not title:
                continue

            changed_apps.append({
                "appid": appid,
                "title": title,
                "last_modified": int(app.get("last_modified", 0) or 0),
                "price_change_number": int(app.get("price_change_number", 0) or 0),
            })

        last_appid = apps[-1]["appid"]

        if not response.get("have_more_results"):
            break

    return changed_apps


def bootstrap_store_catalog() -> None:
    print("[BOOTSTRAP] Store catalog is empty. Downloading full store catalog...")

    apps = fetch_changed_store_apps(0)
    if not apps:
        print("[BOOTSTRAP] No apps fetched.")
        return

    upsert_store_catalog_entries(apps)

    # 10 березня 2026 00:00:00 UTC
    set_store_sync_since(1773100800)

    print(f"[BOOTSTRAP] Saved {len(apps)} store items.")
    print("[BOOTSTRAP] Run checker again to process only future price/store changes.")


def build_price_change_candidates(changed_apps: list[dict]) -> list[dict]:
    if not changed_apps:
        return []

    existing = get_store_catalog_entries([app["appid"] for app in changed_apps])

    candidates = []
    for app in changed_apps:
        old = existing.get(app["appid"])

        if old is None:
            candidates.append({
                "appid": app["appid"],
                "title": app["title"],
            })
            continue

        old_pcn = int(old.get("price_change_number", 0) or 0)
        new_pcn = int(app.get("price_change_number", 0) or 0)

        if old_pcn != new_pcn:
            candidates.append({
                "appid": app["appid"],
                "title": app["title"],
            })

    return candidates


def fetch_app_details(session: requests.Session, appid: str) -> dict[str, Any] | None:
    url = f"{STEAM_URL}?appids={appid}&cc={COUNTRY_CODE}"
    payload = get_json_with_retry(session, url, f"appdetails appid={appid}")
    if not payload:
        return None

    app_block = payload.get(str(appid))
    if not app_block or not app_block.get("success"):
        return None

    return app_block.get("data")


def fetch_reviews_summary(session: requests.Session, appid: str) -> dict[str, Any] | None:
    url = f"{REVIEWS_URL}/{appid}?json=1&filter=all&language=all&purchase_type=all&num_per_page=0"
    payload = get_json_with_retry(session, url, f"reviews appid={appid}")
    if not payload:
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


def build_base_deal(appid: str, data: dict[str, Any]) -> dict[str, Any] | None:
    price = data.get("price_overview")
    if not price:
        return None

    return {
        "appid": appid,
        "title": data.get("name", "Unknown"),
        "type": data.get("type", ""),
        "discount_percent": int(price.get("discount_percent", 0)),
        "final_price": price.get("final", 0) / 100,
        "initial_price": price.get("initial", 0) / 100,
        "currency": price.get("currency", ""),
        "header_image": data.get("header_image", ""),
        "original_description": data.get("short_description", "") or "",
    }


def process_candidate(
    session: requests.Session,
    candidate: dict[str, str],
) -> tuple[str, dict[str, Any] | None]:
    appid = candidate["appid"]
    title = candidate["title"]

    if is_game_blocked(appid):
        return (f"[BLOCKED] {title}", None)

    time.sleep(0.35)
    data = fetch_app_details(session, appid)
    if not data:
        delete_seen_deal(appid)
        return (f"[SKIP] {title} | no appdetails", None)

    deal = build_base_deal(appid, data)
    if not deal:
        delete_seen_deal(appid)
        return (f"[SKIP] {title} | no price data", None)

    if deal["type"] != "game":
        delete_seen_deal(appid)
        return (f"[SKIP] {title} | not a game ({deal['type']})", None)

    if deal["discount_percent"] < DISCOUNT_THRESHOLD:
        delete_seen_deal(appid)
        return (
            f"[SKIP] {title} | discount {deal['discount_percent']}% < {DISCOUNT_THRESHOLD}%",
            None,
        )

    time.sleep(0.35)
    reviews = fetch_reviews_summary(session, appid)
    if not reviews:
        delete_seen_deal(appid)
        return (f"[SKIP] {title} | no reviews data", None)

    review_percent = float(reviews.get("review_percent", 0.0))
    total_reviews = int(reviews.get("total_reviews", 0))
    review_score_desc = reviews.get("review_score_desc", "")

    if total_reviews < MIN_TOTAL_REVIEWS:
        delete_seen_deal(appid)
        return (
            f"[SKIP] {title} | reviews {total_reviews} < {MIN_TOTAL_REVIEWS}",
            None,
        )

    if review_percent < MIN_REVIEW_PERCENT:
        delete_seen_deal(appid)
        return (
            f"[SKIP] {title} | rating {review_percent}% < {MIN_REVIEW_PERCENT}%",
            None,
        )

    sale_end_text = ""
    try:
        sale_end_text = get_sale_end_text(appid) or ""
    except Exception as error:
        print(f"[WARN] sale_end appid={appid}: {error}")

    original_description = deal["original_description"]
    translated_description = translate_description(appid, original_description)

    deal["short_description"] = translated_description
    deal["translated_description"] = translated_description
    deal["review_percent"] = review_percent
    deal["total_reviews"] = total_reviews
    deal["review_score_desc"] = review_score_desc
    deal["sale_end_text"] = sale_end_text

    return ("", deal)


def check_games() -> list[dict[str, Any]]:
    if store_catalog_is_empty():
        bootstrap_store_catalog()
        return []

    since = get_store_sync_since()
    print(f"[STORE FEED] if_modified_since={since}")

    changed_apps = fetch_changed_store_apps(since)
    print(f"[STORE FEED] changed apps: {len(changed_apps)}")

    if not changed_apps:
        set_store_sync_since(int(time.time()))
        return []

    candidates = [
        {
            "appid": app["appid"],
            "title": app["title"],
        }
        for app in changed_apps
    ]

    # тимчасово для backfill
    candidates = candidates[:80]

    print(f"[STORE FEED] changed-app candidates: {len(candidates)}")

    upsert_store_catalog_entries(changed_apps)

    max_last_modified = max((int(app.get("last_modified", 0) or 0) for app in changed_apps), default=0)
    if max_last_modified > 0:
        set_store_sync_since(max_last_modified - 10)

    new_deals: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        sessions = [make_session() for _ in range(MAX_WORKERS)]

        future_to_candidate = {}

        for i, candidate in enumerate(candidates):
            session = sessions[i % MAX_WORKERS]
            future = executor.submit(process_candidate, session, candidate)
            future_to_candidate[future] = candidate

        for future in as_completed(future_to_candidate):
            candidate = future_to_candidate[future]
            appid = candidate["appid"]

            try:
                log_line, deal = future.result()
            except Exception as error:
                print(f"[ERROR] appid={appid}: worker failed: {error}")
                continue

            if log_line:
                print(log_line)

            if not deal:
                continue

            if is_new_deal(deal):
                new_deals.append(deal)
                save_deal(deal)

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

    new_deals.sort(
        key=lambda d: (
            -d["discount_percent"],
            -d["review_percent"],
            -d["total_reviews"],
            d["title"].lower(),
        )
    )

    for deal in new_deals:
        try:
            send_to_moderation(deal)
            time.sleep(0.4)
        except Exception as error:
            print(f"[ERROR] moderation send appid={deal['appid']}: {error}")

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
    set_store_sync_since(1773100800)  # DELETE
    deals = check_games()
    print_deals(deals)