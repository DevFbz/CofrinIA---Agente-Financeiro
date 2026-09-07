import pytest

from app.integrations.audio import AudioTranscriber


@pytest.mark.asyncio
async def test_audio_transcriber_uses_local_provider(monkeypatch):
    monkeypatch.setenv("AUDIO_TRANSCRIPTION_PROVIDER", "local")
    transcriber = AudioTranscriber(
        evolution_url="http://evolution:8080",
        evolution_key="test-key",
        instance="financeiro",
    )

    async def fake_download(_message_key):
        return b"audio", "mensagem.ogg", "audio/ogg"

    async def fake_local(_audio_bytes, _filename):
        return "Gastei trinta reais no almoço via Pix"

    monkeypatch.setattr(transcriber, "_download_audio", fake_download)
    monkeypatch.setattr(transcriber, "_transcribe_local", fake_local)

    result = await transcriber.transcribe({"id": "audio-1"})

    assert result == "Gastei trinta reais no almoço via Pix"
