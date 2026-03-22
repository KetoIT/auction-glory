from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from db.models import Base

# Файл SQLite рядом с этим модулем: auction_bot/db/bot.db
_db_path = Path(__file__).resolve().parent / "bot.db"
_db_path.parent.mkdir(parents=True, exist_ok=True)

database_url = f"sqlite+aiosqlite:///{_db_path.as_posix()}"

engine = create_async_engine(
    database_url,
    echo=False,
)

async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
