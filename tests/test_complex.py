"""Complex field-level tests for VoiceTrack — edge cases not covered by test_pipeline.py.

Run with:  python -m pytest tests/test_complex.py -v
"""

from __future__ import annotations

import unittest
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

from voicetrack.db import Database, DateRange, period_to_range
from voicetrack.dates import resolve_transaction_date, date_from_text
from voicetrack.extractors import (
    OllamaExtractor,
    _parse_model_json,
    category_or_other,
    rule_based_extract,
)
from voicetrack.pipeline import ExtractionError, TransactionPipeline, normalize_extraction


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

class FakeExtractor:
    def __init__(self, response: dict):
        self.response = response

    def extract(self, text: str, now: datetime | None = None) -> dict:
        return dict(self.response)


class FakeSettings:
    ollama_model = "mistral:7b-instruct-q4_K_M"
    ollama_fallback_model = "llama3.2:3b"
    ollama_host = "http://localhost:11434"
    ollama_timeout_seconds = 20


class BrokenOllamaExtractor(OllamaExtractor):
    def _extract_with_model(self, model: str, text: str, now: datetime) -> dict:
        raise RuntimeError(f"{model} unavailable")


def tmp_db() -> tuple[Database, Path]:
    root = Path.cwd() / ".testdata"
    root.mkdir(exist_ok=True)
    path = root / f"test_{uuid4().hex}.db"
    return Database(path), path


# ===========================================================================
# 1. PIPELINE — edge cases on normalize_extraction
# ===========================================================================

class TestNormalizeEdgeCases(unittest.TestCase):

    def _now(self):
        return datetime(2026, 6, 29, 10, 0)

    # Bug 1 regression — empty string time must not crash
    def test_empty_string_time_falls_back_to_now(self):
        result = normalize_extraction(
            {"type": "expense", "amount": 200, "category": "Food & Groceries",
             "description": "lunch", "date": "2026-06-29", "time": ""},
            "lunch 200",
            self._now(),
        )
        self.assertEqual(result["time"], "10:00")

    def test_none_time_falls_back_to_now(self):
        result = normalize_extraction(
            {"type": "expense", "amount": 200, "category": "Food & Groceries",
             "description": "lunch", "date": "2026-06-29", "time": None},
            "lunch 200",
            self._now(),
        )
        self.assertEqual(result["time"], "10:00")

    def test_string_null_time_falls_back_to_now(self):
        result = normalize_extraction(
            {"type": "expense", "amount": 200, "category": "Food & Groceries",
             "description": "lunch", "date": "2026-06-29", "time": "null"},
            "lunch 200",
            self._now(),
        )
        self.assertEqual(result["time"], "10:00")

    def test_valid_time_is_kept(self):
        result = normalize_extraction(
            {"type": "expense", "amount": 200, "category": "Food & Groceries",
             "description": "lunch", "date": "2026-06-29", "time": "13:45"},
            "lunch 200",
            self._now(),
        )
        self.assertEqual(result["time"], "13:45")

    def test_invalid_time_format_raises(self):
        with self.assertRaises(ExtractionError):
            normalize_extraction(
                {"type": "expense", "amount": 200, "category": "Food & Groceries",
                 "description": "lunch", "date": "2026-06-29", "time": "9pm"},
                "lunch 200",
                self._now(),
            )

    def test_zero_amount_raises(self):
        with self.assertRaises(ExtractionError):
            normalize_extraction(
                {"type": "expense", "amount": 0, "category": "Food & Groceries",
                 "description": "free", "date": "2026-06-29", "time": None},
                "free item",
                self._now(),
            )

    def test_negative_amount_raises(self):
        with self.assertRaises(ExtractionError):
            normalize_extraction(
                {"type": "expense", "amount": -500, "category": "Shopping",
                 "description": "refund", "date": "2026-06-29", "time": None},
                "refund 500",
                self._now(),
            )

    def test_amount_as_string_is_coerced(self):
        result = normalize_extraction(
            {"type": "income", "amount": "75000", "category": "Salary",
             "description": "salary", "date": "2026-06-29", "time": None},
            "salary 75000",
            self._now(),
        )
        self.assertEqual(result["amount"], 75000.0)
        self.assertIsInstance(result["amount"], float)

    def test_unknown_type_raises(self):
        with self.assertRaises(ExtractionError):
            normalize_extraction(
                {"type": "transfer", "amount": 500, "category": "Other",
                 "description": "transfer", "date": "2026-06-29", "time": None},
                "transfer",
                self._now(),
            )

    def test_type_casing_is_normalized(self):
        result = normalize_extraction(
            {"type": "Expense", "amount": 100, "category": "Other",
             "description": "misc", "date": "2026-06-29", "time": None},
            "misc",
            self._now(),
        )
        self.assertEqual(result["type"], "expense")

    def test_unknown_confidence_defaults_to_low(self):
        result = normalize_extraction(
            {"type": "expense", "amount": 100, "category": "Other",
             "description": "misc", "date": "2026-06-29", "time": None,
             "confidence": "medium"},
            "misc",
            self._now(),
        )
        self.assertEqual(result["confidence"], "low")

    def test_out_of_category_list_defaults_to_other(self):
        result = normalize_extraction(
            {"type": "expense", "amount": 100, "category": "Gambling",
             "description": "poker", "date": "2026-06-29", "time": None},
            "poker night",
            self._now(),
        )
        self.assertEqual(result["category"], "Other")

    def test_missing_description_uses_original_text(self):
        result = normalize_extraction(
            {"type": "expense", "amount": 300, "category": "Health",
             "date": "2026-06-29", "time": None},
            "doctor visit 300",
            self._now(),
        )
        self.assertEqual(result["description"], "doctor visit 300")

    def test_error_key_in_extraction_raises(self):
        with self.assertRaises(ExtractionError):
            normalize_extraction(
                {"error": "Could not understand input."},
                "gibberish",
                self._now(),
            )


