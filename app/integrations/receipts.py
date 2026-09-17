from __future__ import annotations

import asyncio
import base64
import binascii
import io
import os
import re
from typing import Any

import httpx
from PIL import Image, ImageOps


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
    payment = ""
    if re.search(r"\bpix\b", ocr_text, re.IGNORECASE):
        payment = " via Pix"
    else:
        card = re.search(
            r"cart(?:ã|a)o(?:\s+de\s+(?:cr[eé]dito|d[eé]bito))?\s+([\wÀ-ÿ-]+)",
            ocr_text,
            re.IGNORECASE,
        )
        if card:
            payment = f" via Cartão {card.group(1).title()}"
        elif re.search(r"\bcr[eé]dito\b", ocr_text, re.IGNORECASE):
            payment = " via cartão de crédito"
        elif re.search(r"\bd[eé]bito\b", ocr_text, re.IGNORECASE):
            payment = " via cartão de débito"
    return f"Gastei R$ {amount} no {merchant}{payment}"


def _prepare_image_for_ocr(image_bytes: bytes) -> Image.Image:
    """Normalize PNG/JPEG variants before sending them to Tesseract."""
    with Image.open(io.BytesIO(image_bytes)) as source:
        image = ImageOps.exif_transpose(source).convert("RGBA")
        background = Image.new("RGBA", image.size, "white")
        image = Image.alpha_composite(background, image)
        image = ImageOps.grayscale(image)
        if max(image.size) < 1600:
            scale = max(2, min(4, 1600 // max(image.size)))
            image = image.resize((image.width * scale, image.height * scale), Image.Resampling.LANCZOS)
        return ImageOps.autocontrast(image)


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

            image = _prepare_image_for_ocr(image_bytes)
            errors = []
            for lang in ("por+eng", "eng"):
                for config in ("--psm 6", "--psm 11"):
                    try:
                        ocr_text = await asyncio.to_thread(
                            pytesseract.image_to_string,
                            image,
                            lang=lang,
                            config=config,
                        )
                        try:
                            return build_expense_text(ocr_text)
                        except ValueError as exc:
                            errors.append(exc)
                    except (OSError, RuntimeError, pytesseract.TesseractError) as exc:
                        errors.append(exc)
            raise ValueError(str(errors[-1]) if errors else "não encontrei o valor no comprovante")
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
            return base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ReceiptInterpretationError("imagem inválida") from exc
