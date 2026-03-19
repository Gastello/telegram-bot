import os
import threading
import time
import requests
from deep_translator import GoogleTranslator

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import BOT_TOKEN, MOD_CHAT_ID, CHANNEL_ID, STEAM_API_KEY
from database import (
    init_db,
    block_game,
    clear_upload_request_message_id,
    create_moderation_item,
    delete_moderation_messages_records,
    delete_moderation_messages_records_by_ids,
    get_cached_translation,
    get_last_blocked_game,
    get_last_published_moderation_item,
    get_moderation_item,
    get_moderation_item_by_appid,
    get_moderation_item_by_upload_request_message_id,
    get_moderation_messages,
    list_blocked_games,
    register_moderation_message,
    save_translation,
    set_preview_message_id,
    set_selected_image,
    set_upload_request_message_id,
    unblock_game,
    update_custom_text,
    update_moderation_state,
    update_moderation_status,
)
from image_generator import (
    generate_post_image,
    generate_tiktok_image,
    get_custom_upload_path,
    get_generated_image_path,
    TIKTOK_VARIANT_SOURCE_MAP,
)
from post_formatter import build_post_text
from steam_store_parser import get_sale_end_text
from bot import send_to_moderation, safe_send_photo


STEAM_URL = "https://store.steampowered.com/api/appdetails"
REVIEWS_URL = "https://store.steampowered.com/appreviews"

COUNTRY_CODE = "ua"

DISCOUNT_THRESHOLD = 50
MIN_TOTAL_REVIEWS = 1000
MIN_REVIEW_PERCENT = 70.0

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
        print(f"translation appid={appid}: {error}")
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

            print(f"{label}: {error}")
            return None
        except ValueError as error:
            print(f"{label}: invalid JSON: {error}")
            return None

    return None


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


def generate_deal_for_appid(appid: str) -> dict[str, Any] | None:
    from database import is_game_blocked

    if is_game_blocked(appid):
        print(f"[BLOCKED] {appid}")
        return None

    session = make_session()
    time.sleep(0.35)
    data = fetch_app_details(session, appid)
    if not data:
        print(f"[SKIP] {appid} | no appdetails")
        return None

    deal = build_base_deal(appid, data)
    if not deal:
        print(f"[SKIP] {appid} | no price data")
        return None

    if deal["type"] != "game":
        print(f"[SKIP] {appid} | not a game ({deal['type']})")
        return None

    # Для мануальної генерації не перевіряємо фільтри, щоб дозволити будь-яку гру
    time.sleep(0.35)
    reviews = fetch_reviews_summary(session, appid)
    if not reviews:
        # Якщо немає reviews, встановимо дефолтні
        review_percent = 0.0
        total_reviews = 0
        review_score_desc = ""
    else:
        review_percent = float(reviews.get("review_percent", 0.0))
        total_reviews = int(reviews.get("total_reviews", 0))
        review_score_desc = reviews.get("review_score_desc", "")

    sale_end_text = ""
    try:
        sale_end_text = get_sale_end_text(appid) or ""
    except Exception as error:
        print(f"sale_end appid={appid}: {error}")

    # Extract screenshot URLs from app data
    screenshots = []
    try:
        if data.get("screenshots"):
            screenshots = [
                img.get("path_full", "")
                for img in data["screenshots"][:5]  # Take first 5 screenshots
                if img.get("path_full")
            ]
    except Exception as error:
        print(f"screenshots appid={appid}: {error}")

    original_description = deal["original_description"]
    translated_description = translate_description(appid, original_description)

    deal["short_description"] = translated_description
    deal["translated_description"] = translated_description
    deal["review_percent"] = review_percent
    deal["total_reviews"] = total_reviews
    deal["review_score_desc"] = review_score_desc
    deal["sale_end_text"] = sale_end_text
    deal["screenshots"] = screenshots

    return deal


def generate_and_send(appid: str) -> None:
    deal = generate_deal_for_appid(appid)
    if deal:
        send_to_moderation(deal)
    else:
        print(f"Failed to generate deal for appid {appid}")


TERMINAL_STATES = {"published", "rejected", "blocked"}
STATUS_KINDS = {"published", "rejected", "blocked"}


