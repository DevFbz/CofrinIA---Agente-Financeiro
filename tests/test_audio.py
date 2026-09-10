import base64

import pytest

from app.integrations import audio
from app.integrations.audio import AudioTranscriber, AudioTranscriptionError


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


@pytest.mark.asyncio
async def test_download_audio_decodes_data_uri_and_uses_message_metadata(monkeypatch):
    encoded = base64.b64encode(b"audio-content").decode()

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"base64": f"data:audio/ogg;base64,{encoded}"}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(audio.httpx, "AsyncClient", lambda **kwargs: Client())
    transcriber = AudioTranscriber("http://evolution", "key", "financeiro")

    result = await transcriber._download_audio(
        {"id": "audio-2", "message": {"audioMessage": {"mimetype": "audio/ogg"}}}
    )

    assert result == (b"audio-content", "mensagem.ogg", "audio/ogg")


@pytest.mark.asyncio
async def test_download_audio_rejects_invalid_base64(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"base64": "not-base64!!!"}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(audio.httpx, "AsyncClient", lambda **kwargs: Client())
    transcriber = AudioTranscriber("http://evolution", "key", "financeiro")

    with pytest.raises(AudioTranscriptionError, match="áudio inválido"):
        await transcriber._download_audio({"id": "audio-3"})


@pytest.mark.asyncio
async def test_download_audio_handles_nested_response_and_missing_filename(monkeypatch):
    encoded = base64.b64encode(b"audio-content").decode()

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": {"base64": encoded}, "fileName": None, "mimetype": None}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(audio.httpx, "AsyncClient", lambda **kwargs: Client())
    transcriber = AudioTranscriber("http://evolution", "key", "financeiro")

    result = await transcriber._download_audio(
        {"id": "audio-4", "message": {"audioMessage": {"mimetype": "audio/ogg; codecs=opus"}}}
    )

    assert result == (b"audio-content", "mensagem.ogg", "audio/ogg; codecs=opus")
