from __future__ import annotations

import asyncio
import base64
import binascii
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

import httpx


class AudioTranscriptionError(RuntimeError):
    pass


_LOCAL_MODEL: Any = None
_LOCAL_MODEL_LOCK = threading.Lock()


class AudioTranscriber:
    def __init__(self, evolution_url: str | None = None, evolution_key: str | None = None, instance: str | None = None):
        self.evolution_url = evolution_url or os.getenv("EVOLUTION_API_URL")
        self.evolution_key = evolution_key or os.getenv("EVOLUTION_API_KEY")
        self.instance = instance or os.getenv("EVOLUTION_INSTANCE")
        self.provider = os.getenv("AUDIO_TRANSCRIPTION_PROVIDER", "local").casefold()
        self.openai_key = os.getenv("OPENAI_API_KEY")
        self.openai_model = os.getenv("OPENAI_TRANSCRIPTION_MODEL", "whisper-1")
        self.local_model = os.getenv("WHISPER_MODEL_SIZE", "small")
        self.local_device = os.getenv("WHISPER_DEVICE", "cpu")
        self.local_compute_type = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
        self.local_beam_size = max(1, min(10, int(os.getenv("WHISPER_BEAM_SIZE", "5"))))
        self.model_dir = os.getenv(
            "WHISPER_MODEL_DIR",
            str(Path.home() / ".cache" / "cofrinia" / "whisper_models"),
        )

    async def transcribe(self, message_key: dict[str, Any]) -> str:
        if not self.evolution_url or not self.evolution_key or not self.instance:
            raise AudioTranscriptionError("adaptador de mídia não configurado")

        audio_bytes, filename, mime_type = await self._download_audio(message_key)
        if self.provider == "openai":
            return await self._transcribe_openai(audio_bytes, filename, mime_type)
        if self.provider != "local":
            raise AudioTranscriptionError("provedor de transcrição não suportado")
        return await self._transcribe_local(audio_bytes, filename)

    async def _transcribe_openai(self, audio_bytes: bytes, filename: str, mime_type: str) -> str:
        if not self.openai_key:
            raise AudioTranscriptionError("transcrição OpenAI não configurada")
        files = {"file": (filename, audio_bytes, mime_type)}
        data = {"model": self.openai_model, "language": "pt", "response_format": "text"}
        headers = {"Authorization": f"Bearer {self.openai_key}"}
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers=headers,
                    data=data,
                    files=files,
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AudioTranscriptionError("falha na transcrição OpenAI") from exc
        text = response.text.strip()
        if not text:
            raise AudioTranscriptionError("transcrição vazia")
        return text

    async def _transcribe_local(self, audio_bytes: bytes, filename: str) -> str:
        suffix = Path(filename).suffix or ".ogg"
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temporary:
                temporary.write(audio_bytes)
                audio_path = temporary.name
            return await asyncio.to_thread(self._transcribe_local_file, audio_path)
        except AudioTranscriptionError:
            raise
        except Exception as exc:
            raise AudioTranscriptionError("falha na transcrição local") from exc
        finally:
            if "audio_path" in locals():
                Path(audio_path).unlink(missing_ok=True)

    def _transcribe_local_file(self, audio_path: str) -> str:
        global _LOCAL_MODEL
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise AudioTranscriptionError("faster-whisper não instalado") from exc

        if _LOCAL_MODEL is None:
            with _LOCAL_MODEL_LOCK:
                if _LOCAL_MODEL is None:
                    _LOCAL_MODEL = WhisperModel(
                        self.local_model,
                        device=self.local_device,
                        compute_type=self.local_compute_type,
                        download_root=self.model_dir,
                    )
        try:
            segments, _ = _LOCAL_MODEL.transcribe(
                audio_path,
                language="pt",
                beam_size=self.local_beam_size,
                vad_filter=True,
            )
            text = " ".join(segment.text.strip() for segment in segments).strip()
        except Exception as exc:
            raise AudioTranscriptionError("falha durante a transcrição local") from exc
        if not text:
            raise AudioTranscriptionError("transcrição vazia")
        return text

    async def _download_audio(self, message_key: dict[str, Any]) -> tuple[bytes, str, str]:
        url = f"{self.evolution_url.rstrip('/')}/chat/getBase64FromMediaMessage/{self.instance}"
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    url,
                    headers={"apikey": self.evolution_key},
                    json={"message": {"key": message_key}, "convertToMp4": False},
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AudioTranscriptionError("não foi possível baixar o áudio") from exc

        nested = payload.get("data") if isinstance(payload, dict) else None
        encoded = payload.get("base64") if isinstance(payload, dict) else None
        if not encoded and isinstance(nested, dict):
            encoded = nested.get("base64")
        if not encoded:
            raise AudioTranscriptionError("áudio sem conteúdo")
        if "," in encoded:
            encoded = encoded.split(",", 1)[1]
        try:
            audio_bytes = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise AudioTranscriptionError("áudio inválido") from exc
        audio_message = message_key.get("message", {}).get("audioMessage", {})
        mime_type = payload.get("mimetype") or audio_message.get("mimetype") or "audio/ogg"
        filename = payload.get("fileName") or "mensagem.ogg"
        return (
            audio_bytes,
            filename,
            mime_type,
        )
