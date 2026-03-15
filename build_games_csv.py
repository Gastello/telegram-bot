import csv
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from config import STEAM_API_KEY


API_KEY = STEAM_API_KEY

OUTPUT_FILE = "games.csv"
PROGRESS_FILE = "games_progress.csv"
STATE_FILE = "build_games_state.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}

BLACKLIST_WORDS = [
    "soundtrack",
    "demo",
    "playtest",
    "server",
    "dedicated server",
    "tool",
    "sdk",
    "editor",
    "trailer",
    "beta",
    "test",
    "benchmark",
    "wallpaper",
    "ost",
    "dlc",
]

MIN_APPID = 100000
MIN_TOTAL_REVIEWS = 1000
MIN_REVIEW_PERCENT = 70.0

MAX_WORKERS = 20
MAX_RETRIES = 2
SLEEP_BETWEEN_COMPLETED = 0.03

SAVE_EVERY = 100
REQUESTS_BATCH_SIZE = 2000


def load_state() -> dict:
    path = Path(STATE_FILE)
    if not path.exists():
        return {
            "apps_cursor": 0,
            "processed": 0,
            "kept": 0,
            "completed": False,
        }

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {
            "apps_cursor": 0,
            "processed": 0,
            "kept": 0,
            "completed": False,
        }


def save_state(state: dict) -> None:
    Path(STATE_FILE).write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def ensure_progress_file() -> None:
    path = Path(PROGRESS_FILE)
    if not path.exists():
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["appid", "title", "total_reviews", "review_percent"])


def append_progress_rows(rows: list[dict]) -> None:
    if not rows:
        return

    with open(PROGRESS_FILE, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        for row in rows:
            writer.writerow([
                row["appid"],
                row["title"],
                row["total_reviews"],
                row["review_percent"],
            ])


def load_progress_rows() -> list[dict]:
    path = Path(PROGRESS_FILE)
    if not path.exists():
        return []

    with open(path, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = []
        for row in reader:
            rows.append({
                "appid": row["appid"],
                "title": row["title"],
                "total_reviews": int(float(row["total_reviews"])),
                "review_percent": float(row["review_percent"]),
            })
        return rows


def fetch_apps() -> list[dict]:
    all_apps = []
    last_appid = 0

    while True:
        url = "https://api.steampowered.com/IStoreService/GetAppList/v1/"
        params = {
            "key": API_KEY,
            "include_games": "true",
            "include_dlc": "false",
            "include_software": "false",
            "include_videos": "false",
            "include_hardware": "false",
            "max_results": 50000,
            "last_appid": last_appid,
        }

        r = requests.get(url, params=params, headers=HEADERS, timeout=60)
        r.raise_for_status()

        data = r.json().get("response", {})
        apps = data.get("apps", [])

        if not apps:
            break

        all_apps.extend(apps)
        last_appid = apps[-1]["appid"]

        print(f"Fetched app list: {len(all_apps)}")

        if not data.get("have_more_results"):
            break

    return all_apps


def looks_like_game(title: str) -> bool:
    name = title.lower().strip()

    if not name:
        return False

    for word in BLACKLIST_WORDS:
        if word in name:
            return False

    return True


def normalize_apps(apps: list[dict]) -> list[dict]:
    rows = []
    seen_appids = set()

    for app in apps:
        appid = int(app.get("appid", 0))
        title = str(app.get("name", "")).strip()

        if not appid or not title:
            continue

        if appid < MIN_APPID:
            continue

        if not looks_like_game(title):
            continue

        if appid in seen_appids:
            continue

        seen_appids.add(appid)
        rows.append({
            "appid": str(appid),
            "title": title,
        })

    return rows


def fetch_reviews_summary(session: requests.Session, appid: str):
    url = (
        f"https://store.steampowered.com/appreviews/{appid}"
        f"?json=1&filter=all&language=all&purchase_type=all&num_per_page=0"
    )

    for attempt in range(MAX_RETRIES + 1):
        try:
            response = session.get(url, timeout=10)

            if response.status_code == 403:
                return None

            if response.status_code == 429:
                if attempt < MAX_RETRIES:
                    sleep_time = 2 * (attempt + 1)
                    print(f"[RATE LIMIT] reviews appid={appid}: retry in {sleep_time}s")
                    time.sleep(sleep_time)
                    continue
                return None

            response.raise_for_status()
            payload = response.json()

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
                "total_reviews": total_reviews,
                "review_percent": review_percent,
            }

        except requests.RequestException as error:
            if attempt < MAX_RETRIES:
                sleep_time = 1.5 * (attempt + 1)
                print(f"[RETRY] reviews appid={appid}: {error} | retry in {sleep_time}s")
                time.sleep(sleep_time)
                continue
            return None
        except ValueError:
            return None

    return None


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def process_app_reviews(app: dict):
    session = make_session()

    appid = app["appid"]
    title = app["title"]

    review_data = fetch_reviews_summary(session, appid)
    if not review_data:
        return None

    total_reviews = review_data["total_reviews"]
    review_percent = review_data["review_percent"]

    if total_reviews < MIN_TOTAL_REVIEWS:
        return None

    if review_percent < MIN_REVIEW_PERCENT:
        return None

    return {
        "appid": appid,
        "title": title,
        "total_reviews": total_reviews,
        "review_percent": review_percent,
    }


def write_final_csv(rows: list[dict]) -> None:
    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["appid", "title"])
        for row in rows:
            writer.writerow([row["appid"], row["title"]])


