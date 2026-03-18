import sqlite3
from pathlib import Path

DB_PATH = Path("bot.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS seen_deals (
        appid TEXT PRIMARY KEY,
        discount_percent INTEGER,
        final_price REAL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS blocked_games (
        appid TEXT PRIMARY KEY,
        title TEXT,
        blocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # migration for old blocked_games table without blocked_at
    cur.execute("PRAGMA table_info(blocked_games)")
    blocked_cols = [row["name"] for row in cur.fetchall()]
    if "blocked_at" not in blocked_cols:
        cur.execute("""
        ALTER TABLE blocked_games
        ADD COLUMN blocked_at TIMESTAMP
        """)
        cur.execute("""
        UPDATE blocked_games
        SET blocked_at = CURRENT_TIMESTAMP
        WHERE blocked_at IS NULL
        """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS translation_cache (
        appid TEXT,
        original_text TEXT,
        translated_text TEXT,
        PRIMARY KEY (appid, original_text)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS moderation_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        appid TEXT,
        title TEXT,
        final_price REAL,
        initial_price REAL,
        currency TEXT,
        discount_percent INTEGER,
        header_image TEXT,
        review_percent REAL,
        total_reviews INTEGER,
        short_description TEXT,
        translated_description TEXT,
        sale_end_text TEXT,
        selected_image_variant TEXT,
        selected_image_path TEXT,
        custom_image_path TEXT,
        control_message_id INTEGER,
        upload_request_message_id INTEGER,
        preview_message_id INTEGER,
        state TEXT DEFAULT 'waiting_image',
        status TEXT DEFAULT 'pending'
    )
    """)

    # migration for custom_text
    cur.execute("PRAGMA table_info(moderation_items)")
    moderation_cols = [row["name"] for row in cur.fetchall()]
    if "custom_text" not in moderation_cols:
        cur.execute("""
        ALTER TABLE moderation_items
        ADD COLUMN custom_text TEXT
        """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS moderation_messages (
        moderation_id INTEGER,
        chat_id INTEGER,
        message_id INTEGER,
        kind TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS store_catalog (
        appid TEXT PRIMARY KEY,
        title TEXT,
        last_modified INTEGER DEFAULT 0,
        price_change_number INTEGER DEFAULT 0
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS store_sync_state (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        if_modified_since INTEGER DEFAULT 0
    )
    """)

    cur.execute("""
    INSERT OR IGNORE INTO store_sync_state (id, if_modified_since)
    VALUES (1, 0)
    """)

    conn.commit()
    conn.close()


# ------------------------------------------------
# Seen deals
# ------------------------------------------------

def is_new_deal(deal: dict) -> bool:
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        "SELECT discount_percent, final_price FROM seen_deals WHERE appid=?",
        (deal["appid"],),
    )

    row = cur.fetchone()
    conn.close()

    if not row:
        return True

    old_discount = row["discount_percent"]
    old_price = row["final_price"]

    if old_discount != deal["discount_percent"]:
        return True

    if float(old_price) != float(deal["final_price"]):
        return True

    return False


def save_deal(deal: dict):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    INSERT OR REPLACE INTO seen_deals
    (appid, discount_percent, final_price)
    VALUES (?, ?, ?)
    """, (
        deal["appid"],
        deal["discount_percent"],
        deal["final_price"],
    ))

    conn.commit()
    conn.close()


def delete_seen_deal(appid: str):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        "DELETE FROM seen_deals WHERE appid=?",
        (appid,),
    )

    conn.commit()
    conn.close()


# ------------------------------------------------
# Blocked games
# ------------------------------------------------

def block_game(appid: str, title: str):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    INSERT OR REPLACE INTO blocked_games
    (appid, title, blocked_at)
    VALUES (?, ?, CURRENT_TIMESTAMP)
    """, (appid, title))

    conn.commit()
    conn.close()


def unblock_game(appid: str) -> bool:
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("DELETE FROM blocked_games WHERE appid=?", (appid,))
    deleted = cur.rowcount > 0

    conn.commit()
    conn.close()

    return deleted


def is_game_blocked(appid: str) -> bool:
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        "SELECT 1 FROM blocked_games WHERE appid=?",
        (appid,),
    )

    row = cur.fetchone()
    conn.close()

    return bool(row)


