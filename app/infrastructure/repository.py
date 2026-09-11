from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    desc,
    func,
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
    Column("salary_cents", Integer, nullable=True),
    Column("salary_alert_level", Integer, nullable=False, default=0),
    Column("salary_alert_month", String(7), nullable=True),
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

recurring_expenses = Table(
    "recurring_expenses", metadata,
    Column("id", Integer, primary_key=True), Column("phone", String(32), nullable=False, index=True),
    Column("description", String(500), nullable=False), Column("amount_cents", Integer, nullable=False),
    Column("category", String(64), nullable=False), Column("payment_method", String(64), nullable=False),
    Column("day_of_month", Integer, nullable=False), Column("active", Boolean, nullable=False, default=True),
)

installments = Table(
    "installments", metadata,
    Column("id", Integer, primary_key=True), Column("phone", String(32), nullable=False, index=True),
    Column("description", String(500), nullable=False), Column("total_cents", Integer, nullable=False),
    Column("installment_cents", Integer, nullable=False), Column("total_count", Integer, nullable=False),
    Column("current_count", Integer, nullable=False, default=0), Column("category", String(64), nullable=False),
    Column("payment_method", String(64), nullable=False), Column("first_due_on", Date, nullable=False),
    Column("active", Boolean, nullable=False, default=True),
)

budgets = Table(
    "budgets", metadata,
    Column("id", Integer, primary_key=True), Column("phone", String(32), nullable=False, index=True),
    Column("category", String(64), nullable=False), Column("limit_cents", Integer, nullable=False),
    Column("month", String(7), nullable=False),
    UniqueConstraint("phone", "category", "month", name="uq_budget_phone_category_month"),
)

reminders = Table(
    "reminders", metadata,
    Column("id", Integer, primary_key=True),
    Column("phone", String(32), nullable=False, index=True),
    Column("message", String(500), nullable=False),
    Column("due_at", DateTime(timezone=True), nullable=False),
    Column("sent", Boolean, nullable=False, default=False),
    Column("source_message_id", String(255), nullable=True),
)

