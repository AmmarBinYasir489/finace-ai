"""Canonical FinancialEvent and deterministic number normalization.

The Parser/Auditor agents interpret language, but number normalization is a
deterministic step (spec section 7): every amount becomes an integer. This module
provides that normalizer plus the event vocabulary the Rule Engine consumes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Event vocabulary shared by parser, auditor, loan, shared_expense, rule_engine.
EVENT_TYPES = frozenset({
    "income",
    "expense",
    "loan_given",
    "loan_taken",
    "loan_repayment_made",
    "loan_repayment_received",
    "loan_clear",
    "shared_expense",
    "transfer",
    "saving",
    "investment",
})

# Multipliers for shorthand and word-scale magnitudes (Pakistani + western).
_SCALE = {
    "k": 1_000,
    "thousand": 1_000,
    "lac": 100_000,
    "lakh": 100_000,
    "lakhs": 100_000,
    "m": 1_000_000,
    "million": 1_000_000,
    "crore": 10_000_000,
    "cr": 10_000_000,
    "b": 1_000_000_000,
    "billion": 1_000_000_000,
}

_NUM_WITH_SCALE = re.compile(
    r"(?P<num>\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<scale>k|m|b|lac|lakhs?|cr|crore|thousand|million|billion)?",
    re.IGNORECASE,
)


class NormalizationError(ValueError):
    """Raised when a value cannot be normalized to an integer amount."""


def normalize_amount(value: object) -> int:
    """Convert a money mention to an integer.

    Examples: 80000 -> 80000, "80k" -> 80000, "1.5k" -> 1500,
    "80 thousand" -> 80000, "5 lakh" -> 500000, "1,500" -> 1500.
    Fractional results are rounded to the nearest integer (max 0.01 tolerance
    expected upstream).
    """
    if isinstance(value, bool):  # bool is an int subclass; reject explicitly.
        raise NormalizationError(f"Invalid amount: {value!r}")
    if isinstance(value, (int, float)):
        return int(round(float(value)))

    text = str(value).strip().lower()
    if not text:
        raise NormalizationError("Empty amount")

    match = _NUM_WITH_SCALE.fullmatch(text.replace(" ", "")) or _NUM_WITH_SCALE.fullmatch(
        text
    )
    # Allow "80 thousand" (space between number and word scale).
    if not match:
        parts = text.split()
        if len(parts) == 2 and parts[1] in _SCALE:
            match = _NUM_WITH_SCALE.fullmatch(parts[0])
            scale = _SCALE[parts[1]]
            if match:
                base = float(match.group("num").replace(",", ""))
                return int(round(base * scale))
        raise NormalizationError(f"Cannot normalize amount: {value!r}")

    base = float(match.group("num").replace(",", ""))
    scale_word = (match.group("scale") or "").lower()
    multiplier = _SCALE.get(scale_word, 1)
    return int(round(base * multiplier))


@dataclass
class FinancialEvent:
    """Canonical event the Rule Engine computes on.

    Mirrors the spec schema (section 6). Fields the parser/auditor cannot fill
    stay at their defaults.
    """

    intent: str
    event_type: str
    amount: int
    currency: str = "PKR"
    datetime: str | None = None
    merchant: str | None = None
    category: str | None = None
    participants: list[str] = field(default_factory=list)
    payer: str = "me"
    receiver: str | None = None
    split_details: dict | None = None
    loan_reference: str | None = None
    budget_reference: str | None = None
    notes: str = ""
    confidence_score: float = 0.0
    original_input: str = ""
    transaction_id: str | None = None
    parser_version: str = "0.1"
    auditor_version: str = "0.1"

    def __post_init__(self) -> None:
        self.amount = normalize_amount(self.amount)
        if self.event_type not in EVENT_TYPES:
            raise NormalizationError(f"Unknown event_type: {self.event_type!r}")