# ===========================================================================
# 2. DATE RESOLUTION — complex phrasing
# ===========================================================================

class TestDateResolution(unittest.TestCase):

    def _today(self):
        return date(2026, 6, 29)   # Monday

    def test_yesterday(self):
        self.assertEqual(date_from_text("yesterday i bought food", self._today()), date(2026, 6, 28))

    def test_day_before_yesterday(self):
        self.assertEqual(date_from_text("day before yesterday", self._today()), date(2026, 6, 27))

    def test_n_days_ago(self):
        self.assertEqual(date_from_text("3 days ago", self._today()), date(2026, 6, 26))

    def test_n_weeks_ago(self):
        self.assertEqual(date_from_text("2 weeks ago spent on rent", self._today()), date(2026, 6, 15))

    def test_last_week(self):
        self.assertEqual(date_from_text("from last week shopping", self._today()), date(2026, 6, 22))

    def test_last_month(self):
        result = date_from_text("last month electricity", self._today())
        self.assertEqual(result.month, 5)
        self.assertEqual(result.year, 2026)

    def test_last_friday(self):
        # today = Monday 2026-06-29, last Friday = 2026-06-26
        result = date_from_text("last friday dinner", self._today())
        self.assertEqual(result, date(2026, 6, 26))

    def test_explicit_iso_date_passthrough(self):
        now = datetime(2026, 6, 29, 10, 0)
        result = resolve_transaction_date("2026-05-15", "some text", now)
        self.assertEqual(result, "2026-05-15")

    def test_today_keyword_uses_now(self):
        now = datetime(2026, 6, 29, 10, 0)
        result = resolve_transaction_date("today", "bought food today", now)
        self.assertEqual(result, "2026-06-29")

    def test_text_yesterday_overrides_today_in_raw(self):
        # raw_date says "today" but text says "yesterday" → text wins
        now = datetime(2026, 6, 29, 10, 0)
        result = resolve_transaction_date("today", "yesterday bought soap", now)
        self.assertEqual(result, "2026-06-28")

    def test_invalid_date_raises_value_error(self):
        now = datetime(2026, 6, 29, 10, 0)
        with self.assertRaises(ValueError):
            resolve_transaction_date("not-a-date", "no relative phrase here", now)

    def test_last_month_jan_wraps_to_december(self):
        # Jan 15 → last month = Dec 15
        today = date(2026, 1, 15)
        result = date_from_text("last month", today)
        self.assertEqual(result, date(2025, 12, 15))

    def test_last_month_clamps_to_end_of_short_month(self):
        # March 31 → last month Feb → clamps to Feb 28 (non-leap)
        today = date(2026, 3, 31)
        result = date_from_text("last month", today)
        self.assertEqual(result, date(2026, 2, 28))


