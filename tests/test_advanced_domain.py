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


def test_parses_natural_reminder_with_absolute_time():
    now = datetime(2026, 9, 10, 10, 35, tzinfo=timezone(timedelta(hours=-3)))

    result = parse_reminder_text(
        "Criar lembrete de melhorar o robô para orquestrar a infra às 18:30 horas",
        now=now,
    )

    assert result.message == "melhorar o robô para orquestrar a infra"
    assert result.due_at == datetime(2026, 9, 10, 18, 30, tzinfo=timezone(timedelta(hours=-3)))


def test_parses_natural_reminder_without_space_in_time_unit():
    now = datetime(2026, 9, 10, 10, 35, tzinfo=timezone(timedelta(hours=-3)))

    result = parse_reminder_text("Lembrar de fazer atualização no robô às 18h30", now=now)

    assert result.message == "fazer atualização no robô"
    assert result.due_at == datetime(2026, 9, 10, 18, 30, tzinfo=timezone(timedelta(hours=-3)))


def test_rejects_reminder_that_contains_only_a_date_as_message():
    with pytest.raises(ValueError, match="lembrar"):
        parse_reminder_text("Lembrar 11/09 às 18:30 da noite")


def test_removes_day_word_when_transcription_has_punctuation_before_time():
    now = datetime(2026, 9, 10, 20, 0, tzinfo=timezone(timedelta(hours=-3)))

    result = parse_reminder_text(
        "É, me lembre de revisar o orçamento hoje, às 7h32.",
        now=now,
    )

    assert result.message == "revisar o orçamento"
    assert result.due_at == datetime(2026, 9, 11, 7, 32, tzinfo=timezone(timedelta(hours=-3)))


def test_rejects_invalid_recurring_day():
    with pytest.raises(ValueError, match="dia"):
        parse_recurring_text("aluguel de R$ 1.000 todo dia 35")
