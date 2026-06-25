"""End-to-end tests for VoiceTrack's offline transaction pipeline."""

from __future__ import annotations

import unittest
from uuid import uuid4
from datetime import date, datetime
from pathlib import Path

from voicetrack.db import Database, DateRange, period_to_range
from voicetrack.extractors import rule_based_extract
from voicetrack.pipeline import ExtractionError, TransactionPipeline


class FakeExtractor:
    """Small fake LLM used so tests never depend on Ollama."""

    def __init__(self, response: dict):
        self.response = response

    def extract(self, text: str, now: datetime | None = None) -> dict:
        return dict(self.response)


class PipelineTest(unittest.TestCase):
    """Pipeline tests that run with unittest or pytest."""

    def setUp(self):
        root = Path.cwd() / ".testdata"
        root.mkdir(exist_ok=True)
        self.test_root = root
        self.db_path = root / f"voice_{uuid4().hex}.db"

    def tearDown(self):
        if self.db_path.exists():
            self.db_path.unlink()
        try:
            self.test_root.rmdir()
        except OSError:
            pass

    def db(self) -> Database:
        return Database(self.db_path)

    def test_valid_voice_text_extracts_json_inserts_db_and_notifies_ui(self):
        db = self.db()
        ui_updates = []
        extractor = FakeExtractor(
            {
                "type": "expense",
                "amount": 1500,
                "category": "Utilities",
                "description": "electricity bill",
                "date": "today",
                "time": None,
                "confidence": "high",
            }
        )
        pipeline = TransactionPipeline(db, extractor, on_saved=ui_updates.append)

        saved = pipeline.process_and_save(
            "spent 1500 on electricity bill today",
            now=datetime(2026, 6, 25, 14, 30),
        )

        self.assertEqual(saved["id"], 1)
        self.assertEqual(saved["type"], "expense")
        self.assertEqual(saved["amount"], 1500)
        self.assertEqual(saved["date"], "2026-06-25")
        self.assertEqual(saved["time"], "14:30")
        self.assertEqual(db.list_transactions()[0]["description"], "electricity bill")
        self.assertEqual(ui_updates, [saved])

    def test_missing_amount_returns_error(self):
        db = self.db()
        pipeline = TransactionPipeline(
            db,
            FakeExtractor(
                {
                    "type": "expense",
                    "category": "Food & Groceries",
                    "description": "lunch",
                    "date": "today",
                    "time": None,
                    "confidence": "low",
                }
            ),
        )

        with self.assertRaisesRegex(ExtractionError, "Amount is missing"):
            pipeline.preview("spent on lunch")

    def test_missing_category_defaults_to_other(self):
        db = self.db()
        pipeline = TransactionPipeline(
            db,
            FakeExtractor(
                {
                    "type": "expense",
                    "amount": 700,
                    "description": "unclear purchase",
                    "date": "today",
                    "time": None,
                    "confidence": "high",
                }
            ),
        )

        saved = pipeline.process_and_save("spent 700 somewhere", now=datetime(2026, 6, 25, 9, 0))

        self.assertEqual(saved["category"], "Other")

    def test_db_write_and_read_verification(self):
        db = self.db()
        row_id = db.add_transaction(
            {
                "type": "income",
                "amount": 80000,
                "category": "Salary",
                "description": "monthly salary",
                "date": "2026-06-01",
                "time": "10:00",
            }
        )

        rows = db.list_transactions()

        self.assertEqual(row_id, 1)
        self.assertEqual(rows[0]["type"], "income")
        self.assertEqual(rows[0]["amount"], 80000)

    def test_daily_weekly_monthly_filters_are_accurate(self):
        db = self.db()
        for item in [
            ("expense", 100, "Food & Groceries", "today", "2026-06-25"),
            ("expense", 200, "Transport", "same week", "2026-06-22"),
            ("expense", 300, "Utilities", "same month", "2026-06-03"),
            ("expense", 400, "Rent", "old", "2026-05-31"),
        ]:
            db.add_transaction(
                {
                    "type": item[0],
                    "amount": item[1],
                    "category": item[2],
                    "description": item[3],
                    "date": item[4],
                    "time": "08:00",
                }
            )

        today = date(2026, 6, 25)

        self.assertEqual(db.totals(period_to_range("today", today))["expense"], 100)
        self.assertEqual(db.totals(period_to_range("week", today))["expense"], 300)
        self.assertEqual(db.totals(period_to_range("month", today))["expense"], 600)
        self.assertEqual(db.totals(DateRange(None, None))["expense"], 1000)

    def test_income_vs_expense_balance_calculation(self):
        db = self.db()
        db.add_transaction(
            {
                "type": "income",
                "amount": 1000,
                "category": "Freelance",
                "description": "project",
                "date": "2026-06-25",
                "time": "09:00",
            }
        )
        db.add_transaction(
            {
                "type": "expense",
                "amount": 250,
                "category": "Food & Groceries",
                "description": "groceries",
                "date": "2026-06-25",
                "time": "10:00",
            }
        )

        totals = db.totals(period_to_range("today", date(2026, 6, 25)))

        self.assertEqual(totals, {"income": 1000, "expense": 250, "net": 750})

    def test_rule_based_fallback_handles_screenshot_phrase(self):
        extracted = rule_based_extract("done shopping of 3000.", now=datetime(2026, 6, 25, 16, 30))

        self.assertEqual(extracted["type"], "expense")
        self.assertEqual(extracted["amount"], 3000)
        self.assertEqual(extracted["category"], "Shopping")
        self.assertEqual(extracted["description"], "shopping")
        self.assertEqual(extracted["confidence"], "low")


if __name__ == "__main__":
    unittest.main()
