from __future__ import annotations

import html
import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
    ReplyKeyboardRemove,
)
from sqlalchemy.ext.asyncio import AsyncSession

from bot.keyboards import (
    admin_main_kb,
    auction_card_kb,
    bid_presets_kb,
    gallery_back_kb,
    main_nav_kb,
    order_kb,
    rules_back_kb,
)
from bot.formatting import LINE
from bot.message_tools import safe_edit_to_text
from bot.states import CustomBidStates
from config import get_settings
from db.models import Auction, AuctionStatus
from domain.auctions import (
    as_utc,
    ensure_user,
    get_auction,
    list_gallery_auctions,
    list_recent_bids,
    list_scheduled_and_live,
    minimum_next_bid,
    place_bid,
    sync_auction_statuses,
)

logger = logging.getLogger(__name__)
router = Router(name="user")
_settings = get_settings()


def _is_admin(uid: int | None) -> bool:
    if uid is None:
        return False
    return uid in _settings.admin_id_set


def _tz() -> ZoneInfo:
    return ZoneInfo(_settings.timezone)


def _fmt_dt(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    return as_utc(dt).astimezone(_tz()).strftime("%d.%m.%Y %H:%M")


def _status_ru(a: Auction) -> str:
    if a.status == AuctionStatus.scheduled:
        return "скоро старт"
    if a.status == AuctionStatus.live:
        return "идёт приём ставок"
    if a.status == AuctionStatus.ended:
        return "завершён"
    return "отменён"


def _status_short(a: Auction) -> str:
    """Короткая метка для кнопок (лимит длины Telegram)."""
    if a.status == AuctionStatus.scheduled:
        return "скоро"
    if a.status == AuctionStatus.live:
        return "идёт"
    if a.status == AuctionStatus.ended:
        return "итог"
    return "—"


def _auction_caption(a: Auction) -> str:
    title = html.escape(a.title)
    lines = [
        f"<b>{title}</b>",
        LINE,
        f"Старт <b>{a.start_price} ₽</b>   ·   шаг <b>{a.step_amount} ₽</b>",
        f"Текущая <b>{a.current_price} ₽</b>   ·   минимум следующей <b>{minimum_next_bid(a)} ₽</b>",
        f"Начало {_fmt_dt(a.starts_at)}   ·   конец {_fmt_dt(a.ends_at)}",
        f"Статус: {_status_ru(a)}",
    ]
    if a.description:
        lines.append(LINE)
        lines.append(f"<i>{html.escape(a.description)}</i>")
    lines.append(LINE)
    lines.append(
        "<i>Кнопки +100 / +500 / +1000 увеличивают текущую цену на эту сумму. "
        "Своя сумма — ввод числом сообщением.</i>"
    )
    return "\n".join(lines)


def _mask_participant(tg_id: int) -> str:
    s = str(tg_id)
    return f"***{s[-4:]}" if len(s) >= 4 else "****"


def _artist_url() -> str:
    url = _settings.artist_contact_url.strip()
    if not url.startswith("http"):
        url = f"https://t.me/{url.lstrip('@')}"
    return url


def _home_text() -> str:
    return (
        "<b>Pure Glory - аукцион картин</b>\n"
        f"{LINE}\n"
        "Выберите раздел — <i>это сообщение обновляется кнопками</i>.\n\n"
        "📜 Правила — кнопка ниже или команда <code>/rules</code>."
    )


@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession) -> None:
    if message.from_user:
        await ensure_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name,
        )
    sid = _settings.welcome_sticker_id
    if sid:
        try:
            await message.answer_sticker(sid)
        except TelegramBadRequest:
            logger.warning("Не удалось отправить приветственный стикер (file_id устарел?)")
    await message.answer(
        _home_text()
        + "\n\n<i>💡 Если снизу видна старая клавиатура — нажмите значок ⌨️ у поля ввода, чтобы скрыть.</i>",
        reply_markup=main_nav_kb(_is_admin(message.from_user.id if message.from_user else None)),
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    if not message.from_user:
        return
    if not _settings.admin_id_set:
        await message.answer(
            "⚠️ Администраторы не настроены (<code>ADMIN_IDS</code> в .env).",
            parse_mode=ParseMode.HTML,
        )
        return
    if message.from_user.id not in _settings.admin_id_set:
        await message.answer("🔒 Команда только для администратора.")
        return
    await message.answer(
        "<b>🛠 Панель администратора</b>\n"
        f"{LINE}\n"
        "<i>Активные лоты, статистика, продление окончания, рассылка. "
        "Кнопка «В главное меню» возвращает к обычному меню бота.</i>",
        reply_markup=admin_main_kb(),
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "↩️ Ввод отменён. Можно снова пользоваться меню.",
        reply_markup=main_nav_kb(_is_admin(message.from_user.id if message.from_user else None)),
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("menu"))
async def cmd_menu(message: Message, session: AsyncSession) -> None:
    if message.from_user:
        await ensure_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name,
        )
    await message.answer(
        _home_text(),
        reply_markup=main_nav_kb(_is_admin(message.from_user.id if message.from_user else None)),
        parse_mode=ParseMode.HTML,
    )


