from __future__ import annotations

import os
from datetime import date
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    desc,
    insert,
    select,
    text,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

metadata = MetaData()

users = Table(
    "users",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("phone", String(32), nullable=False, unique=True, index=True),
    Column("welcomed", Boolean, nullable=False, default=False),
)

transactions = Table(
    "transactions",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("phone", String(32), nullable=False, index=True),
    Column("type", String(16), nullable=False),
    Column("amount_cents", Integer, nullable=False),
    Column("description", String(500), nullable=False),
    Column("category", String(64), nullable=False),
    Column("payment_method", String(64), nullable=False, default="não informado"),
    Column("occurred_on", Date, nullable=False),
)

processed_messages = Table(
    "processed_messages",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("message_id", String(255), nullable=False),
    Column("phone", String(32), nullable=False),
    UniqueConstraint("message_id", name="uq_processed_message_id"),
)


def database_url() -> str:
    configured = os.getenv("DATABASE_URL")
    if configured:
        return configured
    return "sqlite+aiosqlite:///./finance.db"


class FinanceRepository:
    def __init__(self, engine: AsyncEngine | None = None) -> None:
        self.engine = engine or create_async_engine(database_url(), pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def initialize(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(metadata.create_all)
            if connection.dialect.name == "postgresql":
                await connection.execute(
                    text(
                        "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS "
                        "payment_method VARCHAR(64) NOT NULL DEFAULT 'não informado'"
                    )
                )
            else:
                try:
                    await connection.execute(
                        text(
                            "ALTER TABLE transactions ADD COLUMN payment_method "
                            "VARCHAR(64) NOT NULL DEFAULT 'não informado'"
                        )
                    )
                except SQLAlchemyError:
                    pass

    async def close(self) -> None:
        await self.engine.dispose()

    async def ensure_user(self, phone: str) -> bool:
        async with self.sessions() as session:
            result = await session.execute(select(users.c.welcomed).where(users.c.phone == phone))
            welcomed = result.scalar_one_or_none()
            if welcomed is None:
                await session.execute(insert(users).values(phone=phone, welcomed=False))
                await session.commit()
                return True
            return not welcomed

    async def mark_welcomed(self, phone: str) -> None:
        async with self.sessions() as session:
            await session.execute(users.update().where(users.c.phone == phone).values(welcomed=True))
            await session.commit()

    async def add_transaction(self, phone: str, transaction: Any, occurred_on: date) -> None:
        amount_cents = round(float(transaction.amount) * 100)
        async with self.sessions() as session:
            await session.execute(
                insert(transactions).values(
                    phone=phone,
                    type=transaction.type,
                    amount_cents=amount_cents,
                    description=transaction.description,
                    category=transaction.category,
                    payment_method=transaction.payment_method,
                    occurred_on=occurred_on,
                )
            )
            await session.commit()

    async def count_expenses(self, phone: str) -> tuple[int, int]:
        async with self.sessions() as session:
            result = await session.execute(
                select(transactions.c.amount_cents).where(
                    transactions.c.phone == phone,
                    transactions.c.type == "expense",
                )
            )
            amounts = list(result.scalars())
            return len(amounts), sum(amounts)

    async def list_expenses(self, phone: str, limit: int = 10) -> list[dict[str, Any]]:
        async with self.sessions() as session:
            result = await session.execute(
                select(
                    transactions.c.amount_cents,
                    transactions.c.description,
                    transactions.c.category,
                    transactions.c.occurred_on,
                )
                .where(
                    transactions.c.phone == phone,
                    transactions.c.type == "expense",
                )
                .order_by(desc(transactions.c.id))
                .limit(limit)
            )
            return [dict(row._mapping) for row in result]

    async def mark_message_once(self, message_id: str | None, phone: str) -> bool:
        if not message_id:
            return True
        async with self.sessions() as session:
            existing = await session.execute(
                select(processed_messages.c.id).where(processed_messages.c.message_id == message_id)
            )
            if existing.scalar_one_or_none() is not None:
                return False
            await session.execute(insert(processed_messages).values(message_id=message_id, phone=phone))
            await session.commit()
            return True
