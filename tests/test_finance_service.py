from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine

from app.infrastructure.repository import FinanceRepository
from app.services.finance import FinanceService


@pytest_asyncio.fixture
async def service(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'finance.db'}")
    repository = FinanceRepository(engine)
    await repository.initialize()
    finance = FinanceService(repository)
    yield finance
    await finance.close()


@pytest.mark.asyncio
async def test_registers_expense_with_warm_confirmation(service):
    result = await service.process_message(
        "Gastei R$ 42,50 no almoço",
        phone="5511999999001",
        today=date(2026, 9, 7),
    )

    assert result.transaction.type == "expense"
    assert result.transaction.amount == pytest.approx(42.50)
    assert "✅" in result.reply
    assert "Alimentação" in result.reply
    assert "R$ 42,50" in result.reply


@pytest.mark.asyncio
async def test_registration_reply_includes_payment_method(service):
    result = await service.process_message(
        "Gastei R$ 127,50 no Mercado no cartão Nubank",
        phone="5511999999007",
    )

    assert result.transaction.payment_method == "Cartão Nubank"
    assert "💳 Pagamento: *Cartão Nubank*" in result.reply


@pytest.mark.asyncio
async def test_first_message_introduces_assistant_and_teaches_basic_commands(service):
    result = await service.process_message("Oi", phone="5511999999002")

    assert result.transaction is None
    assert result.reply.count("Eu sou o *Cofrin*") == 1
    assert "Gastei R$ 32 no almoço" in result.reply


@pytest.mark.asyncio
async def test_count_query_lists_expenses_and_total(service):
    phone = "5511999999003"
    await service.process_message("Gastei R$ 32 no almoço", phone=phone)
    await service.process_message("Gastei R$ 18 no lanche", phone=phone)

    result = await service.process_message("Quantas despesas eu tenho?", phone=phone)

    assert result.transaction is None
    assert "2 despesas" in result.reply
    assert "R$ 50,00" in result.reply
    assert "almoço" in result.reply
    assert "lanche" in result.reply
    assert "não consegui registrar" not in result.reply.lower()


@pytest.mark.asyncio
async def test_count_query_uses_singular_for_one_expense(service):
    phone = "5511999999005"
    await service.process_message("Gastei R$ 32 no almoço", phone=phone)

    result = await service.process_message("Quantas despesas eu tenho?", phone=phone)

    assert "1 despesa registrada" in result.reply
    assert "1 despesas" not in result.reply


@pytest.mark.asyncio
async def test_query_without_transactions_explains_that_there_is_no_data(service):
    result = await service.process_message("Quanto gastei este mês?", phone="5511999999004")

    assert result.transaction is None
    assert "ainda não encontrei despesas" in result.reply.lower()
    assert "não consegui registrar" not in result.reply.lower()


@pytest.mark.asyncio
async def test_message_id_is_idempotent(service):
    phone = "5511999999006"
    first = await service.process_message("Gastei R$ 32 no almoço", phone=phone, message_id="wa-1")
    second = await service.process_message("Gastei R$ 32 no almoço", phone=phone, message_id="wa-1")

    assert first.transaction is not None
    assert second.duplicate is True
    result = await service.process_message("Quantas despesas eu tenho?", phone=phone)
    assert "1 despesa registrada" in result.reply
