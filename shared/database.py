"""SQLAlchemy 2.0 async engine + session for PostgreSQL (Neon / DO Managed)."""
from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from shared.config import POSTGRES_SSL

load_dotenv()


def _database_url() -> str:
    url = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")
    if not url:
        raise RuntimeError("DATABASE_URL (or POSTGRES_URL) must be set")

    parts = urlsplit(url)
    scheme = parts.scheme
    # Canonical async driver for this app is asyncpg. Accept common URL spellings
    # (bare postgresql://, +asyncpg, +psycopg) and always rewrite to +asyncpg so
    # App Platform / CI secrets that use either driver name keep working.
    if scheme in {"postgresql", "postgres", "postgresql+psycopg", "postgresql+psycopg_async"}:
        scheme = "postgresql+asyncpg"
    elif scheme == "postgresql+asyncpg":
        pass
    else:
        raise RuntimeError("DATABASE_URL must use PostgreSQL")

    # asyncpg rejects libpq-only query args (sslmode, channel_binding).
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in {"channel_binding", "sslmode", "ssl"}
    ]
    return urlunsplit((scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


DATABASE_URL = _database_url()

_connect_args: dict = {}
if POSTGRES_SSL:
    _connect_args["ssl"] = True

engine = create_async_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    connect_args=_connect_args,
)

SessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yields an AsyncSession, closes on exit."""
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