@router.message(
    F.text.in_(
        {
            "🖼 Актуальные картины",
            "🎨 Галерея",
            "✉️ Под заказ",
            "Актуальные картины",
            "Галерея",
            "Под заказ",
        }
    )
)
async def legacy_reply_keyboard(message: Message, session: AsyncSession) -> None:
    if message.from_user:
        await ensure_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name,
        )
    await message.answer(
        "✨ Лучше пользоваться <b>инлайн-кнопками</b> под сообщением — так одно окно без лишнего шума.",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode=ParseMode.HTML,
    )
    t = (message.text or "").replace("🖼 ", "").replace("🎨 ", "").replace("✉️ ", "")
    if message.text in ("🖼 Актуальные картины", "Актуальные картины") or t == "Актуальные картины":
        await sync_auction_statuses(session)
        rows = await list_scheduled_and_live(session)
        if not rows:
            await message.answer(
                "😔 Сейчас нет актуальных лотов. Загляните позже или в галерею.",
                reply_markup=main_nav_kb(_is_admin(message.from_user.id if message.from_user else None)),
                parse_mode=ParseMode.HTML,
            )
            return
        await message.answer(
            "<b>🖼 Актуальные картины</b>\n<i>Выберите лот:</i>",
            reply_markup=_live_auctions_keyboard(rows),
            parse_mode=ParseMode.HTML,
        )
    elif message.text in ("🎨 Галерея", "Галерея") or t == "Галерея":
        await sync_auction_statuses(session)
        items = await list_gallery_auctions(session)
        if not items:
            await message.answer(
                "😔 В галерее пока пусто — загляните позже.",
                reply_markup=main_nav_kb(_is_admin(message.from_user.id if message.from_user else None)),
                parse_mode=ParseMode.HTML,
            )
            return
        await message.answer(
            "<b>🎨 Галерея</b>\n<i>Завершённые работы:</i>",
            reply_markup=_gallery_keyboard(items),
            parse_mode=ParseMode.HTML,
        )
    else:
        await message.answer(
            "<b>✉️ Под заказ</b>\n"
            f"{LINE}\n"
            "Опишите идею, сроки и бюджет — <b>напишите художнику</b> кнопкой ниже.",
            reply_markup=order_kb(_artist_url()),
            parse_mode=ParseMode.HTML,
        )


def _live_auctions_keyboard(auctions: list[Auction]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for a in auctions:
        short = (a.title[:34] + "…") if len(a.title) > 34 else a.title
        if len(short) > 58:
            short = short[:57] + "…"
        label = f"{short} · {_status_short(a)}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"a:{a.id}:v")])
    rows.append([InlineKeyboardButton(text="🏠 В главное меню", callback_data="nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _gallery_keyboard(auctions: list[Auction]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for a in auctions:
        short = (a.title[:40] + "…") if len(a.title) > 40 else a.title
        label = f"{short} · {a.current_price} ₽"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"g:{a.id}")])
    rows.append([InlineKeyboardButton(text="🏠 В главное меню", callback_data="nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _empty_live_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🏠 В главное меню", callback_data="nav:home")]]
    )


