from datetime import UTC, date, datetime, timedelta

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
async def test_registration_reply_includes_real_date_and_health_category(service):
    result = await service.process_message(
        "Gastei 21 reais na farmácia no crédito",
        phone="5511999999010",
        today=date(2026, 9, 8),
    )

    assert "Saúde" in result.reply
    assert "farmácia" in result.reply
    assert "Data: 08/09/2026" in result.reply
    assert "Data: hoje" not in result.reply


@pytest.mark.asyncio
async def test_saves_salary_and_alerts_once_at_each_ten_percent(service):
    phone = "5511999999011"
    salary = await service.process_message("Meu salário é R$ 1.000", phone=phone, today=date(2026, 9, 8))
    assert salary.transaction is None
    assert "Salário mensal salvo" in salary.reply

    first = await service.process_message("Gastei R$ 100 no almoço", phone=phone, today=date(2026, 9, 8))
    second = await service.process_message("Gastei R$ 100 no lanche", phone=phone, today=date(2026, 9, 8))

    assert "10,0%" in first.reply
    assert "20,0%" in second.reply
    assert await service.repository.get_salary(phone) == 100000


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


@pytest.mark.asyncio
async def test_pending_confirmation_accepts_category_correction(service, monkeypatch):
    class FakeHermes:
        async def interpret(self, phone, message):
            return {
                "intent": "create_expense",
                "amount": 240.0,
                "description": "compra",
                "category": "outros",
                "payment_method": "Cartão",
                "confidence": 0.7,
                "requires_confirmation": True,
            }

    monkeypatch.setattr("app.services.finance.HermesInterpreter", FakeHermes)
    phone = "5511999999020"

    pending_reply = await service.process_message("Uma compra estranha", phone=phone, message_id="ambiguous-1")
    assert "confirmar" in pending_reply.reply

    correction_reply = await service.process_message(
        "corrigir categoria alimentação",
        phone=phone,
        message_id="correction-1",
    )

    assert "corrigi" in correction_reply.reply.lower()
    pending = await service.repository.get_pending_confirmation(phone)
    assert pending["category"] == "alimentacao"


@pytest.mark.asyncio
async def test_expire_pending_confirmation_changes_status(service, monkeypatch):
    class FakeHermes:
        async def interpret(self, phone, message):
            return {
                "intent": "create_expense",
                "amount": 100.0,
                "description": "compra",
                "category": "outros",
                "payment_method": "não informado",
                "confidence": 0.7,
                "requires_confirmation": True,
            }

    monkeypatch.setattr("app.services.finance.HermesInterpreter", FakeHermes)
    phone = "5511999999021"
    await service.process_message("Uma compra estranha", phone=phone, message_id="ambiguous-2")
    pending = await service.repository.get_pending_confirmation(phone)
    await service.repository.update_confirmation(
        pending["id"],
        {"expires_at": datetime.now(UTC) - timedelta(minutes=1)},
    )

    expired = await service.repository.expire_pending_confirmations(datetime.now(UTC))

    assert expired == 1
    assert await service.repository.get_pending_confirmation(phone) is None


@pytest.mark.asyncio
async def test_confirmation_command_without_pending_does_not_call_hermes(service, monkeypatch):
    calls = 0

    class FakeHermes:
        async def interpret(self, phone, message):
            nonlocal calls
            calls += 1
            return {
                "intent": "create_expense",
                "amount": 100.0,
                "description": "compra",
                "category": "outros",
                "payment_method": "não informado",
                "confidence": 0.7,
                "requires_confirmation": True,
            }

    monkeypatch.setattr("app.services.finance.HermesInterpreter", FakeHermes)
    phone = "5511999999022"
    await service.process_message("Uma compra estranha", phone=phone, message_id="ambiguous-3")
    confirmed = await service.process_message("confirmar", phone=phone, message_id="confirmation-3")
    repeated = await service.process_message("confirmar", phone=phone, message_id="confirmation-4")

    assert confirmed.transaction is not None
    assert "nenhum" in repeated.reply.lower() or "não há" in repeated.reply.lower()
    assert calls == 1
    assert await service.repository.count_expenses(phone) == (1, 10000)


@pytest.mark.asyncio
async def test_confirm_pending_is_atomic_and_idempotent(service, monkeypatch):
    class FakeHermes:
        async def interpret(self, phone, message):
            return {
                "intent": "create_expense",
                "amount": 75.0,
                "description": "compra",
                "category": "outros",
                "payment_method": "Pix",
                "confidence": 0.7,
                "requires_confirmation": True,
            }

    monkeypatch.setattr("app.services.finance.HermesInterpreter", FakeHermes)
    phone = "5511999999023"
    await service.process_message("Uma compra estranha", phone=phone, message_id="ambiguous-4")
    pending = await service.repository.get_pending_confirmation(phone)

    first = await service.repository.confirm_pending_confirmation(pending["id"], phone, date(2026, 9, 9))
    second = await service.repository.confirm_pending_confirmation(pending["id"], phone, date(2026, 9, 9))

    assert first["id"] == pending["id"]
    assert second is None
    assert await service.repository.count_expenses(phone) == (1, 7500)


