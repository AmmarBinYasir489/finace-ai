import json
from unittest.mock import patch, MagicMock

import pytest

import voicetrack.extractor as extractor_module
from voicetrack.extractor import extract, normalize_transactions, OllamaError


def _make_response(content: dict):
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"message": {"content": json.dumps(content)}}
    return mock_resp


# Single transaction round-trip
def test_extract_single_transaction():
    stage1 = {"type": "expense", "amount": 500, "category": "Transport",
               "description": "cab to office", "date": "today", "time": None}
    stage2 = {**stage1, "confidence": "high"}

    responses = iter([_make_response(stage1), _make_response(stage2)])
    with patch("voicetrack.extractor.requests.post", side_effect=lambda *a, **kw: next(responses)):
        result = extract("paid 500 for cab to office")

    assert result["type"] == "expense"
    assert result["amount"] == 500
    assert result["confidence"] == "high"


# Multi-transaction normalize
def test_extract_multi_transaction_normalize():
    stage1 = {"transactions": [
        {"type": "expense", "amount": 1200, "category": "Food & Groceries",
         "description": "grocery shopping", "date": "today", "time": None},
        {"type": "expense", "amount": 300, "category": "Transport",
         "description": "Uber ride", "date": "today", "time": None},
    ]}
    stage2 = {"transactions": [
        {**stage1["transactions"][0], "confidence": "high"},
        {**stage1["transactions"][1], "confidence": "high"},
    ]}

    responses = iter([_make_response(stage1), _make_response(stage2)])
    with patch("voicetrack.extractor.requests.post", side_effect=lambda *a, **kw: next(responses)):
        result = extract("bought groceries 1200 and uber 300")

    txs = normalize_transactions(result)
    assert len(txs) == 2


# LLM natural-language loan/shared parsing routes through build_plan_from_spec
def test_llm_special_intent_routing():
    spec = {"intent": "shared_expense", "payer": "me", "total": 1000,
            "category": "Transport", "description": "cab",
            "participants": ["me", "Ali"],
            "splits": [{"person": "Ali", "mode": "percent", "value": 50}], "date": "today"}
    with patch("voicetrack.extractor._ollama_reachable", return_value=True), \
         patch("voicetrack.finance_intents.parse_special_intent", return_value=None), \
         patch("voicetrack.extractor.requests.post", side_effect=lambda *a, **k: _make_response(spec)):
        result = extract("ali will handle 50% of the 1000 cab")
    assert result["intent"] == "shared_expense"
    assert result["components"][0]["my_share"] == 500
    assert result["people"][0]["name"] == "Ali"


def test_llm_special_intent_skipped_for_plain_expense():
    # No finance signal -> the finance LLM parser must not run; falls to normal extract.
    stage = {"type": "expense", "amount": 500, "category": "Transport",
             "description": "cab", "date": "today", "time": None, "confidence": "high"}
    with patch("voicetrack.extractor._ollama_reachable", return_value=True), \
         patch("voicetrack.extractor._llm_special_intent") as llm_special, \
         patch("voicetrack.extractor.requests.post", side_effect=lambda *a, **k: _make_response(stage)):
        result = extract("paid 500 for cab")
    llm_special.assert_not_called()
    assert result["type"] == "expense"


# Shorthand amounts like "5k" must expand before parsing
def test_expand_shorthand_amounts():
    from voicetrack.extractor import _expand_shorthand_amounts as ex
    assert ex("spent 5k on food") == "spent 5000 on food"
    assert ex("paid 1.5k for cab") == "paid 1500 for cab"
    assert ex("got 80 thousand salary") == "got 80000 salary"
    assert ex("lent Ali 5 lakh") == "lent Ali 500000"
    assert ex("ran 5km today") == "ran 5km today"  # not a money scale


def test_shorthand_amount_accepted_via_fallback():
    # Model unavailable -> fallback must still accept "5k" as 5000, not reject it.
    with patch("voicetrack.extractor._ollama_reachable", return_value=False):
        result = extract("spent 5k on biryani")
    assert "error" not in result
    assert result["amount"] == 5000.0


# Gift income must not be miscategorized as Freelance
def test_gift_income_category_corrected_to_other():
    # The model wrongly labels a gift as Freelance; the deterministic corrector fixes it.
    stage = {"type": "income", "amount": 3000, "category": "Freelance",
             "description": "friend gifted me", "date": "today", "time": None,
             "confidence": "high"}
    responses = iter([_make_response(stage), _make_response(stage)])
    with patch("voicetrack.extractor._ollama_reachable", return_value=True), \
         patch("voicetrack.extractor.requests.post", side_effect=lambda *a, **kw: next(responses)):
        result = extract("my friend gifted me 3000")

    assert result["type"] == "income"
    assert result["category"] == "Other"


# Ollama down → fallback fires with confidence low
def test_extract_falls_back_on_connection_error():
    import requests as req
    with patch("voicetrack.extractor.requests.post", side_effect=req.exceptions.ConnectionError("down")):
        result = extract("my friend gifted me 3000")

    assert result["confidence"] == "low"
    assert "type" in result


# Error JSON from extractor skips orchestrator
def test_extract_returns_error_immediately():
    error_resp = {"error": "Could not understand input. Please rephrase."}
    call_count = {"n": 0}

    def _post(*a, **kw):
        call_count["n"] += 1
        return _make_response(error_resp)

    with patch("voicetrack.extractor.requests.post", side_effect=_post):
        result = extract("asdfghjkl")

    assert "error" in result
    assert call_count["n"] == 1  # orchestrator never called


def test_unrelated_sentence_is_rejected_when_ollama_is_down():
    with patch("voicetrack.extractor._ollama_reachable", return_value=False):
        result = extract("how is the weather today")

    assert "error" in result


def test_model_cannot_invent_amount_for_unrelated_text():
    hallucinated = {
        "type": "income",
        "amount": 500,
        "category": "Salary",
        "description": "salary",
        "date": "today",
        "time": None,
    }
    responses = iter([_make_response(hallucinated), _make_response(hallucinated)])

    with patch("voicetrack.extractor._ollama_reachable", return_value=True), \
            patch("voicetrack.extractor.requests.post", side_effect=lambda *a, **kw: next(responses)):
        result = extract("how is the weather today")

    assert "error" in result


def test_loan_clear_detected_before_model_hallucination():
    result = extract("loan from Ali is cleared")

    assert result["intent"] == "loan_clear"
