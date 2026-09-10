from datetime import datetime, timedelta, timezone

import pytest

from app.domain.advanced import (
    parse_budget_text,
    parse_installment_text,
    parse_recurring_text,
    parse_reminder_text,
)


def test_parses_recurring_expense():
    result = parse_recurring_text("internet de R$ 100 todo dia 10")
    assert result.description == "internet"
    assert result.amount == 100
    assert result.day_of_month == 10


def test_parses_installment_purchase():
    result = parse_installment_text("TV de R$ 2.400 em 12 vezes no cartão Nubank")
    assert result.description == "TV"
    assert result.total_amount == 2400
    assert result.installment_count == 12


def test_parses_budget_limit():
    result = parse_budget_text("meu limite de lazer é R$ 500")
    assert result.category == "lazer"
    assert result.limit_amount == 500


def test_parses_default_three_hour_reminder():
    now = datetime(2026, 9, 8, 10, 0, tzinfo=timezone(timedelta(hours=-3)))
    result = parse_reminder_text("me lembre de pagar a conta", now=now)
    assert result.message == "pagar a conta"
    assert result.due_at == datetime(2026, 9, 8, 13, 0, tzinfo=timezone(timedelta(hours=-3)))


def test_parses_explicit_reminder_hours():
    now = datetime(2026, 9, 8, 10, 0, tzinfo=timezone(timedelta(hours=-3)))
    result = parse_reminder_text("lembre-me de ligar para o banco em 5 horas", now=now)
    assert result.due_at == datetime(2026, 9, 8, 15, 0, tzinfo=timezone(timedelta(hours=-3)))


def test_parses_explicit_reminder_minutes():
    now = datetime(2026, 9, 8, 8, 26, tzinfo=timezone(timedelta(hours=-3)))

    result = parse_reminder_text("me lembre de verificar em 10 minutos", now=now)

    assert result.due_at == datetime(2026, 9, 8, 8, 36, tzinfo=timezone(timedelta(hours=-3)))


def test_parses_daqui_a_reminder_minutes():
    now = datetime(2026, 9, 8, 8, 26, tzinfo=timezone(timedelta(hours=-3)))

    result = parse_reminder_text("lembre-me de verificar daqui a 10 minutos", now=now)

    assert result.due_at == datetime(2026, 9, 8, 8, 36, tzinfo=timezone(timedelta(hours=-3)))


def test_rejects_invalid_recurring_day():
    with pytest.raises(ValueError, match="dia"):
        parse_recurring_text("aluguel de R$ 1.000 todo dia 35")
