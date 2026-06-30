from pathlib import Path
from uuid import uuid4

import pytest

import voicetrack.db as db
from voicetrack.finance_intents import parse_special_intent, build_plan_from_spec


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


def test_shared_percent_natural_phrasing(tmp_db):
    plan, txs = _apply("I paid for cab 1000 and ali will send me 50% later", tmp_db)
    assert plan["intent"] == "shared_expense"
    assert plan["payer"] == "me"
    assert plan["total_paid"] == 1000
    assert plan["components"][0]["my_share"] == 500

    ali = next(a for a in db.get_loan_accounts(path=tmp_db) if a["person_name"] == "Ali")
    assert ali["current_balance"] == 500
    assert ali["loan_type"] == "owed_to_me"


@pytest.mark.parametrize("text", [
    "I paid for cab 1000 and ali will send me 50% later",
    "paid 2000 for dinner, sara owes me 25%",
    "paid 800 cab, ali will give me 50%",
])
def test_shared_percent_variants_detected(text):
    plan = parse_special_intent(text)
    assert plan is not None
    assert plan["intent"] == "shared_expense"
    assert plan["people"], "a participant should be detected"


# --- LLM spec -> plan resolver (no model needed) ----------------------------

def test_build_plan_loan_from_spec():
    plan = build_plan_from_spec({"intent": "loan_given", "person": "sara", "amount": "2,000"})
    assert plan == {"intent": "loan_given", "person": "Sara", "amount": 2000.0,
                    "date": "today", "notes": "", "confidence": "high"}


def test_build_plan_loan_clear_from_spec():
    plan = build_plan_from_spec({"intent": "loan_clear", "person": "Ahmed"})
    assert plan["intent"] == "loan_clear"
    assert plan["person"] == "Ahmed"


def test_build_plan_shared_percent_from_spec():
    spec = {"intent": "shared_expense", "payer": "me", "total": 1000,
            "category": "Transport", "description": "cab",
            "participants": ["me", "Ali"],
            "splits": [{"person": "Ali", "mode": "percent", "value": 50}]}
    plan = build_plan_from_spec(spec)
    assert plan["components"][0]["my_share"] == 500
    assert plan["people"] == [{"name": "Ali", "share": 500.0, "paid_back": False}]


def test_build_plan_shared_equal_from_spec():
    spec = {"intent": "shared_expense", "payer": "me", "total": 3000,
            "description": "dinner", "participants": ["me", "Ali", "Sara"], "splits": []}
    plan = build_plan_from_spec(spec)
    assert plan["components"][0]["my_share"] == 1000
    assert {p["name"]: p["share"] for p in plan["people"]} == {"Ali": 1000.0, "Sara": 1000.0}


def test_build_plan_shared_paid_by_other_from_spec():
    spec = {"intent": "shared_expense", "payer": "Ali", "total": 2000,
            "category": "Food & Groceries", "description": "lunch",
            "participants": ["me", "Ali"], "splits": []}
    plan = build_plan_from_spec(spec)
    assert plan["payer"] == "Ali"
    assert plan["components"][0]["my_share"] == 1000


def test_build_plan_rejects_none_and_invalid():
    assert build_plan_from_spec({"intent": "none"}) is None
    assert build_plan_from_spec({"intent": "loan_given", "person": "Ali"}) is None  # no amount
    assert build_plan_from_spec({"intent": "shared_expense", "total": 0}) is None
    assert build_plan_from_spec("not a dict") is None


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


def test_shared_expense_paid_by_other_creates_payable(tmp_db):
    plan, txs = _apply(
        "we go to the market ali and i, ali paid 3000 and it shared equally",
        tmp_db,
    )

    assert plan["intent"] == "shared_expense"
    assert plan["payer"] == "Ali"
    assert plan["total_paid"] == 3000
    assert plan["components"][0]["category"] == "Shopping"
    assert plan["components"][0]["my_share"] == 1500

    assert len(txs) == 2
    accounts = db.get_loan_accounts(path=tmp_db)
    ali = next(a for a in accounts if a["person_name"] == "Ali")
    assert ali["loan_type"] == "i_owe"
    assert ali["current_balance"] == 1500

    summary = db.get_finance_summary(path=tmp_db)
    assert summary["cash_outflow"] == 0
    assert summary["personal_expenses"] == 1500
    assert summary["outstanding_payables"] == 1500
    assert summary["net_worth"] == -1500


