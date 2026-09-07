from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.domain.transactions import TransactionDraft, parse_transaction_text


class FinanceState(TypedDict, total=False):
    message: str
    transaction: TransactionDraft
    reply: str
    error: str


def parse_message(state: FinanceState) -> FinanceState:
    try:
        return {"transaction": parse_transaction_text(state["message"])}
    except ValueError as exc:
        return {"error": str(exc)}


def compose_reply(state: FinanceState) -> FinanceState:
    if state.get("error"):
        return {"reply": f"Não consegui registrar: {state['error']}. Exemplo: 'gastei R$ 42 no almoço'."}
    transaction = state["transaction"]
    label = "Receita" if transaction.type == "income" else "Despesa"
    formatted = f"{transaction.amount:.2f}".replace(".", ",")
    return {"reply": f"{label} registrada: R$ {formatted} em {transaction.category}."}


def build_finance_graph():
    graph = StateGraph(FinanceState)
    graph.add_node("parse_message", parse_message)
    graph.add_node("compose_reply", compose_reply)
    graph.add_edge(START, "parse_message")
    graph.add_edge("parse_message", "compose_reply")
    graph.add_edge("compose_reply", END)
    return graph.compile()


finance_graph = build_finance_graph()
