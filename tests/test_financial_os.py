"""Tests for the additive Financial OS scaffolding: skill loader, number
normalization, and the deterministic Rule Engine."""

import pytest

from financial_os import skill_loader
from financial_os.events import FinancialEvent, NormalizationError, normalize_amount
from financial_os.rule_engine import compute


# --- Skill loader -----------------------------------------------------------

def test_all_nine_agents_have_skill_files():
    assert skill_loader.all_agents_present()
    assert len(skill_loader.AGENTS) == 9


def test_every_skill_file_has_required_sections():
    for agent in skill_loader.AGENTS:
        missing = skill_loader.validate_skill(agent)
        assert missing == [], f"{agent} missing sections: {missing}"


def test_unknown_agent_rejected():
    with pytest.raises(skill_loader.SkillError):
        skill_loader.load_skill("accountant")


def test_skill_hot_reload(tmp_path, monkeypatch):
    # Point the loader at a temp skills dir and confirm an edit is picked up.
    monkeypatch.setattr(skill_loader, "_SKILLS_DIR", str(tmp_path))
    skill_loader._cache.clear()
    monkeypatch.setattr(skill_loader, "AGENTS", frozenset({"parser"}))
    f = tmp_path / "parser.md"
    f.write_text("ROLE: v1", encoding="utf-8")
    assert "v1" in skill_loader.load_skill("parser").text
    import os, time
    later = os.path.getmtime(f) + 10
    f.write_text("ROLE: v2", encoding="utf-8")
    os.utime(f, (later, later))
    assert "v2" in skill_loader.load_skill("parser").text


# --- Number normalization ---------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    (80000, 80000),
    ("80k", 80000),
    ("25k", 25000),
    ("1.5k", 1500),
    ("80 thousand", 80000),
    ("5 lakh", 500000),
    ("1,500", 1500),
    ("2m", 2_000_000),
    ("1 crore", 10_000_000),
    (1500.0, 1500),
])
def test_normalize_amount(raw, expected):
    assert normalize_amount(raw) == expected


def test_normalize_rejects_garbage():
    with pytest.raises(NormalizationError):
        normalize_amount("a lot of money")
    with pytest.raises(NormalizationError):
        normalize_amount(True)


def test_event_normalizes_amount_on_construction():
    ev = FinancialEvent(intent="expense", event_type="expense", amount="1.5k")
    assert ev.amount == 1500


def test_event_rejects_unknown_type():
    with pytest.raises(NormalizationError):
        FinancialEvent(intent="x", event_type="bribe", amount=10)


# --- Rule Engine ------------------------------------------------------------

def _ev(event_type, amount, **kw):
    return FinancialEvent(intent=event_type, event_type=event_type, amount=amount, **kw)


def test_income_and_expense_basic():
    res = compute([_ev("income", 80000), _ev("expense", 1500)], opening_cash=0)
    out = res["output"]
    assert res["validation_status"] == "PASS"
    assert out["income"] == 80000
    assert out["expenses"] == 1500
    assert out["current_cash"] == 78500
    assert out["net_worth"] == 78500


def test_loan_taken_is_not_income():
    res = compute([_ev("loan_taken", 10000)])
    out = res["output"]
    assert out["income"] == 0          # loan is not income
    assert out["cash_in"] == 10000     # but cash increased
    assert out["payables"] == 10000
    assert out["net_worth"] == 0       # cash up, liability up -> neutral


def test_loan_given_is_not_expense():
    res = compute([_ev("loan_given", 5000)])
    out = res["output"]
    assert out["expenses"] == 0
    assert out["cash_out"] == 5000
    assert out["receivables"] == 5000
    assert out["net_worth"] == 0       # cash down, receivable up -> neutral


def test_loan_lifecycle_nets_out():
    events = [_ev("loan_taken", 10000), _ev("loan_repayment_made", 3000)]
    out = compute(events)["output"]
    assert out["payables"] == 7000
    assert out["current_cash"] == 7000


def test_repayment_exceeding_balance_fails():
    res = compute([_ev("loan_given", 1000), _ev("loan_repayment_received", 5000)])
    assert res["validation_status"] == "FAIL"
    assert "repayment_exceeds_balance" in res["errors"]


def test_transfer_is_net_worth_neutral():
    out = compute([_ev("transfer", 5000)])["output"]
    assert out["income"] == 0 and out["expenses"] == 0
    assert out["net_worth"] == 0


def test_saving_is_asset_movement_not_expense():
    out = compute([_ev("income", 10000), _ev("saving", 4000)])["output"]
    assert out["expenses"] == 0
    assert out["savings"] == 4000
    assert out["current_cash"] == 6000
    assert out["net_worth"] == 10000   # cash 6000 + savings 4000


def test_shared_expense_user_share_only():
    ev = _ev(
        "shared_expense", 3000, payer="me",
        split_details={"my_share": 1000, "receivable_total": 2000},
    )
    out = compute([ev])["output"]
    assert out["expenses"] == 1000          # only user share is expense
    assert out["receivables"] == 2000
    assert out["cash_out"] == 3000          # user fronted the whole bill
    assert out["net_worth"] == -1000        # paid 3000, owns 2000 receivable


def test_net_worth_is_assets_minus_liabilities():
    events = [
        _ev("income", 100000),
        _ev("expense", 20000),
        _ev("loan_given", 5000),
        _ev("loan_taken", 10000),
    ]
    out = compute(events)["output"]
    # cash = 100000 - 20000 - 5000 + 10000 = 85000
    assert out["current_cash"] == 85000
    # assets = cash 85000 + receivables 5000 ; liabilities = payables 10000
    assert out["assets"] == 90000
    assert out["liabilities"] == 10000
    assert out["net_worth"] == 80000