# ===========================================================================
# 3. RULE-BASED EXTRACTOR — tricky phrases
# ===========================================================================

class TestRuleBasedExtractorEdgeCases(unittest.TestCase):

    def _now(self):
        return datetime(2026, 6, 29, 10, 0)

    def test_income_keyword_received(self):
        result = rule_based_extract("received 50000 salary", self._now())
        self.assertEqual(result["type"], "income")
        self.assertEqual(result["amount"], 50000)
        self.assertEqual(result["category"], "Salary")

    def test_freelance_income(self):
        result = rule_based_extract("client paid me 25000 for the project", self._now())
        self.assertEqual(result["type"], "income")
        self.assertEqual(result["category"], "Freelance")

    def test_transport_beats_food_when_amount_near_cab(self):
        # "texas fries" is food context but amount is for the cab ride
        result = rule_based_extract("i went to texas fries on cab for 400", self._now())
        self.assertEqual(result["category"], "Transport")
        self.assertEqual(result["amount"], 400)

    def test_lakh_number_word(self):
        result = rule_based_extract("received one lakh as salary", self._now())
        self.assertEqual(result["amount"], 100000)

    def test_comma_formatted_number(self):
        result = rule_based_extract("paid 1,500 for groceries", self._now())
        self.assertEqual(result["amount"], 1500)

    def test_decimal_amount(self):
        result = rule_based_extract("spent 49.99 on netflix", self._now())
        self.assertEqual(result["amount"], 49.99)
        self.assertEqual(result["category"], "Entertainment")

    def test_empty_input_returns_error(self):
        result = rule_based_extract("", self._now())
        self.assertIn("error", result)

    def test_no_amount_returns_error(self):
        result = rule_based_extract("bought some stuff", self._now())
        self.assertIn("error", result)

    def test_multi_transaction_splits_on_and(self):
        result = rule_based_extract("paid 800 rent and spent 200 on groceries", self._now())
        self.assertIn("transactions", result)
        txns = result["transactions"]
        self.assertEqual(len(txns), 2)
        categories = {t["category"] for t in txns}
        self.assertIn("Rent", categories)
        self.assertIn("Food & Groceries", categories)

    def test_multi_transaction_confidence_is_low(self):
        result = rule_based_extract("spent 300 on food and 150 on bus", self._now())
        self.assertEqual(result["confidence"], "low")

    def test_multi_transaction_single_amount_not_split(self):
        # Only one number → should NOT split into multiple transactions
        result = rule_based_extract("spent 500 on food and drinks", self._now())
        self.assertNotIn("transactions", result)
        self.assertEqual(result["amount"], 500)

    def test_spelling_correction_mosque(self):
        # Pattern must be "went to X on cab" — the destination regex requires that phrasing.
        # "took cab to X" is NOT supported and returns only the mode word ("cab").
        result = rule_based_extract("i went to faisal osque on cab for 350", self._now())
        self.assertIn("mosque", result["description"])

    def test_spelling_correction_mosque_took_cab_returns_mode_only(self):
        # Known limitation: "took cab to X" → description is just "cab" (destination not extracted).
        result = rule_based_extract("took cab to faisal osque for 350", self._now())
        self.assertEqual(result["description"], "cab")
        self.assertEqual(result["category"], "Transport")

    def test_spelling_correction_restaurant(self):
        result = rule_based_extract("dinner at resturant for 1200", self._now())
        self.assertIn("restaurant", result["description"])

    def test_health_keywords(self):
        result = rule_based_extract("paid 2000 for doctor visit", self._now())
        self.assertEqual(result["category"], "Health")

    def test_education_keywords(self):
        result = rule_based_extract("paid 5000 for tuition fee", self._now())
        self.assertEqual(result["category"], "Education")

    def test_rent_keyword(self):
        result = rule_based_extract("paid 30000 for house rent", self._now())
        self.assertEqual(result["category"], "Rent")

    def test_utilities_bill_keyword(self):
        result = rule_based_extract("internet bill paid 1500", self._now())
        self.assertEqual(result["category"], "Utilities")


# ===========================================================================
# 4. OLLAMA JSON PARSER — Bug 3 regression + edge cases
# ===========================================================================

