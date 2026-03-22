from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Всегда ищем .env рядом с этим файлом (auction_bot/.env), а не в текущей папке терминала
_ENV_FILE = Path(__file__).resolve().parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str
    # HTTP(S) прокси до api.telegram.org, например http://127.0.0.1:7890 (если API недоступен)
    telegram_proxy: str | None = None
    admin_ids: str = ""
    artist_contact_url: str = "https://t.me/"
    payment_winner_notice: str = (
        "Поздравляем с победой в аукционе!\n"
        "Свяжитесь для оплаты и доставки — напишите в личные сообщения."
    )
    timezone: str = "Europe/Moscow"
    bid_rate_limit_per_minute: int = 20
    scheduler_interval_sec: int = 30
    recent_bids_limit: int = 15
    # Многострочный HTML-текст правил; если пусто — берётся встроенный DEFAULT_RULES_HTML
    auction_rules_html: str = ""
    # file_id стикера из @Stickers (отправляется при /start перед текстом). Пусто — не слать.
    welcome_sticker_file_id: str = (
        "CAACAgIAAxkBAAEQzKNpv7sviJttZ3QZM50WgQdbf79-ZQACEhgAAhAemUi_BAQfVRhL4ToE"
    )

    @field_validator("welcome_sticker_file_id", mode="before")
    @classmethod
    def empty_welcome_sticker(cls, v: object) -> str:
        if v is None:
            return ""
        return str(v).strip()

    @field_validator("telegram_proxy", mode="before")
    @classmethod
    def empty_proxy(cls, v: object) -> str | None:
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    @field_validator("admin_ids", mode="before")
    @classmethod
    def strip_admins(cls, v: object) -> str:
        if v is None:
            return ""
        return str(v).strip()

    @property
    def admin_id_set(self) -> set[int]:
        if not self.admin_ids:
            return set()
        out: set[int] = set()
        for part in self.admin_ids.replace(";", ",").split(","):
            part = part.strip()
            if not part:
                continue
            try:
                out.add(int(part))
            except ValueError:
                continue
        return out

    def rules_html(self) -> str:
        s = (self.auction_rules_html or "").strip()
        if s:
            return s
        from bot.user_copy import DEFAULT_RULES_HTML

        return DEFAULT_RULES_HTML

    @property
    def welcome_sticker_id(self) -> str | None:
        s = (self.welcome_sticker_file_id or "").strip()
        return s or None


@lru_cache
def get_settings() -> Settings:
    return Settings()
