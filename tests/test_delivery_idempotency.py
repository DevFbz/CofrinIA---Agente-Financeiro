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