def test_shared_expense_paid_by_other_with_covered_wording(tmp_db):
    plan, _ = _apply("Ali covered dinner 2000 and we split equally", tmp_db)

    assert plan["payer"] == "Ali"
    assert plan["components"][0]["category"] == "Food & Groceries"
    assert plan["components"][0]["my_share"] == 1000

    ali = next(a for a in db.get_loan_accounts(path=tmp_db) if a["person_name"] == "Ali")
    assert ali["loan_type"] == "i_owe"
    assert ali["current_balance"] == 1000


def test_loan_clear_uses_existing_account_balance(tmp_db):
    _apply("I lent Ali 5000.", tmp_db)
    plan, txs = _apply("loan from Ali is cleared", tmp_db)

    assert plan["intent"] == "loan_clear"
    assert len(txs) == 1
    assert txs[0]["kind"] == "loan_repayment_received"
    assert txs[0]["amount"] == 5000

    ali = next(a for a in db.get_loan_accounts(path=tmp_db) if a["person_name"] == "Ali")
    assert ali["status"] == "Paid"
    assert ali["current_balance"] == 0


# --- Mark-as-paid / remove ---------------------------------------------------

def test_settle_loan_account_marks_paid_and_clears_balance(tmp_db):
    _apply("I borrowed 10000 from Ahmed.", tmp_db)
    ahmed = next(a for a in db.get_loan_accounts(path=tmp_db) if a["person_name"] == "Ahmed")

    db.settle_loan_account(ahmed["id"], path=tmp_db)

    ahmed = next(a for a in db.get_loan_accounts(path=tmp_db) if a["person_name"] == "Ahmed")
    assert ahmed["status"] == "Paid"
    assert ahmed["current_balance"] == 0
    # Settling a payable is a repayment, not an expense.
    summary = db.get_finance_summary(path=tmp_db)
    assert summary["outstanding_payables"] == 0
    assert summary["personal_expenses"] == 0


def test_cannot_remove_active_loan(tmp_db):
    _apply("I lent Sara 5000.", tmp_db)
    sara = next(a for a in db.get_loan_accounts(path=tmp_db) if a["person_name"] == "Sara")
    with pytest.raises(ValueError):
        db.delete_loan_account(sara["id"], path=tmp_db)
    # still there
    assert any(a["person_name"] == "Sara" for a in db.get_loan_accounts(path=tmp_db))


def test_remove_after_settle_keeps_cash_history(tmp_db):
    _apply("I lent Sara 5000.", tmp_db)
    sara = next(a for a in db.get_loan_accounts(path=tmp_db) if a["person_name"] == "Sara")
    db.settle_loan_account(sara["id"], path=tmp_db)
    cash_before = db.get_finance_summary(path=tmp_db)["cash"]

    db.delete_loan_account(sara["id"], path=tmp_db)

    assert not any(a["person_name"] == "Sara" for a in db.get_loan_accounts(path=tmp_db))
    # Cash history (loan given then collected) is unchanged by removing the record.
    assert db.get_finance_summary(path=tmp_db)["cash"] == cash_before


def test_settle_and_remove_shared_group(tmp_db):
    plan, _ = _apply("split dinner 3000 with Ali and Sara", tmp_db)
    group = db.get_shared_expense_groups(path=tmp_db)[0]
    assert db.shared_group_outstanding(group["id"], path=tmp_db) != 0

    db.settle_shared_group(group["id"], path=tmp_db)
    assert db.shared_group_outstanding(group["id"], path=tmp_db) == 0
    summary = db.get_finance_summary(path=tmp_db)
    assert summary["outstanding_receivables"] == 0

    db.delete_shared_group(group["id"], path=tmp_db)
    assert db.get_shared_expense_groups(path=tmp_db) == []


def test_cannot_remove_unsettled_shared_group(tmp_db):
    _apply("split dinner 3000 with Ali and Sara", tmp_db)
    group = db.get_shared_expense_groups(path=tmp_db)[0]
    with pytest.raises(ValueError):
        db.delete_shared_group(group["id"], path=tmp_db)
