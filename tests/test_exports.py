import csv
import io
from datetime import date

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy.ext.asyncio import create_async_engine

from app import main as app_module
from app.domain.transactions import TransactionDraft
from app.infrastructure.repository import FinanceRepository
from app.integrations.exports import build_transactions_csv, build_transactions_xlsx


@pytest_asyncio.fixture
async def repository(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'finance.db'}")
    repository = FinanceRepository(engine)
    await repository.initialize()
    yield repository
    await repository.close()


def test_build_transactions_csv_preserves_brazilian_fields_and_money():
    rows = [
        {
            "occurred_on": date(2026, 9, 9),
            "type": "expense",
            "amount_cents": 4250,
            "description": "Almoço",
            "category": "alimentacao",
            "payment_method": "Pix",
        }
    ]

    content = build_transactions_csv(rows).decode("utf-8-sig")
    parsed = list(csv.reader(io.StringIO(content), delimiter=";"))

    assert parsed[0] == ["Data", "Tipo", "Valor", "Descrição", "Categoria", "Pagamento"]
    assert parsed[1] == ["09/09/2026", "Despesa", "42,50", "Almoço", "Alimentação", "Pix"]


@pytest.mark.asyncio
async def test_repository_lists_all_transactions_for_one_phone(repository):
    phone = "5511999999990"
    await repository.add_transaction(
        phone,
        TransactionDraft("expense", 42.50, "Almoço", "alimentacao", "Pix"),
        date(2026, 9, 9),
    )
    await repository.add_transaction(
        phone,
        TransactionDraft("income", 1000.00, "Salário", "salario", "não informado"),
        date(2026, 9, 8),
    )

    rows = await repository.list_transactions(phone)

    assert [row["type"] for row in rows] == ["income", "expense"]
    assert [row["amount_cents"] for row in rows] == [100000, 4250]


def test_build_transactions_xlsx_creates_readable_workbook():
    rows = [
        {
            "occurred_on": date(2026, 9, 9),
            "type": "income",
            "amount_cents": 100000,
            "description": "Salário",
            "category": "salario",
            "payment_method": "não informado",
        }
    ]

    workbook = load_workbook(io.BytesIO(build_transactions_xlsx(rows)))
    sheet = workbook["Lançamentos"]

    assert [cell.value for cell in sheet[1]] == ["Data", "Tipo", "Valor", "Descrição", "Categoria", "Pagamento"]
    values = [cell.value for cell in sheet[2]]
    assert values[0].date() == date(2026, 9, 9)
    assert values[1:] == ["Receita", 1000, "Salário", "Salário", "não informado"]


def test_csv_export_requires_internal_token(monkeypatch):
    monkeypatch.setenv("COFRIN_INTERNAL_TOKEN", "test-token")

    response = TestClient(app_module.app).get("/internal/exports/transactions.csv?phone=5511999999990")

    assert response.status_code == 401


def test_csv_export_returns_only_requested_phone_transactions(monkeypatch):
    monkeypatch.setenv("COFRIN_INTERNAL_TOKEN", "test-token")

    async def fake_list_transactions(phone):
        assert phone == "5511999999990"
        return [
            {
                "occurred_on": date(2026, 9, 9),
                "type": "expense",
                "amount_cents": 4250,
                "description": "Almoço",
                "category": "alimentacao",
                "payment_method": "Pix",
            }
        ]

    monkeypatch.setattr(app_module.finance_service.repository, "list_transactions", fake_list_transactions)

    response = TestClient(app_module.app).get(
        "/internal/exports/transactions.csv?phone=5511999999990",
        headers={"X-Internal-Token": "test-token"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "Almoço" in response.content.decode("utf-8-sig")


def test_xlsx_export_returns_excel_download(monkeypatch):
    monkeypatch.setenv("COFRIN_INTERNAL_TOKEN", "test-token")

    async def fake_list_transactions(phone):
        return []

    monkeypatch.setattr(app_module.finance_service.repository, "list_transactions", fake_list_transactions)

    response = TestClient(app_module.app).get(
        "/internal/exports/transactions.xlsx?phone=5511999999990",
        headers={"X-Internal-Token": "test-token"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert response.content[:2] == b"PK"