class TestParseModelJson(unittest.TestCase):

    def test_clean_json_object(self):
        raw = '{"type": "expense", "amount": 500}'
        self.assertEqual(_parse_model_json(raw), {"type": "expense", "amount": 500})

    def test_json_wrapped_in_prose(self):
        raw = 'Sure! Here is the JSON:\n{"type": "income", "amount": 1000}\nHope that helps.'
        result = _parse_model_json(raw)
        self.assertEqual(result["type"], "income")

    def test_json_array_wrapped_as_transactions(self):
        # Bug 3 regression — model returns array instead of object
        raw = '[{"type": "expense", "amount": 200}, {"type": "income", "amount": 500}]'
        result = _parse_model_json(raw)
        self.assertIn("transactions", result)
        self.assertEqual(len(result["transactions"]), 2)

    def test_json_array_in_prose_wrapped_as_transactions(self):
        raw = 'Here are the results:\n[{"type": "expense", "amount": 300}]\nDone.'
        result = _parse_model_json(raw)
        self.assertIn("transactions", result)

    def test_no_json_raises_value_error(self):
        with self.assertRaises((ValueError, Exception)):
            _parse_model_json("I could not understand the input.")

    def test_markdown_code_block_still_parsed(self):
        raw = '```json\n{"type": "expense", "amount": 750}\n```'
        result = _parse_model_json(raw)
        self.assertEqual(result["amount"], 750)


# ===========================================================================
# 5. DATABASE — filtering, ordering, edge cases
# ===========================================================================

class TestDatabaseEdgeCases(unittest.TestCase):

    def setUp(self):
        self.db, self.db_path = tmp_db()

    def tearDown(self):
        if self.db_path.exists():
            self.db_path.unlink()
        try:
            self.db_path.parent.rmdir()
        except OSError:
            pass

    def _add(self, **kwargs):
        defaults = {
            "type": "expense", "amount": 100, "category": "Other",
            "description": "test", "date": "2026-06-29", "time": "10:00",
        }
        defaults.update(kwargs)
        return self.db.add_transaction(defaults)

    def test_rows_returned_newest_first(self):
        self._add(date="2026-06-01", description="old")
        self._add(date="2026-06-29", description="new")
        rows = self.db.list_transactions()
        self.assertEqual(rows[0]["description"], "new")
        self.assertEqual(rows[1]["description"], "old")

    def test_search_filters_by_description(self):
        self._add(description="electricity bill")
        self._add(description="grocery shopping")
        rows = self.db.list_transactions(search="electricity")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["description"], "electricity bill")

    def test_search_is_case_insensitive(self):
        self._add(description="Netflix subscription")
        rows = self.db.list_transactions(search="netflix")
        self.assertEqual(len(rows), 1)

    def test_category_filter(self):
        self._add(category="Health")
        self._add(category="Transport")
        rows = self.db.list_transactions(category="Health")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["category"], "Health")

    def test_type_filter_income_only(self):
        self._add(type="income", amount=5000, category="Salary", description="salary")
        self._add(type="expense", amount=200, category="Food & Groceries", description="lunch")
        rows = self.db.list_transactions(tx_type="income")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["type"], "income")

    def test_delete_removes_row(self):
        row_id = self._add(description="to be deleted")
        self.assertEqual(len(self.db.list_transactions()), 1)
        self.db.delete_transaction(row_id)
        self.assertEqual(len(self.db.list_transactions()), 0)

    def test_delete_nonexistent_row_is_silent(self):
        self.db.delete_transaction(9999)  # must not raise

    def test_totals_with_no_data(self):
        totals = self.db.totals()
        self.assertEqual(totals, {"income": 0.0, "expense": 0.0, "net": 0.0})

    def test_net_is_income_minus_expense(self):
        self._add(type="income", amount=10000, category="Salary", description="salary")
        self._add(type="expense", amount=3000, category="Rent", description="rent")
        totals = self.db.totals()
        self.assertEqual(totals["net"], 7000.0)

    def test_category_breakdown_excludes_income(self):
        self._add(type="income", amount=5000, category="Salary", description="salary")
        self._add(type="expense", amount=800, category="Food & Groceries", description="food")
        breakdown = self.db.category_breakdown()
        categories = [row["category"] for row in breakdown]
        self.assertNotIn("Salary", categories)
        self.assertIn("Food & Groceries", categories)

    def test_category_breakdown_sorted_by_amount_desc(self):
        self._add(category="Health", amount=500)
        self._add(category="Transport", amount=1500)
        self._add(category="Food & Groceries", amount=300)
        breakdown = self.db.category_breakdown()
        amounts = [row["amount"] for row in breakdown]
        self.assertEqual(amounts, sorted(amounts, reverse=True))

    def test_date_range_filter_inclusive_boundaries(self):
        self._add(date="2026-06-01", description="start")
        self._add(date="2026-06-15", description="middle")
        self._add(date="2026-06-30", description="end")
        rows = self.db.list_transactions(date_range=DateRange("2026-06-01", "2026-06-15"))
        self.assertEqual(len(rows), 2)
        descs = {r["description"] for r in rows}
        self.assertIn("start", descs)
        self.assertIn("middle", descs)

    def test_combined_filters_type_and_category(self):
        self._add(type="expense", category="Health", amount=200, description="medicine")
        self._add(type="expense", category="Transport", amount=100, description="cab")
        self._add(type="income", category="Health", amount=0, description="insurance refund")
        rows = self.db.list_transactions(tx_type="expense", category="Health")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["description"], "medicine")

    def test_monthly_income_expense_returns_six_months(self):
        result = self.db.monthly_income_expense(6, today=date(2026, 6, 29))
        self.assertEqual(len(result), 6)
        labels = [row["label"] for row in result]
        self.assertIn("Jun", labels)
        self.assertIn("Jan", labels)

    def test_export_csv_creates_file_with_headers(self):
        self._add(description="csv test")
        out = self.db_path.parent / "export_test.csv"
        self.db.export_csv(out)
        content = out.read_text(encoding="utf-8")
        self.assertIn("id,type,amount,category", content)
        self.assertIn("csv test", content)
        out.unlink()


