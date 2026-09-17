import base64
import io

import pytest
from PIL import Image

from app.integrations import receipts
from app.integrations.receipts import build_expense_text


def test_builds_expense_text_from_receipt_ocr():
    ocr = """MERCADO CENTRAL\nCNPJ 00.000.000/0001-00\nTOTAL R$ 127,50\nPagamento: PIX"""

    result = build_expense_text(ocr)

    assert result == "Gastei R$ 127,50 no Mercado Central via Pix"


def test_rejects_receipt_without_amount():
    with pytest.raises(ValueError, match="valor"):
        build_expense_text("MERCADO CENTRAL\nComprovante de compra")


def test_preserves_named_card_from_receipt_ocr():
    ocr = """FARMACIA CENTRAL\nTOTAL R$ 80,00\nCARTAO NUBANK"""

    result = build_expense_text(ocr)

    assert result == "Gastei R$ 80,00 no Farmacia Central via Cartão Nubank"


def test_prepares_transparent_png_for_ocr():
    image = Image.new("RGBA", (120, 80), (255, 255, 255, 0))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")

    prepared = receipts._prepare_image_for_ocr(buffer.getvalue())

    assert prepared.mode == "L"
    assert prepared.width >= 120
    assert prepared.height >= 80


@pytest.mark.asyncio
async def test_interpret_accepts_data_uri_png_and_uses_ocr_fallback(monkeypatch):
    image = Image.new("RGB", (200, 100), "white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode()

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"base64": f"data:image/png;base64,{encoded}"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            return FakeResponse()

    calls = []

    class FakePytesseract:
        @staticmethod
        def image_to_string(image, lang, config):
            calls.append((image.mode, lang, config))
            if config == "--psm 6":
                return "texto ilegível"
            return "FARMACIA CENTRAL\nTOTAL R$ 21,00\nPIX"

    monkeypatch.setattr(receipts.httpx, "AsyncClient", lambda **kwargs: FakeClient())
    monkeypatch.setitem(__import__("sys").modules, "pytesseract", FakePytesseract)

    result = await receipts.ReceiptInterpreter("http://evolution", "key", "financeiro").interpret(
        {"id": "message-1"}
    )

    assert result == "Gastei R$ 21,00 no Farmacia Central via Pix"
    assert calls == [("L", "por+eng", "--psm 6"), ("L", "por+eng", "--psm 11")]