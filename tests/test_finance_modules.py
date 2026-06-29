from pathlib import Path
from uuid import uuid4

import pytest

import voicetrack.db as db
from voicetrack.finance_intents import parse_special_intent


@pytest.fixture()
def tmp_db():
    root = Path.cwd() / ".testdata"
    root.mkdir(exist_ok=True)
    path = str(root / f"finance_{uuid4().hex}.db")
    db.init_db(path=path)
    yield path
    db_file = Path(path)
    if db_file.exists():
        db_file.unlink()


def _apply(text: str, path: str):
    plan = parse_special_intent(text)
    assert plan is not None
    return plan, db.apply_finance_plan(plan, path=path)


def test_loan_lifecycle_offsets_balance(tmp_db):
    _apply("I borrowed 10000 from Ahmed.", tmp_db)
    _apply("I paid Ahmed 3000.", tmp_db)

    accounts = db.get_loan_accounts(path=tmp_db)
    ahmed = next(a for a in accounts if a["person_name"] == "Ahmed")
    assert ahmed["loan_type"] == "i_owe"
    assert ahmed["current_balance"] == 7000
    assert ahmed["status"] == "Active"


def test_shared_equal_split_creates_personal_expense_and_receivable(tmp_db):
    plan, txs = _apply(
        "I went to Texas Fries with Ali on a cab for 500 and we ate loaded fries for 900. Split equally.",
        tmp_db,
    )

    assert plan["total_paid"] == 1400
    assert sum(c["my_share"] for c in plan["components"]) == 700
    assert plan["people"][0]["share"] == 700
    assert len(txs) == 3

    ali = db.get_loan_accounts(path=tmp_db)[0]
    assert ali["person_name"] == "Ali"
    assert ali["current_balance"] == 700
    assert ali["loan_type"] == "owed_to_me"


def test_shared_percentage_split(tmp_db):
    plan, _ = _apply("I bought groceries for 6000. Ali will pay 40%.", tmp_db)
    assert plan["components"][0]["my_share"] == 3600
    assert plan["people"][0]["share"] == 2400


def test_complex_component_split(tmp_db):
    plan, _ = _apply(
        "Yesterday I went to Savour Foods with Ali and Ahmed. Taxi was 600. "
        "Food was 4500. Taxi should be split among all three. Food only between me and Ali.",
        tmp_db,
    )

    people = {p["name"]: p["share"] for p in plan["people"]}
    assert sum(c["my_share"] for c in plan["components"]) == 2450
    assert people["Ali"] == 2450
    assert people["Ahmed"] == 200