def build_final_preview_keyboard(moderation_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Post", callback_data=f"post|{moderation_id}")],
        [
            InlineKeyboardButton("📤 Своє фото", callback_data=f"upload_custom|{moderation_id}"),
            InlineKeyboardButton("✏️ Редагувати текст", callback_data=f"edit_text|{moderation_id}"),
        ],
        [
            InlineKeyboardButton("❌ Reject", callback_data=f"reject|{moderation_id}"),
            InlineKeyboardButton("🚫 Block game", callback_data=f"block|{moderation_id}"),
        ],
    ])


def build_tiktok_variant_keyboard(
    moderation_id: int,
    variant_key: str,
    single_variant: bool,
) -> InlineKeyboardMarkup:
    if single_variant:
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ Обрати цей варіант",
                    callback_data=f"choose_tiktok_variant|{moderation_id}|{variant_key}",
                )
            ],
            [
                InlineKeyboardButton(
                    "📤 Своє фото",
                    callback_data=f"upload_tiktok_custom|{moderation_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ Reject",
                    callback_data=f"reject_tiktok|{moderation_id}",
                ),
            ],
        ])

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Обрати цей варіант",
                callback_data=f"choose_tiktok_variant|{moderation_id}|{variant_key}",
            )
        ]
    ])


def build_help_text() -> str:
    return (
        "Доступні команди:\n\n"
        "/blacklist — показати чорний список\n"
        "/block APPID — заблокувати гру по appid\n"
        "/unblock APPID — розблокувати гру по appid\n"
        "/unblock_last — розблокувати останню заблоковану гру\n"
        "/generate APPID — згенерувати пост для гри по appid\n"
        "/tiktok APPID — згенерувати TikTok-картинку для гри по appid\n"
        "/tiktok_last — згенерувати TikTok-картинку для останнього опублікованого поста"
    )


async def send_status_log(
    context: ContextTypes.DEFAULT_TYPE,
    moderation_id: int,
    chat_id: int,
    kind: str,
    title: str,
) -> None:
    if kind == "published":
        text = f"✅ Published · {title}"
    elif kind == "rejected":
        text = f"❌ Rejected · {title}"
    elif kind == "blocked":
        text = f"🚫 Blocked · {title}"
    else:
        text = f"ℹ️ {kind} · {title}"

    sent = await context.bot.send_message(chat_id=chat_id, text=text)
    register_moderation_message(moderation_id, chat_id, sent.message_id, kind)


async def cleanup_moderation_chat(context: ContextTypes.DEFAULT_TYPE, moderation_id: int, keep_preview: bool = False) -> None:
    messages = get_moderation_messages(moderation_id)
    
    print(f"[CLEANUP START] moderation_id={moderation_id} messages_count={len(messages)}")
    
    deleted_count = 0
    failed_count = 0
    skipped_count = 0

    for msg in messages:
        kind = msg["kind"]
        chat_id = msg["chat_id"]
        message_id = msg["message_id"]

        print(f"[CLEANUP MSG] message_id={message_id} kind={kind} chat_id={chat_id}")

        # Пропустити публіковані/відхилені/заблоковані постови
        if kind in STATUS_KINDS:
            print(f"[CLEANUP SKIP] message_id={message_id} kind={kind} - status message")
            continue
        
        # Опційно пропустити preview (якщо потрібен для фіналу)
        if keep_preview and kind == "preview":
            print(f"[CLEANUP SKIP] message_id={message_id} kind={kind} - keep preview")
            continue

        # Пропустити видалення в групах/каналах (де chat_id < 0), бо бот може не мати прав
        # Але дозволити видалення в модераційному чаті (MOD_CHAT_ID)
        if chat_id < 0 and chat_id != MOD_CHAT_ID:
            print(f"[CLEANUP SKIP] message_id={message_id} kind={kind} - channel/group chat_id={chat_id}")
            skipped_count += 1
            continue

        try:
            await context.bot.delete_message(
                chat_id=chat_id,
                message_id=message_id,
            )
            deleted_count += 1
            print(f"[CLEANUP] moderation_id={moderation_id} message_id={message_id} kind={kind} deleted")
        except Exception as error:
            failed_count += 1
            print(
                f"[CLEANUP WARN] moderation_id={moderation_id} "
                f"message_id={message_id} kind={kind} chat_id={chat_id}: {error}"
            )

    delete_moderation_messages_records(moderation_id)
    
    print(f"[CLEANUP RESULT] moderation_id={moderation_id} deleted={deleted_count} failed={failed_count} skipped={skipped_count}")


