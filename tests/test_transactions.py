import pytest

from app.domain.transactions import parse_transaction_text


def test_parses_brazilian_expense_message():
    result = parse_transaction_text("Gastei R$ 42,50 no almoço")

    assert result.type == "expense"
    assert result.amount == pytest.approx(42.50)
    assert result.description == "almoço"
    assert result.category == "alimentacao"


def test_parses_income_message():
    result = parse_transaction_text("Recebi 3500 de salário")

    assert result.type == "income"
    assert result.amount == pytest.approx(3500.00)
    assert result.category == "salario"


def test_rejects_message_without_amount():
    with pytest.raises(ValueError, match="valor"):
        parse_transaction_text("paguei a conta do mercado")


def test_extracts_named_card_payment_method_and_removes_it_from_description():
    result = parse_transaction_text("Gastei R$ 127,50 no Mercado no cartão Nubank")

    assert result.payment_method == "Cartão Nubank"
    assert result.description == "Mercado"


def test_extracts_pix_payment_method():
    result = parse_transaction_text("Paguei R$ 25 no almoço via Pix")

    assert result.payment_method == "Pix"
    assert result.description == "almoço"


def test_parses_pharmacy_and_removes_reais_from_description():
    result = parse_transaction_text("Gastei 21 reais na farmácia no crédito")

    assert result.amount == pytest.approx(21)
    assert result.description == "farmácia"
    assert result.category == "saude"
    assert result.payment_method == "Cartão de crédito"


def test_parses_short_informal_message_with_amount_at_end():
    result = parse_transaction_text("padaria 15")

    assert result.amount == pytest.approx(15)
    assert result.description == "padaria"
    assert result.category == "alimentacao"


def test_parses_short_pharmacy_message_with_decimal_amount_at_end():
    result = parse_transaction_text("farmácia 48,90")

    assert result.amount == pytest.approx(48.90)
    assert result.description == "farmácia"
    assert result.category == "saude"


def test_strips_speech_filler_before_transaction_verb():
    result = parse_transaction_text("Um gastei 30 reais no almoço, via Pix.")

    assert result.amount == 30
    assert result.description == "almoço"
    assert result.category == "alimentacao"
    assert result.payment_method == "Pix"