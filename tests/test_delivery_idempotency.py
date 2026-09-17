from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine

from app.infrastructure.repository import FinanceRepository
from app.services.finance import FinanceService


@pytest_asyncio.fixture
async def repository(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'finance.db'}")
    repository = FinanceRepository(engine)
    await repository.initialize()
    yield repository
    await repository.close()


@pytest.mark.asyncio
async def test_delivery_key_is_claimed_only_once_after_success(repository):
    now = datetime(2026, 9, 9, tzinfo=UTC)
    key = "report:daily:5511999999999:2026-09-09"

    first = await repository.claim_delivery(key, "report", "5511999999999", now)
    second = await repository.claim_delivery(key, "report", "5511999999999", now)
    await repository.complete_delivery(key, success=True, completed_at=now)
    third = await repository.claim_delivery(key, "report", "5511999999999", now)

    assert first is True
    assert second is False
    assert third is False


@pytest.mark.asyncio
async def test_send_reply_does_not_send_same_delivery_twice(repository, monkeypatch):
    from app import main

    class FakeResponse:
        status_code = 201

        def raise_for_status(self):
            return None

        def json(self):
            return {"key": {"id": "outbound-1"}}

    class FakeClient:
        calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            self.calls += 1
            return FakeResponse()

    client = FakeClient()
    original_service = main.finance_service
    main.finance_service = FinanceService(repository)
    monkeypatch.setenv("EVOLUTION_API_URL", "http://evolution")
    monkeypatch.setenv("EVOLUTION_API_KEY", "test-key")
    monkeypatch.setenv("EVOLUTION_INSTANCE", "financeiro")
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda **kwargs: client)
    try:
        first = await main._send_reply(
            "5511999999999@s.whatsapp.net",
            "mensagem",
            delivery_key="reminder:1",
            delivery_kind="reminder",
        )
        second = await main._send_reply(
            "5511999999999@s.whatsapp.net",
            "mensagem",
            delivery_key="reminder:1",
            delivery_kind="reminder",
        )
    finally:
        main.finance_service = original_service

    assert first is True
    assert second is True
    assert client.calls == 1


@pytest.mark.asyncio
async def test_stale_delivery_claim_can_be_recovered(repository):
    first_time = datetime(2026, 9, 9, tzinfo=UTC)
    retry_time = first_time + timedelta(minutes=11)
    key = "report:weekly:5511999999999:2026-09-09"

    first = await repository.claim_delivery(key, "report", "5511999999999", first_time)
    recovered = await repository.claim_delivery(key, "report", "5511999999999", retry_time)

    assert first is True
    assert recovered is True


@pytest.mark.asyncio
async def test_repeating_reminder_advances_hourly_until_deadline(repository):
    start = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
    task_id = await repository.create_task_and_reminder(
        "5511999999999",
        "Comprar o presente",
        start + timedelta(hours=1),
        source_message_id="repeat-source-1",
        repeat_interval_minutes=60,
        repeat_until=start + timedelta(hours=3),
    )

    first_due = await repository.due_reminders(start + timedelta(hours=1))
    reminder_id = first_due[0]["id"]
    assert first_due[0]["task_id"] == task_id
    await repository.mark_reminder_sent(reminder_id)

    second_due = await repository.due_reminders(start + timedelta(hours=2))
    assert second_due[0]["id"] == reminder_id
    assert second_due[0]["due_at"].replace(tzinfo=UTC) == start + timedelta(hours=2)
    await repository.mark_reminder_sent(reminder_id)

    final_due = await repository.due_reminders(start + timedelta(hours=3))
    assert final_due[0]["id"] == reminder_id
    await repository.mark_reminder_sent(reminder_id)
    assert await repository.due_reminders(start + timedelta(hours=4)) == []


@pytest.mark.asyncio
async def test_send_reply_includes_native_whatsapp_quote(repository, monkeypatch):
    from app import main

    class FakeResponse:
        status_code = 201

        def raise_for_status(self):
            return None

        def json(self):
            return {"key": {"id": "outbound-quote"}}

    class FakeClient:
        payload = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, _url, **kwargs):
            self.payload = kwargs["json"]
            return FakeResponse()

    client = FakeClient()
    original_service = main.finance_service
    main.finance_service = FinanceService(repository)
    monkeypatch.setenv("EVOLUTION_API_URL", "http://evolution")
    monkeypatch.setenv("EVOLUTION_API_KEY", "test-key")
    monkeypatch.setenv("EVOLUTION_INSTANCE", "financeiro")
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda **kwargs: client)
    try:
        result = await main._send_reply(
            "5511999999999@s.whatsapp.net",
            "Entendi sua mensagem.",
            delivery_key="reply:quote-1",
            delivery_kind="inbound_reply",
            quoted_message={"remoteJid": "5511999999999@s.whatsapp.net", "fromMe": False, "id": "inbound-1"},
            quoted_text="Pode me ajudar?",
        )
    finally:
        main.finance_service = original_service

    assert result is True
    assert client.payload["quoted"] == {
        "key": {"remoteJid": "5511999999999@s.whatsapp.net", "fromMe": False, "id": "inbound-1"},
        "message": {"conversation": "Pode me ajudar?"},
    }
