"""Financial Rule Engine — the deterministic truth layer.

This is pure Python: NO LLM, NO language interpretation. It consumes validated
FinancialEvent objects and computes financial state independently per KPI, exactly
as specified in skills/rule_engine.md. Net worth is assets - liabilities, never
income - expenses.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from financial_os.events import EVENT_TYPES, FinancialEvent


class RuleEngineError(ValueError):
    """Raised when an event cannot be applied to the financial state."""


@dataclass
class FinancialState:
    income: int = 0
    expenses: int = 0
    cash_in: int = 0
    cash_out: int = 0
    receivables: int = 0
    payables: int = 0
    savings: int = 0
    investments: int = 0
    opening_cash: int = 0

    @property
    def current_cash(self) -> int:
        return self.opening_cash + self.cash_in - self.cash_out

    @property
    def assets(self) -> int:
        # Assets computed independently: liquid cash + money owed to user + parked
        # savings/investments. No shortcut through net_worth.
        return self.current_cash + self.receivables + self.savings + self.investments

    @property
    def liabilities(self) -> int:
        return self.payables

    @property
    def net_worth(self) -> int:
        return self.assets - self.liabilities  # NEVER income - expenses

    def kpis(self) -> dict:
        return {
            "income": self.income,
            "expenses": self.expenses,
            "cash_in": self.cash_in,
            "cash_out": self.cash_out,
            "current_cash": self.current_cash,
            "receivables": self.receivables,
            "payables": self.payables,
            "savings": self.savings,
            "investments": self.investments,
            "assets": self.assets,
            "liabilities": self.liabilities,
            "net_worth": self.net_worth,
        }


def _as_event(event: object) -> FinancialEvent:
    if isinstance(event, FinancialEvent):
        return event
    if isinstance(event, dict):
        # Only known fields; ignore extras so upstream snapshots stay flexible.
        known = {k: v for k, v in event.items() if k in FinancialEvent.__dataclass_fields__}
        return FinancialEvent(**known)
    raise RuleEngineError(f"Not a FinancialEvent: {event!r}")


def _apply(state: FinancialState, ev: FinancialEvent, log: list[str]) -> None:
    amount = ev.amount
    t = ev.event_type

    if t == "income":
        state.income += amount
        state.cash_in += amount
    elif t == "expense":
        state.expenses += amount
        state.cash_out += amount
    elif t == "loan_taken":
        state.cash_in += amount
        state.payables += amount
    elif t == "loan_given":
        state.cash_out += amount
        state.receivables += amount
    elif t == "loan_repayment_made":
        state.cash_out += amount
        state.payables -= amount
        if state.payables < 0:
            raise RuleEngineError("repayment_exceeds_balance")
    elif t == "loan_repayment_received":
        state.cash_in += amount
        state.receivables -= amount
        if state.receivables < 0:
            raise RuleEngineError("repayment_exceeds_balance")
    elif t in ("transfer",):
        pass  # no income, no expense, no net-worth change
    elif t == "saving":
        state.cash_out += amount
        state.savings += amount
    elif t == "investment":
        state.cash_out += amount
        state.investments += amount
    elif t == "shared_expense":
        details = ev.split_details or {}
        my_share = int(details.get("my_share", amount))
        receivable = int(details.get("receivable_total", 0))
        payable = int(details.get("payable_total", 0))
        state.expenses += my_share
        if ev.payer == "me":
            state.cash_out += my_share + receivable  # user fronted the whole bill
            state.receivables += receivable
        else:
            state.payables += payable  # someone else paid; user owes their share
    elif t == "loan_clear":
        raise RuleEngineError("loan_clear must be expanded to a repayment event")
    else:
        raise RuleEngineError("unknown_event_type")

    log.append(f"{t} {amount} -> cash_in={state.cash_in} cash_out={state.cash_out} "
               f"recv={state.receivables} pay={state.payables}")


def compute(events: list, opening_cash: int = 0) -> dict:
    """Compute financial state from validated events. Returns the debug envelope."""
    state = FinancialState(opening_cash=int(opening_cash))
    log: list[str] = []
    errors: list[str] = []
    snapshot = []
    try:
        for raw in events:
            ev = _as_event(raw)
            snapshot.append({"event_type": ev.event_type, "amount": ev.amount})
            _apply(state, ev, log)
        status = "PASS"
    except RuleEngineError as exc:
        errors.append(str(exc))
        status = "FAIL"

    return {
        "stage": "rule_engine",
        "input": snapshot,
        "output": state.kpis(),
        "validation_status": status,
        "errors": errors,
        "debug_log": " | ".join(log),
    }