def _empty_gallery_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🏠 В главное меню", callback_data="nav:home")]]
    )


@router.callback_query(F.data == "nav:home")
async def cb_nav_home(cq: CallbackQuery) -> None:
    await cq.answer()
    uid = cq.from_user.id if cq.from_user else None
    await safe_edit_to_text(cq, _home_text(), main_nav_kb(_is_admin(uid)))


@router.callback_query(F.data == "nav:admin")
async def cb_nav_admin(cq: CallbackQuery) -> None:
    if not cq.from_user or cq.from_user.id not in _settings.admin_id_set:
        await cq.answer("Доступ только для администратора", show_alert=True)
        return
    await cq.answer()
    await safe_edit_to_text(
        cq,
        "<b>🛠 Панель администратора</b>\n"
        f"{LINE}\n"
        "<i>Активные лоты, статистика, продление, рассылка. "
        "«В главное меню» — к пользовательскому меню.</i>",
        admin_main_kb(),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == "nav:live")
async def cb_nav_live(cq: CallbackQuery, session: AsyncSession) -> None:
    await cq.answer()
    await sync_auction_statuses(session)
    rows = await list_scheduled_and_live(session)
    if not rows:
        await safe_edit_to_text(
            cq,
            "<b>🖼 Актуальные картины</b>\n"
            f"{LINE}\n"
            "😔 Сейчас лотов нет. Загляните позже или в <b>галерею</b>.",
            _empty_live_kb(),
        )
        return
    await safe_edit_to_text(
        cq,
        "<b>🖼 Актуальные картины</b>\n"
        f"{LINE}\n"
        "<i>Нажмите на лот, чтобы открыть карточку</i>",
        _live_auctions_keyboard(rows),
    )


@router.callback_query(F.data == "nav:gallery")
async def cb_nav_gallery(cq: CallbackQuery, session: AsyncSession) -> None:
    await cq.answer()
    await sync_auction_statuses(session)
    items = await list_gallery_auctions(session)
    if not items:
        await safe_edit_to_text(
            cq,
            "<b>🎨 Галерея</b>\n"
            f"{LINE}\n"
            "😔 Пока нет завершённых лотов.",
            _empty_gallery_kb(),
        )
        return
    await safe_edit_to_text(
        cq,
        "<b>🎨 Галерея</b>\n"
        f"{LINE}\n"
        "<i>Завершённые аукционы — выберите работу</i>",
        _gallery_keyboard(items),
    )


@router.callback_query(F.data == "nav:order")
async def cb_nav_order(cq: CallbackQuery) -> None:
    await cq.answer()
    await safe_edit_to_text(
        cq,
        "<b>✉️ Под заказ</b>\n"
        f"{LINE}\n"
        "Опишите идею, сроки и бюджет — откройте чат с художником кнопкой ниже.",
        order_kb(_artist_url()),
    )


@router.callback_query(F.data == "nav:rules")
async def cb_nav_rules(cq: CallbackQuery) -> None:
    await cq.answer()
    await safe_edit_to_text(
        cq,
        _settings.rules_html(),
        rules_back_kb(),
    )


@router.message(Command("rules"))
async def cmd_rules(message: Message) -> None:
    await message.answer(
        _settings.rules_html(),
        reply_markup=main_nav_kb(_is_admin(message.from_user.id if message.from_user else None)),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data.in_({"lst:menu", "lst:live", "lst:gallery"}))
async def cb_legacy_list_callbacks(cq: CallbackQuery, session: AsyncSession) -> None:
    """Старые callback_data с прошлых клавиатур."""
    if cq.data == "lst:menu":
        await cb_nav_home(cq)
        return
    if cq.data == "lst:live":
        await cb_nav_live(cq, session)
        return
    await cb_nav_gallery(cq, session)