pending_confirmations = Table(
    "pending_confirmations", metadata,
    Column("id", Integer, primary_key=True),
    Column("phone", String(32), nullable=False, index=True),
    Column("message_id", String(255), nullable=False, unique=True),
    Column("intent", String(32), nullable=False),
    Column("amount_cents", Integer, nullable=False),
    Column("description", String(500), nullable=False),
    Column("category", String(64), nullable=False),
    Column("payment_method", String(64), nullable=False),
    Column("confidence", String(16), nullable=False),
    Column("status", String(16), nullable=False, default="pending"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
)

delivery_records = Table(
    "delivery_records", metadata,
    Column("id", Integer, primary_key=True),
    Column("delivery_key", String(255), nullable=False, unique=True),
    Column("kind", String(32), nullable=False),
    Column("recipient", String(32), nullable=False),
    Column("status", String(16), nullable=False, default="sending"),
    Column("attempts", Integer, nullable=False, default=0),
    Column("last_error", String(500), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=True),
)

conversation_messages = Table(
    "conversation_messages",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("phone", String(32), nullable=False, index=True),
    Column("role", String(16), nullable=False),
    Column("content", String(4000), nullable=False),
    Column("message_id", String(255), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
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
            reminder_column = (
                "ALTER TABLE reminders ADD COLUMN IF NOT EXISTS source_message_id VARCHAR(255)"
                if connection.dialect.name == "postgresql"
                else "ALTER TABLE reminders ADD COLUMN source_message_id VARCHAR(255)"
            )
            try:
                await connection.execute(text(reminder_column))
            except SQLAlchemyError:
                pass
            for statement in (
                "ALTER TABLE users ADD COLUMN salary_cents INTEGER",
                "ALTER TABLE users ADD COLUMN salary_alert_level INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE users ADD COLUMN salary_alert_month VARCHAR(7)",
            ):
                try:
                    await connection.execute(text(statement))
                except SQLAlchemyError:
                    pass

    async def close(self) -> None:
        await self.engine.dispose()

    async def append_conversation_message(
        self,
        phone: str,
        role: str,
        content: str,
        message_id: str | None = None,
    ) -> None:
        content = (content or "").strip()
        if not content:
            return
        async with self.sessions() as session:
            await session.execute(
                insert(conversation_messages).values(
                    phone=phone,
                    role=role,
                    content=content[:4000],
                    message_id=message_id,
                    created_at=datetime.now(UTC),
                )
            )
            await session.commit()

    async def recent_conversation(self, phone: str, limit: int = 12) -> list[dict[str, Any]]:
        async with self.sessions() as session:
            result = await session.execute(
                select(
                    conversation_messages.c.role,
                    conversation_messages.c.content,
                    conversation_messages.c.created_at,
                )
                .where(conversation_messages.c.phone == phone)
                .order_by(desc(conversation_messages.c.id))
                .limit(max(1, min(limit, 50)))
            )
            return [dict(row._mapping) for row in reversed(result.all())]

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

    async def get_salary(self, phone: str) -> int | None:
        async with self.sessions() as session:
            result = await session.execute(select(users.c.salary_cents).where(users.c.phone == phone))
            return result.scalar_one_or_none()

    async def set_salary(self, phone: str, salary_cents: int) -> None:
        async with self.sessions() as session:
            await session.execute(users.update().where(users.c.phone == phone).values(salary_cents=salary_cents, salary_alert_level=0, salary_alert_month=None))
            await session.commit()

    async def monthly_expense_total(self, phone: str, month_start: date, next_month: date) -> int:
        async with self.sessions() as session:
            result = await session.execute(select(transactions.c.amount_cents).where(transactions.c.phone == phone, transactions.c.type == "expense", transactions.c.occurred_on >= month_start, transactions.c.occurred_on < next_month))
            return sum(result.scalars())

    async def get_salary_alert(self, phone: str, month: str) -> int:
        async with self.sessions() as session:
            result = await session.execute(select(users.c.salary_alert_level, users.c.salary_alert_month).where(users.c.phone == phone))
            row = result.one_or_none()
            return int(row.salary_alert_level or 0) if row and row.salary_alert_month == month else 0

    async def set_salary_alert(self, phone: str, month: str, level: int) -> None:
        async with self.sessions() as session:
            await session.execute(users.update().where(users.c.phone == phone).values(salary_alert_level=level, salary_alert_month=month))
            await session.commit()

    async def save_confirmation(self, phone: str, message_id: str, data: dict[str, Any]) -> None:
        now = datetime.now(UTC)
        async with self.sessions() as session:
            await session.execute(insert(pending_confirmations).values(phone=phone, message_id=message_id, intent=data["intent"], amount_cents=round(float(data["amount"]) * 100), description=data.get("description") or "não informado", category=data.get("category") or "outros", payment_method=data.get("payment_method") or "não informado", confidence=str(data["confidence"]), status="pending", created_at=now, expires_at=now + timedelta(hours=24)))
            await session.commit()

    async def get_pending_confirmation(self, phone: str) -> dict[str, Any] | None:
        async with self.sessions() as session:
            result = await session.execute(select(pending_confirmations).where(pending_confirmations.c.phone == phone, pending_confirmations.c.status == "pending", pending_confirmations.c.expires_at > datetime.now(UTC)).order_by(desc(pending_confirmations.c.id)).limit(1))
            row = result.first()
            return dict(row._mapping) if row else None

    async def get_latest_confirmation(self, phone: str) -> dict[str, Any] | None:
        async with self.sessions() as session:
            result = await session.execute(
                select(pending_confirmations)
                .where(pending_confirmations.c.phone == phone)
                .order_by(desc(pending_confirmations.c.id))
                .limit(1)
            )
            row = result.first()
            return dict(row._mapping) if row else None

    async def confirm_pending_confirmation(self, confirmation_id: int, phone: str, occurred_on: date) -> dict[str, Any] | None:
        now = datetime.now(UTC)
        async with self.sessions() as session:
            result = await session.execute(
                select(pending_confirmations)
                .where(
                    pending_confirmations.c.id == confirmation_id,
                    pending_confirmations.c.phone == phone,
                    pending_confirmations.c.status == "pending",
                    pending_confirmations.c.expires_at > now,
                )
                .with_for_update()
            )
            row = result.first()
            if not row:
                return None
            pending = dict(row._mapping)
            await session.execute(
                insert(transactions).values(
                    phone=phone,
                    type="income" if pending["intent"] == "create_income" else "expense",
                    amount_cents=pending["amount_cents"],
                    description=pending["description"],
                    category=pending["category"],
                    payment_method=pending["payment_method"],
                    occurred_on=occurred_on,
                )
            )
            await session.execute(
                pending_confirmations.update()
                .where(
                    pending_confirmations.c.id == confirmation_id,
                    pending_confirmations.c.status == "pending",
                )
                .values(status="confirmed")
            )
            await session.commit()
            return pending

    async def expire_pending_confirmations(self, now: datetime) -> int:
        async with self.sessions() as session:
            result = await session.execute(
                pending_confirmations.update()
                .where(
                    pending_confirmations.c.status == "pending",
                    pending_confirmations.c.expires_at <= now,
                )
                .values(status="expired")
            )
            await session.commit()
            return result.rowcount or 0

    async def update_confirmation(self, confirmation_id: int, values: dict[str, Any]) -> None:
        async with self.sessions() as session:
            await session.execute(pending_confirmations.update().where(pending_confirmations.c.id == confirmation_id).values(**values))
            await session.commit()

    async def update_confirmation_status(self, confirmation_id: int, status: str) -> None:
        async with self.sessions() as session:
            await session.execute(pending_confirmations.update().where(pending_confirmations.c.id == confirmation_id).values(status=status))
            await session.commit()

    async def claim_delivery(self, delivery_key: str, kind: str, recipient: str, now: datetime) -> bool:
        async with self.sessions() as session:
            result = await session.execute(
                select(delivery_records)
                .where(delivery_records.c.delivery_key == delivery_key)
                .with_for_update()
            )
            row = result.first()
            if row and row.status == "sent":
                return False
            if row and row.status == "sending":
                claimed_at = row.created_at
                if claimed_at.tzinfo is None:
                    claimed_at = claimed_at.replace(tzinfo=UTC)
                current_time = now if now.tzinfo else now.replace(tzinfo=UTC)
                if current_time - claimed_at <= timedelta(minutes=10):
                    return False
            if row:
                await session.execute(
                    delivery_records.update()
                    .where(delivery_records.c.delivery_key == delivery_key)
                    .values(status="sending", attempts=delivery_records.c.attempts + 1, last_error=None)
                )
            else:
                await session.execute(
                    insert(delivery_records).values(
                        delivery_key=delivery_key,
                        kind=kind,
                        recipient=recipient,
                        status="sending",
                        attempts=1,
                        created_at=now,
                    )
                )
            await session.commit()
            return True

    async def complete_delivery(self, delivery_key: str, success: bool, completed_at: datetime, error: str | None = None) -> None:
        async with self.sessions() as session:
            await session.execute(
                delivery_records.update()
                .where(delivery_records.c.delivery_key == delivery_key)
                .values(
                    status="sent" if success else "failed",
                    last_error=error,
                    completed_at=completed_at,
                )
            )
            await session.commit()

    async def metrics_snapshot(self) -> dict[str, int]:
        async with self.sessions() as session:
            queries = {
                "users": select(func.count()).select_from(users),
                "transactions": select(func.count()).select_from(transactions),
                "processed_messages": select(func.count()).select_from(processed_messages),
                "pending_confirmations": select(func.count()).select_from(pending_confirmations).where(pending_confirmations.c.status == "pending"),
                "delivery_sent": select(func.count()).select_from(delivery_records).where(delivery_records.c.status == "sent"),
                "delivery_failed": select(func.count()).select_from(delivery_records).where(delivery_records.c.status == "failed"),
            }
            values = {}
            for name, query in queries.items():
                values[name] = int((await session.execute(query)).scalar_one())
            return values

    async def list_user_phones(self) -> list[str]:
        async with self.sessions() as session:
            result = await session.execute(select(users.c.phone).order_by(users.c.id))
            return list(result.scalars())

    async def list_recurring(self, phone: str) -> list[dict[str, Any]]:
        async with self.sessions() as session:
            result = await session.execute(
                select(
                    recurring_expenses.c.id,
                    recurring_expenses.c.description,
                    recurring_expenses.c.amount_cents,
                    recurring_expenses.c.category,
                    recurring_expenses.c.payment_method,
                    recurring_expenses.c.day_of_month,
                    recurring_expenses.c.active,
                )
                .where(recurring_expenses.c.phone == phone)
                .order_by(recurring_expenses.c.id)
            )
            return [dict(row._mapping) for row in result]

    async def add_recurring(self, phone: str, draft: Any) -> None:
        async with self.sessions() as session:
            await session.execute(insert(recurring_expenses).values(phone=phone, description=draft.description, amount_cents=round(draft.amount * 100), category=draft.category, payment_method=draft.payment_method, day_of_month=draft.day_of_month, active=True))
            await session.commit()

    async def add_installment(self, phone: str, draft: Any, first_due_on: date) -> None:
        cents = round(draft.total_amount * 100)
        async with self.sessions() as session:
            await session.execute(insert(installments).values(phone=phone, description=draft.description, total_cents=cents, installment_cents=round(cents / draft.installment_count), total_count=draft.installment_count, current_count=0, category="outros", payment_method=draft.payment_method, first_due_on=first_due_on, active=True))
            await session.commit()

    async def set_budget(self, phone: str, draft: Any, month: str) -> None:
        async with self.sessions() as session:
            await session.execute(insert(budgets).values(phone=phone, category=draft.category, limit_cents=round(draft.limit_amount * 100), month=month))
            await session.commit()

    async def budget_status(self, phone: str, category: str, month_start: date, next_month: date, month: str) -> tuple[int, int] | None:
        async with self.sessions() as session:
            result = await session.execute(select(budgets.c.limit_cents).where(budgets.c.phone == phone, budgets.c.category == category, budgets.c.month == month))
            limit = result.scalar_one_or_none()
            if limit is None:
                return None
            spent_result = await session.execute(select(transactions.c.amount_cents).where(transactions.c.phone == phone, transactions.c.type == "expense", transactions.c.category == category, transactions.c.occurred_on >= month_start, transactions.c.occurred_on < next_month))
            return int(limit), sum(spent_result.scalars())

    async def add_reminder(self, phone: str, draft: Any, source_message_id: str | None = None) -> None:
        async with self.sessions() as session:
            await session.execute(
                insert(reminders).values(
                    phone=phone,
                    message=draft.message,
                    due_at=draft.due_at,
                    sent=False,
                    source_message_id=source_message_id,
                )
            )
            await session.commit()

    async def due_reminders(self, now: Any) -> list[dict[str, Any]]:
        async with self.sessions() as session:
            result = await session.execute(select(reminders).where(reminders.c.due_at <= now, reminders.c.sent.is_(False)))
            return [dict(row._mapping) for row in result]

    async def mark_reminder_sent(self, reminder_id: int) -> None:
        async with self.sessions() as session:
            await session.execute(reminders.update().where(reminders.c.id == reminder_id).values(sent=True))
            await session.commit()

    async def generate_installments(self, current_day: date) -> list[dict[str, Any]]:
        created = []
        async with self.sessions() as session:
            result = await session.execute(select(installments).where(installments.c.active.is_(True), installments.c.first_due_on <= current_day))
            for purchase in result:
                months = (current_day.year - purchase.first_due_on.year) * 12 + current_day.month - purchase.first_due_on.month
                number = months + 1
                if number < 1 or number > purchase.total_count:
                    continue
                due_on = current_day.replace(day=min(purchase.first_due_on.day, 28))
                existing = await session.execute(select(transactions.c.id).where(transactions.c.phone == purchase.phone, transactions.c.description.like(f"{purchase.description} (%)"), transactions.c.occurred_on == due_on))
                if existing.scalar_one_or_none() is not None:
                    continue
                await session.execute(insert(transactions).values(phone=purchase.phone, type="expense", amount_cents=purchase.installment_cents, description=f"{purchase.description} ({number}/{purchase.total_count})", category=purchase.category, payment_method=purchase.payment_method, occurred_on=due_on))
                await session.execute(installments.update().where(installments.c.id == purchase.id).values(current_count=number, active=number < purchase.total_count))
                created.append({"phone": purchase.phone, "description": purchase.description, "installment": number, "total": purchase.total_count, "amount_cents": purchase.installment_cents})
            await session.commit()
        return created

    async def generate_recurring(self, current_day: date) -> list[dict[str, Any]]:
        created = []
        async with self.sessions() as session:
            result = await session.execute(select(recurring_expenses).where(recurring_expenses.c.active.is_(True), recurring_expenses.c.day_of_month == current_day.day))
            for recurring in result:
                existing = await session.execute(select(transactions.c.id).where(transactions.c.phone == recurring.phone, transactions.c.description == recurring.description, transactions.c.occurred_on == current_day))
                if existing.scalar_one_or_none() is not None:
                    continue
                await session.execute(insert(transactions).values(phone=recurring.phone, type="expense", amount_cents=recurring.amount_cents, description=recurring.description, category=recurring.category, payment_method=recurring.payment_method, occurred_on=current_day))
                created.append({"phone": recurring.phone, "description": recurring.description, "amount_cents": recurring.amount_cents})
            await session.commit()
        return created

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

    async def expense_summary(self, phone: str, start: date, end: date, category: str | None = None, payment_method: str | None = None) -> tuple[int, int]:
        async with self.sessions() as session:
            conditions = [transactions.c.phone == phone, transactions.c.type == "expense", transactions.c.occurred_on >= start, transactions.c.occurred_on < end]
            if category:
                conditions.append(transactions.c.category == category)
            if payment_method:
                conditions.append(transactions.c.payment_method == payment_method)
            result = await session.execute(select(transactions.c.amount_cents).where(*conditions))
            amounts = list(result.scalars())
            return len(amounts), sum(amounts)

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

    async def get_latest_transaction(self, phone: str) -> dict[str, Any] | None:
        async with self.sessions() as session:
            result = await session.execute(
                select(
                    transactions.c.id,
                    transactions.c.type,
                    transactions.c.amount_cents,
                    transactions.c.description,
                    transactions.c.category,
                    transactions.c.payment_method,
                    transactions.c.occurred_on,
                )
                .where(transactions.c.phone == phone)
                .order_by(desc(transactions.c.id))
                .limit(1)
            )
            row = result.first()
            return dict(row._mapping) if row else None

    async def update_transaction(self, phone: str, transaction_id: int, values: dict[str, Any]) -> None:
        async with self.sessions() as session:
            await session.execute(
                transactions.update()
                .where(transactions.c.phone == phone, transactions.c.id == transaction_id)
                .values(**values)
            )
            await session.commit()

    async def list_transactions(self, phone: str) -> list[dict[str, Any]]:
        async with self.sessions() as session:
            result = await session.execute(
                select(
                    transactions.c.type,
                    transactions.c.amount_cents,
                    transactions.c.description,
                    transactions.c.category,
                    transactions.c.payment_method,
                    transactions.c.occurred_on,
                )
                .where(transactions.c.phone == phone)
                .order_by(desc(transactions.c.id))
            )
            return [dict(row._mapping) for row in result]

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
