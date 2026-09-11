from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


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
    brazil = ZoneInfo("America/Sao_Paulo")
    current = now or datetime.now(brazil)
    if current.tzinfo is None:
        current = current.replace(tzinfo=brazil)
    else:
        current = current.astimezone(brazil)
    cleaned_text = re.sub(
        r"^(?:(?:é|e|então|entao|bom|olha)\s*[,;:]?\s*)+",
        "",
        text.strip(),
        flags=re.IGNORECASE,
    )
    prefix = re.match(
        r"^(?:me\s+lembre|me\s+lembra|lembre[- ]me|me\s+lembrar|lembrar|lembra|"
        r"criar\s+(?:um\s+)?lembrete|lembrete|"
        r"(?:pode\s+)?me\s+lembrar|me\s+avise|avise[- ]me|"
        r"quero\s+que\s+me\s+lembre)"
        r"(?:\s+(?:de|para|que))?\s*(?P<body>.+)$",
        cleaned_text,
        re.IGNORECASE,
    )
    if not prefix:
        raise ValueError("informe o que devo lembrar")
    body = prefix.group("body").strip(" .!?\t")
    if not body:
        raise ValueError("informe o que devo lembrar")

    relative = re.search(
        r"\s+(?:em|daqui\s+a)\s*(?P<quantity>\d+)\s*"
        r"(?P<unit>minutos?|mins?|m|horas?|h|dias?|d)\s*$",
        body,
        re.IGNORECASE,
    )
    if relative:
        message = body[: relative.start()].strip(" .,!?:;")
        quantity = int(relative.group("quantity"))
        unit = relative.group("unit").casefold()
        if unit.startswith(("min", "m")):
            delta = timedelta(minutes=quantity)
        elif unit.startswith(("dia", "d")):
            delta = timedelta(days=quantity)
        else:
            delta = timedelta(hours=quantity)
        return ReminderDraft(message, current + delta)

    absolute = re.search(
        r"\s+(?:(?P<day>hoje|amanhã|amanha)\s*[,;]?\s+)?"
        r"(?:(?P<marker>às|as|para|pra)\s+)?"
        r"(?P<hour>\d{1,2})(?:(?::|h)(?P<minute>\d{2}))?\s*"
        r"(?P<clock_unit>horas?|h)?(?:\s+da\s+(?P<period>manhã|manha|tarde|noite))?\s*$",
        body,
        re.IGNORECASE,
    )
    if absolute and not (
        absolute.group("marker")
        or absolute.group("minute")
        or absolute.group("clock_unit")
    ):
        absolute = None
    if absolute:
        hour = int(absolute.group("hour"))
        minute = int(absolute.group("minute") or 0)
        period = (absolute.group("period") or "").casefold()
        if not 0 <= minute <= 59:
            raise ValueError("os minutos do lembrete devem estar entre 00 e 59")
        if not 0 <= hour <= 23:
            raise ValueError("a hora do lembrete deve estar entre 00 e 23")
        if period in {"noite", "tarde"} and 1 <= hour <= 11:
            hour += 12
        elif period in {"manhã", "manha"} and hour == 12:
            hour = 0
        message = body[: absolute.start()].strip(" .,!?:;")
        if re.fullmatch(r"\d{1,2}/\d{1,2}(?:/\d{2,4})?", message):
            raise ValueError("informe o que devo lembrar")
        target_date = current.date()
        day = (absolute.group("day") or "").casefold()
        if day in {"amanhã", "amanha"}:
            target_date += timedelta(days=1)
        due_at = datetime.combine(target_date, datetime.min.time(), tzinfo=brazil).replace(hour=hour, minute=minute)
        if due_at <= current and day != "amanhã" and day != "amanha":
            due_at += timedelta(days=1)
        return ReminderDraft(message, due_at)

    if re.fullmatch(r"\d{1,2}/\d{1,2}(?:/\d{2,4})?", body):
        raise ValueError("informe o que devo lembrar")
    return ReminderDraft(body, current + timedelta(hours=3))
