"""Financial Auditor — deterministic validation/correction before any write.

The LLM/regex parser proposes an intent; the Auditor verifies it against the
deterministic source of truth (the loan ledger) and fixes the one class of error
that loses money: repayment DIRECTION. When the truth is unknowable, it refuses
to guess and asks for clarification instead.

This is pure Python + DB reads. It performs NO arithmetic on balances (that stays
in the rule engine) — it only decides which deterministic action is correct.
"""

from __future__ import annotations

import voicetrack.db as db

_REPAYMENT_INTENTS = {"loan_repayment_made", "loan_repayment_received"}


class ClarificationNeeded(ValueError):
    """Raised when the correct interpretation cannot be determined from state.

    Subclasses ValueError so existing callers that surface ValueError messages
    will show the clarification question to the user instead of guessing.
    """

    def __init__(self, question: str, plan: dict | None = None):
        super().__init__(question)
        self.question = question
        self.plan = plan


def audit_finance_plan(plan: dict, path=None) -> dict:
    """Return a validated/corrected plan, or raise ClarificationNeeded.

    Corrections are recorded under plan['_audit']['corrections'] for the trace.
    """
    intent = str(plan.get("intent", ""))
    corrections: list[str] = []

    if intent in _REPAYMENT_INTENTS:
        person = str(plan.get("person", "")).strip()
        account = db.get_loan_account_by_name(person, path=path) if person else None
        balance = round(float(account["current_balance"]), 2) if account else 0.0

        if not account or balance == 0:
            # No open loan -> "I paid Ahmed 3000" might be a service payment, a
            # gift, or a pre-payment. Refuse to fabricate a repayment.
            raise ClarificationNeeded(
                f"You have no open loan with {person or 'that person'}. "
                f"Was this a repayment, money you lent, or a regular payment?",
                plan,
            )

        # The ledger is the truth: repay whichever side actually has a balance.
        correct = ("loan_repayment_received"
                   if account["loan_type"] == "owed_to_me"
                   else "loan_repayment_made")
        if correct != intent:
            corrections.append(
                f"direction corrected {intent} -> {correct} "
                f"(ledger shows {account['loan_type']} {balance:g} with {person})"
            )
            plan = {**plan, "intent": correct}

        # Don't let a repayment exceed what is actually owed.
        amount = round(float(plan.get("amount", 0)), 2)
        if amount > balance:
            raise ClarificationNeeded(
                f"You only have {balance:g} outstanding with {person}, "
                f"but tried to settle {amount:g}. Please confirm the amount.",
                plan,
            )

    audited = dict(plan)
    audited["_audit"] = {"corrections": corrections}
    return audited
