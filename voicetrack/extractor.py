"""Active natural-language extraction pipeline for the GUI.

The real NLP path is:
1. A fast local Qwen/Ollama extractor proposes JSON.
2. A second local Qwen/Ollama orchestrator validates that JSON against the
   original sentence and fixes type, category, amount, date, and description.
3. The regex fallback is used only when Ollama is unavailable or times out.
"""

from __future__ import annotations

import json
import re
import socket
import threading
import urllib.parse
from datetime import datetime, timedelta

import requests

from voicetrack.config import OLLAMA_FALLBACK_MODEL, OLLAMA_HOST, OLLAMA_MODEL, OLLAMA_TIMEOUT
from voicetrack.prompts import (
    EXTRACTOR_SYSTEM_PROMPT,
    FINANCE_INTENT_SYSTEM_PROMPT,
    ORCHESTRATOR_SYSTEM_PROMPT,
)


_ALLOWED_TYPES = {"expense", "income"}
_ALLOWED_CATEGORIES = {
    "Food & Groceries",
    "Transport",
    "Utilities",
    "Health",
    "Education",
    "Shopping",
    "Entertainment",
    "Rent",
    "Salary",
    "Freelance",
    "Other",
}

_AMOUNT_WORDS = {
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen", "twenty", "thirty",
    "forty", "fifty", "sixty", "seventy", "eighty", "ninety", "hundred",
    "thousand", "lakh", "lac", "million", "billion", "rupee", "rupees",
    "pkr", "rs",
}


_SCALE_WORDS = {
    "k": 1_000, "thousand": 1_000,
    "lac": 100_000, "lakh": 100_000, "lakhs": 100_000,
    "m": 1_000_000, "million": 1_000_000,
    "cr": 10_000_000, "crore": 10_000_000,
    "b": 1_000_000_000, "billion": 1_000_000_000,
}
_SHORTHAND_RX = re.compile(
    r"\b(\d[\d,]*(?:\.\d+)?)\s*"
    r"(k|m|b|lac|lakhs?|cr|crore|thousand|million|billion)\b",
    re.IGNORECASE,
)


def _expand_shorthand_amounts(text: str) -> str:
    """Expand spoken money shorthand to full integers before any parsing.

    e.g. "5k" -> "5000", "1.5k" -> "1500", "80 thousand" -> "80000",
    "5 lakh" -> "500000". Keeps the rest of the sentence intact so the model,
    the fallback parser, and amount validation all see the same number.
    """
    def _repl(match: re.Match) -> str:
        base = float(match.group(1).replace(",", ""))
        scale = _SCALE_WORDS[match.group(2).lower()]
        return str(int(round(base * scale)))

    return _SHORTHAND_RX.sub(_repl, text)


_LLM_DEADLINE = float(OLLAMA_TIMEOUT)
_WARMUP_DONE = False
_WARMUP_LOCK = threading.Lock()


class OllamaError(RuntimeError):
    """Raised when Ollama cannot produce valid transaction JSON."""


def _candidate_models() -> list[str]:
    """Return configured Ollama models without duplicates."""
    models: list[str] = []
    for model in [OLLAMA_MODEL, OLLAMA_FALLBACK_MODEL]:
        if model and model not in models:
            models.append(model)
    return models


def _host_port() -> tuple[str, int]:
    """Return host/port from the configured Ollama URL."""
    parsed = urllib.parse.urlparse(OLLAMA_HOST)
    return parsed.hostname or "localhost", parsed.port or 11434


def _ollama_reachable() -> bool:
    """Fast local socket check before spending time on model calls."""
    try:
        with socket.create_connection(_host_port(), timeout=0.8):
            return True
    except OSError:
        return False


def _parse_json_payload(raw: str) -> dict:
    """Parse JSON even if a model returns an array or wraps JSON in text."""
    raw = raw.strip()
    try:
        value = json.loads(raw)
        if isinstance(value, list):
            return {"transactions": value}
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass

    for pattern in (r"\[.*\]", r"\{.*\}"):
        match = re.search(pattern, raw, flags=re.DOTALL)
        if not match:
            continue
        value = json.loads(match.group(0))
        if isinstance(value, list):
            return {"transactions": value}
        if isinstance(value, dict):
            return value
    raise OllamaError("Model did not return valid JSON")


def _call_ollama(model: str, system_prompt: str, user_content: str) -> dict:
    """Run one non-streaming Ollama chat request and return parsed JSON."""
    payload = {
        "model": model,
        "stream": False,
        "format": "json",
        "keep_alive": "15m",
        "options": {"temperature": 0},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    }
    try:
        response = requests.post(f"{OLLAMA_HOST}/api/chat", json=payload, timeout=(3, OLLAMA_TIMEOUT))
        response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        raise OllamaError(str(exc)) from exc

    content = response.json().get("message", {}).get("content", "")
    return _parse_json_payload(content)


