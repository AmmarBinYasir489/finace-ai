"""Tests for the read-only finance assistant. Every answer must be computed from
the seeded database."""

from pathlib import Path
from uuid import uuid4

import pytest

import voicetrack.db as db
import voicetrack.assistant as assistant
from voicetrack.finance_intents import parse_special_intent


@pytest.fixture()
def tmp_db(monkeypatch):
    root = Path.cwd() / ".testdata"
    root.mkdir(exist_ok=True)
    path = str(root / f"assistant_{uuid4().hex}.db")
    db.init_db(path=path)
    # The assistant calls db with no path; point the module default at the temp db.
    monkeypatch.setattr(db, "_db_path", path)
    yield path
    f = Path(path)
    if f.exists():
        f.unlink()


def _apply(text, path):
    plan = parse_special_intent(text)
    assert plan is not None
    db.apply_finance_plan(plan, path=path)


def _seed(path):
    db.insert_transaction({"type": "income", "amount": 80000, "category": "Salary",
                           "description": "salary", "date": "today"}, path=path)
    db.insert_transaction({"type": "expense", "amount": 2000, "category": "Food & Groceries",
                           "description": "lunch", "date": "today"}, path=path)
    db.insert_transaction({"type": "expense", "amount": 500, "category": "Transport",
                           "description": "cab", "date": "today"}, path=path)
    _apply("I lent Ahmed 5000.", path)
    _apply("I borrowed 10000 from Sara.", path)


def test_spend_on_category(tmp_db):
    _seed(tmp_db)
    reply = assistant.answer("how much did I spend on food")
    assert "2,000" in reply
    assert "Food & Groceries" in reply


def test_total_spend(tmp_db):
    _seed(tmp_db)
    reply = assistant.answer("how much did I spend")
    assert "2,500" in reply  # 2000 food + 500 transport, loans excluded


def test_who_owes_me(tmp_db):
    _seed(tmp_db)
    reply = assistant.answer("who owes me money?")
    assert "Ahmed" in reply
    assert "5,000" in reply


def test_how_much_do_i_owe(tmp_db):
    _seed(tmp_db)
    reply = assistant.answer("how much loan do I need to pay?")
    assert "Sara" in reply
    assert "10,000" in reply


def test_person_specific_loan(tmp_db):
    _seed(tmp_db)
    assert "5,000" in assistant.answer("how much does Ahmed owe me?")
    assert "Sara" in assistant.answer("how much do I owe Sara?")


def test_income_question(tmp_db):
    _seed(tmp_db)
    assert "80,000" in assistant.answer("how much did I earn?")


def test_net_worth_and_cash(tmp_db):
    _seed(tmp_db)
    nw = assistant.answer("what is my net worth?")
    assert "net worth" in nw.lower()
    cash = assistant.answer("how much cash do I have?")
    assert "cash" in cash.lower()


def test_empty_and_unknown_show_help(tmp_db):
    assert "I can answer" in assistant.answer("")
    assert "didn't understand" in assistant.answer("tell me a joke")


def test_last_week_period_filters_by_range(tmp_db):
    from datetime import date, timedelta
    today = date.today()
    this_monday = today - timedelta(days=today.weekday())
    last_monday = this_monday - timedelta(days=7)
    # One expense in last week, one today (this week).
    db.insert_transaction({"type": "expense", "amount": 1111, "category": "Food & Groceries",
                           "description": "last week lunch", "date": last_monday.isoformat()},
                          path=tmp_db)
    db.insert_transaction({"type": "expense", "amount": 2222, "category": "Food & Groceries",
                           "description": "today lunch", "date": today.isoformat()},
                          path=tmp_db)
    reply = assistant.answer("how much did I spend on food last week?")
    assert "1,111" in reply
    assert "2,222" not in reply
    assert "last week" in reply


def test_no_receivables_message(tmp_db):
    db.insert_transaction({"type": "income", "amount": 100, "category": "Other",
                           "description": "x", "date": "today"}, path=tmp_db)
    assert "No one currently owes you" in assistant.answer("who owes me money?")
