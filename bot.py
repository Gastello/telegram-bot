import asyncio
from io import BytesIO

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import RetryAfter, TimedOut

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

# Підняті таймаути, щоб не ловити фальшиві TimedOut
TG_CONNECT_TIMEOUT = 20
TG_READ_TIMEOUT = 60
TG_WRITE_TIMEOUT = 60
TG_POOL_TIMEOUT = 20


async def safe_send_photo(
    bot: Bot,
    *,
    chat_id: int,
    photo_bytes: bytes,
    filename: str,
    caption: str,
    reply_markup=None,
):
    for attempt in range(4):
        try:
            bio = BytesIO(photo_bytes)
            bio.name = filename

            return await bot.send_photo(
                chat_id=chat_id,
                photo=bio,
                caption=caption,
                reply_markup=reply_markup,
                connect_timeout=TG_CONNECT_TIMEOUT,
                read_timeout=TG_READ_TIMEOUT,
                write_timeout=TG_WRITE_TIMEOUT,
                pool_timeout=TG_POOL_TIMEOUT,
            )

        except RetryAfter as error:
            wait_time = int(getattr(error, "retry_after", 5)) + 1
            print(f"[TG RETRY] send_photo retry after {wait_time}s")
            await asyncio.sleep(wait_time)

        except TimedOut:
            wait_time = 5 * (attempt + 1)
            print(f"[TG RETRY] send_photo timed out, retry in {wait_time}s")
            await asyncio.sleep(wait_time)

    raise RuntimeError("Failed to send photo to Telegram after retries")


async def safe_send_message(bot: Bot, **kwargs):
    for attempt in range(4):
        try:
            return await bot.send_message(
                **kwargs,
                connect_timeout=TG_CONNECT_TIMEOUT,
                read_timeout=TG_READ_TIMEOUT,
                write_timeout=TG_WRITE_TIMEOUT,
                pool_timeout=TG_POOL_TIMEOUT,
            )
        except RetryAfter as error:
            wait_time = int(getattr(error, "retry_after", 5)) + 1
            print(f"[TG RETRY] send_message retry after {wait_time}s")
            await asyncio.sleep(wait_time)
        except TimedOut:
            wait_time = 5 * (attempt + 1)
            print(f"[TG RETRY] send_message timed out, retry in {wait_time}s")
            await asyncio.sleep(wait_time)

    raise RuntimeError("Failed to send message to Telegram after retries")


def build_variant_keyboard(
    moderation_id: int,
    variant_key: str,
    single_variant: bool,
) -> InlineKeyboardMarkup:
    if single_variant:
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ Обрати цей варіант",
                    callback_data=f"choose_variant|{moderation_id}|{variant_key}",
                )
            ],
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

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Обрати цей варіант",
                callback_data=f"choose_variant|{moderation_id}|{variant_key}",
            )
        ]
    ])


async def _send_to_moderation_async(deal: dict) -> None:
    moderation_id = create_moderation_item(deal)
    bot = Bot(token=BOT_TOKEN)

    generated_variants: list[dict] = []

    for variant_key, source_type in VARIANT_SOURCE_MAP.items():
        image_path = None

        try:
            image_path = generate_post_image(
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

        generated_variants.append({
            "variant_key": variant_key,
            "image_path": image_path,
        })

    if not generated_variants:
        raise RuntimeError(
            f"No image variants generated for appid={deal['appid']} title={deal['title']}"
        )

    single_variant = len(generated_variants) == 1

    for item in generated_variants:
        variant_key = item["variant_key"]
        image_path = item["image_path"]

        keyboard = build_variant_keyboard(
            moderation_id=moderation_id,
            variant_key=variant_key,
            single_variant=single_variant,
        )

        with open(image_path, "rb") as image_file:
            photo_bytes = image_file.read()

        sent = await safe_send_photo(
            bot,
            chat_id=MOD_CHAT_ID,
            photo_bytes=photo_bytes,
            filename=f"{deal['appid']}_{variant_key}.png",
            caption=f"{deal['title']} · варіант {variant_key}",
            reply_markup=keyboard,
        )

        register_moderation_message(
            moderation_id,
            MOD_CHAT_ID,
            sent.message_id,
            f"variant_{variant_key}",
        )

        await asyncio.sleep(1.0)

    if not single_variant:
        control_text = (
            f"{deal['title']}\n"
            f"Обери варіант зображення"
        )

        if generated_variants:
            control_text += f" ({', '.join(v['variant_key'] for v in generated_variants)})"

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

        control_message = await safe_send_message(
            bot,
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