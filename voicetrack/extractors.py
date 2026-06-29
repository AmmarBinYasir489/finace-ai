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
from .dates import resolve_transaction_date


SYSTEM_PROMPT = """You are a financial data extraction assistant. Extract structured data from the user's natural language input and return ONLY valid JSON -- no explanation, no markdown, no preamble.

For one transaction, return this exact structure:
{
  "type": "expense" or "income",
  "amount": number (e.g. 2000),
  "category": one of [Food & Groceries, Transport, Utilities, Health, Education, Shopping, Entertainment, Rent, Salary, Freelance, Other],
  "description": short phrase describing the transaction,
  "date": "YYYY-MM-DD", "today", "yesterday", "last week", "last month", or "today" if not mentioned,
  "time": "HH:MM" or null if not mentioned,
  "confidence": "high" or "low"
}

If the input is unclear or not a financial transaction, return:
{ "error": "Could not understand input. Please rephrase." }

If the user mentions multiple separate transactions, return:
{
  "transactions": [
    {
      "type": "expense" or "income",
      "amount": number,
      "category": one allowed category,
      "description": short corrected phrase,
      "date": "YYYY-MM-DD" or "today",
      "time": "HH:MM" or null,
      "confidence": "high" or "low"
    }
  ]
}

Category rules:
- If the amount is for cab, taxi, Uber, Careem, bus, train, rickshaw, fuel, petrol, fare, or ride, use Transport.
- If the sentence mentions a destination or restaurant but the amount is attached to cab/fare/ride, use Transport, not Food & Groceries.
- Use Food & Groceries only when the amount is for food, groceries, lunch, dinner, breakfast, restaurant, fries, burger, pizza, tea, or coffee.
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
    """Parse JSON even if a model accidentally wraps it in extra text or returns an array."""
    raw = raw.strip()
    try:
        result = json.loads(raw)
        if isinstance(result, list):
            return {"transactions": result}
        return result
    except json.JSONDecodeError:
        pass
    match = re.search(r"\[.*\]", raw, flags=re.DOTALL)
    if match:
        try:
            items = json.loads(match.group(0))
            if isinstance(items, list) and items:
                return {"transactions": items}
        except json.JSONDecodeError:
            pass
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
    parts = _split_transaction_parts(cleaned)
    if len(parts) > 1:
        transactions = []
        for part in parts:
            extracted = _rule_based_extract_one(part, now)
            if "error" in extracted:
                return {"error": "Could not understand one of the transactions. Please enter it separately."}
            transactions.append(extracted)
        return {"transactions": transactions, "confidence": "low"}

    return _rule_based_extract_one(cleaned, now)


def _rule_based_extract_one(text: str, now: datetime) -> dict:
    """Extract one transaction with local rules."""
    cleaned = text.strip()
    number_pattern = r"(?<!\w)(\d+(?:,\d{3})*(?:\.\d+)?)(?!\w)"
    number_matches = list(re.finditer(number_pattern, cleaned))
    if len(number_matches) > 1:
        return {"error": "Multiple transactions detected."}

    match = number_matches[0] if number_matches else None
    amount_text = ""
    if match:
        amount = float(match.group(1).replace(",", ""))
        amount_text = match.group(0)
    else:
        amount, amount_text = _extract_number_words(cleaned)
    if amount is None:
        return {"error": "Could not understand input. Please rephrase."}

    lowered = cleaned.lower()
    tx_type = "income" if any(word in lowered for word in ["received", "salary", "income", "earned", "paid me", "freelance"]) else "expense"
    category = _guess_category(lowered, tx_type, amount_text)
    description = _clean_description(cleaned, amount_text, category)
    return {
        "type": tx_type,
        "amount": amount,
        "category": category,
        "description": description or category.lower(),
        "date": resolve_transaction_date(None, cleaned, now),
        "time": None,
        "confidence": "low",
    }


def _guess_category(lowered: str, tx_type: str, amount_text: str = "") -> str:
    """Classify common words without doing any arithmetic."""
    if tx_type == "income":
        if "salary" in lowered:
            return "Salary"
        if "freelance" in lowered or "client" in lowered or "project" in lowered:
            return "Freelance"
        return "Other"
    amount_category = _category_near_amount(lowered, amount_text)
    if amount_category:
        return amount_category
    keyword_map = [
        ("Transport", ["cab", "uber", "careem", "fuel", "petrol", "bus", "train", "transport", "taxi", "rickshaw", "fare", "ride"]),
        ("Food & Groceries", ["food", "grocery", "groceries", "lunch", "dinner", "breakfast", "restaurant", "fries", "burger", "pizza", "tea", "coffee"]),
        ("Utilities", ["electricity", "gas", "water", "internet", "bill", "utility"]),
        ("Health", ["doctor", "medicine", "hospital", "health", "clinic"]),
        ("Education", ["school", "book", "course", "tuition", "education"]),
        ("Shopping", ["shopping", "shop", "bought", "purchase", "purchased", "clothes", "shoes", "soap", "shampoo", "detergent", "toothpaste"]),
        ("Entertainment", ["movie", "netflix", "game", "entertainment"]),
        ("Rent", ["rent", "house payment"]),
    ]
    for category, words in keyword_map:
        if any(word in lowered for word in words):
            return category
    return "Other"


def _split_transaction_parts(text: str) -> list[str]:
    """Split obvious multi-transaction prompts into simple clauses."""
    pieces = [part.strip(" .,-") for part in re.split(r"\s+(?:and|then|also)\s+|;", text, flags=re.I) if part.strip(" .,-")]
    if len(pieces) < 2:
        return [text]
    amount_pattern = r"(?<!\w)\d+(?:,\d{3})*(?:\.\d+)?(?!\w)"
    pieces_with_amount = [part for part in pieces if re.search(amount_pattern, part)]
    if len(pieces_with_amount) < 2:
        return [text]
    return pieces


def _category_near_amount(lowered: str, amount_text: str) -> str | None:
    """Prefer the category whose words are closest to the detected amount."""
    if not amount_text:
        return None
    amount_index = lowered.find(amount_text.lower())
    if amount_index < 0:
        return None

    keyword_map = {
        "Transport": ["cab", "taxi", "uber", "careem", "bus", "train", "rickshaw", "fare", "ride", "fuel", "petrol", "transport"],
        "Food & Groceries": ["food", "grocery", "groceries", "lunch", "dinner", "breakfast", "restaurant", "fries", "burger", "pizza", "tea", "coffee"],
        "Utilities": ["electricity", "gas", "water", "internet", "bill", "utility"],
        "Health": ["doctor", "medicine", "hospital", "health", "clinic"],
        "Education": ["school", "book", "course", "tuition", "education"],
        "Shopping": ["shopping", "shop", "bought", "purchase", "purchased", "clothes", "shoes", "soap", "shampoo", "detergent", "toothpaste"],
        "Entertainment": ["movie", "netflix", "game", "entertainment"],
        "Rent": ["rent", "house payment"],
    }
    best_category: str | None = None
    best_distance = 9999
    for category, words in keyword_map.items():
        for word in words:
            for match in re.finditer(rf"\b{re.escape(word)}\b", lowered):
                distance = abs(match.start() - amount_index)
                if distance < best_distance:
                    best_distance = distance
                    best_category = category
    return best_category if best_distance <= 55 else None


def _clean_description(text: str, amount_text: str, category: str = "") -> str:
    """Remove common command words to make a readable description."""
    if category == "Transport":
        transport_description = _clean_transport_description(text, amount_text)
        if transport_description:
            return transport_description
    without_amount = re.sub(re.escape(amount_text), " ", text, flags=re.I) if amount_text else text
    without_amount = re.sub(
        r"\b(spent|paid|done|on|of|off|for|from|i|have|has|had|rs|pkr|rupees|received|got|income|expense|purchased|purchase|bought|today|yesterday|last|week|month|previous)\b",
        " ",
        without_amount,
        flags=re.I,
    )
    without_amount = re.sub(r"[^\w &-]+", " ", without_amount)
    return _clean_spelling(re.sub(r"\s+", " ", without_amount).strip().lower())


def _clean_transport_description(text: str, amount_text: str) -> str:
    """Create readable transport descriptions like 'cab to texas fries'."""
    without_amount = re.sub(re.escape(amount_text), " ", text, flags=re.I) if amount_text else text
    lowered = without_amount.lower()
    mode_match = re.search(r"\b(cab|taxi|uber|careem|rickshaw|bus|train|ride|fare)\b", lowered)
    if not mode_match:
        return ""
    mode = mode_match.group(1)
    destination_match = re.search(
        r"\b(?:went|go|gone|travelled|traveled)\s+to\s+(.+?)\s+(?:on|by|via|in)\s+(?:a\s+)?(?:cab|taxi|uber|careem|rickshaw|bus|train|ride)\b",
        lowered,
    )
    if destination_match:
        destination = re.sub(r"\s+", " ", destination_match.group(1)).strip(" .,-")
        destination = re.sub(r"^(the|a|an)\s+", "", destination)
        destination = _clean_spelling(destination)
        if destination:
            return f"{mode} to {destination}"
    return mode


def _clean_spelling(text: str) -> str:
    """Fix common speech/misspelling artifacts in generated descriptions."""
    corrections = {
        "osque": "mosque",
        "mosqe": "mosque",
        "mosq": "mosque",
        "resturant": "restaurant",
        "restraunt": "restaurant",
        "restuarant": "restaurant",
        "electrcity": "electricity",
        "electricty": "electricity",
        "groccery": "grocery",
        "grossery": "grocery",
        "trasport": "transport",
        "texi": "taxi",
    }
    words = [corrections.get(word, word) for word in text.split()]
    return re.sub(r"\s+", " ", " ".join(words)).strip()


NUMBER_WORDS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}

SCALE_WORDS = {"hundred": 100, "thousand": 1000, "lakh": 100000}


def _extract_number_words(text: str) -> tuple[float | None, str]:
    """Find and parse the longest simple number-word phrase in text."""
    words = re.findall(r"[a-zA-Z]+", text.lower().replace("-", " "))
    best_value: int | None = None
    best_phrase: list[str] = []
    current: list[str] = []

    for word in words:
        if word in NUMBER_WORDS or word in SCALE_WORDS or word == "and":
            current.append(word)
            value = _parse_number_words(current)
            if value is not None and len(current) > len(best_phrase):
                best_value = value
                best_phrase = list(current)
        else:
            current = []

    if best_value is None:
        return None, ""
    return float(best_value), " ".join(best_phrase)


def _parse_number_words(words: list[str]) -> int | None:
    """Parse common English amount words into an integer."""
    total = 0
    current = 0
    seen_number = False
    for word in words:
        if word == "and":
            continue
        if word in NUMBER_WORDS:
            current += NUMBER_WORDS[word]
            seen_number = True
            continue
        if word == "hundred":
            current = max(current, 1) * 100
            seen_number = True
            continue
        if word in {"thousand", "lakh"}:
            total += max(current, 1) * SCALE_WORDS[word]
            current = 0
            seen_number = True
            continue
        return None
    if not seen_number:
        return None
    return total + current