# ===========================================================================
# 6. PIPELINE MULTI-TRANSACTION — save_many, preview, validation
# ===========================================================================

class TestMultiTransactionPipeline(unittest.TestCase):

    def setUp(self):
        self.db, self.db_path = tmp_db()

    def tearDown(self):
        if self.db_path.exists():
            self.db_path.unlink()
        try:
            self.db_path.parent.rmdir()
        except OSError:
            pass

    def _pipeline(self, response: dict) -> TransactionPipeline:
        return TransactionPipeline(self.db, FakeExtractor(response))

    def test_multi_preview_returns_all_rows(self):
        pipeline = self._pipeline({
            "transactions": [
                {"type": "expense", "amount": 500, "category": "Transport",
                 "description": "cab", "date": "today", "time": None, "confidence": "high"},
                {"type": "expense", "amount": 1200, "category": "Food & Groceries",
                 "description": "dinner", "date": "today", "time": None, "confidence": "high"},
            ]
        })
        preview = pipeline.preview("cab 500 and dinner 1200", now=datetime(2026, 6, 29, 20, 0))
        self.assertIn("transactions", preview)
        self.assertEqual(len(preview["transactions"]), 2)

    def test_save_many_persists_all_rows(self):
        pipeline = self._pipeline({
            "transactions": [
                {"type": "expense", "amount": 500, "category": "Transport",
                 "description": "cab", "date": "2026-06-29", "time": None, "confidence": "high"},
                {"type": "income", "amount": 80000, "category": "Salary",
                 "description": "monthly salary", "date": "2026-06-29", "time": None, "confidence": "high"},
            ]
        })
        preview = pipeline.preview("multi", now=datetime(2026, 6, 29, 9, 0))
        saved = pipeline.save_many(preview["transactions"])
        self.assertEqual(len(saved), 2)
        self.assertEqual(len(self.db.list_transactions()), 2)

    def test_save_many_all_or_nothing_per_row_not_batch(self):
        # save_many saves row by row; a bad row raises mid-loop
        pipeline = TransactionPipeline(self.db, FakeExtractor({}))
        bad_items = [
            {"type": "expense", "amount": 100, "category": "Other",
             "description": "fine", "date": "2026-06-29", "time": None, "confidence": "high"},
            {"type": "expense", "amount": -1, "category": "Other",
             "description": "bad", "date": "2026-06-29", "time": None, "confidence": "high"},
        ]
        with self.assertRaises(ExtractionError):
            pipeline.save_many(bad_items)
        # first row was committed before the error
        self.assertEqual(len(self.db.list_transactions()), 1)

    def test_multi_preview_confidence_low_if_any_row_is_low(self):
        pipeline = self._pipeline({
            "transactions": [
                {"type": "expense", "amount": 500, "category": "Transport",
                 "description": "cab", "date": "today", "time": None, "confidence": "high"},
                {"type": "expense", "amount": 200, "category": "Other",
                 "description": "misc", "date": "today", "time": None, "confidence": "low"},
            ]
        })
        preview = pipeline.preview("multi", now=datetime(2026, 6, 29, 9, 0))
        self.assertEqual(preview["confidence"], "low")

    def test_multi_preview_confidence_high_when_all_high(self):
        pipeline = self._pipeline({
            "transactions": [
                {"type": "expense", "amount": 500, "category": "Transport",
                 "description": "cab", "date": "today", "time": None, "confidence": "high"},
                {"type": "income", "amount": 5000, "category": "Freelance",
                 "description": "project", "date": "today", "time": None, "confidence": "high"},
            ]
        })
        preview = pipeline.preview("multi", now=datetime(2026, 6, 29, 9, 0))
        self.assertEqual(preview["confidence"], "high")

    def test_empty_text_raises_on_preview(self):
        pipeline = self._pipeline({"type": "expense", "amount": 100, "category": "Other",
                                    "description": "x", "date": "today", "time": None})
        with self.assertRaises(ExtractionError):
            pipeline.preview("   ")

    def test_on_saved_callback_fires_for_each_row_in_save_many(self):
        calls = []
        pipeline = TransactionPipeline(self.db, FakeExtractor({}), on_saved=calls.append)
        items = [
            {"type": "expense", "amount": 100, "category": "Other",
             "description": "a", "date": "2026-06-29", "time": None, "confidence": "high"},
            {"type": "expense", "amount": 200, "category": "Other",
             "description": "b", "date": "2026-06-29", "time": None, "confidence": "high"},
        ]
        pipeline.save_many(items)
        self.assertEqual(len(calls), 2)


