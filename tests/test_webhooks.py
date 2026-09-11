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
