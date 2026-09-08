from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app


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
