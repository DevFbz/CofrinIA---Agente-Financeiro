from __future__ import annotations

import hashlib
import hmac
import logging
import os
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

from app.integrations.audio import AudioTranscriber, AudioTranscriptionError
from app.services.finance import FinanceService

app = FastAPI(title="Finance WhatsApp Assistant", version="0.2.0")
finance_service = FinanceService()
logger = logging.getLogger(__name__)


class ProcessMessageRequest(BaseModel):
    message: str
    phone: str = "local"


def _signature_is_valid(body: bytes, signature: str | None, static_secret: str | None) -> bool:
    secret = os.getenv("EVOLUTION_WEBHOOK_SECRET")
    if not secret or secret == "change-me":
        return True
    if static_secret and hmac.compare_digest(static_secret, secret):
        return True
    if not signature:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature.removeprefix("sha256="), expected)


def _extract_evolution_message(
    payload: dict[str, Any],
) -> tuple[str | None, str | None, str | None, bool, bool, dict[str, Any]]:
    data = payload.get("data", payload)
    message = data.get("message", {}) if isinstance(data, dict) else {}
    text = message.get("conversation") or message.get("extendedTextMessage", {}).get("text")
    key = data.get("key", {}) if isinstance(data, dict) else {}
    remote_jid = key.get("remoteJid")
    message_id = key.get("id")
    return text, remote_jid, message_id, bool(key.get("fromMe")), bool(message.get("audioMessage")), key


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/process")
async def process_message(request: ProcessMessageRequest) -> dict[str, Any]:
    result = await finance_service.process_message(request.message, request.phone)
    return {"reply": result.reply, "transaction": result.transaction}


@app.get("/webhooks/evolution")
def verify_evolution_webhook():
    return {"status": "ok"}


async def _send_reply(remote_jid: str, text: str) -> None:
    evolution_url = os.getenv("EVOLUTION_API_URL")
    evolution_key = os.getenv("EVOLUTION_API_KEY")
    instance = os.getenv("EVOLUTION_INSTANCE")
    if not evolution_url or not evolution_key or not instance:
        return
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            response = await client.post(
                f"{evolution_url.rstrip('/')}/message/sendText/{instance}",
                headers={"apikey": evolution_key},
                json={"number": remote_jid.split("@")[0], "text": text},
            )
            response.raise_for_status()
        except httpx.HTTPError:
            logger.exception("failed to send finance reply through Evolution")


@app.post("/webhooks/evolution")
async def evolution_webhook(
    request: Request,
    x_webhook_signature: str | None = Header(default=None),
    x_evolution_webhook_secret: str | None = Header(default=None),
):
    body = await request.body()
    if not _signature_is_valid(body, x_webhook_signature, x_evolution_webhook_secret):
        raise HTTPException(status_code=401, detail="assinatura inválida")
    payload = await request.json()
    text, remote_jid, message_id, from_me, is_audio, message_key = _extract_evolution_message(payload)
    if not remote_jid or remote_jid.endswith("@g.us") or from_me:
        return {"status": "ignored"}

    if is_audio and not text:
        try:
            text = await AudioTranscriber().transcribe(message_key)
        except AudioTranscriptionError:
            logger.exception("failed to transcribe incoming audio")
            await _send_reply(
                remote_jid,
                "🎙️ Recebi seu áudio, mas não consegui interpretá-lo agora. "
                "Tente enviar o texto ou gravar o áudio novamente, por favor.",
            )
            return {
                "status": "audio_unavailable",
                "reply": "🎙️ Recebi seu áudio, mas não consegui interpretá-lo agora. "
                "Tente enviar o texto ou gravar o áudio novamente, por favor.",
            }
    if not text:
        return {"status": "ignored"}

    result = await finance_service.process_message(
        text,
        remote_jid.split("@")[0],
        message_id=message_id,
    )
    if result.duplicate:
        return {"status": "ignored"}
    await _send_reply(remote_jid, result.reply)
    return {"status": "processed", "reply": result.reply}


@app.post("/webhooks/evolution/{event_name}")
async def evolution_event_webhook(
    event_name: str,
    request: Request,
    x_webhook_signature: str | None = Header(default=None),
    x_evolution_webhook_secret: str | None = Header(default=None),
):
    del event_name
    return await evolution_webhook(request, x_webhook_signature, x_evolution_webhook_secret)
