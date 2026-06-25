"""Validation and persistence pipeline for extracted transactions."""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from .constants import TRANSACTION_TYPES
from .db import Database
from .extractors import Extractor, category_or_other


class ExtractionError(ValueError):
    """Raised when the app cannot safely save a transaction."""


class TransactionPipeline:
    """Turn plain English into validated database rows."""

    def __init__(self, database: Database, extractor: Extractor, on_saved: Callable[[dict], None] | None = None):
        self.database = database
        self.extractor = extractor
        self.on_saved = on_saved

    def preview(self, text: str, now: datetime | None = None) -> dict:
        """Extract and normalize fields without saving them."""
        if not text.strip():
            raise ExtractionError("Please type or speak a transaction first.")
        now = now or datetime.now()
        extracted = self.extractor.extract(text.strip(), now=now)
        return normalize_extraction(extracted, text.strip(), now)

    def save(self, item: dict) -> dict:
        """Save a normalized transaction and notify the UI."""
        normalized = normalize_extraction(item, item.get("description", ""), datetime.now())
        transaction_id = self.database.add_transaction(normalized)
        saved = {**normalized, "id": transaction_id}
        if self.on_saved:
            self.on_saved(saved)
        return saved

    def process_and_save(self, text: str, now: datetime | None = None) -> dict:
        """Convenience method used by tests and quick entry flows."""
        return self.save(self.preview(text, now=now))


def normalize_extraction(extracted: dict, original_text: str, now: datetime) -> dict:
    """Validate LLM output and fill missing date, time, and category safely."""
    if "error" in extracted:
        raise ExtractionError(str(extracted["error"]))

    tx_type = str(extracted.get("type", "")).lower().strip()
    if tx_type not in TRANSACTION_TYPES:
        raise ExtractionError("Could not decide whether this is income or an expense.")

    amount = extracted.get("amount")
    try:
        amount_value = float(amount)
    except (TypeError, ValueError) as exc:
        raise ExtractionError("Amount is missing. Please include a number.") from exc
    if amount_value <= 0:
        raise ExtractionError("Amount must be greater than zero.")

    raw_date = str(extracted.get("date") or "today").strip().lower()
    if raw_date in {"today", "none", "null", ""}:
        date_value = now.date().isoformat()
    else:
        try:
            date_value = datetime.strptime(raw_date, "%Y-%m-%d").date().isoformat()
        except ValueError as exc:
            raise ExtractionError("Date must be in YYYY-MM-DD format.") from exc

    raw_time = extracted.get("time")
    if raw_time in {None, "", "null", "None"}:
        time_value = now.strftime("%H:%M")
    else:
        time_text = str(raw_time).strip()
        try:
            time_value = datetime.strptime(time_text, "%H:%M").strftime("%H:%M")
        except ValueError as exc:
            raise ExtractionError("Time must be in HH:MM format.") from exc

    confidence = str(extracted.get("confidence") or "low").lower()
    if confidence not in {"high", "low"}:
        confidence = "low"

    description = str(extracted.get("description") or original_text).strip()
    return {
        "type": tx_type,
        "amount": amount_value,
        "category": category_or_other(extracted.get("category")),
        "description": description,
        "date": date_value,
        "time": time_value,
        "confidence": confidence,
    }
