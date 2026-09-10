from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TransactionDraft:
    type: str
    amount: float
    description: str
    category: str
    payment_method: str = "não informado"


_AMOUNT_RE = re.compile(r"(?:r\$\s*)?([0-9]{1,3}(?:\.[0-9]{3})+(?:,[0-9]{1,2})?|[0-9]+(?:[,.][0-9]{1,2})?)\b", re.IGNORECASE)


def _amount_from_text(text: str) -> float:
    match = _AMOUNT_RE.search(text)
    if not match:
        raise ValueError("não encontrei um valor na mensagem")
    raw = match.group(1)
    if "." in raw and "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        raw = raw.replace(",", ".")
    elif "." in raw and (len(raw.rsplit(".", 1)[1]) == 3 or raw.count(".") > 1):
        raw = raw.replace(".", "")
    return float(raw)


def _payment_method(text: str) -> str:
    normalized = text.casefold()
    if re.search(r"\bpix\b", normalized):
        return "Pix"
    if re.search(r"\b(?:dinheiro|esp[eé]cie)\b", normalized):
        return "Dinheiro"

    card_type = re.search(
        r"cart(?:ã|a)o\s+de\s+(cr[eé]dito|d[eé]bito)(?:\s+([\wÀ-ÿ-]+))?",
        normalized,
        flags=re.IGNORECASE,
    )
    if card_type:
        label = "Cartão de crédito" if card_type.group(1).casefold() in {"crédito", "credito"} else "Cartão de débito"
        brand = card_type.group(2)
        return f"{label} {brand.title()}" if brand else label

    plain_card = re.search(r"cart(?:ã|a)o\s+([\wÀ-ÿ-]+)", normalized, flags=re.IGNORECASE)
    if plain_card and plain_card.group(1).casefold() not in {"de", "crédito", "credito", "débito", "debito"}:
        return f"Cartão {plain_card.group(1).title()}"

    if re.search(r"\b(?:cr[eé]dito)\b", normalized):
        return "Cartão de crédito"
    if re.search(r"\b(?:d[eé]bito)\b", normalized):
        return "Cartão de débito"
    return "não informado"


def _category(text: str, transaction_type: str) -> str:
    normalized = text.casefold()
    if transaction_type == "income" and any(word in normalized for word in ("salário", "salario", "ordenado")):
        return "salario"
    categories = {
        "alimentacao": ("almoço", "almoco", "jantar", "lanche", "comida", "restaurante", "mercado", "padaria", "delivery", "ifood"),
        "transporte": ("gasolina", "combustível", "combustivel", "uber", "ônibus", "onibus"),
        "moradia": ("aluguel", "condomínio", "condominio", "luz", "água", "agua"),
        "lazer": ("cinema", "viagem", "jogo", "bar"),
        "saude": ("farmácia", "farmacia", "remédio", "remedio", "consulta", "médico", "medico", "hospital"),
    }
    for category, words in categories.items():
        if any(word in normalized for word in words):
            return category
    return "outros"


def parse_transaction_text(text: str) -> TransactionDraft:
    if not text or not text.strip():
        raise ValueError("a mensagem está vazia")
    normalized = text.casefold()
    transaction_type = "income" if any(word in normalized for word in ("recebi", "ganhei", "salário", "salario")) else "expense"
    amount = _amount_from_text(text)
    payment_method = _payment_method(text)
    description = re.sub(r"\s+", " ", text).strip()
    description = re.sub(r"^(?:(?:um|eu|olha|então|entao)\s+)*(?:gastei|paguei|comprei|recebi|ganhei)\s+", "", description, flags=re.IGNORECASE)
    description = re.sub(r"[.!?]+$", "", description).strip()
    description = re.sub(
        r"^(?:r\$\s*)?[0-9.,]+\s*(?:reais?|real|r\$)?\s*(?:em|no|na|de)?\s*",
        "",
        description,
        flags=re.IGNORECASE,
    )
    description = re.sub(
        r"\s+(?:(?:via|com|usando|no|na)\s+)?(?:pix|dinheiro|esp[eé]cie|cr[eé]dito|d[eé]bito|cart(?:ã|a)o(?:\s+de\s+(?:cr[eé]dito|d[eé]bito))?(?:\s+[\wÀ-ÿ-]+)?)\s*$",
        "",
        description,
        flags=re.IGNORECASE,
    ).strip()
    description = re.sub(
        r"\s+(?:r\$\s*)?[0-9]{1,3}(?:\.[0-9]{3})*(?:,[0-9]{1,2})?\s*$",
        "",
        description,
        flags=re.IGNORECASE,
    ).strip()
    description = re.sub(r"[,.;:!?]+$", "", description).strip()
    return TransactionDraft(
        transaction_type,
        amount,
        description or "não informado",
        _category(text, transaction_type),
        payment_method,
    )