async def cleanup_tiktok_variants(
        context: ContextTypes.DEFAULT_TYPE,
        moderation_id: int,
        keep_message_id: int | None = None,
    ) -> None:
        """Delete all TikTok variant messages except selected one"""
        
        messages = get_moderation_messages(moderation_id)

        deleted_ids = []

        for msg in messages:
            kind = msg["kind"]

            if not kind.startswith("tiktok_variant_"):
                continue

            message_id = msg["message_id"]
            chat_id = msg["chat_id"]

            # Пропускаємо вибраний
            if keep_message_id and message_id == keep_message_id:
                continue

            try:
                await context.bot.delete_message(
                    chat_id=chat_id,
                    message_id=message_id,
                )
                deleted_ids.append(message_id)
                print(f"[TIKTOK CLEANUP] deleted {message_id}")

            except Exception as e:
                print(f"[TIKTOK CLEANUP WARN] {message_id}: {e}")

        # ❗ ВАЖЛИВО: чистимо записи з БД
        if deleted_ids:
            delete_moderation_messages_records_by_ids(deleted_ids)

def cleanup_generated_files(title: str, appid: str | int) -> None:
    """Видаляє тимчасово згенеровані файли для гри"""
    try:
        import glob
        pattern = os.path.join("generated", f"*{appid}*")
        files = glob.glob(pattern)
        
        for file in files:
            try:
                os.remove(file)
                print(f"[FILE CLEANUP] Deleted {file}")
            except Exception as e:
                print(f"[FILE CLEANUP WARN] Could not delete {file}: {e}")
    except Exception as error:
        print(f"[FILE CLEANUP ERROR] Cleanup for appid={appid}: {error}")


async def _safe_edit_status(query, text: str) -> None:
    try:
        if query.message and query.message.photo:
            await query.edit_message_caption(caption=text)
        else:
            await query.edit_message_text(text=text)
    except Exception:
        if query.message:
            await query.message.reply_text(text)





async def send_final_preview(
    context: ContextTypes.DEFAULT_TYPE,
    item: dict,
    chat_id: int,
) -> None:
    # Перед новим прев'ю обов'язково видаляємо попередні модераційні повідомлення
    # (варіанти + старі прев'ю). Це уникне ситуацій зі «старими кнопками».
    await cleanup_moderation_chat(context, item["id"])

    text = build_post_text(item)
    image_path = item.get("selected_image_path", "")

    if image_path and os.path.exists(image_path):
        with open(image_path, "rb") as image_file:
            sent = await context.bot.send_photo(
                chat_id=chat_id,
                photo=image_file,
                caption=text,
                parse_mode="HTML",
                reply_markup=build_final_preview_keyboard(item["id"]),
            )
    else:
        sent = await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=build_final_preview_keyboard(item["id"]),
        )

    set_preview_message_id(item["id"], sent.message_id)
    register_moderation_message(item["id"], chat_id, sent.message_id, "preview")


async def blacklist_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat:
        return

    blocked = list_blocked_games(limit=50)

    if not blocked:
        await context.bot.send_message(
            chat_id=chat.id,
            text="📭 Чорний список порожній.\nЖодна гра ще не заблокована."
        )
        return

    await context.bot.send_message(
        chat_id=chat.id,
        text=f"🚫 У чорному списку: {len(blocked)} ігор"
    )

    for item in blocked:
        text = f"🎮 {item['title']}\nappid: {item['appid']}"
        await context.bot.send_message(
            chat_id=chat.id,
            text=text,
            reply_markup=build_unblock_keyboard(item["appid"]),
        )


async def block_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.effective_message.reply_text("Використання: /block APPID")
        return

    appid = context.args[0].strip()

    block_game(appid, "")
    await update.effective_message.reply_text(f"🚫 Заблоковано гру з appid {appid}")


