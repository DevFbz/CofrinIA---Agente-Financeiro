from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
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
    action = re.search(
        r"(?:me\s+lembre|me\s+lembra|lembre[- ]me|me\s+lembrar|lembrar|lembra|"
        r"criar\s+(?:um\s+)?lembrete|lembrete|"
        r"(?:pode\s+)?me\s+lembrar|me\s+avise|avise[- ]me|"
        r"quero\s+que\s+me\s+lembre)",
        cleaned_text,
        re.IGNORECASE,
    )
    if not action:
        raise ValueError("informe o que devo lembrar")

    before_action = cleaned_text[: action.start()].strip(" ,;:.")
    after_action = cleaned_text[action.end() :].strip(" .!?\t")
    if before_action and not re.search(
        r"\b(?:dia|às|as|em|daqui\s+a|hoje|amanhã|amanha|"
        r"segunda(?:-feira|\s+feira)?|terça(?:-feira|\s+feira)?|terca(?:-feira|\s+feira)?|"
        r"quarta(?:-feira|\s+feira)?|quinta(?:-feira|\s+feira)?|sexta(?:-feira|\s+feira)?|"
        r"sábado|sabado|domingo)\b|\d{1,2}[:/]\d{1,2}",
        before_action,
        re.IGNORECASE,
    ):
        raise ValueError("informe o que devo lembrar")
    after_action = re.sub(
        r"^(?:(?:de|para|que)\s+)(?:o\s+)?",
        "",
        after_action,
        flags=re.IGNORECASE,
    ).strip()
    if before_action:
        schedule_source = before_action
        body = after_action
    else:
        schedule_source = after_action
        body = after_action
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

    month_names = {
        "janeiro": 1,
        "fevereiro": 2,
        "março": 3,
        "marco": 3,
        "abril": 4,
        "maio": 5,
        "junho": 6,
        "julho": 7,
        "agosto": 8,
        "setembro": 9,
        "outubro": 10,
        "novembro": 11,
        "dezembro": 12,
    }
    weekday_names = {
        "segunda-feira": 0,
        "segunda feira": 0,
        "terça-feira": 1,
        "terca-feira": 1,
        "terça feira": 1,
        "terca feira": 1,
        "quarta-feira": 2,
        "quarta feira": 2,
        "quinta-feira": 3,
        "quinta feira": 3,
        "sexta-feira": 4,
        "sexta feira": 4,
        "sábado": 5,
        "sabado": 5,
        "domingo": 6,
    }
    month_pattern = "|".join(sorted(month_names, key=len, reverse=True))
    weekday_pattern = "|".join(sorted(weekday_names, key=len, reverse=True))
    date_pattern = re.compile(
        rf"(?:\b(?:no\s+)?dia\s+)?(?P<day>\d{{1,2}})\s*(?:/|-|de\s+)\s*"
        rf"(?P<month_num>\d{{1,2}})(?:\s*(?:/|-)\s*(?P<year_num>\d{{2,4}}))?"
        rf"|(?:\b(?:no\s+)?dia\s+)?(?P<day_name>\d{{1,2}})\s+de\s+(?P<month_name>{month_pattern})"
        rf"(?:\s+de\s+(?P<year_name>\d{{4}}))?",
        re.IGNORECASE,
    )
    # The first alternative above is numeric day/month; the second is month name.
    date_match = date_pattern.search(schedule_source)
    if not date_match and schedule_source is not body:
        date_match = date_pattern.search(body)

    time_pattern = re.compile(
        r"(?:(?P<marker>às|as|para|pra)\s+)?"
        r"(?P<hour>\d{1,2})"
        r"(?:(?::|h)(?P<minute>\d{2})|(?P<hour_suffix>h))?"
        r"(?:\s+(?P<clock_unit>horas?))?"
        r"(?:\s+da\s+(?P<period>manhã|manha|tarde|noite))?",
        re.IGNORECASE,
    )
    time_match = None
    for candidate in time_pattern.finditer(schedule_source):
        if candidate.group("marker") or candidate.group("minute") or candidate.group("hour_suffix") or candidate.group("clock_unit") or candidate.group("period"):
            time_match = candidate
    if not time_match and schedule_source is not body:
        for candidate in time_pattern.finditer(body):
            if candidate.group("marker") or candidate.group("minute") or candidate.group("hour_suffix") or candidate.group("clock_unit") or candidate.group("period"):
                time_match = candidate

    weekday_match = None
    weekday_pattern_re = re.compile(rf"(?P<weekday>{weekday_pattern})", re.IGNORECASE)
    weekday_source = schedule_source if date_match or schedule_source is not body else body
    weekday_match = weekday_pattern_re.search(weekday_source)
    named_day_match = None
    if not date_match and not weekday_match and schedule_source is body and time_match:
        named_day_match = re.search(r"\b(?P<named_day>hoje|amanhã|amanha)\b", body[: time_match.start()], re.IGNORECASE)

    if date_match or time_match or weekday_match or named_day_match:
        if not time_match:
            raise ValueError("informe o horário do lembrete")
        hour = int(time_match.group("hour"))
        minute = int(time_match.group("minute") or 0)
        period = (time_match.group("period") or "").casefold()
        if not 0 <= minute <= 59:
            raise ValueError("os minutos do lembrete devem estar entre 00 e 59")
        if not 0 <= hour <= 23:
            raise ValueError("a hora do lembrete deve estar entre 00 e 23")
        if period in {"noite", "tarde"} and 1 <= hour <= 11:
            hour += 12
        elif period in {"manhã", "manha"} and hour == 12:
            hour = 0

        target_date = current.date()
        explicit_day = ""
        if date_match:
            explicit_day = date_match.group("day") or date_match.group("day_name")
            month_raw = date_match.group("month_num") or date_match.group("month_name")
            month = int(month_raw) if month_raw.isdigit() else month_names[month_raw.casefold()]
            year_raw = date_match.group("year_num") or date_match.group("year_name")
            year = int(year_raw) if year_raw else current.year
            if year < 100:
                year += 2000
            try:
                target_date = date(year, month, int(explicit_day))
            except ValueError as exc:
                raise ValueError("a data do lembrete é inválida") from exc
            if not year_raw and target_date < current.date():
                target_date = date(year + 1, month, int(explicit_day))
            schedule_start = date_match.start()
            schedule_end = max(date_match.end(), time_match.end())
        elif weekday_match:
            desired_weekday = weekday_names[weekday_match.group("weekday").casefold()]
            days_ahead = (desired_weekday - current.weekday()) % 7
            target_date = current.date() + timedelta(days=days_ahead)
            schedule_start = weekday_match.start()
            schedule_end = max(weekday_match.end(), time_match.end())
        elif named_day_match:
            if named_day_match.group("named_day").casefold() in {"amanhã", "amanha"}:
                target_date += timedelta(days=1)
            schedule_start = named_day_match.start()
            schedule_end = time_match.end()
        else:
            schedule_start = time_match.start()
            schedule_end = time_match.end()

        if weekday_match and date_match:
            expected_weekday = weekday_names[weekday_match.group("weekday").casefold()]
            if target_date.weekday() != expected_weekday:
                raise ValueError("a data e o dia da semana não conferem")

        if before_action:
            message = body
        else:
            message = (body[:schedule_start] + " " + body[schedule_end:]).strip(" .,!?:;\t")
            message = re.sub(r"^(?:para\s+)?(?:o\s+)?$", "", message, flags=re.IGNORECASE).strip()
        if not message or re.fullmatch(r"\d{1,2}/\d{1,2}(?:/\d{2,4})?", message):
            raise ValueError("informe o que devo lembrar")
        due_at = datetime.combine(target_date, datetime.min.time(), tzinfo=brazil).replace(hour=hour, minute=minute)
        if due_at <= current and not date_match and not weekday_match:
            due_at += timedelta(days=1)
        return ReminderDraft(message, due_at)

    if re.fullmatch(r"\d{1,2}/\d{1,2}(?:/\d{2,4})?", body):
        raise ValueError("informe o que devo lembrar")
    return ReminderDraft(body, current + timedelta(hours=3))
