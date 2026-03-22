from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from config import get_settings
from db.models import Auction, AuctionStatus, Bid, User

_settings = get_settings()
_rate: dict[int, deque[float]] = defaultdict(deque)
_rate_lock_time = time.monotonic


def as_utc(dt: datetime) -> datetime:
    """SQLite может вернуть naive datetime — для сравнений с now(UTC) приводим к UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _check_rate_limit(telegram_id: int) -> None:
    limit = _settings.bid_rate_limit_per_minute
    now = _rate_lock_time()
    q = _rate[telegram_id]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= limit:
        raise ValueError("Слишком много ставок подряд. Подождите минуту.")
    q.append(now)


def minimum_next_bid(auction: Auction) -> int:
    if auction.leading_user_id is None:
        return auction.start_price
    return auction.current_price + auction.step_amount


async def ensure_user(
    session: AsyncSession,
    *,
    telegram_id: int,
    username: str | None,
    full_name: str | None,
) -> User:
    res = await session.execute(select(User).where(User.telegram_id == telegram_id))
    row = res.scalar_one_or_none()
    if row:
        changed = False
        if username and row.username != username:
            row.username = username
            changed = True
        fn = (full_name or "").strip() or None
        if fn and row.full_name != fn:
            row.full_name = fn
            changed = True
        if changed:
            await session.flush()
        return row
    u = User(
        telegram_id=telegram_id,
        username=username,
        full_name=(full_name or "").strip() or None,
    )
    session.add(u)
    await session.flush()
    return u


async def get_auction(session: AsyncSession, auction_id: int) -> Auction | None:
    res = await session.execute(
        select(Auction)
        .options(selectinload(Auction.leading_user))
        .where(Auction.id == auction_id)
    )
    return res.scalar_one_or_none()


async def list_scheduled_and_live(session: AsyncSession) -> list[Auction]:
    res = await session.execute(
        select(Auction)
        .where(Auction.status.in_((AuctionStatus.scheduled, AuctionStatus.live)))
        .order_by(Auction.starts_at.asc())
    )
    return list(res.scalars().all())


async def list_gallery_auctions(session: AsyncSession) -> list[Auction]:
    res = await session.execute(
        select(Auction)
        .where(Auction.status == AuctionStatus.ended, Auction.show_in_gallery.is_(True))
        .order_by(Auction.ends_at.desc())
    )
    return list(res.scalars().all())


async def list_recent_bids(session: AsyncSession, auction_id: int, limit: int) -> list[Bid]:
    res = await session.execute(
        select(Bid)
        .options(selectinload(Bid.user))
        .where(Bid.auction_id == auction_id)
        .order_by(Bid.created_at.desc())
        .limit(limit)
    )
    return list(res.scalars().all())


async def place_bid(
    session: AsyncSession,
    *,
    auction_id: int,
    user: User,
    amount: int,
) -> Bid:
    _check_rate_limit(user.telegram_id)

    if amount <= 0:
        raise ValueError("Сумма должна быть больше нуля.")

    res = await session.execute(
        select(Auction).where(Auction.id == auction_id).with_for_update()
    )
    auction = res.scalar_one_or_none()
    if auction is None:
        raise ValueError("Лот не найден.")

    now = datetime.now(UTC)
    if auction.status != AuctionStatus.live:
        raise ValueError("Аукцион сейчас не принимает ставки.")
    if now < as_utc(auction.starts_at) or now > as_utc(auction.ends_at):
        raise ValueError("Вне времени приёма ставок.")

    need = minimum_next_bid(auction)
    if amount < need:
        raise ValueError(f"Минимальная ставка: {need} ₽")

    old_price = auction.current_price
    bid = Bid(auction_id=auction.id, user_id=user.id, amount=amount)
    session.add(bid)
    await session.flush()

    upd = await session.execute(
        update(Auction)
        .where(Auction.id == auction.id, Auction.current_price == old_price)
        .values(current_price=amount, leading_user_id=user.id)
    )
    if upd.rowcount != 1:
        raise ValueError("Цена изменилась — обновите экран и попробуйте снова.")

    auction.current_price = amount
    auction.leading_user_id = user.id
    return bid


async def sync_auction_statuses(session: AsyncSession, now: datetime | None = None) -> list[Auction]:
    """Переводит scheduled→live и live→ended по времени. Возвращает лоты, только что ставшие ended."""
    now = now or datetime.now(UTC)
    ended_now: list[Auction] = []

    res = await session.execute(
        select(Auction).where(Auction.status == AuctionStatus.scheduled, Auction.starts_at <= now)
    )
    for a in res.scalars().all():
        if as_utc(a.ends_at) <= now:
            a.status = AuctionStatus.ended
            ended_now.append(a)
        else:
            a.status = AuctionStatus.live

    res2 = await session.execute(
        select(Auction).where(Auction.status == AuctionStatus.live, Auction.ends_at <= now)
    )
    for a in res2.scalars().all():
        a.status = AuctionStatus.ended
        ended_now.append(a)

    await session.flush()
    return ended_now


async def admin_force_end_auction(session: AsyncSession, auction_id: int) -> Auction | None:
    """Досрочно завершить live/scheduled → ended (для уведомлений вызывайте notify_auction_ended после commit)."""
    a = await session.get(Auction, auction_id)
    if a is None or a.status not in (AuctionStatus.scheduled, AuctionStatus.live):
        return None
    now = datetime.now(UTC)
    a.status = AuctionStatus.ended
    if as_utc(a.ends_at) > now:
        a.ends_at = now
    await session.flush()
    return a


async def admin_cancel_auction(session: AsyncSession, auction_id: int) -> Auction | None:
    """Отменить лот: не в актуальных, не в галерее."""
    a = await session.get(Auction, auction_id)
    if a is None or a.status not in (AuctionStatus.scheduled, AuctionStatus.live):
        return None
    a.status = AuctionStatus.cancelled
    a.show_in_gallery = False
    await session.flush()
    return a


async def list_all_user_telegram_ids(session: AsyncSession) -> list[int]:
    res = await session.execute(select(User.telegram_id))
    return [row[0] for row in res.all()]


async def count_bids_for_auctions(session: AsyncSession, auction_ids: list[int]) -> dict[int, int]:
    if not auction_ids:
        return {}
    res = await session.execute(
        select(Bid.auction_id, func.count(Bid.id))
        .where(Bid.auction_id.in_(auction_ids))
        .group_by(Bid.auction_id)
    )
    return {row[0]: int(row[1]) for row in res.all()}


async def admin_extend_auction_end(
    session: AsyncSession,
    auction_id: int,
    *,
    minutes: int,
) -> tuple[Auction | None, str | None]:
    """Продлить окончание лота (scheduled/live)."""
    if minutes <= 0:
        return None, "Интервал должен быть положительным"
    a = await session.get(Auction, auction_id)
    if a is None:
        return None, "Лот не найден"
    if a.status not in (AuctionStatus.scheduled, AuctionStatus.live):
        return None, "Лот уже завершён или отменён"
    a.ends_at = a.ends_at + timedelta(minutes=minutes)
    await session.flush()
    return a, None


async def list_auctions_for_admin_edit(
    session: AsyncSession, *, limit: int = 40
) -> list[Auction]:
    res = await session.execute(
        select(Auction)
        .where(Auction.status != AuctionStatus.cancelled)
        .order_by(Auction.id.desc())
        .limit(limit)
    )
    return list(res.scalars().all())


_ALLOWED_EDIT_FIELDS = frozenset(
    {
        "title",
        "description",
        "photo_file_id",
        "start_price",
        "step_amount",
        "starts_at",
        "ends_at",
        "show_in_gallery",
    }
)


async def update_auction_fields(
    session: AsyncSession,
    auction_id: int,
    **fields: object,
) -> tuple[bool, str | None]:
    """Обновить поля лота (не cancelled). Возвращает (успех, текст ошибки)."""
    a = await session.get(Auction, auction_id)
    if a is None:
        return False, "Лот не найден"
    if a.status == AuctionStatus.cancelled:
        return False, "Отменённый лот нельзя редактировать"
    extra = set(fields) - _ALLOWED_EDIT_FIELDS
    if extra:
        return False, "Неизвестные поля"

    if "title" in fields:
        t = str(fields["title"] or "").strip()
        if not t:
            return False, "Пустое название"
        a.title = t[:512]

    if "description" in fields:
        d = fields["description"]
        a.description = None if d in (None, "") else str(d)

    if "photo_file_id" in fields:
        a.photo_file_id = str(fields["photo_file_id"])[:512]

    if "start_price" in fields:
        sp = int(fields["start_price"])  # type: ignore[arg-type]
        if sp <= 0:
            return False, "Начальная цена должна быть больше нуля"
        a.start_price = sp

    if "step_amount" in fields:
        st = int(fields["step_amount"])  # type: ignore[arg-type]
        if st <= 0:
            return False, "Шаг должен быть больше нуля"
        a.step_amount = st

    if "starts_at" in fields:
        a.starts_at = fields["starts_at"]  # type: ignore[assignment]
    if "ends_at" in fields:
        a.ends_at = fields["ends_at"]  # type: ignore[assignment]

    if "show_in_gallery" in fields:
        a.show_in_gallery = bool(fields["show_in_gallery"])

    if as_utc(a.ends_at) <= as_utc(a.starts_at):
        return False, "Окончание должно быть позже старта"

    if a.leading_user_id is None and "start_price" in fields:
        a.current_price = a.start_price

    await session.flush()
    return True, None