def _call_deadline(model: str, system_prompt: str, user_content: str, deadline: float = _LLM_DEADLINE) -> dict | None:
    """Run an Ollama call with a hard wall-clock deadline."""
    box: list[dict] = []

    def work() -> None:
        try:
            box.append(_call_ollama(model, system_prompt, user_content))
        except Exception:
            return

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    thread.join(timeout=deadline)
    return box[0] if box else None


def _runtime_context(user_input: str) -> str:
    """Attach current local date/time to the extractor request."""
    now = datetime.now()
    return (
        f"Current local date: {now.date().isoformat()}\n"
        f"Current local time: {now.strftime('%H:%M')}\n"
        f"User input: {user_input}"
    )


def _orchestrator_context(user_input: str, extracted: dict) -> str:
    """Attach original text and stage-one JSON to the validator request."""
    now = datetime.now()
    return (
        f"Current local date: {now.date().isoformat()}\n"
        f"Current local time: {now.strftime('%H:%M')}\n\n"
        f"Original user text:\n{user_input}\n\n"
        f"Extractor JSON output:\n{json.dumps(extracted, indent=2)}"
    )


def _mark_confidence(result: dict, confidence: str) -> None:
    """Apply confidence to single or multi-transaction payloads."""
    if isinstance(result.get("transactions"), list):
        for item in result["transactions"]:
            if isinstance(item, dict):
                item.setdefault("confidence", confidence)
        return
    result.setdefault("confidence", confidence)


def _default_date(value: object, now: datetime) -> str:
    """Resolve missing or relative dates before the GUI preview is shown."""
    text = str(value or "").strip().lower()
    if not text or text in {"null", "none", "today", "now"}:
        return now.date().isoformat()
    if text == "yesterday":
        return (now.date() - timedelta(days=1)).isoformat()
    if text in {"last week", "lastweek"}:
        return (now.date() - timedelta(days=7)).isoformat()
    if text in {"last month", "lastmonth"}:
        first_day = now.date().replace(day=1)
        if first_day.month == 1:
            return first_day.replace(year=first_day.year - 1, month=12).isoformat()
        return first_day.replace(month=first_day.month - 1).isoformat()
    return str(value).strip()


def _resolve_date_phrase(phrase: str, now: datetime) -> str:
    """Resolve a date phrase found in the original text."""
    text = phrase.strip().lower()
    if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", text):
        parts = [int(p) for p in text.split("-")]
        return datetime(parts[0], parts[1], parts[2]).date().isoformat()
    if text == "yesterday":
        return (now.date() - timedelta(days=1)).isoformat()
    if text == "today":
        return now.date().isoformat()
    if text in {"last week", "lastweek"}:
        return (now.date() - timedelta(days=7)).isoformat()
    if text in {"last month", "lastmonth"}:
        first_day = now.date().replace(day=1)
        if first_day.month == 1:
            return first_day.replace(year=first_day.year - 1, month=12).isoformat()
        return first_day.replace(month=first_day.month - 1).isoformat()
    return now.date().isoformat()


def _date_mentions(user_input: str) -> list[tuple[int, int, str]]:
    """Find relative/explicit date phrases in the original text."""
    mentions: list[tuple[int, int, str]] = []
    patterns = [
        r"\b\d{4}-\d{1,2}-\d{1,2}\b",
        r"\blast\s+week\b",
        r"\blast\s+month\b",
        r"\byesterday\b",
        r"\btoday\b",
        r"\bthis\s+week\b",
        r"\bthis\s+month\b",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, user_input, flags=re.IGNORECASE):
            mentions.append((match.start(), match.end(), match.group(0)))
    return sorted(mentions, key=lambda item: item[0])


def _amount_position(user_input: str, amount: float) -> int | None:
    """Find where an extracted amount appears in the original sentence."""
    amount_rx = _amount_pattern(amount)
    match = re.search(rf"\b(?:{amount_rx})\b", user_input)
    return match.start() if match else None


def _clause_bounds(user_input: str, pos: int) -> tuple[int, int]:
    """Return the rough sentence clause around a given character position."""
    separators = list(re.finditer(r"\s+(?:and|then|plus|also)\s+|[,;]", user_input, flags=re.IGNORECASE))
    start = 0
    end = len(user_input)
    for sep in separators:
        if sep.end() <= pos:
            start = sep.end()
        elif sep.start() > pos:
            end = sep.start()
            break
    return start, end


