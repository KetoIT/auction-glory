"""Безопасная замена текста в сообщении callback (в т.ч. после фото / без текста)."""

from __future__ import annotations

import logging

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

logger = logging.getLogger(__name__)


def message_has_media(msg: Message) -> bool:
    return bool(msg.photo or msg.video or msg.document or msg.animation)


async def safe_edit_to_text(
    cq: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    *,
    parse_mode: str | None = ParseMode.HTML,
) -> None:
    msg = cq.message
    if not msg:
        return
    chat_id = msg.chat.id
    if message_has_media(msg):
        try:
            await msg.delete()
        except TelegramBadRequest:
            logger.warning("Could not delete message when switching to text panel")
        await cq.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
        return
    try:
        await msg.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except TelegramBadRequest as e:
        desc = (e.message or "").lower()
        if "message is not modified" in desc:
            return
        if (
            "there is no text in the message" in desc
            or "message to edit" in desc
            or "message can't be edited" in desc
        ):
            try:
                await msg.delete()
            except TelegramBadRequest:
                pass
            await cq.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
            return
        raise
