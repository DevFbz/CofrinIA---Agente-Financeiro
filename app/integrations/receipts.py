from __future__ import annotations

import base64
import io
import os
import re
from typing import Any

import httpx
from PIL import Image


class ReceiptInterpretationError(RuntimeError):
    pass


_AMOUNT_PATTERNS = (
    re.compile(r"(?:total|valor\s+(?:total|pago)|pago|valor)\s*[:=-]?\s*r?\$?\s*([0-9][0-9.,]*)", re.IGNORECASE),
    re.compile(r"r\$\s*([0-9][0-9.,]*)", re.IGNORECASE),
    re.compile(r"\b([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2})\b"),
)


def _normalize_amount(raw: str) -> str:
    value = raw.strip().replace(" ", "")
    if "." in value and "," in value:
        value = value.replace(".", "").replace(",", ".")
    elif "," in value:
        value = value.replace(",", ".")
    elif value.count(".") > 1:
        value = value.replace(".", "")
    return f"{float(value):.2f}".replace(".", ",")


def _merchant(ocr_text: str) -> str:
    ignored = ("cnpj", "cpf", "comprovante", "pagamento", "total", "valor", "data", "hora", "nsu")
    for line in ocr_text.splitlines():
        clean = re.sub(r"[^\wÀ-ÿ&' .-]", "", line).strip()
        if len(clean) >= 3 and not any(token in clean.casefold() for token in ignored):
            return " ".join(clean.split()).title()
    return "estabelecimento não identificado"


def build_expense_text(ocr_text: str) -> str:
    if not ocr_text or not ocr_text.strip():
        raise ValueError("não encontrei texto no comprovante")
    amount = None
    for pattern in _AMOUNT_PATTERNS:
        match = pattern.search(ocr_text)
        if match:
            amount = _normalize_amount(match.group(1))
            break
    if amount is None:
        raise ValueError("não encontrei o valor no comprovante")
    merchant = _merchant(ocr_text)
    payment = " via Pix" if re.search(r"\bpix\b", ocr_text, re.IGNORECASE) else ""
    return f"Gastei R$ {amount} no {merchant}{payment}"


class ReceiptInterpreter:
    def __init__(self, evolution_url: str | None = None, evolution_key: str | None = None, instance: str | None = None):
        self.evolution_url = evolution_url or os.getenv("EVOLUTION_API_URL")
        self.evolution_key = evolution_key or os.getenv("EVOLUTION_API_KEY")
        self.instance = instance or os.getenv("EVOLUTION_INSTANCE")

    async def interpret(self, message_key: dict[str, Any]) -> str:
        if not self.evolution_url or not self.evolution_key or not self.instance:
            raise ReceiptInterpretationError("adaptador de mídia não configurado")
        image_bytes = await self._download_image(message_key)
        try:
            import pytesseract

            image = Image.open(io.BytesIO(image_bytes))
            ocr_text = await __import__("asyncio").to_thread(
                pytesseract.image_to_string,
                image,
                lang="por+eng",
                config="--psm 6",
            )
            return build_expense_text(ocr_text)
        except (ValueError, OSError) as exc:
            raise ReceiptInterpretationError(str(exc)) from exc
        except Exception as exc:
            raise ReceiptInterpretationError("não foi possível ler o comprovante") from exc

    async def _download_image(self, message_key: dict[str, Any]) -> bytes:
        url = f"{self.evolution_url.rstrip('/')}/chat/getBase64FromMediaMessage/{self.instance}"
        try:
            async with httpx.AsyncClient(timeout=45) as client:
                response = await client.post(
                    url,
                    headers={"apikey": self.evolution_key},
                    json={"message": {"key": message_key}, "convertToMp4": False},
                )
                response.raise_for_status()
                encoded = response.json().get("base64")
        except (httpx.HTTPError, ValueError) as exc:
            raise ReceiptInterpretationError("não foi possível baixar a imagem") from exc
        if not encoded:
            raise ReceiptInterpretationError("imagem sem conteúdo")
        if "," in encoded:
            encoded = encoded.split(",", 1)[1]
        try:
            return base64.b64decode(encoded)
        except ValueError as exc:
            raise ReceiptInterpretationError("imagem inválida") from exc
