from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,  # log SQL queries
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,  # ping the database to check if it's still alive
    # Our tables live in DB_SCHEMA. `public` stays on the path because that is
    # where the pgvector extension puts its type and operators.
    connect_args={"server_settings": {"search_path": f"{settings.DB_SCHEMA},public"}},
)

SessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


# Commits before the response is sent, so a failed commit becomes an error
# response instead of a silent loss.
SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]

# For streaming responses, where the session must outlive the handler.
StreamingSessionDep = Annotated[AsyncSession, Depends(get_session)]
