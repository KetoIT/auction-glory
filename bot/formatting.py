"""Единый стиль подписей и разделителей в сообщениях (HTML)."""

LINE = "──────────────"
BULLET = "▫️"


def status_badge_scheduled() -> str:
    return "⏳ <i>скоро старт</i>"


def status_badge_live() -> str:
    return "🟢 <i>идёт приём ставок</i>"


def status_badge_ended() -> str:
    return "✅ <i>завершён</i>"


def status_badge_cancelled() -> str:
    return "⛔️ <i>отменён</i>"
