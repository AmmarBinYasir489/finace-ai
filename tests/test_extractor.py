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
