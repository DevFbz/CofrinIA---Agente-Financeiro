from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class RecurringDraft:
    description: str
    amount: float
    day_of_month: int
    category: str = "outros"
    payment_method: str = "não informado"


@dataclass(frozen=True)
class InstallmentDraft:
    description: str
    total_amount: float
    installment_count: int
    payment_method: str = "não informado"


@dataclass(frozen=True)
class BudgetDraft:
    category: str
    limit_amount: float


@dataclass(frozen=True)
class ReminderDraft:
    message: str
    due_at: datetime


_AMOUNT = r"(?:r\$\s*)?([0-9]{1,3}(?:\.[0-9]{3})*(?:,[0-9]{1,2})?|[0-9]+(?:[,.][0-9]{1,2})?)"


def _amount(raw: str) -> float:
    raw = re.sub(r"r\$\s*", "", raw, flags=re.IGNORECASE).strip()
    raw = raw.replace(".", "").replace(",", ".") if "," in raw else raw
    if "." in raw and len(raw.rsplit(".", 1)[1]) == 3:
        raw = raw.replace(".", "")
    return float(raw)


def parse_recurring_text(text: str) -> RecurringDraft:
    match = re.search(rf"(?P<amount>{_AMOUNT}).*?(?:todo|cada)\s+dia\s+(?P<day>\d{{1,2}})", text, re.IGNORECASE)
    if not match:
        raise ValueError("informe o valor e o dia da recorrência")
    day = int(match.group("day"))
    if not 1 <= day <= 31:
        raise ValueError("o dia da recorrência deve estar entre 1 e 31")
    description = re.sub(r"\s+(?:de|no|na|para)\s*$", "", text[: match.start("amount")], flags=re.IGNORECASE).strip()
    return RecurringDraft(description or "despesa recorrente", _amount(match.group("amount")), day)


def parse_installment_text(text: str) -> InstallmentDraft:
    match = re.search(rf"(?P<amount>{_AMOUNT}).*?em\s+(?P<count>\d{{1,3}})\s*vezes", text, re.IGNORECASE)
    if not match:
        raise ValueError("informe o valor total e a quantidade de parcelas")
    description = re.sub(r"\s+(?:de|no|na)\s*$", "", text[: match.start("amount")], flags=re.IGNORECASE).strip()
    return InstallmentDraft(description or "compra parcelada", _amount(match.group("amount")), int(match.group("count")))


def parse_budget_text(text: str) -> BudgetDraft:
    match = re.search(rf"(?:limite|meta).*?(?:de|para)\s+([\wÀ-ÿ ]+?)\s+(?:é|e|de)\s+{_AMOUNT}\s*$", text, re.IGNORECASE)
    if not match:
        match = re.search(rf"(?:limite|meta).*?(?:de|para)\s+([\wÀ-ÿ ]+?)\s+{_AMOUNT}", text, re.IGNORECASE)
    if not match:
        raise ValueError("informe a categoria e o valor do limite")
    amount_match = re.search(_AMOUNT + r"\s*$", text, re.IGNORECASE)
    category = re.sub(r"\s+", " ", match.group(1)).strip().casefold()
    return BudgetDraft(category, _amount(amount_match.group(1)))


def parse_reminder_text(text: str, now: datetime | None = None) -> ReminderDraft:
    brazil = timezone(timedelta(hours=-3))
    current = now or datetime.now(brazil)
    match = re.search(
        r"(?:me lembre|lembre-me)\s+de\s+(.+?)(?:\s+(?:em|daqui a)\s+(\d+)\s*(minutos?|mins?|m|horas?|h|dias?|d))?$",
        text,
        re.IGNORECASE,
    )
    if not match:
        raise ValueError("informe o que devo lembrar")
    message = match.group(1).strip()
    quantity = match.group(2)
    unit = (match.group(3) or "horas").casefold()
    if quantity is None:
        delta = timedelta(hours=3)
    elif unit.startswith(("min", "m")):
        delta = timedelta(minutes=int(quantity))
    elif unit.startswith(("dia", "d")):
        delta = timedelta(days=int(quantity))
    else:
        delta = timedelta(hours=int(quantity))
    return ReminderDraft(message, current + delta)
