from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel

from app.integrations.audio import AudioTranscriber, AudioTranscriptionError
from app.integrations.exports import build_transactions_csv, build_transactions_xlsx
from app.integrations.receipts import ReceiptInterpretationError, ReceiptInterpreter
from app.services.finance import BRAZIL_TIMEZONE, FinanceService

app = FastAPI(title="Finance WhatsApp Assistant", version="0.2.0")
finance_service = FinanceService()
logger = logging.getLogger("uvicorn.error")
logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())


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
) -> tuple[str | None, str | None, str | None, bool, bool, bool, dict[str, Any]]:
    data = payload.get("data", payload)
    message = data.get("message", {}) if isinstance(data, dict) else {}
    text = message.get("conversation") or message.get("extendedTextMessage", {}).get("text")
    key = data.get("key", {}) if isinstance(data, dict) else {}
    remote_jid = key.get("remoteJid")
    message_id = key.get("id")
    return (
        text,
        remote_jid,
        message_id,
        bool(key.get("fromMe")),
        bool(message.get("audioMessage")),
        bool(message.get("imageMessage")),
        key,
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics", response_class=PlainTextResponse)
async def metrics() -> PlainTextResponse:
    await finance_service.initialize()
    snapshot = await finance_service.repository.metrics_snapshot()
    lines = [
        "# HELP cofrinia_up CofrinIA application availability.",
        "# TYPE cofrinia_up gauge",
        "cofrinia_up 1",
    ]
    for name, value in snapshot.items():
        lines.extend((f"# TYPE cofrinia_{name}_total gauge", f"cofrinia_{name}_total {value}"))
    return PlainTextResponse("\n".join(lines) + "\n")


def _internal_token_is_valid(token: str | None) -> bool:
    expected = os.getenv("COFRIN_INTERNAL_TOKEN")
    return bool(expected and token and hmac.compare_digest(token, expected))


@app.get("/internal/users/active")
async def active_users(x_internal_token: str | None = Header(default=None)) -> dict[str, Any]:
    if not _internal_token_is_valid(x_internal_token):
        raise HTTPException(status_code=401, detail="token interno inválido")
    await finance_service.initialize()
    return {"users": await finance_service.repository.list_user_phones()}


async def _dispatch_periodic_report(days: int, label: str) -> dict[str, Any]:
    today = datetime.now(BRAZIL_TIMEZONE).date()
    start = today - timedelta(days=days - 1)
    sent, failed = [], []
    for phone in await finance_service.repository.list_user_phones():
        count, cents = await finance_service.repository.expense_summary(phone, start, today + timedelta(days=1))
        report = f"📊 *Resumo financeiro {label}*\n\n📅 Período: {start.strftime('%d/%m/%Y')} a {today.strftime('%d/%m/%Y')}\n💸 Total de despesas: {finance_service._money(cents / 100)}\n🧾 Lançamentos: {count}\n\nCofrinIA 💚"
        delivery_key = f"report:{label}:{phone}:{today.isoformat()}"
        if await _send_reply(f"{phone}@s.whatsapp.net", report, delivery_key, "report"):
            sent.append(phone)
        else:
            failed.append(phone)
    return {"processed": len(sent), "sent": sent, "failed": failed}


@app.post("/internal/reports/daily/dispatch")
async def dispatch_daily_reports(x_internal_token: str | None = Header(default=None)) -> dict[str, Any]:
    if not _internal_token_is_valid(x_internal_token):
        raise HTTPException(status_code=401, detail="token interno inválido")
    await finance_service.initialize()
    return await _dispatch_periodic_report(1, "diário")


@app.post("/internal/reports/weekly/dispatch")
async def dispatch_weekly_reports(x_internal_token: str | None = Header(default=None)) -> dict[str, Any]:
    if not _internal_token_is_valid(x_internal_token):
        raise HTTPException(status_code=401, detail="token interno inválido")
    await finance_service.initialize()
    return await _dispatch_periodic_report(7, "semanal")


@app.post("/internal/reports/monthly/dispatch")
async def dispatch_monthly_reports(x_internal_token: str | None = Header(default=None)) -> dict[str, Any]:
    if not _internal_token_is_valid(x_internal_token):
        raise HTTPException(status_code=401, detail="token interno inválido")
    await finance_service.initialize()
    today = datetime.now(BRAZIL_TIMEZONE).date()
    start = today.replace(day=1)
    end = date(today.year + (today.month == 12), 1 if today.month == 12 else today.month + 1, 1)
    sent, failed = [], []
    for phone in await finance_service.repository.list_user_phones():
        count, cents = await finance_service.repository.expense_summary(phone, start, end)
        salary = await finance_service.repository.get_salary(phone)
        salary_line = f"💼 Salário: {finance_service._money(salary / 100)}" if salary else "💼 Salário mensal ainda não informado"
        report = f"📊 *Resumo financeiro mensal*\n\n📅 Período: {today.strftime('%Y-%m')}\n💸 Total de despesas: {finance_service._money(cents / 100)}\n🧾 Quantidade de lançamentos: {count}\n\n{salary_line}\n\nCofrinIA 💚"
        delivery_key = f"report:monthly:{phone}:{today.strftime('%Y-%m')}"
        if await _send_reply(f"{phone}@s.whatsapp.net", report, delivery_key, "report"):
            sent.append(phone)
        else:
            failed.append(phone)
    return {"processed": len(sent), "sent": sent, "failed": failed}


@app.get("/internal/reports/monthly")
async def monthly_report(phone: str, x_internal_token: str | None = Header(default=None)) -> dict[str, Any]:
    if not _internal_token_is_valid(x_internal_token):
        raise HTTPException(status_code=401, detail="token interno inválido")
    await finance_service.initialize()
    today = datetime.now(BRAZIL_TIMEZONE).date()
    start = today.replace(day=1)
    end = date(today.year + (today.month == 12), 1 if today.month == 12 else today.month + 1, 1)
    count, cents = await finance_service.repository.expense_summary(phone, start, end)
    salary = await finance_service.repository.get_salary(phone)
    return {"phone": phone, "period": today.strftime("%Y-%m"), "expense_count": count, "expense_cents": cents, "salary_cents": salary, "salary_percent": round(cents * 100 / salary, 2) if salary else None}


async def _export_transactions(phone: str, extension: str) -> Response:
    rows = await finance_service.repository.list_transactions(phone)
    today = datetime.now(BRAZIL_TIMEZONE).strftime("%Y%m%d")
    if extension == "csv":
        return Response(
            content=build_transactions_csv(rows),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="cofrinia-transacoes-{today}.csv"'},
        )
    return Response(
        content=build_transactions_xlsx(rows),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="cofrinia-transacoes-{today}.xlsx"'},
    )


@app.get("/internal/exports/transactions.csv")
async def export_transactions_csv(phone: str, x_internal_token: str | None = Header(default=None)) -> Response:
    if not _internal_token_is_valid(x_internal_token):
        raise HTTPException(status_code=401, detail="token interno inválido")
    if not phone.strip():
        raise HTTPException(status_code=400, detail="telefone obrigatório")
    await finance_service.initialize()
    return await _export_transactions(phone.strip(), "csv")


@app.get("/internal/exports/transactions.xlsx")
async def export_transactions_xlsx(phone: str, x_internal_token: str | None = Header(default=None)) -> Response:
    if not _internal_token_is_valid(x_internal_token):
        raise HTTPException(status_code=401, detail="token interno inválido")
    if not phone.strip():
        raise HTTPException(status_code=400, detail="telefone obrigatório")
    await finance_service.initialize()
    return await _export_transactions(phone.strip(), "xlsx")


@app.post("/internal/reminders/process")
async def process_due_reminders(x_internal_token: str | None = Header(default=None)) -> dict[str, Any]:
    if not _internal_token_is_valid(x_internal_token):
        raise HTTPException(status_code=401, detail="token interno inválido")
    await finance_service.initialize()
    due = await finance_service.repository.due_reminders(datetime.now(UTC))
    return {"reminders": due}


@app.post("/internal/reminders/dispatch")
async def dispatch_due_reminders(x_internal_token: str | None = Header(default=None)) -> dict[str, Any]:
    if not _internal_token_is_valid(x_internal_token):
        raise HTTPException(status_code=401, detail="token interno inválido")
    await finance_service.initialize()
    due = await finance_service.repository.due_reminders(datetime.now(UTC))
    sent, failed = [], []
    for reminder in due:
        ok = await _send_reply(
            f"{reminder['phone']}@s.whatsapp.net",
            f"⏰ Lembrete: {reminder['message']}",
            f"reminder:{reminder['id']}",
            "reminder",
            quoted_message={
                "remoteJid": f"{reminder['phone']}@s.whatsapp.net",
                "fromMe": False,
                "id": reminder.get("source_message_id"),
            },
        )
        (sent if ok else failed).append(reminder["id"])
        if ok:
            await finance_service.repository.mark_reminder_sent(reminder["id"])
    return {"processed": len(sent), "sent": sent, "failed": failed}


@app.post("/internal/installments/generate")
async def generate_installments(x_internal_token: str | None = Header(default=None)) -> dict[str, Any]:
    if not _internal_token_is_valid(x_internal_token):
        raise HTTPException(status_code=401, detail="token interno inválido")
    await finance_service.initialize()
    created = await finance_service.repository.generate_installments(datetime.now(BRAZIL_TIMEZONE).date())
    return {"processed": len(created), "created": created}


@app.post("/internal/recurring/generate")
async def generate_recurring_expenses(x_internal_token: str | None = Header(default=None)) -> dict[str, Any]:
    if not _internal_token_is_valid(x_internal_token):
        raise HTTPException(status_code=401, detail="token interno inválido")
    await finance_service.initialize()
    created = await finance_service.repository.generate_recurring(datetime.now(BRAZIL_TIMEZONE).date())
    return {"processed": len(created), "created": created}


@app.post("/internal/reminders/{reminder_id}/complete")
async def complete_reminder(reminder_id: int, x_internal_token: str | None = Header(default=None)) -> dict[str, str]:
    if not _internal_token_is_valid(x_internal_token):
        raise HTTPException(status_code=401, detail="token interno inválido")
    await finance_service.initialize()
    await finance_service.repository.mark_reminder_sent(reminder_id)
    return {"status": "completed"}


@app.post("/api/process")
async def process_message(request: ProcessMessageRequest) -> dict[str, Any]:
    result = await finance_service.process_message(request.message, request.phone)
    await finance_service.repository.append_conversation_message(request.phone, "assistant", result.reply)
    return {"reply": result.reply, "transaction": result.transaction}


@app.get("/webhooks/evolution")
def verify_evolution_webhook():
    return {"status": "ok"}


async def _send_reply(
    remote_jid: str,
    text: str,
    delivery_key: str | None = None,
    delivery_kind: str = "reply",
    quoted_message: dict[str, Any] | None = None,
    quoted_text: str | None = None,
) -> bool:
    evolution_url = os.getenv("EVOLUTION_API_URL")
    evolution_key = os.getenv("EVOLUTION_API_KEY")
    instance = os.getenv("EVOLUTION_INSTANCE")
    if not evolution_url or not evolution_key or not instance:
        return False
    if delivery_key:
        await finance_service.initialize()
        claimed = await finance_service.repository.claim_delivery(
            delivery_key,
            delivery_kind,
            remote_jid.split("@")[0],
            datetime.now(UTC),
        )
        if not claimed:
            return True
    url = f"{evolution_url.rstrip('/')}/message/sendText/{instance}"
    headers = {"apikey": evolution_key}
    payload = {"number": remote_jid.split("@")[0], "text": text}
    if quoted_message and quoted_message.get("id"):
        quoted_key = {
            key: quoted_message[key]
            for key in ("remoteJid", "fromMe", "id", "participant")
            if key in quoted_message
        }
        payload["quoted"] = {"key": quoted_key}
        if quoted_text:
            payload["quoted"]["message"] = {"conversation": quoted_text[:4000]}
    last_error: str | None = None
    for attempt in range(1, 4):
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                response_data = response.json()
                logger.warning("Evolution send accepted attempt=%s jid_hash=%s status=%s outbound_id=%s", attempt, hashlib.sha256(remote_jid.encode()).hexdigest()[:12], response.status_code, response_data.get("key", {}).get("id") if isinstance(response_data, dict) else None)
                if delivery_key:
                    await finance_service.repository.complete_delivery(delivery_key, True, datetime.now(UTC))
                return True
        except (httpx.HTTPError, ValueError) as exc:
            last_error = str(exc)[:500]
            logger.exception("failed to send finance reply through Evolution attempt=%s", attempt)
            if attempt < 3:
                await asyncio.sleep(attempt * 2)
    if delivery_key:
        await finance_service.repository.complete_delivery(delivery_key, False, datetime.now(UTC), last_error)
    return False


async def _send_presence(remote_jid: str) -> None:
    evolution_url = os.getenv("EVOLUTION_API_URL")
    evolution_key = os.getenv("EVOLUTION_API_KEY")
    instance = os.getenv("EVOLUTION_INSTANCE")
    if not evolution_url or not evolution_key or not instance:
        return
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.post(
                f"{evolution_url.rstrip('/')}/chat/sendPresence/{instance}",
                headers={"apikey": evolution_key},
                json={
                    "number": remote_jid.split("@")[0],
                    "delay": 1200,
                    "presence": "composing",
                },
            )
            response.raise_for_status()
    except httpx.HTTPError:
        logger.info("Evolution presence unavailable", exc_info=True)


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
    text, remote_jid, message_id, from_me, is_audio, is_image, message_key = _extract_evolution_message(payload)
    logger.info("incoming Evolution message jid_hash=%s from_me=%s audio=%s image=%s", hashlib.sha256((remote_jid or "").encode()).hexdigest()[:12], from_me, is_audio, is_image)
    if not remote_jid or remote_jid.endswith("@g.us") or from_me:
        return {"status": "ignored"}
    destination = message_key.get("remoteJidAlt") or remote_jid
    await _send_presence(destination)

    if is_audio and not text:
        try:
            text = await AudioTranscriber().transcribe(message_key)
            logger.info(
                "audio transcribed message_id=%s chars=%s",
                message_id,
                len(text),
            )
        except AudioTranscriptionError:
            logger.exception("failed to transcribe incoming audio")
            await _send_reply(
                destination,
                "🎙️ Recebi seu áudio, mas não consegui interpretá-lo agora. "
                "Tente enviar o texto ou gravar o áudio novamente, por favor.",
                f"reply:{message_id}" if message_id else None,
                "inbound_reply",
                quoted_message=message_key,
                quoted_text=text,
            )
            return {
                "status": "audio_unavailable",
                "reply": "🎙️ Recebi seu áudio, mas não consegui interpretá-lo agora. "
                "Tente enviar o texto ou gravar o áudio novamente, por favor.",
            }
    if is_image:
        try:
            recognized_text = await ReceiptInterpreter().interpret(message_key)
            text = f"{recognized_text} {text or ''}".strip()
        except ReceiptInterpretationError:
            logger.exception("failed to interpret incoming receipt image")
            reply = (
                "🧾 Recebi a imagem do comprovante, mas não consegui identificar o valor agora.\n\n"
                "Tente enviar uma foto mais nítida, com o total visível, ou me informe o gasto por texto."
            )
            await _send_reply(
                destination,
                reply,
                f"reply:{message_id}" if message_id else None,
                "inbound_reply",
                quoted_message=message_key,
                quoted_text=text,
            )
            return {"status": "image_unavailable", "reply": reply}
    if not text:
        return {"status": "ignored"}

    result = await finance_service.process_message(
        text,
        remote_jid.split("@")[0],
        message_id=message_id,
    )
    if result.duplicate:
        return {"status": "ignored"}
    logger.info(
        "inbound processed message_id=%s transaction=%s reply_chars=%s",
        message_id,
        result.transaction is not None,
        len(result.reply),
    )
    await finance_service.repository.append_conversation_message(
        remote_jid.split("@")[0],
        "assistant",
        result.reply,
    )
    delivery_key = f"reply:{message_id}" if message_id else None
    await _send_reply(
        destination,
        result.reply,
        delivery_key,
        "inbound_reply",
        quoted_message=message_key,
        quoted_text=text,
    )
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
