import logging
from datetime import UTC, date, datetime

import pytest

from app import worker


class FakeRepository:
    def __init__(self):
        self.calls = []

    async def initialize(self):
        self.calls.append("initialize")

    async def expire_pending_confirmations(self, now):
        self.calls.append(("expire", now))
        return 2

    async def generate_recurring(self, current_day):
        self.calls.append(("recurring", current_day))
        return [{"description": "internet"}]

    async def generate_installments(self, current_day):
        self.calls.append(("installments", current_day))
        return [{"description": "celular"}]


@pytest.mark.asyncio
async def test_worker_runs_housekeeping_and_generation_cycle():
    repository = FakeRepository()
    worker.logger.setLevel(logging.INFO)
    current_day = date(2026, 9, 9)
    now = datetime(2026, 9, 9, 12, tzinfo=UTC)

    result = await worker.run_once(repository, current_day=current_day, now=now)

    assert result == {"expired": 2, "recurring": 1, "installments": 1}
    assert repository.calls == [
        "initialize",
        ("expire", now),
        ("recurring", current_day),
        ("installments", current_day),
    ]


@pytest.mark.asyncio
async def test_worker_dispatches_task_digest_at_17_brazil_time(monkeypatch):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, endpoint, headers):
            calls.append((endpoint, headers))
            return FakeResponse()

    monkeypatch.setenv("COFRIN_INTERNAL_TOKEN", "test-token")
    monkeypatch.setenv("APP_INTERNAL_URL", "http://app:8000")
    monkeypatch.setattr(worker.httpx, "AsyncClient", lambda **kwargs: FakeClient())

    result = await worker.dispatch_task_digest_if_due(datetime(2026, 9, 12, 20, 0, tzinfo=UTC))

    assert result is True
    assert calls == [("http://app:8000/internal/tasks/digest/dispatch", {"X-Internal-Token": "test-token"})]
