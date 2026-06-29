import pytest
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

import voicetrack.db as db_module
from voicetrack.db import (
    init_db, insert_transaction, get_transactions, delete_transaction,
    get_summary, get_category_totals, CATEGORIES,
)


@pytest.fixture()
def tmp_db():
    root = Path.cwd() / ".testdata"
    root.mkdir(exist_ok=True)
    path = str(root / f"test_{uuid4().hex}.db")
    init_db(path=path)
    yield path
    db_file = Path(path)
    if db_file.exists():
        db_file.unlink()


def test_init_creates_tables_and_seeds(tmp_db):
    con = db_module._connect(tmp_db)
    try:
        rows = con.execute("SELECT name FROM categories ORDER BY name").fetchall()
    finally:
        con.close()
    names = {r[0] for r in rows}
    for cat in CATEGORIES:
        assert cat in names


def test_insert_and_get(tmp_db):
    tx_id = insert_transaction(
        {"type": "expense", "amount": 500, "category": "Transport",
         "description": "cab", "date": "today", "time": None, "confidence": "high"},
        path=tmp_db,
    )
    assert isinstance(tx_id, int)
    rows = get_transactions(path=tmp_db)
    assert len(rows) == 1
    assert rows[0]["amount"] == 500.0
    assert rows[0]["date"] == date.today().isoformat()


def test_date_yesterday(tmp_db):
    insert_transaction(
        {"type": "expense", "amount": 100, "category": "Other",
         "date": "yesterday", "confidence": "low"},
        path=tmp_db,
    )
    rows = get_transactions(path=tmp_db)
    expected = (date.today() - timedelta(days=1)).isoformat()
    assert rows[0]["date"] == expected


def test_get_summary(tmp_db):
    insert_transaction({"type": "income", "amount": 10000, "category": "Salary",
                        "date": "today", "confidence": "high"}, path=tmp_db)
    insert_transaction({"type": "expense", "amount": 3000, "category": "Rent",
                        "date": "today", "confidence": "high"}, path=tmp_db)
    s = get_summary(path=tmp_db)
    assert s["total_income"] == 10000.0
    assert s["total_expense"] == 3000.0
    assert s["balance"] == 7000.0
    assert s["this_month_expense"] == 3000.0


def test_delete_transaction(tmp_db):
    tx_id = insert_transaction(
        {"type": "expense", "amount": 200, "category": "Other",
         "date": "today", "confidence": "low"}, path=tmp_db
    )
    delete_transaction(tx_id, path=tmp_db)
    assert get_transactions(path=tmp_db) == []


def test_category_totals_sorted(tmp_db):
    insert_transaction({"type": "expense", "amount": 500, "category": "Transport",
                        "date": "today", "confidence": "high"}, path=tmp_db)
    insert_transaction({"type": "expense", "amount": 1200, "category": "Food & Groceries",
                        "date": "today", "confidence": "high"}, path=tmp_db)
    insert_transaction({"type": "expense", "amount": 300, "category": "Other",
                        "date": "today", "confidence": "low"}, path=tmp_db)
    totals = get_category_totals("expense", path=tmp_db)
    amounts = [t["total"] for t in totals]
    assert amounts == sorted(amounts, reverse=True)
    assert totals[0]["category"] == "Food & Groceries"