def list_blocked_games(limit: int = 100) -> list[dict]:
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    SELECT appid, title, blocked_at
    FROM blocked_games
    ORDER BY datetime(blocked_at) DESC, LOWER(title), appid
    LIMIT ?
    """, (limit,))

    rows = cur.fetchall()
    conn.close()

    return [dict(row) for row in rows]


def get_last_blocked_game() -> dict | None:
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    SELECT appid, title, blocked_at
    FROM blocked_games
    ORDER BY datetime(blocked_at) DESC
    LIMIT 1
    """)

    row = cur.fetchone()
    conn.close()

    if not row:
        return None

    return dict(row)


# ------------------------------------------------
# Translation cache
# ------------------------------------------------

def get_cached_translation(appid: str, original_text: str) -> str | None:
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    SELECT translated_text
    FROM translation_cache
    WHERE appid=? AND original_text=?
    """, (appid, original_text))

    row = cur.fetchone()
    conn.close()

    if row:
        return row["translated_text"]

    return None


def save_translation(appid: str, original_text: str, translated_text: str):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    INSERT OR REPLACE INTO translation_cache
    (appid, original_text, translated_text)
    VALUES (?, ?, ?)
    """, (appid, original_text, translated_text))

    conn.commit()
    conn.close()


# ------------------------------------------------
# Moderation items
# ------------------------------------------------

def create_moderation_item(deal: dict) -> int:
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    INSERT INTO moderation_items (
        appid,
        title,
        final_price,
        initial_price,
        currency,
        discount_percent,
        header_image,
        review_percent,
        total_reviews,
        short_description,
        translated_description,
        sale_end_text,
        custom_text
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        deal["appid"],
        deal["title"],
        deal["final_price"],
        deal["initial_price"],
        deal["currency"],
        deal["discount_percent"],
        deal.get("header_image"),
        deal.get("review_percent"),
        deal.get("total_reviews"),
        deal.get("short_description"),
        deal.get("translated_description"),
        deal.get("sale_end_text"),
        None,  # custom_text
    ))

    moderation_id = cur.lastrowid

    conn.commit()
    conn.close()

    return moderation_id


def update_custom_text(moderation_id: int, custom_text: str) -> None:
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        "UPDATE moderation_items SET custom_text=? WHERE id=?",
        (custom_text, moderation_id),
    )

    conn.commit()
    conn.close()


def get_moderation_item(moderation_id: int) -> dict | None:
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        "SELECT * FROM moderation_items WHERE id=?",
        (moderation_id,),
    )

    row = cur.fetchone()
    conn.close()

    if not row:
        return None

    return dict(row)


def get_moderation_item_by_appid(appid: str) -> dict | None:
    """Get moderation item by appid"""
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        "SELECT * FROM moderation_items WHERE appid=?",
        (appid,),
    )

    row = cur.fetchone()
    conn.close()

    if not row:
        return None

    return dict(row)


def update_moderation_state(moderation_id: int, state: str):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    UPDATE moderation_items
    SET state=?
    WHERE id=?
    """, (state, moderation_id))

    conn.commit()
    conn.close()


def update_moderation_status(moderation_id: int, status: str):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    UPDATE moderation_items
    SET status=?
    WHERE id=?
    """, (status, moderation_id))

    conn.commit()
    conn.close()


def get_last_published_moderation_item() -> dict | None:
    """Get the most recently published moderation item"""
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    SELECT * FROM moderation_items
    WHERE status = 'published'
    ORDER BY id DESC
    LIMIT 1
    """)

    row = cur.fetchone()
    conn.close()

    if row:
        return dict(row)
    return None


# ------------------------------------------------
# Image selection
# ------------------------------------------------

def set_selected_image(
    moderation_id: int,
    variant: str,
    image_path: str,
    custom_image_path: str | None = None,
):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    UPDATE moderation_items
    SET
        selected_image_variant=?,
        selected_image_path=?,
        custom_image_path=?
    WHERE id=?
    """, (
        variant,
        image_path,
        custom_image_path,
        moderation_id,
    ))

    conn.commit()
    conn.close()


def set_control_message_id(moderation_id: int, message_id: int):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    UPDATE moderation_items
    SET control_message_id=?
    WHERE id=?
    """, (message_id, moderation_id))

    conn.commit()
    conn.close()


def set_preview_message_id(moderation_id: int, message_id: int):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    UPDATE moderation_items
    SET preview_message_id=?
    WHERE id=?
    """, (message_id, moderation_id))

    conn.commit()
    conn.close()