async def unblock_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.effective_message.reply_text("Використання: /unblock APPID")
        return

    appid = context.args[0].strip()

    if unblock_game(appid):
        await update.effective_message.reply_text(f"🔓 Розблоковано гру з appid {appid}")
    else:
        await update.effective_message.reply_text(f"Гру з appid {appid} не знайдено в чорному списку.")


async def unblock_last_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    item = get_last_blocked_game()
    if not item:
        await update.effective_message.reply_text("📭 Чорний список порожній.")
        return

    if unblock_game(item["appid"]):
        await update.effective_message.reply_text(
            f"🔓 Розблоковано останню гру:\n🎮 {item['title']}\nappid: {item['appid']}"
        )
    else:
        await update.effective_message.reply_text("Не вдалося розблокувати останню гру.")


async def generate_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args or len(context.args) != 1:
        await update.effective_message.reply_text("Використання: /generate <appid>")
        return

    appid = context.args[0]
    if not appid.isdigit():
        await update.effective_message.reply_text("appid має бути числом.")
        return

    await update.effective_message.reply_text(f"Генерую пост для appid {appid}...")

    # Запускаємо в окремому потоці, щоб не блокувати бота
    threading.Thread(target=generate_and_send, args=(appid,)).start()


async def tiktok_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args or len(context.args) != 1:
        await update.effective_message.reply_text("Використання: /tiktok <appid>")
        return

    appid = context.args[0]
    if not appid.isdigit():
        await update.effective_message.reply_text("appid має бути числом.")
        return

    await update.effective_message.reply_text(f"Генерую TikTok-картинку для appid {appid}...")

    # Запускаємо в окремому потоці, щоб не блокувати бота
    threading.Thread(target=generate_tiktok_for_appid, args=(appid,)).start()


async def tiktok_last_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text("Генерую TikTok-картинку для останнього опублікованого поста...")

    # Запускаємо в окремому потоці, щоб не блокувати бота
    threading.Thread(target=generate_tiktok_for_last_published).start()


def generate_tiktok_for_appid(appid: str) -> None:
    """Generate TikTok image variants for specific appid"""
    try:
        # Get deal data for the appid
        deal = generate_deal_for_appid(appid)
        if not deal:
            print(f"[TIKTOK] Failed to generate deal for appid {appid}")
            return

        # Generate TikTok variants
        # Create or get moderation item for TikTok generation
        moderation_item = get_moderation_item_by_appid(appid)
        if moderation_item:
            moderation_id = moderation_item['id']
        else:
            # Create a temporary moderation item for TikTok generation
            moderation_id = create_moderation_item(deal)
        
        generate_tiktok_variants(deal, moderation_id)

    except Exception as error:
        print(f"[TIKTOK ERROR] Failed to generate TikTok variants for appid {appid}: {error}")


def generate_tiktok_for_last_published() -> None:
    """Generate TikTok image variants for the last published moderation item"""
    try:
        # Get the last published item
        item = get_last_published_moderation_item()
        if not item:
            print("[TIKTOK] No published items found")
            return

        # Convert moderation item to deal format
        deal = {
            "appid": item["appid"],
            "title": item["title"],
            "final_price": item["final_price"],
            "initial_price": item["initial_price"],
            "currency": item["currency"],
            "header_image": item.get("header_image", ""),
            "sale_end_text": item.get("sale_end_text", ""),
        }

        # Fetch screenshots from Steam API
        try:
            session = make_session()
            data = fetch_app_details(session, item["appid"])
            if data and data.get("screenshots"):
                screenshots = [
                    img.get("path_full", "")
                    for img in data["screenshots"][:5]
                    if img.get("path_full")
                ]
                deal["screenshots"] = screenshots
            else:
                deal["screenshots"] = []
        except Exception as error:
            print(f"[TIKTOK] Failed to fetch screenshots for {item['appid']}: {error}")
            deal["screenshots"] = []

        # Generate TikTok variants
        generate_tiktok_variants(deal, item["id"])

    except Exception as error:
        print(f"[TIKTOK ERROR] Failed to generate TikTok variants for last published item: {error}")


