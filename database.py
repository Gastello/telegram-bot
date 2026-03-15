import sqlite3
from pathlib import Path

DB_PATH = Path("bot.db")


def get_conn():
    return sqlite3.connect(DB_PATH)


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
        title TEXT
    )
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

    cur.execute("""
    CREATE TABLE IF NOT EXISTS moderation_messages (
        moderation_id INTEGER,
        chat_id INTEGER,
        message_id INTEGER,
        kind TEXT
    )
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

    old_discount, old_price = row

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
    (appid, title)
    VALUES (?, ?)
    """, (appid, title))

    conn.commit()
    conn.close()


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
        return row[0]

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
        sale_end_text
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    ))

    moderation_id = cur.lastrowid

    conn.commit()
    conn.close()

    return moderation_id


def get_moderation_item(moderation_id: int) -> dict | None:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
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
    conn.row_factory = sqlite3.Row
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
    conn.row_factory = sqlite3.Row
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