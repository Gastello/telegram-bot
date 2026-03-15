import sqlite3
from typing import Any

DB_PATH = "bot.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS seen_deals (
            appid TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            discount_percent INTEGER NOT NULL,
            final_price REAL NOT NULL,
            initial_price REAL NOT NULL,
            currency TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS moderation_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            appid TEXT NOT NULL,
            title TEXT NOT NULL,
            discount_percent INTEGER NOT NULL,
            final_price REAL NOT NULL,
            initial_price REAL NOT NULL,
            currency TEXT,
            short_description TEXT,
            original_description TEXT DEFAULT '',
            translated_description TEXT DEFAULT '',
            review_percent REAL DEFAULT 0,
            total_reviews INTEGER DEFAULT 0,
            sale_end_text TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            state TEXT NOT NULL DEFAULT 'pending_image_choice',
            selected_image_variant TEXT DEFAULT '',
            selected_image_path TEXT DEFAULT '',
            custom_image_path TEXT DEFAULT '',
            control_message_id INTEGER DEFAULT 0,
            preview_message_id INTEGER DEFAULT 0,
            upload_request_message_id INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS blocked_games (
            appid TEXT PRIMARY KEY,
            title TEXT,
            blocked_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS moderation_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            moderation_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    existing_columns = {
        row["name"]
        for row in cur.execute("PRAGMA table_info(moderation_queue)").fetchall()
    }

    needed_columns = {
        "review_percent": "ALTER TABLE moderation_queue ADD COLUMN review_percent REAL DEFAULT 0",
        "total_reviews": "ALTER TABLE moderation_queue ADD COLUMN total_reviews INTEGER DEFAULT 0",
        "sale_end_text": "ALTER TABLE moderation_queue ADD COLUMN sale_end_text TEXT DEFAULT ''",
        "original_description": "ALTER TABLE moderation_queue ADD COLUMN original_description TEXT DEFAULT ''",
        "translated_description": "ALTER TABLE moderation_queue ADD COLUMN translated_description TEXT DEFAULT ''",
        "state": "ALTER TABLE moderation_queue ADD COLUMN state TEXT DEFAULT 'pending_image_choice'",
        "selected_image_variant": "ALTER TABLE moderation_queue ADD COLUMN selected_image_variant TEXT DEFAULT ''",
        "selected_image_path": "ALTER TABLE moderation_queue ADD COLUMN selected_image_path TEXT DEFAULT ''",
        "custom_image_path": "ALTER TABLE moderation_queue ADD COLUMN custom_image_path TEXT DEFAULT ''",
        "control_message_id": "ALTER TABLE moderation_queue ADD COLUMN control_message_id INTEGER DEFAULT 0",
        "preview_message_id": "ALTER TABLE moderation_queue ADD COLUMN preview_message_id INTEGER DEFAULT 0",
        "upload_request_message_id": "ALTER TABLE moderation_queue ADD COLUMN upload_request_message_id INTEGER DEFAULT 0",
    }

    for column_name, sql in needed_columns.items():
        if column_name not in existing_columns:
            cur.execute(sql)

    conn.commit()
    conn.close()


def get_seen_deal(appid: str) -> dict[str, Any] | None:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT appid, title, discount_percent, final_price, initial_price, currency
        FROM seen_deals
        WHERE appid = ?
    """, (appid,))

    row = cur.fetchone()
    conn.close()

    return dict(row) if row else None


def is_new_deal(deal: dict[str, Any]) -> bool:
    existing = get_seen_deal(deal["appid"])

    if existing is None:
        return True
    if existing["discount_percent"] != deal["discount_percent"]:
        return True
    if existing["final_price"] != deal["final_price"]:
        return True
    if existing["initial_price"] != deal["initial_price"]:
        return True

    return False


def save_deal(deal: dict[str, Any]) -> None:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO seen_deals (
            appid, title, discount_percent, final_price, initial_price, currency, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(appid) DO UPDATE SET
            title = excluded.title,
            discount_percent = excluded.discount_percent,
            final_price = excluded.final_price,
            initial_price = excluded.initial_price,
            currency = excluded.currency,
            updated_at = CURRENT_TIMESTAMP
    """, (
        deal["appid"],
        deal["title"],
        deal["discount_percent"],
        deal["final_price"],
        deal["initial_price"],
        deal["currency"],
    ))

    conn.commit()
    conn.close()