def generate_tiktok_variants(deal: dict, moderation_id: int) -> None:
    """Generate multiple TikTok image variants and send to moderation chat"""
    import asyncio
    from telegram import Bot
    
    async def _generate_and_send():
        bot = Bot(token=BOT_TOKEN)
        appid = deal["appid"]
        
        generated_variants: list[dict] = []

        for variant_key, source_type in TIKTOK_VARIANT_SOURCE_MAP.items():
            image_path = None

            try:
                image_path = generate_tiktok_image(
                    appid=deal["appid"],
                    title=deal["title"],
                    final_price=deal["final_price"],
                    initial_price=deal["initial_price"],
                    currency=deal["currency"],
                    header_image_url=deal.get("header_image", ""),
                    sale_end_text=deal.get("sale_end_text", ""),
                    source_type=source_type,
                    screenshots=deal.get("screenshots", []),
                    output_path=get_generated_image_path(
                        deal["title"],
                        deal["appid"],
                        f"tiktok_variant_{variant_key}",
                    ),
                )
            except Exception as error:
                print(
                    f"[TIKTOK VARIANT ERROR] appid={appid} "
                    f"variant={variant_key}: {error}"
                )

            if not image_path:
                continue

            generated_variants.append({
                "variant_key": variant_key,
                "image_path": image_path,
            })

        if not generated_variants:
            print(f"[TIKTOK] No TikTok variants generated for appid={appid}")
            return

        single_variant = len(generated_variants) == 1

        for item in generated_variants:
            variant_key = item["variant_key"]
            image_path = item["image_path"]

            keyboard = build_tiktok_variant_keyboard(
                moderation_id=moderation_id,
                variant_key=variant_key,
                single_variant=single_variant,
            )

            try:
                with open(image_path, "rb") as image_file:
                    photo_bytes = image_file.read()

                sent = await safe_send_photo(
                    bot,
                    chat_id=MOD_CHAT_ID,
                    photo_bytes=photo_bytes,
                    filename=f"tiktok_{appid}_{variant_key}.png",
                    caption=f"🎬 TikTok · {deal['title']} · варіант {variant_key}",
                    reply_markup=keyboard,
                )
                
                # Register message for cleanup
                register_moderation_message(moderation_id, MOD_CHAT_ID, sent.message_id, f"tiktok_variant_{variant_key}")
                
            except Exception as error:
                print(f"[TIKTOK SEND ERROR] Failed to send variant {variant_key}: {error}")

    # Run the async function
    asyncio.run(_generate_and_send())