@pytest.mark.asyncio
async def test_expired_confirmation_is_not_confirmed(service, monkeypatch):
    class FakeHermes:
        async def interpret(self, phone, message):
            return {
                "intent": "create_expense",
                "amount": 80.0,
                "description": "compra",
                "category": "outros",
                "payment_method": "Pix",
                "confidence": 0.7,
                "requires_confirmation": True,
            }

    monkeypatch.setattr("app.services.finance.HermesInterpreter", FakeHermes)
    phone = "5511999999024"
    await service.process_message("Uma compra estranha", phone=phone, message_id="ambiguous-5")
    pending = await service.repository.get_pending_confirmation(phone)
    await service.repository.update_confirmation(
        pending["id"],
        {"expires_at": datetime.now(UTC) - timedelta(minutes=1)},
    )

    result = await service.process_message("confirmar", phone=phone, message_id="confirmation-5")

    assert result.transaction is None
    assert "expirou" in result.reply.lower()
    assert await service.repository.count_expenses(phone) == (0, 0)


@pytest.mark.asyncio
async def test_ambiguous_transaction_with_amount_requires_confirmation(service, monkeypatch):
    calls = 0

    class FakeHermes:
        async def interpret(self, phone, message):
            nonlocal calls
            calls += 1
            return {
                "intent": "create_expense",
                "amount": 240.0,
                "description": "uma coisa",
                "category": "outros",
                "payment_method": "Cartão",
                "confidence": 0.7,
                "requires_confirmation": True,
            }

    monkeypatch.setattr("app.services.finance.HermesInterpreter", FakeHermes)
    phone = "5511999999025"

    result = await service.process_message(
        "Gastei por volta de R$ 240 em uma coisa no cartão",
        phone=phone,
        message_id="ambiguous-6",
    )

    assert result.transaction is None
    assert "confirmar" in result.reply.lower()
    assert calls == 1
    assert await service.repository.count_expenses(phone) == (0, 0)


@pytest.mark.asyncio
async def test_category_correction_updates_last_registered_transaction(service):
    phone = "5511999999026"
    await service.process_message("Gastei R$ 25 em uma compra", phone=phone, message_id="image-like-1")

    result = await service.process_message(
        "corrigir categoria para alimentação",
        phone=phone,
        message_id="correction-last-1",
    )

    rows = await service.repository.list_transactions(phone)
    assert "atualizei" in result.reply.lower() or "corrigi" in result.reply.lower()
    assert rows[0]["category"] == "alimentacao"


@pytest.mark.asyncio
async def test_category_report_filters_requested_category(service):
    phone = "5511999999027"
    await service.process_message("Gastei R$ 30 no almoço", phone=phone, message_id="category-report-1")
    await service.process_message("Gastei R$ 20 na gasolina", phone=phone, message_id="category-report-2")

    result = await service.process_message("relatório de alimentação", phone=phone, message_id="category-report-3")

    assert "Alimentação" in result.reply
    assert "R$ 30,00" in result.reply
    assert "R$ 50,00" not in result.reply


@pytest.mark.asyncio
async def test_recurring_payment_query_lists_user_recurring_expenses(service):
    phone = "5511999999028"
    await service.process_message("internet de R$ 100 todo dia 10", phone=phone, message_id="recurring-create-1")

    result = await service.process_message("consultar pagamentos recorrentes", phone=phone, message_id="recurring-query-1")

    assert "internet" in result.reply.lower()
    assert "recorr" in result.reply.lower()


@pytest.mark.asyncio
async def test_hermes_query_intent_returns_category_report(service, monkeypatch):
    class FakeHermes:
        async def interpret(self, phone, message):
            return {
                "intent": "query_category",
                "amount": None,
                "description": None,
                "category": "alimentacao",
                "payment_method": None,
                "confidence": 0.95,
                "requires_confirmation": False,
                "reply": None,
            }

    monkeypatch.setattr("app.services.finance.HermesInterpreter", FakeHermes)
    phone = "5511999999029"
    await service.process_message("Gastei R$ 30 no almoço", phone=phone, message_id="hermes-query-1")

    result = await service.process_message("Como estão meus gastos com alimentação?", phone=phone, message_id="hermes-query-2")

    assert "R$ 30,00" in result.reply
    assert "Alimentação" in result.reply


@pytest.mark.asyncio
async def test_hermes_reply_is_used_for_general_question(service, monkeypatch):
    class FakeHermes:
        async def interpret(self, phone, message):
            return {
                "intent": "unknown",
                "amount": None,
                "description": None,
                "category": None,
                "payment_method": None,
                "confidence": 0.9,
                "requires_confirmation": False,
                "reply": "Posso ajudar com seus gastos, relatórios e pagamentos recorrentes.",
            }

    monkeypatch.setattr("app.services.finance.HermesInterpreter", FakeHermes)

    result = await service.process_message("Qual a melhor forma de organizar minhas finanças?", phone="5511999999030", message_id="hermes-reply-1")

    assert "organizar" not in result.reply.lower()
    assert "pagamentos recorrentes" in result.reply.lower()
