import asyncio
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

from config import BOT_TOKEN, MOD_CHAT_ID
from database import (
    create_moderation_item,
    register_moderation_message,
    set_control_message_id,
)
from image_generator import (
    VARIANT_SOURCE_MAP,
    generate_post_image,
    get_generated_image_path,
)


async def _send_to_moderation_async(deal: dict) -> None:
    moderation_id = create_moderation_item(deal)

    bot = Bot(token=BOT_TOKEN)

    generated_variants: list[str] = []

    for variant_key, source_type in VARIANT_SOURCE_MAP.items():

        image_path = None

        try:
            image_path = generate_post_image(
                appid=deal["appid"],
                final_price=deal["final_price"],
                initial_price=deal["initial_price"],
                currency=deal["currency"],
                header_image_url=deal.get("header_image", ""),
                sale_end_text=deal.get("sale_end_text", ""),
                source_type=source_type,
                output_path=get_generated_image_path(
                    moderation_id,
                    f"variant_{variant_key}",
                ),
            )

        except Exception as error:
            print(
                f"[IMG VARIANT ERROR] moderation_id={moderation_id} "
                f"variant={variant_key}: {error}"
            )

        if not image_path:
            continue

        generated_variants.append(variant_key)

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ Обрати цей варіант",
                    callback_data=f"choose_variant|{moderation_id}|{variant_key}",
                )
            ]
        ])

        with open(image_path, "rb") as image_file:
            sent = await bot.send_photo(
                chat_id=MOD_CHAT_ID,
                photo=image_file,
                caption=f"{deal['title']} · варіант {variant_key}",
                reply_markup=keyboard,
            )

        register_moderation_message(
            moderation_id,
            MOD_CHAT_ID,
            sent.message_id,
            f"variant_{variant_key}",
        )

    control_text = (
        f"{deal['title']}\n"
        f"Обери варіант зображення"
    )

    if generated_variants:
        control_text += f" ({', '.join(generated_variants)})"

    control_text += "\nабо завантаж своє фото."

    control_keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📤 Своє фото",
                callback_data=f"upload_custom|{moderation_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "❌ Reject",
                callback_data=f"reject|{moderation_id}",
            ),
            InlineKeyboardButton(
                "🚫 Block game",
                callback_data=f"block|{moderation_id}",
            ),
        ],
    ])

    control_message = await bot.send_message(
        chat_id=MOD_CHAT_ID,
        text=control_text,
        reply_markup=control_keyboard,
    )

    set_control_message_id(moderation_id, control_message.message_id)

    register_moderation_message(
        moderation_id,
        MOD_CHAT_ID,
        control_message.message_id,
        "control",
    )


def send_to_moderation(deal: dict) -> None:
    asyncio.run(_send_to_moderation_async(deal))