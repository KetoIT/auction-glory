from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from sqlalchemy import select

from config import get_settings
from db.models import Auction, User
from db.session import async_session_maker
from domain.auctions import sync_auction_statuses

logger = logging.getLogger(__name__)


async def scheduler_loop(bot: Bot) -> None:
    settings = get_settings()
    interval = max(5, settings.scheduler_interval_sec)
    while True:
        try:
            await asyncio.sleep(interval)
            ended_ids: list[int] = []
            async with async_session_maker() as session:
                ended = await sync_auction_statuses(session)
                ended_ids = [a.id for a in ended]
                await session.commit()
            for eid in ended_ids:
                await notify_auction_ended(bot, eid)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("scheduler tick failed")


async def notify_auction_ended(bot: Bot, auction_id: int) -> None:
    settings = get_settings()
    async with async_session_maker() as session:
        res = await session.execute(select(Auction).where(Auction.id == auction_id))
        a = res.scalar_one_or_none()
        if a is None or a.winner_notified:
            return
        winner_tg: int | None = None
        winner_uname = ""
        if a.leading_user_id:
            u = await session.get(User, a.leading_user_id)
            if u:
                winner_tg = u.telegram_id
                winner_uname = u.username or ""
        if winner_tg:
            try:
                await bot.send_message(winner_tg, settings.payment_winner_notice)
            except Exception:
                logger.exception("Не удалось написать победителю аукциона %s", auction_id)
        admin_text = (
            f"Аукцион завершён\n#{a.id} {a.title}\n"
            f"Итоговая ставка: {a.current_price} ₽\n"
        )
        if winner_tg:
            admin_text += f"Победитель: {winner_tg}" + (f" @{winner_uname}" if winner_uname else "")
        else:
            admin_text += "Ставок не было."
        for aid in settings.admin_id_set:
            try:
                await bot.send_message(aid, admin_text)
            except Exception:
                logger.exception("Не удалось уведомить админа %s", aid)
        a.winner_notified = True
        await session.commit()
