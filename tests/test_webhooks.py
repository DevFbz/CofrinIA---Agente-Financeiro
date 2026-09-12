from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient

from app.domain.transactions import TransactionDraft
from app.main import app
from app.services.finance import FinanceResult


def _payload(from_me: bool = False):
    return {
        "event": "messages.upsert",
        "data": {
            "key": {"remoteJid": "5511999999999@s.whatsapp.net", "fromMe": from_me, "id": f"msg-{uuid4()}"},
            "message": {"conversation": "gastei 42 no almoco"},
        },
    }


def test_accepts_event_specific_evolution_webhook_path():
    response = TestClient(app).post("/webhooks/evolution/messages-upsert", json=_payload())

    assert response.status_code == 200
    assert response.json()["status"] == "processed"


def test_ignores_messages_sent_by_the_bot():
    response = TestClient(app).post("/webhooks/evolution", json=_payload(from_me=True))

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


def test_accepts_evolution_static_secret(monkeypatch):
    monkeypatch.setenv("EVOLUTION_WEBHOOK_SECRET", "test-secret")

    response = TestClient(app).post(
        "/webhooks/evolution/messages-upsert",
        json=_payload(),
        headers={"x-evolution-webhook-secret": "test-secret"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "processed"


def test_audio_without_transcription_config_returns_friendly_message(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    payload = {
        "event": "messages.upsert",
        "data": {
            "key": {"remoteJid": "5511999999999@s.whatsapp.net", "fromMe": False, "id": f"audio-{uuid4()}"},
            "message": {"audioMessage": {"mimetype": "audio/ogg"}},
        },
    }

    response = TestClient(app).post("/webhooks/evolution/messages-upsert", json=payload)

    assert response.status_code == 200
    assert response.json()["status"] == "audio_unavailable"
    assert "não consegui interpretá-lo" in response.json()["reply"]


def test_image_without_ocr_connection_returns_friendly_message():
    payload = {
        "event": "messages.upsert",
        "data": {
            "key": {"remoteJid": "5511999999999@s.whatsapp.net", "fromMe": False, "id": f"image-{uuid4()}"},
            "message": {"imageMessage": {"mimetype": "image/jpeg"}},
        },
    }

    response = TestClient(app).post("/webhooks/evolution/messages-upsert", json=payload)

    assert response.status_code == 200
    assert response.json()["status"] == "image_unavailable"
    assert "comprovante" in response.json()["reply"].lower()


def test_pdf_document_is_ignored():
    payload = {
        "event": "messages.upsert",
        "data": {
            "key": {"remoteJid": "5511999999998@s.whatsapp.net", "fromMe": False, "id": f"pdf-{uuid4()}"},
            "message": {"documentMessage": {"mimetype": "application/pdf", "fileName": "nota.pdf"}},
        },
    }

    response = TestClient(app).post("/webhooks/evolution/messages-upsert", json=payload)

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


def test_metrics_endpoint_exposes_operational_counters():
    response = TestClient(app).get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "cofrinia_up 1" in response.text
    assert "cofrinia_transactions_total" in response.text
    assert "cofrinia_pending_confirmations" in response.text


def test_audio_message_is_transcribed_and_sent_to_finance(monkeypatch):
    transcribed = []
    sent = []

    class FakeAudioTranscriber:
        async def transcribe(self, message_key):
            transcribed.append(message_key)
            return "Gastei trinta reais no almoço via Pix"

    async def fake_process_message(message, phone, message_id=None):
        assert message == "Gastei trinta reais no almoço via Pix"
        assert phone == "5511999999997"
        return FinanceResult(
            "✅ Despesa registrada",
            TransactionDraft("expense", 30.0, "almoço", "alimentacao", "Pix"),
        )

    async def fake_send_reply(*args, **kwargs):
        sent.append((args, kwargs))
        return True

    monkeypatch.setattr("app.main.AudioTranscriber", FakeAudioTranscriber)
    monkeypatch.setattr("app.main.finance_service.process_message", fake_process_message)
    monkeypatch.setattr("app.main._send_reply", fake_send_reply)
    payload = {
        "event": "messages.upsert",
        "data": {
            "key": {"remoteJid": "5511999999997@s.whatsapp.net", "fromMe": False, "id": f"audio-{uuid4()}"},
            "message": {"audioMessage": {"mimetype": "audio/ogg"}},
        },
    }

    response = TestClient(app).post("/webhooks/evolution/messages-upsert", json=payload)

    assert response.status_code == 200
    assert response.json()["status"] == "processed"
    assert transcribed and sent[0][0][1] == "✅ Despesa registrada"


def test_task_digest_dispatch_sends_pending_tasks_only(monkeypatch):
    from app import main

    class FakeRepository:
        async def list_user_phones(self):
            return ["5511999999001", "5511999999002"]

        async def list_tasks(self, phone):
            return [{"id": 1, "title": "Comprar o presente"}] if phone.endswith("001") else []

    class FakeService:
        repository = FakeRepository()

        async def initialize(self):
            return None

    sent = []

    async def fake_send_reply(*args, **kwargs):
        sent.append((args, kwargs))
        return True

    original_service = main.finance_service
    original_send = main._send_reply
    main.finance_service = FakeService()
    main._send_reply = fake_send_reply
    monkeypatch.setenv("COFRIN_INTERNAL_TOKEN", "test-token")
    try:
        response = TestClient(app).post(
            "/internal/tasks/digest/dispatch",
            headers={"x-internal-token": "test-token"},
        )
    finally:
        main.finance_service = original_service
        main._send_reply = original_send

    assert response.status_code == 200
    assert response.json()["processed"] == 1
    assert response.json()["skipped"] == ["5511999999002"]
    assert sent[0][0][1].startswith("📋 *Sua lista de tarefas*")
    assert "Comprar o presente" in sent[0][0][1]


def test_repeating_reminder_dispatch_includes_stop_option_and_occurrence_key(monkeypatch):
    from app import main

    due_at = datetime(2026, 9, 14, 13, 0, tzinfo=UTC)

    class FakeRepository:
        async def due_reminders(self, now):
            return [{
                "id": 7,
                "phone": "5511999999001",
                "message": "Comprar o presente",
                "due_at": due_at,
                "source_message_id": "source-7",
                "repeat_interval_minutes": 60,
                "repeat_until": datetime(2026, 9, 16, 13, 0, tzinfo=UTC),
                "cancelled": False,
            }]

        async def mark_reminder_sent(self, reminder_id, sent_at=None):
            return None

    class FakeService:
        repository = FakeRepository()

        async def initialize(self):
            return None

    sent = []

    async def fake_send_reply(*args, **kwargs):
        sent.append((args, kwargs))
        return True

    original_service = main.finance_service
    original_send = main._send_reply
    main.finance_service = FakeService()
    main._send_reply = fake_send_reply
    monkeypatch.setenv("COFRIN_INTERNAL_TOKEN", "test-token")
    try:
        response = TestClient(app).post(
            "/internal/reminders/dispatch",
            headers={"x-internal-token": "test-token"},
        )
    finally:
        main.finance_service = original_service
        main._send_reply = original_send

    assert response.status_code == 200
    assert "parar lembrete" in sent[0][0][1].lower()
    assert sent[0][0][2] == "reminder:7:2026-09-14T13:00:00+00:00"