def _date_for_amount(user_input: str, amount: float, now: datetime) -> str | None:
    """Map a spoken date phrase to the transaction amount it belongs to."""
    mentions = _date_mentions(user_input)
    if not mentions:
        return None

    if len(mentions) == 1:
        return _resolve_date_phrase(mentions[0][2], now)

    pos = _amount_position(user_input, amount)
    if pos is None:
        return None

    start, end = _clause_bounds(user_input, pos)
    for mention_start, mention_end, phrase in mentions:
        if start <= mention_start and mention_end <= end:
            return _resolve_date_phrase(phrase, now)
    return None


def _default_time(value: object, now: datetime) -> str:
    """Resolve missing times to the current local time."""
    text = str(value or "").strip()
    if not text or text.lower() in {"null", "none"}:
        return now.strftime("%H:%M")
    return text


def _amount_pattern(amount: float) -> str:
    """Return a regex fragment for matching an amount in the source text."""
    if float(amount).is_integer():
        return rf"{int(amount):,}|{int(amount)}"
    return re.escape(str(amount))


def _title_phrase(phrase: str) -> str:
    """Title-case a short place phrase without changing separators."""
    words = re.sub(r"\s+", " ", phrase).strip(" .,;:-")
    return " ".join(part.capitalize() for part in words.split())


def _place_from_input(user_input: str) -> str | None:
    """Find a destination/place mentioned before a transport phrase."""
    pattern = (
        r"\b(?:went|go|going|travel(?:ed)?|visited)\s+to\s+"
        r"(.+?)(?=\s+(?:on|by)\s+(?:cab|taxi|ride|bus|train|rickshaw|uber|careem)\b|\s+and\b|$)"
    )
    match = re.search(pattern, user_input, flags=re.IGNORECASE)
    if not match:
        return None
    place = match.group(1)
    place = re.sub(r"\b(the|a|an)\b", " ", place, flags=re.IGNORECASE)
    place = re.sub(r"\s+", " ", place).strip(" .,;:-")
    return _title_phrase(place) if place else None


def _transport_word(user_input: str) -> str:
    """Return the transport word the user used, when present."""
    match = re.search(r"\b(cab|taxi|ride|bus|train|rickshaw|uber|careem)\b", user_input, flags=re.IGNORECASE)
    return match.group(1).lower() if match else "transport"


def _food_place_for_amount(user_input: str, amount: float) -> str | None:
    """Find a food place near the amount, such as 'at savour foods for 1800'."""
    amount_rx = _amount_pattern(amount)
    pattern = rf"\b(?:at|from)\s+(.+?)\s+(?:for|of)?\s*(?:{amount_rx})\b"
    match = re.search(pattern, user_input, flags=re.IGNORECASE)
    if not match:
        return None
    place = re.sub(r"\s+", " ", match.group(1)).strip(" .,;:-")
    return _title_phrase(place) if place else None


def _item_for_amount(user_input: str, amount: float) -> str | None:
    """Find a short purchased/eaten item phrase near the amount."""
    amount_rx = _amount_pattern(amount)
    pattern = rf"\b(?:ate|eat|had|ordered|bought|purchased|paid\s+for)\s+(.+?)\s+(?:for|of)?\s*(?:{amount_rx})\b"
    match = re.search(pattern, user_input, flags=re.IGNORECASE)
    if not match:
        return None
    item = re.sub(r"\s+(?:at|from)\s+.+$", "", match.group(1), flags=re.IGNORECASE)
    item = re.sub(r"\s+", " ", item).strip(" .,;:-")
    return item.lower() if item else None


_GIFT_INCOME_WORDS = (
    "gift", "gifted", "present", "eidi", "eidy", "refund", "refunded",
    "cashback", "cash back", "reimburse", "reimbursed", "reimbursement",
)
_SALARY_WORDS = ("salary", "paycheck", "pay check", "wages", "monthly pay")
_FREELANCE_WORDS = (
    "freelance", "client", "project payment", "upwork", "fiverr", "commission",
)


def _correct_income_category(row: dict, user_input: str) -> str:
    """Fix obvious income mis-categorization the small model gets wrong.

    Gifts/refunds/cashback are not Salary or Freelance income; the model often
    guesses Freelance. Only override on a clear keyword signal so genuine model
    choices are preserved.
    """
    if str(row.get("type", "")).strip().lower() != "income":
        return row.get("category", "Other")
    text = user_input.lower()
    if any(word in text for word in _SALARY_WORDS):
        return "Salary"
    if any(word in text for word in _FREELANCE_WORDS):
        return "Freelance"
    if any(word in text for word in _GIFT_INCOME_WORDS):
        return "Other"
    return row.get("category", "Other")