@router.callback_query(F.data.startswith("a:"))
async def cb_auction_card(cq: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    parts = (cq.data or "").split(":")
    if len(parts) != 3 or not parts[1].isdigit() or parts[2] not in {"v", "b", "p"}:
        await cq.answer()
        return
    aid = int(parts[1])
    action = parts[2]
    await sync_auction_statuses(session)
    a = await get_auction(session, aid)
    if a is None:
        await cq.answer("Лот не найден", show_alert=True)
        return
    if action == "b":
        await cq.answer()
        await _show_recent_bids(cq, session, a)
        return
    if action == "p":
        if a.status != AuctionStatus.live:
            await cq.answer("Ставки сейчас недоступны", show_alert=True)
            return
        n = datetime.now(UTC)
        if n < as_utc(a.starts_at) or n > as_utc(a.ends_at):
            await cq.answer("Вне окна приёма ставок", show_alert=True)
            return
        cap = (
            _auction_caption(a)
            + f"\n{LINE}\n"
            + "<b>Ставка</b>\n<i>Шаг или своя сумма</i>"
        )
        kb = bid_presets_kb(a.id, a.step_amount)
        await cq.answer()
        if cq.message.photo:
            await cq.message.edit_caption(caption=cap, reply_markup=kb, parse_mode=ParseMode.HTML)
        else:
            chat_id = cq.message.chat.id
            try:
                await cq.message.delete()
            except TelegramBadRequest:
                pass
            await cq.bot.send_photo(
                chat_id,
                photo=a.photo_file_id,
                caption=cap,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
        return
    await cq.answer()
    await _render_auction_card(cq, a)


@router.callback_query(F.data.startswith("g:"))
async def cb_gallery_item(cq: CallbackQuery, session: AsyncSession) -> None:
    await cq.answer()
    parts = (cq.data or "").split(":")
    if len(parts) != 2 or not parts[1].isdigit():
        return
    aid = int(parts[1])
    await sync_auction_statuses(session)
    a = await get_auction(session, aid)
    if a is None or a.status != AuctionStatus.ended:
        await cq.answer("Картина недоступна", show_alert=True)
        return
    cap = (
        f"<b>{html.escape(a.title)}</b>\n"
        f"{LINE}\n"
        f"Итог <b>{a.current_price} ₽</b>\n"
        f"Завершён {_fmt_dt(a.ends_at)}"
    )
    if a.description:
        cap += f"\n\n<i>{html.escape(a.description)}</i>"
    media = InputMediaPhoto(media=a.photo_file_id, caption=cap, parse_mode=ParseMode.HTML)
    chat_id = cq.message.chat.id
    try:
        await cq.message.edit_media(media=media, reply_markup=gallery_back_kb())
    except Exception:
        logger.exception("edit_media failed, sending new message")
        try:
            await cq.message.delete()
        except TelegramBadRequest:
            pass
        await cq.bot.send_photo(
            chat_id,
            photo=a.photo_file_id,
            caption=cap,
            parse_mode=ParseMode.HTML,
            reply_markup=gallery_back_kb(),
        )


async def _render_auction_card(cq: CallbackQuery, a: Auction) -> None:
    chat_id = cq.message.chat.id
    media = InputMediaPhoto(media=a.photo_file_id, caption=_auction_caption(a), parse_mode=ParseMode.HTML)
    if cq.message.photo:
        try:
            await cq.message.edit_media(media=media, reply_markup=auction_card_kb(a.id))
            return
        except Exception:
            logger.exception("edit_media failed")
    try:
        await cq.message.delete()
    except Exception:
        pass
    await cq.bot.send_photo(
        chat_id,
        photo=a.photo_file_id,
        caption=_auction_caption(a),
        parse_mode=ParseMode.HTML,
        reply_markup=auction_card_kb(a.id),
    )


async def _show_recent_bids(cq: CallbackQuery, session: AsyncSession, a: Auction) -> None:
    chat_id = cq.message.chat.id
    bids = await list_recent_bids(session, a.id, _settings.recent_bids_limit)
    if not bids:
        text = "<i>Пока нет ставок.</i>"
    else:
        lines = []
        for b in bids:
            tid = b.user.telegram_id if b.user else 0
            lines.append(f"· {_mask_participant(tid)} — <b>{b.amount} ₽</b>")
        text = "<b>Последние ставки</b>\n" + "\n".join(lines)
    full = _auction_caption(a) + f"\n{LINE}\n" + text
    if cq.message.photo:
        try:
            await cq.message.edit_caption(
                caption=full,
                reply_markup=auction_card_kb(a.id),
                parse_mode=ParseMode.HTML,
            )
            return
        except Exception:
            logger.exception("edit_caption failed")
    try:
        await cq.message.delete()
    except TelegramBadRequest:
        pass
    await cq.bot.send_message(
        chat_id,
        full,
        parse_mode=ParseMode.HTML,
        reply_markup=auction_card_kb(a.id),
    )


@router.callback_query(F.data.startswith("b:"))
async def cb_bid_actions(
    cq: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    parts = (cq.data or "").split(":")
    if len(parts) != 3 or not parts[1].isdigit():
        await cq.answer()
        return
    aid = int(parts[1])
    kind = parts[2]

    if kind == "x":
        await cq.answer()
        await state.set_state(CustomBidStates.entering_amount)
        await state.update_data(auction_id=aid)
        await cq.message.answer(
            "<b>Своя сумма</b>\n"
            f"{LINE}\n"
            "Введите целое число в рублях, например <code>1500</code>.",
            parse_mode=ParseMode.HTML,
        )
        return

    if not kind.isdigit():
        await cq.answer()
        return

    delta = int(kind)
    if not cq.from_user:
        await cq.answer()
        return
    user = await ensure_user(
        session,
        telegram_id=cq.from_user.id,
        username=cq.from_user.username,
        full_name=cq.from_user.full_name,
    )
    a = await get_auction(session, aid)
    if a is None:
        await cq.answer("Лот не найден", show_alert=True)
        return
    amount = a.current_price + delta
    try:
        await place_bid(session, auction_id=aid, user=user, amount=amount)
    except ValueError as e:
        await cq.answer(str(e), show_alert=True)
        return
    await cq.answer(f"Принято: {amount} ₽", show_alert=False)
    a = await get_auction(session, aid)
    if a and cq.message.photo:
        try:
            await cq.message.edit_caption(
                caption=_auction_caption(a)
                + f"\n{LINE}\n"
                + "<b>Ставка</b>\n<i>Шаг или своя сумма</i>",
                reply_markup=bid_presets_kb(a.id, a.step_amount),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            logger.exception("edit_caption after bid failed")


@router.message(CustomBidStates.entering_amount, F.text)
async def msg_custom_bid_amount(message: Message, session: AsyncSession, state: FSMContext) -> None:
    data = await state.get_data()
    aid = int(data.get("auction_id", 0))
    if not message.from_user or not aid:
        await state.clear()
        return
    raw = (message.text or "").strip().replace(" ", "")
    try:
        amount = int(raw)
    except ValueError:
        await message.answer(
            "Нужно <b>целое число</b>. Повторите или /menu.",
            parse_mode=ParseMode.HTML,
        )
        return
    user = await ensure_user(
        session,
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
    )
    try:
        await place_bid(session, auction_id=aid, user=user, amount=amount)
    except ValueError as e:
        await message.answer(str(e))
        return
    await state.clear()
    await message.answer(
        f"Ставка <b>{amount} ₽</b> принята.\n"
        f"{LINE}\n"
        "<i>Меню — кнопками ниже или /menu.</i>",
        reply_markup=main_nav_kb(_is_admin(message.from_user.id if message.from_user else None)),
        parse_mode=ParseMode.HTML,
    )

