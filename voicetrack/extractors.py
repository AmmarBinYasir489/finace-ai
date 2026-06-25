"""Natural-language transaction extraction with local Ollama."""

from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.request
from datetime import datetime
from typing import Protocol

from .config import Settings
from .constants import CATEGORIES


SYSTEM_PROMPT = """You are a financial data extraction assistant. Extract structured data from the user's natural language input and return ONLY valid JSON -- no explanation, no markdown, no preamble.

Return this exact structure:
{
  "type": "expense" or "income",
  "amount": number (e.g. 2000),
  "category": one of [Food & Groceries, Transport, Utilities, Health, Education, Shopping, Entertainment, Rent, Salary, Freelance, Other],
  "description": short phrase describing the transaction,
  "date": "YYYY-MM-DD" or "today" if not mentioned,
  "time": "HH:MM" or null if not mentioned,
  "confidence": "high" or "low"
}

If the input is unclear or not a financial transaction, return:
{ "error": "Could not understand input. Please rephrase." }
"""


class Extractor(Protocol):
    """Protocol used by tests and the production Ollama extractor."""

    def extract(self, text: str, now: datetime | None = None) -> dict:
        """Return extracted JSON-like transaction fields."""


class OllamaExtractor:
    """Call a local Ollama model and parse its JSON response."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def extract(self, text: str, now: datetime | None = None) -> dict:
        """Try Ollama models, then fall back to a low-confidence local parse."""
        now = now or datetime.now()
        last_error: Exception | None = None
        for model in [self.settings.ollama_model, self.settings.ollama_fallback_model]:
            if not model:
                continue
            try:
                return self._extract_with_model(model, text, now)
            except Exception as exc:
                last_error = exc
        fallback = rule_based_extract(text, now)
        if "error" not in fallback:
            return fallback
        return {"error": f"Ollama extraction failed: {last_error}. Please check Ollama or rephrase."}

    def _extract_with_model(self, model: str, text: str, now: datetime) -> dict:
        """Send one non-streaming generation request to Ollama."""
        prompt = (
            f"{SYSTEM_PROMPT}\n"
            f"Current local date: {now.date().isoformat()}\n"
            f"Current local time: {now.strftime('%H:%M')}\n"
            f"User input: {text}\n"
            "JSON:"
        )
        payload = json.dumps(
            {
                "model": model,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "keep_alive": "10m",
                "options": {"temperature": 0},
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.settings.ollama_host}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.settings.ollama_timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (TimeoutError, socket.timeout) as exc:
            raise RuntimeError(f"{model} timed out after {self.settings.ollama_timeout_seconds}s") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Cannot reach Ollama at {self.settings.ollama_host}") from exc
        return _parse_model_json(data.get("response", ""))


def _parse_model_json(raw: str) -> dict:
    """Parse JSON even if a model accidentally wraps it in extra text."""
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not match:
            raise ValueError("Model did not return JSON")
        return json.loads(match.group(0))


def category_or_other(value: str | None) -> str:
    """Keep categories inside the fixed list required by the app."""
    if value in CATEGORIES:
        return value
    return "Other"


def rule_based_extract(text: str, now: datetime | None = None) -> dict:
    """Low-confidence rescue parser for simple entries when Ollama is slow."""
    now = now or datetime.now()
    cleaned = text.strip()
    match = re.search(r"(?<!\w)(\d+(?:,\d{3})*(?:\.\d+)?)(?!\w)", cleaned)
    if not match:
        return {"error": "Could not understand input. Please rephrase."}

    lowered = cleaned.lower()
    amount = float(match.group(1).replace(",", ""))
    tx_type = "income" if any(word in lowered for word in ["received", "salary", "income", "earned", "paid me", "freelance"]) else "expense"
    category = _guess_category(lowered, tx_type)
    description = _clean_description(cleaned, match.group(0))
    return {
        "type": tx_type,
        "amount": amount,
        "category": category,
        "description": description or category.lower(),
        "date": "today",
        "time": None,
        "confidence": "low",
    }


def _guess_category(lowered: str, tx_type: str) -> str:
    """Classify common words without doing any arithmetic."""
    if tx_type == "income":
        if "salary" in lowered:
            return "Salary"
        if "freelance" in lowered or "client" in lowered or "project" in lowered:
            return "Freelance"
        return "Other"
    keyword_map = [
        ("Food & Groceries", ["food", "grocery", "groceries", "lunch", "dinner", "breakfast", "restaurant"]),
        ("Transport", ["uber", "careem", "fuel", "petrol", "bus", "train", "transport", "taxi"]),
        ("Utilities", ["electricity", "gas", "water", "internet", "bill", "utility"]),
        ("Health", ["doctor", "medicine", "hospital", "health", "clinic"]),
        ("Education", ["school", "book", "course", "tuition", "education"]),
        ("Shopping", ["shopping", "shop", "bought", "purchase", "clothes", "shoes"]),
        ("Entertainment", ["movie", "netflix", "game", "entertainment"]),
        ("Rent", ["rent", "house payment"]),
    ]
    for category, words in keyword_map:
        if any(word in lowered for word in words):
            return category
    return "Other"


def _clean_description(text: str, amount_text: str) -> str:
    """Remove common command words to make a readable description."""
    without_amount = text.replace(amount_text, " ")
    without_amount = re.sub(r"\b(spent|paid|done|on|of|for|rs|pkr|received|got|income|expense)\b", " ", without_amount, flags=re.I)
    without_amount = re.sub(r"[^\w &-]+", " ", without_amount)
    return re.sub(r"\s+", " ", without_amount).strip().lower()