def _polish_description(row: dict, user_input: str) -> str:
    """Keep descriptions grounded in the original sentence after LLM extraction."""
    place = _place_from_input(user_input)
    category = row.get("category")
    amount = float(row.get("amount", 0))

    if category == "Transport" and place:
        return f"{_transport_word(user_input)} to {place}"

    if category == "Food & Groceries":
        food_place = _food_place_for_amount(user_input, amount)
        item = _item_for_amount(user_input, amount)
        if item and food_place:
            return f"{item} at {food_place}"
        if item and place:
            return f"{item} at {place}"
        if item:
            return item

    return row.get("description", "")


def _complete_transaction(item: dict, confidence: str, now: datetime, user_input: str) -> dict:
    """Return a complete app-safe transaction row."""
    completed = dict(item)
    completed["type"] = str(completed.get("type", "")).strip().lower()
    completed["amount"] = float(completed.get("amount"))
    completed["category"] = str(completed.get("category", "Other")).strip() or "Other"
    completed["category"] = _correct_income_category(completed, user_input)
    completed["description"] = str(completed.get("description", "")).strip()
    completed["date"] = _date_for_amount(user_input, completed["amount"], now) or _default_date(completed.get("date"), now)
    completed["time"] = _default_time(completed.get("time"), now)
    completed["confidence"] = str(completed.get("confidence") or confidence).strip().lower()
    if completed["confidence"] not in {"high", "low"}:
        completed["confidence"] = confidence
    completed["description"] = _polish_description(completed, user_input).strip() or completed["description"]
    return completed


def _complete_payload(result: dict, confidence: str, user_input: str) -> dict:
    """Fill safe defaults after model validation without changing meaning."""
    now = datetime.now()
    if "transactions" in result:
        return {
            "transactions": [
                _complete_transaction(row, confidence, now, user_input)
                for row in result["transactions"]
            ]
        }
    return _complete_transaction(result, confidence, now, user_input)


def _input_numeric_amounts(user_input: str) -> set[float]:
    """Return numeric money-like values explicitly present in the input."""
    text = re.sub(r"\d{4}-\d{1,2}-\d{1,2}", " ", user_input)
    text = re.sub(r"\b\d{1,2}:\d{2}\b", " ", text)
    amounts: set[float] = set()
    for match in re.finditer(r"\b\d[\d,]*(?:\.\d+)?\b", text):
        try:
            amounts.add(round(float(match.group().replace(",", "")), 2))
        except ValueError:
            continue
    return amounts


def _has_amount_language(user_input: str) -> bool:
    """Return True when the user said an amount as digits or words."""
    if _input_numeric_amounts(user_input):
        return True
    words = set(re.findall(r"\b[a-z]+\b", user_input.lower()))
    return bool(words & _AMOUNT_WORDS)


def _payload_amounts(result: dict) -> list[float]:
    """Return all amounts from a single or multi-transaction payload."""
    rows = result.get("transactions") if isinstance(result.get("transactions"), list) else [result]
    amounts: list[float] = []
    for row in rows:
        try:
            amounts.append(round(float(row.get("amount")), 2))
        except (AttributeError, TypeError, ValueError):
            return []
    return amounts


def _amounts_supported_by_input(result: dict, user_input: str) -> bool:
    """Reject model/fallback rows that invent or duplicate numeric amounts."""
    input_amounts = _input_numeric_amounts(user_input)
    output_amounts = _payload_amounts(result)
    if not input_amounts:
        # Word amounts such as "three thousand" are allowed through the LLM,
        # but unrelated text must not become a fake transaction.
        return bool(output_amounts) and _has_amount_language(user_input)
    return bool(output_amounts) and all(amount in input_amounts for amount in output_amounts)


def _valid_transaction(item: object) -> bool:
    """Check that one model row is safe to save."""
    if not isinstance(item, dict):
        return False
    required = {"type", "amount", "category", "description"}
    if not required.issubset(item.keys()):
        return False
    if str(item.get("type", "")).strip().lower() not in _ALLOWED_TYPES:
        return False
    if str(item.get("category", "")).strip() not in _ALLOWED_CATEGORIES:
        return False
    if not str(item.get("description", "")).strip():
        return False
    try:
        return float(item.get("amount")) > 0
    except (TypeError, ValueError):
        return False