async def help_fallback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat

    if not message or not chat:
        return

    # Реагувати тільки в особистому чаті з ботом або в модераційному чаті для обробки reply
    if chat.type != 'private' and chat.id != MOD_CHAT_ID:
        print(f"[HELP SKIP] message from chat_id={chat.id}, type={chat.type}")
        return

    # В особистому чаті з ботом завжди показувати список команд
    if chat.type == 'private':
        await context.bot.send_message(
            chat_id=chat.id,
            text=build_help_text(),
        )
        return

    # В модераційному чаті перевіряти, чи це reply до edit_text_prompt
    if message.reply_to_message:
        reply_message_id = message.reply_to_message.message_id
        item = get_moderation_item_by_upload_request_message_id(reply_message_id)
        if item and item.get("state") == "waiting_custom_text":
            custom_text = message.text.strip()
            if custom_text:
                update_custom_text(item["id"], custom_text)
                clear_upload_request_message_id(item["id"])
                update_moderation_state(item["id"], "waiting_image")  # back to waiting image

                await cleanup_moderation_chat(context, item["id"])

                updated_item = get_moderation_item(item["id"])
                if updated_item:
                    await send_final_preview(context, updated_item, chat.id)

                # Delete the user's reply message
                try:
                    await context.bot.delete_message(chat_id=chat.id, message_id=message.message_id)
                except Exception as e:
                    print(f"Failed to delete user reply: {e}")

                return
            else:
                await message.reply_text("Текст не може бути порожнім.")
                return

    # Якщо не reply і не приватний чат, показати help
    await context.bot.send_message(
        chat_id=chat.id,
        text=build_help_text(),
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return

    await query.answer()

    raw_data = query.data or ""
    parts = raw_data.split("|")

    if not parts:
        await _safe_edit_status(query, "⚠️ Bad callback data")
        return

    action = parts[0]

    if action == "choose_tiktok_variant":
        if len(parts) < 3:
            await _safe_edit_status(query, "⚠️ No TikTok variant selected")
            return

        moderation_id = int(parts[1])
        variant_key = parts[2]
        
        chat_id = query.message.chat_id if query.message else None
        if chat_id is None or chat_id != MOD_CHAT_ID:
            await _safe_edit_status(query, "⚠️ TikTok variant selection works only in moderation chat")
            return

        # Mark this variant as selected
        try:
            if query.message.photo:
                moderation_item = get_moderation_item(moderation_id)
                title = moderation_item["title"] if moderation_item else "Unknown"
                await query.edit_message_caption(
                    caption=f"🎬 TikTok · {title}",
                    reply_markup=None
                )
            else:
                await query.edit_message_text(
                    text=f"🎬 TikTok · {title}",
                    reply_markup=None
                )
        except Exception as e:
            print(f"[TIKTOK SELECT ERROR] {e}")

        # Clean up other TikTok variant messages
        await cleanup_tiktok_variants(
            context,
            moderation_id,
            keep_message_id=query.message.message_id,
        )

        # Set selected image
        moderation_item = get_moderation_item(moderation_id)
        if moderation_item:
            image_path = get_generated_image_path(
                moderation_item["title"],
                moderation_item["appid"],
                f"tiktok_variant_{variant_key}",
            )
            set_selected_image(moderation_id, variant_key, image_path)
        else:
            print(f"[TIKTOK CLEANUP WARN] moderation item not found for moderation_id={moderation_id}")

        return

    if action == "upload_tiktok_custom":
        if len(parts) < 2:
            await _safe_edit_status(query, "⚠️ No moderation_id for TikTok custom upload")
            return

        moderation_id = int(parts[1])
        chat_id = query.message.chat_id if query.message else None
        if chat_id is None or chat_id != MOD_CHAT_ID:
            await _safe_edit_status(query, "⚠️ TikTok custom upload works only in moderation chat")
            return

        # Clean up variant messages
        await cleanup_tiktok_variants(context, moderation_id, keep_message_id=None)

        # Set up custom upload for TikTok
        update_moderation_state(moderation_id, "waiting_custom_image")

        prompt = await context.bot.send_message(
            chat_id=chat_id,
            text=f"Надішли своє фото для TikTok reply-ом НА ЦЕ повідомлення."
        )

        set_upload_request_message_id(moderation_id, prompt.message_id)
        register_moderation_message(moderation_id, chat_id, prompt.message_id, "tiktok_upload_prompt")

        return

    if action == "reject_tiktok":
        if len(parts) < 2:
            await _safe_edit_status(query, "⚠️ No moderation_id for TikTok reject")
            return

        moderation_id = int(parts[1])
        chat_id = query.message.chat_id if query.message else None
        if chat_id is None or chat_id != MOD_CHAT_ID:
            await _safe_edit_status(query, "⚠️ TikTok reject works only in moderation chat")
            return

        # Get moderation item to get title
        moderation_item = get_moderation_item(moderation_id)
        title = moderation_item['title'] if moderation_item else f"moderation_id_{moderation_id}"

        # Clean up all TikTok variant messages
        await cleanup_tiktok_variants(context, moderation_id, keep_message_id=None)

        await context.bot.send_message(
            chat_id=chat_id,
            text=f"❌ TikTok генерація відхилена"
        )

        # Clean up generated files
        cleanup_generated_files(title, appid)

        return

    if action == "unblock_game":
        if len(parts) < 2:
            await _safe_edit_status(query, "⚠️ No appid for unblock")
            return

        appid = parts[1].strip()
        if unblock_game(appid):
            await _safe_edit_status(query, f"🔓 Unblocked appid {appid}")
        else:
            await _safe_edit_status(query, f"⚠️ appid {appid} not found in blacklist")
        return

    if len(parts) < 2:
        await _safe_edit_status(query, f"⚠️ Bad callback data: {raw_data}")
        return

    try:
        moderation_id = int(parts[1])
    except ValueError:
        await _safe_edit_status(query, f"⚠️ Bad moderation id: {parts[1]}")
        return

    item = get_moderation_item(moderation_id)
    if not item:
        await _safe_edit_status(query, "⚠️ Draft not found")
        return

    chat_id = query.message.chat_id if query.message else None
    if chat_id is None:
        return

    # Реагувати тільки в модераційному чаті
    if chat_id != MOD_CHAT_ID:
        print(f"[BUTTON SKIP] button from chat_id={chat_id}, expected MOD_CHAT_ID={MOD_CHAT_ID}")
        await _safe_edit_status(query, "⚠️ Кнопки працюють тільки в модераційному чаті")
        return

    current_state = (item.get("state") or "").strip()
    current_status = (item.get("status") or "").strip()
    if current_state in TERMINAL_STATES or current_status in TERMINAL_STATES:
        await _safe_edit_status(query, f"⚠️ Це вже завершений пост: {current_status or current_state}")
        return

    if action == "choose_variant":
        if len(parts) < 3:
            await _safe_edit_status(query, "⚠️ No variant selected")
            return

        variant_key = parts[2]
        image_path = get_generated_image_path(item["title"], item["appid"], f"variant_{variant_key}")

        if not os.path.exists(image_path):
            await _safe_edit_status(query, "⚠️ Variant image not found")
            return

        set_selected_image(moderation_id, variant_key, image_path)
        
        await cleanup_moderation_chat(context, moderation_id)

        updated_item = get_moderation_item(moderation_id)
        if updated_item:
            await send_final_preview(context, updated_item, chat_id)

    elif action == "upload_custom":
        # При завантаженні власного фото видаляємо старе прев'ю щоб не лишати старі кнопки
        await cleanup_moderation_chat(context, moderation_id)

        update_moderation_state(moderation_id, "waiting_custom_image")

        prompt = await context.bot.send_message(
            chat_id=chat_id,
            text="Надішли своє фото reply-ом НА ЦЕ повідомлення."
        )

        set_upload_request_message_id(moderation_id, prompt.message_id)
        register_moderation_message(moderation_id, chat_id, prompt.message_id, "upload_prompt")

    elif action == "edit_text":
        await cleanup_moderation_chat(context, moderation_id)
        
        update_moderation_state(moderation_id, "waiting_custom_text")

        current_text = build_post_text(item)

        prompt = await context.bot.send_message(
            chat_id=chat_id,
            text=f"Поточний текст поста:\n\n{current_text}\n\nНадішли новий текст reply-ом НА ЦЕ повідомлення."
        )

        set_upload_request_message_id(moderation_id, prompt.message_id)
        register_moderation_message(moderation_id, chat_id, prompt.message_id, "edit_text_prompt")

    elif action == "post":
        selected_image_path = item.get("selected_image_path", "")
        if not selected_image_path:
            await _safe_edit_status(query, "⚠️ Спочатку обери варіант зображення або завантаж своє фото.")
            return

        text = build_post_text(item)

        if selected_image_path and os.path.exists(selected_image_path):
            with open(selected_image_path, "rb") as image_file:
                await context.bot.send_photo(
                    chat_id=CHANNEL_ID,
                    photo=image_file,
                    caption=text,
                    parse_mode="HTML",
                )
        else:
            await context.bot.send_message(
                chat_id=CHANNEL_ID,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )

        update_moderation_status(moderation_id, "published")
        update_moderation_state(moderation_id, "published")

        await send_status_log(
            context=context,
            moderation_id=moderation_id,
            chat_id=chat_id,
            kind="published",
            title=item.get("title", ""),
        )

        await cleanup_moderation_chat(context, moderation_id)
        cleanup_generated_files(item["title"], item["appid"])

    elif action == "reject":
        update_moderation_status(moderation_id, "rejected")
        update_moderation_state(moderation_id, "rejected")

        await send_status_log(
            context=context,
            moderation_id=moderation_id,
            chat_id=chat_id,
            kind="rejected",
            title=item.get("title", ""),
        )

        await cleanup_moderation_chat(context, moderation_id)
        cleanup_generated_files(item["title"], item["appid"])

    elif action == "block":
        block_game(item["appid"], item.get("title", ""))
        update_moderation_status(moderation_id, "blocked")
        update_moderation_state(moderation_id, "blocked")

        await send_status_log(
            context=context,
            moderation_id=moderation_id,
            chat_id=chat_id,
            kind="blocked",
            title=item.get("title", ""),
        )

        await cleanup_moderation_chat(context, moderation_id)
        cleanup_generated_files(item["title"], item["appid"])

    else:
        await _safe_edit_status(query, f"⚠️ Unknown action: {action}")


async def upload_image_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message

    if not message:
        return

    # Реагувати тільки в модераційному чаті
    if message.chat_id != MOD_CHAT_ID:
        print(f"[UPLOAD SKIP] upload from chat_id={message.chat_id}, expected MOD_CHAT_ID={MOD_CHAT_ID}")
        return

    if not message.reply_to_message:
        return

    reply_message_id = message.reply_to_message.message_id
    item = get_moderation_item_by_upload_request_message_id(reply_message_id)

    if not item:
        return

    # Check if this is a TikTok custom upload
    is_tiktok_upload = False
    messages = get_moderation_messages(item["id"])
    for msg in messages:
        if msg['message_id'] == reply_message_id and msg['kind'] == 'tiktok_upload_prompt':
            is_tiktok_upload = True
            break

    current_state = (item.get("state") or "").strip()
    current_status = (item.get("status") or "").strip()
    if current_state in TERMINAL_STATES or current_status in TERMINAL_STATES:
        return

    photo_file = None
    ext = "jpg"

    if message.photo:
        photo = message.photo[-1]
        photo_file = await photo.get_file()
        ext = "jpg"

    elif message.document:
        mime_type = message.document.mime_type or ""

        if mime_type.startswith("image/"):
            photo_file = await message.document.get_file()
            filename = message.document.file_name or ""
            if "." in filename:
                ext = filename.rsplit(".", 1)[-1].lower()
        else:
            await message.reply_text("⚠️ Надішли саме фото або image-файл.")
            return
    else:
        await message.reply_text("⚠️ Надішли саме фото або image-файл.")
        return

    register_moderation_message(item["id"], message.chat_id, message.message_id, "user_upload")

    custom_upload_path = get_custom_upload_path(item["title"], item["appid"], ext)

    await photo_file.download_to_drive(custom_upload_path)

    try:
        if is_tiktok_upload:
            final_custom_path = generate_tiktok_image(
                appid=item["appid"],
                title=item["title"],
                final_price=item["final_price"],
                initial_price=item["initial_price"],
                currency=item["currency"],
                header_image_url=item.get("header_image", ""),
                sale_end_text=item.get("sale_end_text", ""),
                source_type="auto",  # Use custom background
                custom_background_path=custom_upload_path,
                output_path=get_generated_image_path(item["title"], item["appid"], "tiktok_custom"),
            )
        else:
            final_custom_path = generate_post_image(
                appid=item["appid"],
                title=item["title"],
                final_price=item["final_price"],
                initial_price=item["initial_price"],
                currency=item["currency"],
                sale_end_text=item.get("sale_end_text", ""),
                custom_background_path=custom_upload_path,
                output_path=get_generated_image_path(item["title"], item["appid"], "custom"),
            )
    except Exception as error:
        await message.reply_text(f"⚠️ Не вдалося обробити кастомне фото: {error}")
        return

    set_selected_image(
        item["id"],
        "tiktok_custom" if is_tiktok_upload else "custom",
        final_custom_path,
        custom_image_path=custom_upload_path,
    )
    clear_upload_request_message_id(item["id"])

    await cleanup_moderation_chat(context, item["id"])

    updated_item = get_moderation_item(item["id"])
    if updated_item:
        await send_final_preview(context, updated_item, message.chat_id)


def run_bot() -> None:
    init_db()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("blacklist", blacklist_command))
    app.add_handler(CommandHandler("block", block_command))
    app.add_handler(CommandHandler("unblock", unblock_command))
    app.add_handler(CommandHandler("unblock_last", unblock_last_command))
    app.add_handler(CommandHandler("generate", generate_command))
    app.add_handler(CommandHandler("tiktok", tiktok_command))
    app.add_handler(CommandHandler("tiktok_last", tiktok_last_command))

    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(
        MessageHandler(
            (filters.PHOTO | filters.Document.IMAGE),
            upload_image_handler,
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            help_fallback_handler,
        )
    )

    print("Moderator bot running...")
    app.run_polling()


if __name__ == "__main__":
    run_bot()