import os

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import BOT_TOKEN, CHANNEL_ID
from database import (
    block_game,
    clear_upload_request_message_id,
    delete_moderation_messages_records,
    get_moderation_item,
    get_moderation_item_by_upload_request_message_id,
    get_moderation_messages,
    register_moderation_message,
    set_preview_message_id,
    set_selected_image,
    set_upload_request_message_id,
    update_moderation_state,
    update_moderation_status,
)
from image_generator import (
    generate_post_image,
    get_custom_upload_path,
    get_generated_image_path,
)
from post_formatter import build_post_text


TERMINAL_STATES = {"published", "rejected", "blocked"}
STATUS_KINDS = {"published", "rejected", "blocked"}


def build_final_preview_keyboard(moderation_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Post", callback_data=f"post|{moderation_id}")],
        [InlineKeyboardButton("📤 Своє фото", callback_data=f"upload_custom|{moderation_id}")],
        [
            InlineKeyboardButton("❌ Reject", callback_data=f"reject|{moderation_id}"),
            InlineKeyboardButton("🚫 Block game", callback_data=f"block|{moderation_id}"),
        ],
    ])


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


async def cleanup_moderation_chat(context: ContextTypes.DEFAULT_TYPE, moderation_id: int) -> None:
    messages = get_moderation_messages(moderation_id)

    for msg in messages:
        kind = msg["kind"]

        if kind in STATUS_KINDS:
            continue

        try:
            await context.bot.delete_message(
                chat_id=msg["chat_id"],
                message_id=msg["message_id"],
            )
        except Exception as error:
            print(
                f"[CLEANUP WARN] moderation_id={moderation_id} "
                f"message_id={msg['message_id']} kind={kind}: {error}"
            )

    delete_moderation_messages_records(moderation_id)


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


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return

    await query.answer()

    raw_data = query.data or ""
    parts = raw_data.split("|")

    if len(parts) < 2:
        await _safe_edit_status(query, f"⚠️ Bad callback data: {raw_data}")
        return

    action = parts[0]

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
        image_path = get_generated_image_path(moderation_id, f"variant_{variant_key}")

        if not os.path.exists(image_path):
            await _safe_edit_status(query, "⚠️ Variant image not found")
            return

        set_selected_image(moderation_id, variant_key, image_path)
        await cleanup_moderation_chat(context, moderation_id)

        updated_item = get_moderation_item(moderation_id)
        if updated_item:
            await send_final_preview(context, updated_item, chat_id)

    elif action == "upload_custom":
        update_moderation_state(moderation_id, "waiting_custom_image")

        prompt = await context.bot.send_message(
            chat_id=chat_id,
            text="Надішли своє фото reply-ом НА ЦЕ повідомлення."
        )

        set_upload_request_message_id(moderation_id, prompt.message_id)
        register_moderation_message(moderation_id, chat_id, prompt.message_id, "upload_prompt")

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

    else:
        await _safe_edit_status(query, f"⚠️ Unknown action: {action}")


async def upload_image_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message

    print("[UPLOAD DEBUG] handler triggered")
    print("[UPLOAD DEBUG] effective_message exists:", bool(message))
    print("[UPLOAD DEBUG] message_id:", message.message_id if message else None)
    print("[UPLOAD DEBUG] has_photo:", bool(message.photo) if message else None)
    print("[UPLOAD DEBUG] has_document:", bool(message.document) if message else None)
    print(
        "[UPLOAD DEBUG] reply_to_message_id:",
        message.reply_to_message.message_id if message and message.reply_to_message else None,
    )

    if not message:
        print("[UPLOAD DEBUG] skipped: no effective_message")
        return

    if not message.reply_to_message:
        print("[UPLOAD DEBUG] skipped: no reply")
        return

    reply_message_id = message.reply_to_message.message_id
    item = get_moderation_item_by_upload_request_message_id(reply_message_id)

    print("[UPLOAD DEBUG] matched moderation item:", item["id"] if item else None)

    if not item:
        print("[UPLOAD DEBUG] skipped: no matching moderation item")
        return

    current_state = (item.get("state") or "").strip()
    current_status = (item.get("status") or "").strip()
    if current_state in TERMINAL_STATES or current_status in TERMINAL_STATES:
        print("[UPLOAD DEBUG] skipped: moderation already finished")
        return

    photo_file = None
    ext = "jpg"

    if message.photo:
        photo = message.photo[-1]
        photo_file = await photo.get_file()
        ext = "jpg"
        print("[UPLOAD DEBUG] using telegram photo")

    elif message.document:
        mime_type = message.document.mime_type or ""
        print("[UPLOAD DEBUG] document mime:", mime_type)

        if mime_type.startswith("image/"):
            photo_file = await message.document.get_file()
            filename = message.document.file_name or ""
            if "." in filename:
                ext = filename.rsplit(".", 1)[-1].lower()
            print("[UPLOAD DEBUG] using image document")
        else:
            await message.reply_text("⚠️ Надішли саме фото або image-файл.")
            print("[UPLOAD DEBUG] skipped: document is not image")
            return
    else:
        await message.reply_text("⚠️ Надішли саме фото або image-файл.")
        print("[UPLOAD DEBUG] skipped: no photo/document")
        return

    register_moderation_message(item["id"], message.chat_id, message.message_id, "user_upload")

    custom_upload_path = get_custom_upload_path(item["id"], ext)
    print("[UPLOAD DEBUG] saving upload to:", custom_upload_path)

    await photo_file.download_to_drive(custom_upload_path)

    try:
        final_custom_path = generate_post_image(
            appid=item["appid"],
            final_price=item["final_price"],
            initial_price=item["initial_price"],
            currency=item["currency"],
            custom_background_path=custom_upload_path,
            output_path=get_generated_image_path(item["id"], "custom"),
        )
        print("[UPLOAD DEBUG] generated custom preview:", final_custom_path)
    except Exception as error:
        print("[UPLOAD DEBUG] generation error:", error)
        await message.reply_text(f"⚠️ Не вдалося обробити кастомне фото: {error}")
        return

    set_selected_image(
        item["id"],
        "custom",
        final_custom_path,
        custom_image_path=custom_upload_path,
    )
    clear_upload_request_message_id(item["id"])

    await cleanup_moderation_chat(context, item["id"])

    updated_item = get_moderation_item(item["id"])
    if updated_item:
        await send_final_preview(context, updated_item, message.chat_id)


def run_bot() -> None:
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(
        MessageHandler(
            (filters.PHOTO | filters.Document.IMAGE),
            upload_image_handler,
        )
    )

    print("Moderator bot running...")
    app.run_polling()


if __name__ == "__main__":
    run_bot()