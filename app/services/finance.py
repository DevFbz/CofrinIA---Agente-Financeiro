from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from app.domain.transactions import TransactionDraft, parse_transaction_text
from app.infrastructure.repository import FinanceRepository

logger = logging.getLogger(__name__)
BRAZIL_TIMEZONE = timezone(timedelta(hours=-3))


@dataclass(frozen=True)
class FinanceResult:
    reply: str
    transaction: TransactionDraft | None
    duplicate: bool = False


_CATEGORY_LABELS = {
    "alimentacao": "Alimentação",
    "transporte": "Transporte",
    "moradia": "Moradia",
    "lazer": "Lazer",
    "salario": "Salário",
    "outros": "Outros",
}

_CATEGORY_ICONS = {
    "alimentacao": "🍽️",
    "transporte": "🚗",
    "moradia": "🏠",
    "lazer": "🎮",
    "salario": "💼",
    "outros": "📌",
}


class FinanceService:
    def __init__(self, repository: FinanceRepository | None = None) -> None:
        self.repository = repository or FinanceRepository()
        self._initialized = False
        self._initialization_lock = asyncio.Lock()

    async def initialize(self) -> None:
        if self._initialized:
            return
        async with self._initialization_lock:
            if not self._initialized:
                await self.repository.initialize()
                self._initialized = True

    async def close(self) -> None:
        await self.repository.close()

    async def process_message(
        self,
        message: str,
        phone: str,
        today: date | None = None,
        message_id: str | None = None,
    ) -> FinanceResult:
        await self.initialize()
        first_message = await self.repository.ensure_user(phone)
        if not await self.repository.mark_message_once(message_id, phone):
            return FinanceResult("", None, duplicate=True)

        try:
            current_day = today or datetime.now(BRAZIL_TIMEZONE).date()
            normalized = self._normalize(message)
            await self.repository.mark_welcomed(phone)

            if self._is_greeting(normalized):
                return FinanceResult(self._welcome_message(), None)

            if self._is_count_query(normalized):
                reply = await self._count_expenses(phone)
                return FinanceResult(self._with_welcome(reply, first_message), None)

            if self._is_spending_query(normalized):
                reply = await self._spending_summary(phone)
                return FinanceResult(self._with_welcome(reply, first_message), None)

            transaction = parse_transaction_text(message)
            await self.repository.add_transaction(phone, transaction, current_day)
            reply = self._registration_reply(transaction)
            return FinanceResult(self._with_welcome(reply, first_message), transaction)
        except ValueError as exc:
            reply = (
                "🤔 Posso registrar sua movimentação, mas preciso do valor.\n\n"
                f"{exc}.\n\n"
                "Exemplo: *Gastei R$ 42 no almoço*."
            )
            return FinanceResult(self._with_welcome(reply, first_message), None)
        except Exception:
            logger.exception("unexpected finance message processing failure")
            reply = (
                "⚠️ Tive uma pequena dificuldade para concluir isso agora.\n\n"
                "Seus dados não foram alterados. Tente novamente em alguns instantes."
            )
            return FinanceResult(self._with_welcome(reply, first_message), None)

    @staticmethod
    def _normalize(message: str) -> str:
        return re.sub(r"\s+", " ", (message or "").strip().casefold())

    @staticmethod
    def _is_greeting(message: str) -> bool:
        return message in {"oi", "olá", "ola", "olá!", "ola!", "bom dia", "boa tarde", "boa noite", "menu", "ajuda"}

    @staticmethod
    def _is_count_query(message: str) -> bool:
        return (
            "quantas despesas" in message
            or "quantos gastos" in message
            or "número de despesas" in message
            or "numero de despesas" in message
        )

    @staticmethod
    def _is_spending_query(message: str) -> bool:
        query_terms = ("quanto gastei", "quanto eu gastei", "resumo", "relatório", "relatorio", "meus gastos")
        return any(term in message for term in query_terms)

    async def _count_expenses(self, phone: str) -> str:
        count, cents = await self.repository.count_expenses(phone)
        if not count:
            return (
                "📭 Ainda não encontrei despesas registradas para você.\n\n"
                "Quando quiser, envie algo como: *Gastei R$ 40 no almoço*."
            )
        noun = "despesa" if count == 1 else "despesas"
        verb = "registrada" if count == 1 else "registradas"
        entries = await self.repository.list_expenses(phone)
        lines = [
            f"{index}. {self._category_icon(entry['category'])} {entry['description']} — *{self._money(entry['amount_cents'] / 100)}*"
            for index, entry in enumerate(entries, start=1)
        ]
        listing = "\n".join(lines)
        suffix = "" if len(entries) == count else f"\n\nMostrando os {len(entries)} lançamentos mais recentes."
        return (
            f"📊 Você tem *{count} {noun} {verb}*, somando *{self._money(cents / 100)}*.\n\n"
            f"🧾 *Seus lançamentos:*\n{listing}{suffix}"
        )

    async def _spending_summary(self, phone: str) -> str:
        count, cents = await self.repository.count_expenses(phone)
        if not count:
            return (
                "📭 Ainda não encontrei despesas registradas para este período.\n\n"
                "Você pode começar enviando: *Gastei R$ 40 no almoço*."
            )
        return (
            "📊 *Resumo dos seus gastos*\n\n"
            f"💸 Total de despesas: *{self._money(cents / 100)}*\n"
            f"🧾 Lançamentos: *{count}*\n\n"
            "Se quiser, posso mostrar suas despesas recentes ou separar por categoria."
        )

    def _registration_reply(self, transaction: TransactionDraft) -> str:
        label = "Receita" if transaction.type == "income" else "Despesa"
        category = _CATEGORY_LABELS.get(transaction.category, "Outros")
        icon = _CATEGORY_ICONS.get(transaction.category, "📌")
        description = transaction.description if transaction.description != "não informado" else "não informada"
        return (
            f"✅ *{label} registrada com sucesso!*\n\n"
            f"💰 Valor: *{self._money(transaction.amount)}*\n"
            f"{icon} Categoria: *{category}*\n"
            f"📝 Descrição: {description}\n"
            f"💳 Pagamento: *{transaction.payment_method}*\n"
            "📅 Data: hoje\n\n"
            "Tudo certo por aqui 😊"
        )

    @staticmethod
    def _category_icon(category: str) -> str:
        return _CATEGORY_ICONS.get(category, "📌")

    @staticmethod
    def _money(value: float) -> str:
        return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    @staticmethod
    def _welcome_message() -> str:
        return (
            "👋 Oi! Eu sou o *Cofrin*, seu assistente financeiro 💰\n\n"
            "Posso registrar gastos e receitas, consultar seus números e montar resumos.\n\n"
            "Experimente:\n"
            "• *Gastei R$ 32 no almoço*\n"
            "• *Quanto gastei este mês?*\n"
            "• *Quantas despesas eu tenho?*\n"
            "• *Gere meu relatório*\n\n"
            "Pode falar comigo de forma natural 😊"
        )

    @staticmethod
    def _with_welcome(reply: str, first_message: bool) -> str:
        if not first_message:
            return reply
        return f"{FinanceService._welcome_message()}\n\n━━━━━━━━━━━━\n\n{reply}"
