from __future__ import annotations

import asyncio
import html
import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.keyboards import (
    admin_auctions_manage_kb,
    admin_broadcast_confirm_kb,
    admin_confirm_cancel_kb,
    admin_confirm_end_kb,
    admin_confirm_kb,
    admin_edit_field_kb,
    admin_edit_list_kb,
    admin_extend_time_kb,
    admin_main_kb,
    admin_stat_back_kb,
)
from bot.formatting import LINE
from bot.message_tools import safe_edit_to_text
from bot.scheduler import notify_auction_ended
from bot.states import BroadcastStates, EditAuctionStates, NewAuctionStates
from config import get_settings
from db.models import Auction, AuctionStatus
from domain.auctions import (
    admin_cancel_auction,
    admin_extend_auction_end,
    admin_force_end_auction,
    as_utc,
    count_bids_for_auctions,
    get_auction,
    list_all_user_telegram_ids,
    list_auctions_for_admin_edit,
    sync_auction_statuses,
    update_auction_fields,
)

logger = logging.getLogger(__name__)
_settings = get_settings()


class IsAdmin(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        uid = event.from_user.id if event.from_user else None
        return uid is not None and uid in _settings.admin_id_set


router = Router(name="admin")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


def _tz() -> ZoneInfo:
    return ZoneInfo(_settings.timezone)


def _fmt(dt: datetime) -> str:
    return as_utc(dt).astimezone(_tz()).strftime("%d.%m.%Y %H:%M")


def _fmt_timedelta_left(ends_at: datetime, now: datetime) -> str:
    end = as_utc(ends_at)
    if end <= now:
        return "уже пора завершить"
    delta = end - now
    total_sec = int(delta.total_seconds())
    h, rem = divmod(total_sec, 3600)
    m, _ = divmod(rem, 60)
    if h >= 48:
        d = h // 24
        return f"~{d} д."
    if h >= 1:
        return f"~{h} ч {m} мин"
    return f"~{m} мин"


def _leader_html(a: Auction) -> str:
    if a.leading_user_id is None:
        return "👤 Лидер: <i>ставок пока нет</i>"
    u = a.leading_user
    if u is None:
        return "👤 Лидер: <i>нет данных</i>"
    bits: list[str] = [f"<code>{u.telegram_id}</code>"]
    if u.username:
        bits.append(f"@{html.escape(u.username)}")
    if u.full_name:
        bits.append(html.escape(u.full_name))
    return "👤 Лидер: " + " · ".join(bits)


def _status_ru(a: Auction) -> str:
    if a.status == AuctionStatus.scheduled:
        return "ожидает старта"
    if a.status == AuctionStatus.live:
        return "идёт"
    return a.status.value


def _parse_dt(text: str) -> datetime:
    s = text.strip()
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%y %H:%M"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=_tz())
        except ValueError:
            continue
    raise ValueError("Формат: ДД.ММ.ГГГГ ЧЧ:ММ (местное время)")


def _parse_adm_ef(data: str) -> tuple[int, str] | None:
    if not data.startswith("adm:ef:"):
        return None
    rest = data[7:]
    idx = rest.rfind(":")
    if idx <= 0:
        return None
    try:
        aid = int(rest[:idx])
    except ValueError:
        return None
    field = rest[idx + 1 :]
    return (aid, field) if field else None


def _summary(data: dict) -> str:
    sa = data.get("starts_at")
    ea = data.get("ends_at")
    sa_s = _fmt(sa) if isinstance(sa, datetime) else str(sa)
    ea_s = _fmt(ea) if isinstance(ea, datetime) else str(ea)
    title = html.escape(str(data.get("title") or ""))
    desc = html.escape(str(data.get("description") or "")) or "—"
    return (
        "<b>📋 Проверьте лот</b>\n"
        f"{LINE}\n"
        f"📝 Название: {title}\n"
        f"📄 Описание: {desc}\n"
        f"💵 Старт: {data.get('start_price')} ₽  ·  Шаг: {data.get('step_amount')} ₽\n"
        f"🕐 Старт: {sa_s}\n"
        f"🕠 Конец: {ea_s}\n"
    )


@router.callback_query(F.data == "adm:new")
async def cb_new_start(cq: CallbackQuery, state: FSMContext) -> None:
    await cq.answer()
    await state.set_state(NewAuctionStates.photo)
    await cq.message.answer(
        "<b>🖼 Шаг 1/7</b>\n"
        f"{LINE}\n"
        "Пришлите <b>фото</b> картины (как сжатое фото, не файлом документа).",
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == "adm:cancel")
async def cb_cancel(cq: CallbackQuery, state: FSMContext) -> None:
    await cq.answer("Отменено")
    await state.clear()
    await cq.message.answer(
        "↩️ Создание лота отменено. Снова: <code>/admin</code>",
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == "adm:list")
async def cb_list_active(cq: CallbackQuery, session: AsyncSession) -> None:
    await cq.answer()
    await sync_auction_statuses(session)
    res = await session.execute(
        select(Auction).where(
            Auction.status.in_((AuctionStatus.scheduled, AuctionStatus.live))
        )
    )
    rows = list(res.scalars().all())
    if not rows:
        text = "📭 Нет активных или запланированных лотов."
        kb = admin_main_kb()
    else:
        now = datetime.now(UTC)
        counts = await count_bids_for_auctions(session, [a.id for a in rows])
        lines = []
        for a in rows:
            n = counts.get(a.id, 0)
            left = _fmt_timedelta_left(a.ends_at, now)
            lines.append(
                f"▫️ <b>#{a.id}</b> · {html.escape(a.title)}\n"
                f"   {_status_ru(a)} · до {_fmt(a.ends_at)} "
                f"(осталось {left})\n"
                f"   💰 {a.current_price} ₽  ·  ставок: <b>{n}</b>"
            )
        text = (
            "<b>📋 Активные лоты</b>\n"
            f"{LINE}\n"
            + "\n".join(lines)
            + "\n\n<i>Завершить, отменить, статистика или продление — кнопками ниже</i>"
        )
        kb = admin_auctions_manage_kb(rows)
    await safe_edit_to_text(cq, text, kb, parse_mode=ParseMode.HTML)


@router.callback_query(F.data.startswith("adm:stat:"))
async def cb_auction_stat(cq: CallbackQuery, session: AsyncSession) -> None:
    await cq.answer()
    try:
        aid = int((cq.data or "").split(":")[2])
    except (IndexError, ValueError):
        await cq.answer("Нет такого лота", show_alert=True)
        return
    await sync_auction_statuses(session)
    a = await get_auction(session, aid)
    if a is None or a.status not in (AuctionStatus.scheduled, AuctionStatus.live):
        await cq.answer("Лот недоступен", show_alert=True)
        return
    counts = await count_bids_for_auctions(session, [aid])
    n = counts.get(aid, 0)
    now = datetime.now(UTC)
    left = _fmt_timedelta_left(a.ends_at, now)
    text = (
        f"<b>📊 Лот #{aid}</b>\n«{html.escape(a.title)}»\n"
        f"{LINE}\n"
        f"Статус: <b>{_status_ru(a)}</b>\n"
        f"Старт: {_fmt(a.starts_at)}  ·  конец: {_fmt(a.ends_at)} "
        f"(осталось {left})\n"
        f"Стартовая цена: {a.start_price} ₽  ·  шаг: {a.step_amount} ₽\n"
        f"Текущая цена: <b>{a.current_price} ₽</b>\n"
        f"{_leader_html(a)}\n"
        f"📝 Ставок в истории: <b>{n}</b>"
    )
    await safe_edit_to_text(cq, text, admin_stat_back_kb(), parse_mode=ParseMode.HTML)


@router.callback_query(F.data.startswith("adm:emenu:"))
async def cb_extend_menu(cq: CallbackQuery, session: AsyncSession) -> None:
    await cq.answer()
    try:
        aid = int((cq.data or "").split(":")[2])
    except (IndexError, ValueError):
        await cq.answer("Нет такого лота", show_alert=True)
        return
    await sync_auction_statuses(session)
    a = await session.get(Auction, aid)
    if not a or a.status not in (AuctionStatus.scheduled, AuctionStatus.live):
        await cq.answer("Лот недоступен", show_alert=True)
        return
    text = (
        f"<b>⏱ Продлить лот #{aid}</b>\n«{html.escape(a.title)}»\n"
        f"{LINE}\n"
        f"Сейчас конец: <b>{_fmt(a.ends_at)}</b>\n"
        "<i>На сколько сдвинуть окончание?</i>"
    )
    await safe_edit_to_text(cq, text, admin_extend_time_kb(aid), parse_mode=ParseMode.HTML)


@router.callback_query(F.data.startswith("adm:ext:"))
async def cb_extend_do(cq: CallbackQuery, session: AsyncSession) -> None:
    parts = (cq.data or "").split(":")
    if len(parts) != 4 or not parts[2].isdigit() or not parts[3].isdigit():
        await cq.answer()
        return
    aid = int(parts[2])
    minutes = int(parts[3])
    if minutes not in (15, 30, 60, 180, 1440):
        await cq.answer("Нет такого варианта", show_alert=True)
        return
    a, err = await admin_extend_auction_end(session, aid, minutes=minutes)
    if not a:
        await cq.answer(err or "Не удалось", show_alert=True)
        return
    await session.commit()
    await sync_auction_statuses(session)
    await cq.answer(f"✅ +{minutes} мин → конец {_fmt(a.ends_at)}", show_alert=False)
    await safe_edit_to_text(
        cq,
        f"<b>⏱ Лот #{aid} продлён</b>\n"
        f"{LINE}\n"
        f"Новое окончание: <b>{_fmt(a.ends_at)}</b>\n"
        "<i>При необходимости сообщите участникам в чате или рассылкой.</i>",
        admin_extend_time_kb(aid),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == "adm:home")
async def cb_admin_home(cq: CallbackQuery) -> None:
    await cq.answer()
    await safe_edit_to_text(
        cq,
        "<b>🛠 Панель администратора</b>\n"
        f"{LINE}\n"
        "<i>Лоты, рассылка, продление и статистика — кнопками ниже. "
        "«В главное меню» — экран пользователя.</i>",
        admin_main_kb(),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data.startswith("adm:eq:"))
async def cb_end_confirm(cq: CallbackQuery, session: AsyncSession) -> None:
    aid = int(cq.data.split(":")[2])
    res = await session.execute(select(Auction).where(Auction.id == aid))
    a = res.scalar_one_or_none()
    if not a or a.status not in (AuctionStatus.scheduled, AuctionStatus.live):
        await cq.answer("Лот уже недоступен", show_alert=True)
        return
    await cq.answer()
    await safe_edit_to_text(
        cq,
        f"<b>🏁 Завершить аукцион #{aid}</b>\n"
        f"{LINE}\n"
        f"«{html.escape(a.title)}»\n\n"
        "Победителю уйдёт текст из <code>PAYMENT_WINNER_NOTICE</code> в .env.",
        admin_confirm_end_kb(aid),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data.startswith("adm:ed:"))
async def cb_end_do(cq: CallbackQuery, session: AsyncSession) -> None:
    aid = int(cq.data.split(":")[2])
    a = await admin_force_end_auction(session, aid)
    if not a:
        await cq.answer("Не удалось завершить", show_alert=True)
        return
    await cq.answer()
    await session.commit()
    await notify_auction_ended(cq.bot, aid)
    await safe_edit_to_text(
        cq,
        f"✅ Лот <b>#{aid}</b> завершён.\n"
        "📨 Уведомления отправлены.",
        admin_main_kb(),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data.startswith("adm:cq:"))
async def cb_cancel_confirm(cq: CallbackQuery, session: AsyncSession) -> None:
    aid = int(cq.data.split(":")[2])
    res = await session.execute(select(Auction).where(Auction.id == aid))
    a = res.scalar_one_or_none()
    if not a or a.status not in (AuctionStatus.scheduled, AuctionStatus.live):
        await cq.answer("Лот уже недоступен", show_alert=True)
        return
    await cq.answer()
    await safe_edit_to_text(
        cq,
        f"<b>🗑 Отменить лот #{aid}</b>\n"
        f"{LINE}\n"
        f"«{html.escape(a.title)}»\n\n"
        "Лот исчезнет из актуальных и <b>не попадёт в галерею</b>.\n"
        "<i>Ставки в базе сохраняются.</i>",
        admin_confirm_cancel_kb(aid),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data.startswith("adm:cd:"))
async def cb_cancel_do(cq: CallbackQuery, session: AsyncSession) -> None:
    aid = int(cq.data.split(":")[2])
    a = await admin_cancel_auction(session, aid)
    if not a:
        await cq.answer("Не удалось отменить", show_alert=True)
        return
    await cq.answer()
    title = a.title
    await session.commit()
    for admin_id in _settings.admin_id_set:
        try:
            await cq.bot.send_message(admin_id, f"Лот #{aid} «{title}» отменён.")
        except Exception:
            logger.exception("admin notify cancel failed")
    await safe_edit_to_text(
        cq,
        f"⛔️ Лот <b>#{aid}</b> отменён.",
        admin_main_kb(),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == "adm:pub")
async def cb_publish(cq: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await cq.answer()
    data = await state.get_data()
    required = ("photo_file_id", "title", "start_price", "step_amount", "starts_at", "ends_at")
    if not all(data.get(k) for k in required):
        await cq.message.answer("Черновик неполный. Начните снова: /admin")
        await state.clear()
        return
    starts_at: datetime = data["starts_at"]
    ends_at: datetime = data["ends_at"]
    if ends_at <= starts_at:
        await cq.message.answer("Дата окончания должна быть позже старта.")
        return
    now = datetime.now(UTC)
    if ends_at <= now:
        await cq.message.answer("Аукцион уже должен был закончиться — поправьте даты.")
        return
    if now < starts_at:
        status = AuctionStatus.scheduled
    else:
        status = AuctionStatus.live
    a = Auction(
        title=data["title"],
        description=data.get("description"),
        photo_file_id=data["photo_file_id"],
        start_price=int(data["start_price"]),
        step_amount=int(data["step_amount"]),
        starts_at=starts_at,
        ends_at=ends_at,
        status=status,
        current_price=int(data["start_price"]),
        leading_user_id=None,
        show_in_gallery=True,
    )
    session.add(a)
    await session.flush()
    await state.clear()
    await cq.message.answer(
        f"🚀 Лот <b>#{a.id}</b> опубликован.\n"
        f"Статус: <b>{_status_ru(a)}</b>.",
        parse_mode=ParseMode.HTML,
    )


@router.message(NewAuctionStates.photo, F.photo)
async def adm_photo(message: Message, state: FSMContext) -> None:
    p = message.photo[-1]
    await state.update_data(photo_file_id=p.file_id)
    await state.set_state(NewAuctionStates.title)
    await message.answer("Шаг 2/7: введите название картины.")


@router.message(NewAuctionStates.photo)
async def adm_photo_bad(message: Message) -> None:
    await message.answer("Нужно отправить изображение как фото.")


@router.message(NewAuctionStates.title, F.text)
async def adm_title(message: Message, state: FSMContext) -> None:
    await state.update_data(title=message.text.strip())
    await state.set_state(NewAuctionStates.description)
    await message.answer("Шаг 3/7: описание (или отправьте «-» чтобы пропустить).")


@router.message(NewAuctionStates.description, F.text)
async def adm_description(message: Message, state: FSMContext) -> None:
    raw = message.text.strip()
    desc = None if raw in {"-", "—"} else raw
    await state.update_data(description=desc)
    await state.set_state(NewAuctionStates.start_price)
    await message.answer("Шаг 4/7: начальная цена (целое число, руб.).")


@router.message(NewAuctionStates.start_price, F.text)
async def adm_start_price(message: Message, state: FSMContext) -> None:
    try:
        v = int(message.text.replace(" ", "").strip())
    except ValueError:
        await message.answer("Нужно целое число.")
        return
    if v <= 0:
        await message.answer("Цена должна быть больше нуля.")
        return
    await state.update_data(start_price=v)
    await state.set_state(NewAuctionStates.step_amount)
    await message.answer("Шаг 5/7: минимальный шаг ставки (руб.).")


@router.message(NewAuctionStates.step_amount, F.text)
async def adm_step(message: Message, state: FSMContext) -> None:
    try:
        v = int(message.text.replace(" ", "").strip())
    except ValueError:
        await message.answer("Нужно целое число.")
        return
    if v <= 0:
        await message.answer("Шаг должен быть больше нуля.")
        return
    await state.update_data(step_amount=v)
    await state.set_state(NewAuctionStates.starts_at)
    await message.answer(
        "Шаг 6/7: дата и время старта в местном времени, формат ДД.ММ.ГГГГ ЧЧ:ММ"
    )


@router.message(NewAuctionStates.starts_at, F.text)
async def adm_starts(message: Message, state: FSMContext) -> None:
    try:
        dt = _parse_dt(message.text)
    except ValueError as e:
        await message.answer(str(e))
        return
    await state.update_data(starts_at=dt)
    await state.set_state(NewAuctionStates.ends_at)
    await message.answer("Шаг 7/7: дата и время окончания, тот же формат.")


@router.message(NewAuctionStates.ends_at, F.text)
async def adm_ends(message: Message, state: FSMContext) -> None:
    try:
        dt = _parse_dt(message.text)
    except ValueError as e:
        await message.answer(str(e))
        return
    data = await state.get_data()
    starts_at: datetime | None = data.get("starts_at")
    if starts_at and dt <= starts_at:
        await message.answer("Окончание должно быть позже старта.")
        return
    await state.update_data(ends_at=dt)
    await state.set_state(NewAuctionStates.confirm)
    full = {**data, "ends_at": dt}
    await message.answer(
        _summary(full),
        parse_mode=ParseMode.HTML,
        reply_markup=admin_confirm_kb(),
    )


@router.callback_query(F.data == "adm:broadcast")
async def cb_broadcast_start(cq: CallbackQuery, state: FSMContext) -> None:
    await cq.answer()
    await state.clear()
    await state.set_state(BroadcastStates.entering)
    await cq.message.answer(
        "<b>📣 Рассылка</b>\n"
        f"{LINE}\n"
        "Пришлите <b>текст</b> или <b>фото</b> (с подписью или без). "
        "Сообщение уйдёт всем, кто есть в базе бота.",
        parse_mode=ParseMode.HTML,
    )


@router.message(BroadcastStates.entering, F.photo)
async def broadcast_entering_photo(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    p = message.photo[-1]
    ids = await list_all_user_telegram_ids(session)
    await state.update_data(
        broadcast_photo_file_id=p.file_id,
        broadcast_caption=(message.caption or "").strip() or None,
        broadcast_text=None,
        recipient_count=len(ids),
    )
    await state.set_state(BroadcastStates.confirm)
    cap = message.caption or ""
    preview = (
        f"<b>Предпросмотр</b> (фото)\n{LINE}\n"
        f"<i>{html.escape(cap) if cap else 'без подписи'}</i>\n\n"
        f"Получателей в базе: <b>{len(ids)}</b>"
    )
    await message.answer(
        preview,
        parse_mode=ParseMode.HTML,
        reply_markup=admin_broadcast_confirm_kb(),
    )


@router.message(BroadcastStates.entering, F.text)
async def broadcast_entering_text(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    raw = (message.text or "").strip()
    if not raw:
        await message.answer("Нужен непустой текст.")
        return
    ids = await list_all_user_telegram_ids(session)
    await state.update_data(
        broadcast_text=raw,
        broadcast_photo_file_id=None,
        broadcast_caption=None,
        recipient_count=len(ids),
    )
    await state.set_state(BroadcastStates.confirm)
    preview = (
        f"<b>Предпросмотр</b>\n{LINE}\n{html.escape(raw[:500])}"
        + ("…" if len(raw) > 500 else "")
        + f"\n\nПолучателей в базе: <b>{len(ids)}</b>"
    )
    await message.answer(
        preview,
        parse_mode=ParseMode.HTML,
        reply_markup=admin_broadcast_confirm_kb(),
    )


@router.message(BroadcastStates.entering)
async def broadcast_entering_bad(message: Message) -> None:
    await message.answer("Нужен текст или фото.")


@router.callback_query(F.data == "adm:bc_cancel")
async def broadcast_cancel(cq: CallbackQuery, state: FSMContext) -> None:
    await cq.answer()
    await state.clear()
    await safe_edit_to_text(
        cq,
        "↩️ Рассылка отменена.",
        admin_main_kb(),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == "adm:bc_send")
async def broadcast_send(cq: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await cq.answer()
    data = await state.get_data()
    if await state.get_state() != BroadcastStates.confirm.state:
        await cq.message.answer("Сессия рассылки устарела. Начните снова: /admin")
        await state.clear()
        return
    photo = data.get("broadcast_photo_file_id")
    text = data.get("broadcast_text")
    caption = data.get("broadcast_caption")
    if photo is None and not text:
        await state.clear()
        await cq.message.answer("Нет сообщения для отправки.")
        return
    ids = await list_all_user_telegram_ids(session)
    await state.clear()
    ok = 0
    fail = 0
    for tid in ids:
        try:
            if photo:
                await cq.bot.send_photo(
                    tid,
                    photo,
                    caption=caption,
                )
            else:
                await cq.bot.send_message(tid, text)
            ok += 1
        except Exception:
            fail += 1
            logger.exception("broadcast failed for %s", tid)
        await asyncio.sleep(0.05)
    await safe_edit_to_text(
        cq,
        f"<b>📣 Рассылка завершена</b>\n"
        f"{LINE}\n"
        f"Успешно: <b>{ok}</b>  ·  ошибок: <b>{fail}</b>",
        admin_main_kb(),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == "adm:edit")
async def cb_edit_auctions_list(cq: CallbackQuery, session: AsyncSession) -> None:
    await cq.answer()
    await sync_auction_statuses(session)
    rows = await list_auctions_for_admin_edit(session)
    if not rows:
        await safe_edit_to_text(
            cq,
            "<b>✏️ Изменить лот</b>\n"
            f"{LINE}\n"
            "Нет лотов (отменённые не показываются).",
            admin_main_kb(),
            parse_mode=ParseMode.HTML,
        )
        return
    lines = [
        f"▫️ #{a.id} · {html.escape(a.title)} · {_status_ru(a)}"
        for a in rows
    ]
    text = (
        "<b>✏️ Выберите лот</b>\n"
        f"{LINE}\n"
        + "\n".join(lines)
        + "\n\n<i>Дальше — поле для правки</i>"
    )
    await safe_edit_to_text(cq, text, admin_edit_list_kb(rows), parse_mode=ParseMode.HTML)


@router.callback_query(F.data.startswith("adm:esel:"))
async def cb_edit_select(cq: CallbackQuery, session: AsyncSession) -> None:
    await cq.answer()
    try:
        aid = int((cq.data or "").split(":")[2])
    except (IndexError, ValueError):
        await cq.answer("Нет такого лота", show_alert=True)
        return
    await sync_auction_statuses(session)
    a = await session.get(Auction, aid)
    if not a or a.status == AuctionStatus.cancelled:
        await cq.answer("Лот недоступен", show_alert=True)
        return
    await safe_edit_to_text(
        cq,
        f"<b>✏️ Лот #{aid}</b>\n«{html.escape(a.title)}»\n\nВыберите поле:",
        admin_edit_field_kb(aid),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data.startswith("adm:ef:"))
async def cb_edit_field(cq: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    parsed = _parse_adm_ef(cq.data or "")
    if not parsed:
        await cq.answer()
        return
    aid, field = parsed
    a = await session.get(Auction, aid)
    if not a or a.status == AuctionStatus.cancelled:
        await cq.answer("Лот недоступен", show_alert=True)
        return
    if field == "toggle":
        await cq.answer()
        a.show_in_gallery = not a.show_in_gallery
        await session.commit()
        await sync_auction_statuses(session)
        await safe_edit_to_text(
            cq,
            f"<b>✏️ Лот #{aid}</b>\n«{html.escape(a.title)}»\n\n"
            f"Показ в галерее: <b>{'да' if a.show_in_gallery else 'нет'}</b>",
            admin_edit_field_kb(aid),
            parse_mode=ParseMode.HTML,
        )
        return
    await cq.answer()
    prompts = {
        "title": "Введите новое название:",
        "desc": "Пришлите новое описание (или «-» чтобы очистить).",
        "photo": "Пришлите новое фото картины (как изображение).",
        "prices": "Введите два числа через пробел: начальная цена и шаг (например 1000 100).",
        "start": "Дата и время старта: ДД.ММ.ГГГГ ЧЧ:ММ (местное время).",
        "end": "Дата и время окончания: тот же формат.",
    }
    if field not in prompts:
        await cq.message.answer("Неизвестное поле.")
        return
    await state.set_state(EditAuctionStates.waiting_value)
    await state.update_data(edit_auction_id=aid, edit_field=field)
    await cq.message.answer(prompts[field])


@router.message(EditAuctionStates.waiting_value, F.text)
async def edit_auction_text_value(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    data = await state.get_data()
    aid = int(data.get("edit_auction_id") or 0)
    field = data.get("edit_field")
    if not aid or not field:
        await state.clear()
        return
    raw = (message.text or "").strip()
    ok: bool
    err: str | None
    if field == "title":
        ok, err = await update_auction_fields(session, aid, title=raw)
    elif field == "desc":
        desc = None if raw in {"-", "—"} else raw
        ok, err = await update_auction_fields(session, aid, description=desc)
    elif field == "prices":
        parts = raw.split()
        if len(parts) != 2:
            await message.answer("Нужно два числа через пробел.")
            return
        try:
            sp = int(parts[0].replace(" ", ""))
            st = int(parts[1].replace(" ", ""))
        except ValueError:
            await message.answer("Нужны целые числа.")
            return
        ok, err = await update_auction_fields(session, aid, start_price=sp, step_amount=st)
    elif field == "start":
        try:
            dt = _parse_dt(raw)
        except ValueError as e:
            await message.answer(str(e))
            return
        ok, err = await update_auction_fields(session, aid, starts_at=dt)
    elif field == "end":
        try:
            dt = _parse_dt(raw)
        except ValueError as e:
            await message.answer(str(e))
            return
        ok, err = await update_auction_fields(session, aid, ends_at=dt)
    else:
        await message.answer("Сейчас ожидается другое действие (например фото).")
        return
    if not ok:
        await message.answer(err or "Не удалось сохранить")
        return
    await session.commit()
    await sync_auction_statuses(session)
    await state.clear()
    await message.answer(
        "✅ Сохранено.",
        reply_markup=admin_edit_field_kb(aid),
    )


@router.message(EditAuctionStates.waiting_value, F.photo)
async def edit_auction_photo_value(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    data = await state.get_data()
    if data.get("edit_field") != "photo":
        await message.answer("Сейчас нужен текст, не фото. /cancel — отмена.")
        return
    aid = int(data.get("edit_auction_id") or 0)
    if not aid:
        await state.clear()
        return
    fid = message.photo[-1].file_id
    ok, err = await update_auction_fields(session, aid, photo_file_id=fid)
    if not ok:
        await message.answer(err or "Не удалось сохранить")
        return
    await session.commit()
    await sync_auction_statuses(session)
    await state.clear()
    await message.answer(
        "✅ Фото обновлено.",
        reply_markup=admin_edit_field_kb(aid),
    )


@router.message(EditAuctionStates.waiting_value)
async def edit_auction_waiting_other(message: Message) -> None:
    await message.answer("Нужен текст или фото в зависимости от поля. /cancel — отмена.")
