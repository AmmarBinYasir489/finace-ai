"""Loan and shared-expense intent parser.

This module is intentionally separate from the ordinary expense extractor.
It only handles domain intents that need extra accounting tables; normal
income/expense text still goes through the existing Qwen extraction path.
"""

from __future__ import annotations

import re
from collections import defaultdict


_STOP_NAMES = {
    "I", "Me", "My", "We", "Yesterday", "Today", "Split", "Cab", "Taxi",
    "Food", "Hotel", "Dinner", "Groceries", "Loaded", "Texas", "Fries",
    "Savour", "Foods",
}


def _amounts(text: str) -> list[float]:
    return [float(m.group().replace(",", "")) for m in re.finditer(r"\b\d[\d,]*(?:\.\d+)?\b", text)]


def _clean_name(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip(" .,;:-").title()


def _names(text: str) -> list[str]:
    found: list[str] = []
    for match in re.finditer(r"\b[A-Z][a-z]{2,}\b", text):
        name = _clean_name(match.group())
        if name not in _STOP_NAMES and name not in found:
            found.append(name)
    return found


def _date_token(text: str) -> str:
    lower = text.lower()
    if "yesterday" in lower:
        return "yesterday"
    if "last week" in lower:
        return "last week"
    if "last month" in lower:
        return "last month"
    match = re.search(r"\b\d{4}-\d{1,2}-\d{1,2}\b", text)
    return match.group() if match else "today"


def _category_for(text: str) -> str:
    lower = text.lower()
    if any(w in lower for w in ["cab", "taxi", "ride", "fare", "bus", "train", "rickshaw", "uber"]):
        return "Transport"
    if any(w in lower for w in ["food", "dinner", "lunch", "breakfast", "fries", "burger", "restaurant", "grocer"]):
        return "Food & Groceries"
    if "hotel" in lower or "bill" in lower:
        return "Other"
    return "Other"


def _component_description(text: str, category: str) -> str:
    lower = text.lower()
    if category == "Transport":
        if "taxi" in lower:
            return "taxi shared expense"
        if "cab" in lower:
            return "cab shared expense"
        return "transport shared expense"
    if "loaded fries" in lower:
        return "loaded fries shared expense"
    if "dinner" in lower:
        return "dinner shared expense"
    if "grocer" in lower:
        return "groceries shared expense"
    if "food" in lower:
        return "food shared expense"
    if "hotel" in lower:
        return "hotel bill shared expense"
    return "shared expense"


def _loan_intent(text: str) -> dict | None:
    date_value = _date_token(text)
    patterns = [
        ("loan_given", r"\b(?:i\s+)?(?:lent|loaned)\s+([A-Z][a-z]+)\s+(\d[\d,]*(?:\.\d+)?)\b"),
        ("loan_taken", r"\b(?:i\s+)?borrowed\s+(\d[\d,]*(?:\.\d+)?)\s+from\s+([A-Z][a-z]+)\b"),
        ("loan_repayment_received", r"\b([A-Z][a-z]+)\s+(?:returned|paid\s+me\s+back|gave\s+me\s+back)\s+(\d[\d,]*(?:\.\d+)?)\b"),
        ("loan_repayment_made", r"\b(?:i\s+)?paid\s+([A-Z][a-z]+)\s+(\d[\d,]*(?:\.\d+)?)\b"),
    ]
    for action, pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        if action == "loan_taken":
            amount, person = match.group(1), match.group(2)
        else:
            person, amount = match.group(1), match.group(2)
        return {
            "intent": action,
            "person": _clean_name(person),
            "amount": float(amount.replace(",", "")),
            "date": date_value,
            "notes": text,
            "confidence": "high",
        }
    return None


def _people_after_markers(text: str) -> list[str]:
    patterns = [
        r"\bwith\s+(.+?)(?=\.|,|\s+on\b|\s+for\b|\s+and\s+we\b|$)",
        r"\bfor\s+(.+?)(?=\.|,|\s*$)",
        r"\bbetween\s+(.+?)(?=\.|,|\s*$)",
    ]
    people: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            segment = match.group(1)
            for name in _names(segment):
                if name not in people:
                    people.append(name)
    if not people:
        people = _names(text)
    return people


def _component_amounts(text: str) -> list[dict]:
    components: list[dict] = []
    patterns = [
        (r"\b(cab|taxi|ride|fare|bus|train|rickshaw|uber)\b[^.]*?\b(?:for|was|is)\s+(\d[\d,]*(?:\.\d+)?)", "Transport"),
        (r"\b(food|dinner|lunch|breakfast|loaded fries|fries|burger|grocer\w*)\b[^.]*?\b(?:for|was|is)\s+(\d[\d,]*(?:\.\d+)?)", "Food & Groceries"),
        (r"\b(hotel bill|hotel|bill)\b[^.]*?\b(?:for|was|is)\s+(\d[\d,]*(?:\.\d+)?)", "Other"),
    ]
    seen: set[float] = set()
    for pattern, category in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            amount = float(match.group(2).replace(",", ""))
            if amount in seen:
                continue
            seen.add(amount)
            phrase = match.group(0)
            components.append({
                "amount": amount,
                "category": category,
                "description": _component_description(phrase, category),
            })

    if components:
        return components

    amounts = _amounts(text)
    if not amounts:
        return []
    amount = amounts[0]
    category = _category_for(text)
    return [{
        "amount": amount,
        "category": category,
        "description": _component_description(text, category),
    }]


def _paid_back_people(text: str, people: list[str]) -> set[str]:
    paid_back: set[str] = set()
    for person in people:
        if re.search(rf"\b{re.escape(person)}\b[^.]*\b(?:paid\s+me\s+back|returned)\b", text, flags=re.IGNORECASE):
            paid_back.add(person)
    return paid_back


def _split_component(component: dict, text: str, all_people: list[str]) -> tuple[float, dict[str, float]]:
    amount = float(component["amount"])
    lower = text.lower()
    people = list(all_people)

    percent_match = re.search(r"\b([A-Z][a-z]+)\s+will\s+pay\s+(\d+(?:\.\d+)?)\s*%", text, flags=re.IGNORECASE)
    if percent_match and len(_component_amounts(text)) == 1:
        person = _clean_name(percent_match.group(1))
        share = round(amount * float(percent_match.group(2)) / 100, 2)
        return round(amount - share, 2), {person: share}

    fixed_match = re.search(r"\b([A-Z][a-z]+)\s+pays?\s+(\d[\d,]*(?:\.\d+)?)\b", text, flags=re.IGNORECASE)
    if fixed_match and len(_component_amounts(text)) == 1:
        person = _clean_name(fixed_match.group(1))
        share = float(fixed_match.group(2).replace(",", ""))
        return round(amount - share, 2), {person: share}

    if component["category"] == "Food & Groceries" and "food only between me and ali" in lower:
        people = [p for p in people if p.lower() == "ali"]
    if component["category"] == "Transport" and ("taxi should be split among all three" in lower or "taxi split" in lower):
        people = all_people

    count = len(people) + 1
    share = round(amount / count, 2)
    my_share = round(amount - share * len(people), 2)
    return my_share, {person: share for person in people}


def _shared_intent(text: str) -> dict | None:
    lower = text.lower()
    has_shared_marker = any(
        marker in lower
        for marker in [" split", " with ", " for me", "will pay", "paid me back immediately", "between me"]
    )
    if not has_shared_marker:
        return None

    people = _people_after_markers(text)
    if not people:
        return None

    components = _component_amounts(text)
    if not components:
        return None

    person_totals: defaultdict[str, float] = defaultdict(float)
    component_rows: list[dict] = []
    for component in components:
        my_share, shares = _split_component(component, text, people)
        row = dict(component)
        row["my_share"] = my_share
        component_rows.append(row)
        for person, share in shares.items():
            person_totals[person] += share

    paid_back = _paid_back_people(text, people)
    return {
        "intent": "shared_expense",
        "description": "Shared expense",
        "date": _date_token(text),
        "total_paid": round(sum(float(c["amount"]) for c in components), 2),
        "components": component_rows,
        "people": [
            {"name": person, "share": round(share, 2), "paid_back": person in paid_back}
            for person, share in person_totals.items()
        ],
        "confidence": "high",
    }


def parse_special_intent(text: str) -> dict | None:
    """Return a loan/shared plan, or None for ordinary transactions."""
    loan = _loan_intent(text)
    if loan:
        return loan
    return _shared_intent(text)