def delete_seen_deal(appid: str) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM seen_deals WHERE appid = ?", (appid,))
    conn.commit()
    conn.close()


def create_moderation_item(deal: dict[str, Any]) -> int:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO moderation_queue (
            appid, title, discount_percent, final_price, initial_price, currency,
            short_description, original_description, translated_description,
            review_percent, total_reviews, sale_end_text, status, state
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 'pending_image_choice')
    """, (
        deal["appid"],
        deal["title"],
        deal["discount_percent"],
        deal["final_price"],
        deal["initial_price"],
        deal["currency"],
        deal.get("short_description", ""),
        deal.get("original_description", ""),
        deal.get("translated_description", ""),
        deal.get("review_percent", 0),
        deal.get("total_reviews", 0),
        deal.get("sale_end_text", ""),
    ))

    moderation_id = cur.lastrowid
    conn.commit()
    conn.close()
    return moderation_id


def get_moderation_item(item_id: int) -> dict[str, Any] | None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM moderation_queue WHERE id = ?", (item_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_moderation_item_by_upload_request_message_id(message_id: int) -> dict[str, Any] | None:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM moderation_queue
        WHERE upload_request_message_id = ?
          AND state = 'waiting_custom_image'
        ORDER BY id DESC
        LIMIT 1
    """, (message_id,))

    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def update_moderation_status(item_id: int, status: str) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE moderation_queue SET status = ? WHERE id = ?", (status, item_id))
    conn.commit()
    conn.close()


def update_moderation_state(item_id: int, state: str) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE moderation_queue SET state = ? WHERE id = ?", (state, item_id))
    conn.commit()
    conn.close()


def set_selected_image(item_id: int, variant: str, image_path: str, custom_image_path: str = "") -> None:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        UPDATE moderation_queue
        SET selected_image_variant = ?,
            selected_image_path = ?,
            custom_image_path = ?,
            state = 'ready_to_post'
        WHERE id = ?
    """, (variant, image_path, custom_image_path, item_id))

    conn.commit()
    conn.close()


def set_control_message_id(item_id: int, message_id: int) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE moderation_queue SET control_message_id = ? WHERE id = ?", (message_id, item_id))
    conn.commit()
    conn.close()


def set_preview_message_id(item_id: int, message_id: int) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE moderation_queue SET preview_message_id = ? WHERE id = ?", (message_id, item_id))
    conn.commit()
    conn.close()


def set_upload_request_message_id(item_id: int, message_id: int) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE moderation_queue SET upload_request_message_id = ? WHERE id = ?", (message_id, item_id))
    conn.commit()
    conn.close()


def clear_upload_request_message_id(item_id: int) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE moderation_queue SET upload_request_message_id = 0 WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()


def register_moderation_message(moderation_id: int, chat_id: int, message_id: int, kind: str) -> None:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO moderation_messages (moderation_id, chat_id, message_id, kind)
        VALUES (?, ?, ?, ?)
    """, (moderation_id, chat_id, message_id, kind))

    conn.commit()
    conn.close()


def get_moderation_messages(moderation_id: int) -> list[dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT moderation_id, chat_id, message_id, kind
        FROM moderation_messages
        WHERE moderation_id = ?
        ORDER BY id ASC
    """, (moderation_id,))

    rows = cur.fetchall()
    conn.close()

    return [dict(row) for row in rows]


def delete_moderation_messages_records(moderation_id: int) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM moderation_messages WHERE moderation_id = ?", (moderation_id,))
    conn.commit()
    conn.close()


def block_game(appid: str, title: str = "") -> None:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO blocked_games (appid, title, blocked_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(appid) DO UPDATE SET
            title = excluded.title,
            blocked_at = CURRENT_TIMESTAMP
    """, (appid, title))

    conn.commit()
    conn.close()


def is_game_blocked(appid: str) -> bool:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM blocked_games WHERE appid = ?", (appid,))
    row = cur.fetchone()
    conn.close()
    return row is not None


def get_cached_translation(appid: str, original_description: str) -> str | None:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT translated_description
        FROM moderation_queue
        WHERE appid = ?
          AND original_description = ?
          AND translated_description != ''
        ORDER BY id DESC
        LIMIT 1
    """, (appid, original_description))

    row = cur.fetchone()
    conn.close()

    return row["translated_description"] if row else None