def set_upload_request_message_id(moderation_id: int, message_id: int):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    UPDATE moderation_items
    SET upload_request_message_id=?
    WHERE id=?
    """, (message_id, moderation_id))

    conn.commit()
    conn.close()


def clear_upload_request_message_id(moderation_id: int):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    UPDATE moderation_items
    SET upload_request_message_id=NULL
    WHERE id=?
    """, (moderation_id,))

    conn.commit()
    conn.close()


def get_moderation_item_by_upload_request_message_id(message_id: int) -> dict | None:
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    SELECT *
    FROM moderation_items
    WHERE upload_request_message_id=?
    """, (message_id,))

    row = cur.fetchone()
    conn.close()

    if not row:
        return None

    return dict(row)


# ------------------------------------------------
# Moderation messages
# ------------------------------------------------

def register_moderation_message(
    moderation_id: int,
    chat_id: int,
    message_id: int,
    kind: str,
):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    INSERT INTO moderation_messages
    (moderation_id, chat_id, message_id, kind)
    VALUES (?, ?, ?, ?)
    """, (
        moderation_id,
        chat_id,
        message_id,
        kind,
    ))

    conn.commit()
    conn.close()


def get_moderation_messages(moderation_id: int) -> list[dict]:
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    SELECT *
    FROM moderation_messages
    WHERE moderation_id=?
    """, (moderation_id,))

    rows = cur.fetchall()
    conn.close()

    return [dict(r) for r in rows]


def delete_moderation_messages_records(moderation_id: int):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    DELETE FROM moderation_messages
    WHERE moderation_id=?
    """, (moderation_id,))

    conn.commit()
    conn.close()

def delete_moderation_messages_records_by_ids(message_ids: list[int]) -> None:
    if not message_ids:
        return

    conn = get_conn()  # ← ОСЬ ТУТ ВАЖЛИВО
    cur = conn.cursor()

    placeholders = ",".join("?" for _ in message_ids)

    cur.execute(
        f"DELETE FROM moderation_messages WHERE message_id IN ({placeholders})",
        message_ids,
    )

    conn.commit()
    conn.close()

# ------------------------------------------------
# Store catalog / sync state
# ------------------------------------------------

def get_store_sync_since() -> int:
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT if_modified_since FROM store_sync_state WHERE id=1")
    row = cur.fetchone()
    conn.close()

    if not row:
        return 0

    return int(row["if_modified_since"])


def set_store_sync_since(value: int) -> None:
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    INSERT INTO store_sync_state (id, if_modified_since)
    VALUES (1, ?)
    ON CONFLICT(id) DO UPDATE SET if_modified_since=excluded.if_modified_since
    """, (int(value),))

    conn.commit()
    conn.close()


def store_catalog_is_empty() -> bool:
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT 1 FROM store_catalog LIMIT 1")
    row = cur.fetchone()
    conn.close()

    return row is None


def get_store_catalog_entries(appids: list[str]) -> dict[str, dict]:
    if not appids:
        return {}

    conn = get_conn()
    cur = conn.cursor()

    placeholders = ",".join(["?"] * len(appids))
    cur.execute(
        f"""
        SELECT appid, title, last_modified, price_change_number
        FROM store_catalog
        WHERE appid IN ({placeholders})
        """,
        appids,
    )

    rows = cur.fetchall()
    conn.close()

    return {row["appid"]: dict(row) for row in rows}


def upsert_store_catalog_entries(rows: list[dict]) -> None:
    if not rows:
        return

    conn = get_conn()
    cur = conn.cursor()

    cur.executemany("""
    INSERT INTO store_catalog (appid, title, last_modified, price_change_number)
    VALUES (?, ?, ?, ?)
    ON CONFLICT(appid) DO UPDATE SET
        title=excluded.title,
        last_modified=excluded.last_modified,
        price_change_number=excluded.price_change_number
    """, [
        (
            str(row["appid"]),
            row.get("title", ""),
            int(row.get("last_modified", 0) or 0),
            int(row.get("price_change_number", 0) or 0),
        )
        for row in rows
    ])

    conn.commit()
    conn.close()