def _valid_payload(result: object) -> bool:
    """Check single or multi transaction payload shape."""
    if not isinstance(result, dict) or "error" in result:
        return False
    if "transactions" in result:
        rows = result.get("transactions")
        return isinstance(rows, list) and bool(rows) and all(_valid_transaction(row) for row in rows)
    return _valid_transaction(result)


def _safe_payload(result: object, user_input: str) -> bool:
    """Validate shape and make sure numeric amounts came from the user text."""
    return isinstance(result, dict) and _valid_payload(result) and _amounts_supported_by_input(result, user_input)


def _warmup() -> None:
    """Load the primary model into Ollama RAM in the background."""
    global _WARMUP_DONE
    with _WARMUP_LOCK:
        if _WARMUP_DONE:
            return
    if not _ollama_reachable():
        return
    try:
        requests.post(
            f"{OLLAMA_HOST}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "stream": False,
                "format": "json",
                "keep_alive": "15m",
                "options": {"temperature": 0},
                "messages": [{"role": "user", "content": 'Return {"ready": true} as JSON.'}],
            },
            timeout=(3, max(OLLAMA_TIMEOUT, 60)),
        )
    except Exception:
        pass
    finally:
        with _WARMUP_LOCK:
            _WARMUP_DONE = True


def warmup_async() -> None:
    """Start model warm-up without blocking the GUI."""
    threading.Thread(target=_warmup, daemon=True).start()


# Loan/shared signal words. Deliberately excludes bare "gave me"/"sent me"/"paid me"
# — those are income, not loans, and would make the model hallucinate a lender.
_FINANCE_SIGNAL_RX = re.compile(
    r"\b(lent|loaned|loan|borrow(?:ed|ing)?|owe[ds]?|owes|repaid|repay|"
    r"settle[ds]?|clear(?:ed)?|split|shared?|share|each|everyone|between|evenly|"
    r"half|percent|back)\b|%",
    re.IGNORECASE,
)


def _looks_like_finance_intent(text: str) -> bool:
    """Cheap gate so the LLM finance parser only runs on loan/shared-looking text."""
    return bool(_FINANCE_SIGNAL_RX.search(text))


def _llm_special_intent(user_input: str) -> dict | None:
    """Use the local model to extract a loan/shared plan from natural language."""
    from voicetrack.finance_intents import build_plan_from_spec

    for model in _candidate_models():
        spec = _call_deadline(model, FINANCE_INTENT_SYSTEM_PROMPT, _runtime_context(user_input))
        if spec is None:
            continue
        # Pass the original text so invented (hallucinated) names are rejected.
        plan = build_plan_from_spec(spec, text=user_input)
        if plan:
            return plan
    return None


def extract(user_input: str) -> dict:
    """Extract one or more transactions from natural language."""
    from voicetrack import fallback
    from voicetrack.finance_intents import parse_special_intent

    user_input = _expand_shorthand_amounts(user_input)

    # 1. Fast deterministic loan/shared parser (works fully offline).
    special = parse_special_intent(user_input)
    if special:
        return special

    # 2. Natural-language loan/shared parser via the local model, for phrasings the
    #    regex parser misses. Python still computes all split/loan math.
    if _ollama_reachable() and _looks_like_finance_intent(user_input):
        llm_plan = _llm_special_intent(user_input)
        if llm_plan:
            return llm_plan

    if _ollama_reachable():
        last_extractor_result: dict | None = None
        for model in _candidate_models():
            raw = _call_deadline(model, EXTRACTOR_SYSTEM_PROMPT, _runtime_context(user_input))
            if raw is None:
                continue
            if "error" in raw:
                return raw

            if _safe_payload(raw, user_input):
                last_extractor_result = raw
            validated = _call_deadline(model, ORCHESTRATOR_SYSTEM_PROMPT, _orchestrator_context(user_input, raw))
            if validated is not None and _safe_payload(validated, user_input):
                return _complete_payload(validated, "high", user_input)

        if last_extractor_result is not None:
            _mark_confidence(last_extractor_result, "low")
            return _complete_payload(last_extractor_result, "low", user_input)

    result = fallback.parse(user_input)
    _mark_confidence(result, "low")
    if not _safe_payload(result, user_input):
        return {"error": "Could not understand input. Please rephrase."}
    return result


def normalize_transactions(result: dict) -> list[dict]:
    """Return a list shape for GUI saving code."""
    if result.get("intent") in {
        "loan_given",
        "loan_taken",
        "loan_repayment_received",
        "loan_repayment_made",
        "loan_clear",
        "shared_expense",
    }:
        return []
    if "transactions" in result:
        return result["transactions"]
    if "error" in result:
        return []
    return [result]
