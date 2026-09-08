import pytest

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