# ===========================================================================
# 7. CATEGORY GUARD
# ===========================================================================

class TestCategoryOrOther(unittest.TestCase):

    def test_valid_category_passes_through(self):
        self.assertEqual(category_or_other("Health"), "Health")

    def test_invalid_category_becomes_other(self):
        self.assertEqual(category_or_other("Gambling"), "Other")

    def test_none_becomes_other(self):
        self.assertEqual(category_or_other(None), "Other")

    def test_empty_string_becomes_other(self):
        self.assertEqual(category_or_other(""), "Other")

    def test_all_valid_categories_pass(self):
        from voicetrack.constants import CATEGORIES
        for cat in CATEGORIES:
            self.assertEqual(category_or_other(cat), cat)


# ===========================================================================
# 8. PERIOD RANGES — boundary correctness
# ===========================================================================

class TestPeriodToRange(unittest.TestCase):

    def test_today_range_equals_single_day(self):
        r = period_to_range("today", date(2026, 6, 29))
        self.assertEqual(r.start, "2026-06-29")
        self.assertEqual(r.end, "2026-06-29")

    def test_week_starts_on_monday(self):
        r = period_to_range("week", date(2026, 6, 29))  # Monday
        self.assertEqual(r.start, "2026-06-29")
        self.assertEqual(r.end, "2026-07-05")

    def test_week_from_wednesday_goes_back_to_monday(self):
        r = period_to_range("week", date(2026, 7, 1))   # Wednesday
        self.assertEqual(r.start, "2026-06-29")

    def test_month_range_june(self):
        r = period_to_range("month", date(2026, 6, 15))
        self.assertEqual(r.start, "2026-06-01")
        self.assertEqual(r.end, "2026-06-30")

    def test_month_range_december_wraps_year(self):
        r = period_to_range("month", date(2026, 12, 1))
        self.assertEqual(r.end, "2026-12-31")

    def test_month_range_january(self):
        r = period_to_range("month", date(2026, 1, 10))
        self.assertEqual(r.start, "2026-01-01")
        self.assertEqual(r.end, "2026-01-31")

    def test_all_returns_none_range(self):
        r = period_to_range("all")
        self.assertIsNone(r.start)
        self.assertIsNone(r.end)


if __name__ == "__main__":
    unittest.main()