def process_apps_incrementally(apps: list[dict], start_index: int = 0, limit: int | None = None) -> list[dict]:
    ensure_progress_file()
    state = load_state()
    kept_rows = load_progress_rows()

    total_apps = len(apps)

    cursor_from_state = int(state.get("apps_cursor", 0))
    if cursor_from_state > total_apps:
        cursor_from_state = 0

    # Якщо completed=true, але ми ще не дійшли до кінця всього списку,
    # значить це старий некоректний стан — виправляємо.
    if state.get("completed") and cursor_from_state < total_apps:
        print("Found stale completed=true flag. Resetting it because full list is not finished yet.")
        state["completed"] = False
        save_state(state)

    # Якщо completed=true і cursor уже в кінці — лише пересортувати фінальний CSV.
    if state.get("completed") and cursor_from_state >= total_apps:
        print("Build already fully completed before. Re-sorting existing progress into games.csv...")
        kept_rows.sort(
            key=lambda r: (
                -r["total_reviews"],
                -r["review_percent"],
                r["title"].lower(),
            )
        )
        write_final_csv(kept_rows)
        return kept_rows

    cursor = max(start_index, cursor_from_state)
    processed_total = int(state.get("processed", 0))
    kept_total = int(state.get("kept", len(kept_rows)))

    # end_index для поточного запуску
    if limit is None:
        run_end_index = total_apps
    else:
        run_end_index = min(cursor + limit, total_apps)

    print(f"Resume from index: {cursor}")
    print(f"Run will stop at index: {run_end_index}")
    print(f"Already processed: {processed_total}")
    print(f"Already kept: {kept_total}")

    while cursor < run_end_index:
        chunk_end = min(cursor + REQUESTS_BATCH_SIZE, run_end_index)
        chunk = apps[cursor:chunk_end]

        print(f"\nProcessing chunk: {cursor}..{chunk_end - 1} ({len(chunk)} apps)")

        chunk_kept = []

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {
                executor.submit(process_app_reviews, app): app
                for app in chunk
            }

            chunk_processed = 0

            for future in as_completed(futures):
                chunk_processed += 1
                processed_total += 1
                app = futures[future]

                try:
                    row = future.result()
                except Exception as error:
                    print(f"[ERROR] appid={app['appid']}: {error}")
                    row = None

                if row:
                    chunk_kept.append(row)
                    kept_total += 1
                    print(
                        f"[KEEP] {row['title']} | "
                        f"reviews={row['total_reviews']} | "
                        f"rating={row['review_percent']}%"
                    )

                if processed_total % SAVE_EVERY == 0:
                    append_progress_rows(chunk_kept)
                    kept_rows.extend(chunk_kept)
                    chunk_kept = []

                    state["apps_cursor"] = cursor + chunk_processed
                    state["processed"] = processed_total
                    state["kept"] = kept_total
                    state["completed"] = False
                    save_state(state)

                    print(f"[SAVE] processed={processed_total} kept={kept_total}")

                time.sleep(SLEEP_BETWEEN_COMPLETED)

        append_progress_rows(chunk_kept)
        kept_rows.extend(chunk_kept)

        cursor = chunk_end
        state["apps_cursor"] = cursor
        state["processed"] = processed_total
        state["kept"] = kept_total
        state["completed"] = False
        save_state(state)

        print(f"[CHUNK DONE] processed={processed_total} kept={kept_total}")

    kept_rows.sort(
        key=lambda r: (
            -r["total_reviews"],
            -r["review_percent"],
            r["title"].lower(),
        )
    )

    write_final_csv(kept_rows)

    # completed=True тільки якщо реально дійшли до кінця всього списку apps
    fully_completed = cursor >= total_apps

    state["apps_cursor"] = cursor
    state["processed"] = processed_total
    state["kept"] = kept_total
    state["completed"] = fully_completed
    save_state(state)

    if fully_completed:
        print(f"\nFinal games: {len(kept_rows)}")
        print(f"Saved to {OUTPUT_FILE}")
        print("Build fully completed.")
    else:
        print(f"\nPartial save: {len(kept_rows)} games")
        print(f"Saved to {OUTPUT_FILE}")
        print(f"Next resume index: {cursor}")

    return kept_rows


def main():
    limit = None
    reset = False

    for arg in sys.argv[1:]:
        if arg == "--reset":
            reset = True
        else:
            limit = int(arg)

    if reset:
        for file_name in (STATE_FILE, PROGRESS_FILE, OUTPUT_FILE):
            path = Path(file_name)
            if path.exists():
                path.unlink()
                print(f"Deleted {file_name}")

    print("Downloading Steam app list...")
    apps = fetch_apps()

    print("Total apps from Steam:", len(apps))

    apps = normalize_apps(apps)
    print("After base filtering:", len(apps))

    if limit:
        print("Limit for this run:", limit)

    process_apps_incrementally(apps, start_index=0, limit=limit)


if __name__ == "__main__":
    main()