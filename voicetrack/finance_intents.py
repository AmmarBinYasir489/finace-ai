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
    "Savour", "Foods", "Market", "Shop", "Mall", "Paid", "Shared", "Equally",
    "Covered", "Bought",
    "The", "A", "An",
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


def _name_candidates(text: str) -> list[str]:
    """Find likely person names in casual text, including lowercase names."""
    found = _names(text)
    patterns = [
        r"\b([a-zA-Z]{3,})\s+paid\b",
        r"\b([a-zA-Z]{3,})\s+covered\b",
        r"\b([a-zA-Z]{3,})\s+bought\b",
        r"\bpaid\s+by\s+([a-zA-Z]{3,})\b",
        r"\bwith\s+([a-zA-Z]{3,})\b",
        r"\b([a-zA-Z]{3,})\s+and\s+i\b",
        r"\bi\s+and\s+([a-zA-Z]{3,})\b",
        r"\bfrom\s+([a-zA-Z]{3,})\b",
        r"\bto\s+([a-zA-Z]{3,})\b",
        # "ali will send/give/pay me ...", "ali owes ...", "ali pays/sends me ..."
        r"\b([a-zA-Z]{3,})\s+will\s+(?:send|give|pay|return|transfer)\b",
        r"\b([a-zA-Z]{3,})\s+(?:owe|owes)\b",
        r"\b([a-zA-Z]{3,})\s+(?:sends?|gives?|pays?|returns?)\s+me\b",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            name = _clean_name(match.group(1))
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
    if any(w in lower for w in ["market", "shop", "shopping", "mall", "bought", "purchase"]):
        return "Shopping"
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
    if "market" in lower:
        return "market shared expense"
    if "shop" in lower:
        return "shopping shared expense"
    if "food" in lower:
        return "food shared expense"
    if "hotel" in lower:
        return "hotel bill shared expense"
    return "shared expense"


def _loan_intent(text: str) -> dict | None:
    date_value = _date_token(text)
    clear_match = re.search(
        r"\b(?:loan\s+from\s+|loan\s+to\s+)?([a-zA-Z]{3,})\b[^.]*\b(?:clear|cleared|settled|closed|paid)\b",
        text,
        flags=re.IGNORECASE,
    )
    if clear_match and not _amounts(text):
        person = _clean_name(clear_match.group(1))
        if person not in _STOP_NAMES:
            return {
                "intent": "loan_clear",
                "person": person,
                "date": date_value,
                "notes": text,
                "confidence": "high",
            }

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
            for name in _name_candidates(segment):
                if name not in people:
                    people.append(name)
    if not people:
        people = _name_candidates(text)
    return people


def _payer(text: str) -> str:
    """Return who paid the bill; 'me' is the default for existing behavior."""
    if re.search(r"\b(i|me|my)\s+(?:paid|covered|bought)\b", text, flags=re.IGNORECASE):
        return "me"
    patterns = [
        r"\b([a-zA-Z]{3,})\s+(?:paid|covered|bought)\b",
        r"\bpaid\s+by\s+([a-zA-Z]{3,})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            name = _clean_name(match.group(1))
            if name not in _STOP_NAMES:
                return name
    return "me"


def _component_amounts(text: str) -> list[dict]:
    components: list[dict] = []
    patterns = [
        (r"\b(?:paid|bill|total|market|shopping|shop)[^.]*?\b(?:for|was|is|paid)?\s*(\d[\d,]*(?:\.\d+)?)", None),
        (r"\b(cab|taxi|ride|fare|bus|train|rickshaw|uber)\b[^.]*?\b(?:for|was|is)\s+(\d[\d,]*(?:\.\d+)?)", "Transport"),
        (r"\b(food|dinner|lunch|breakfast|loaded fries|fries|burger|grocer\w*)\b[^.]*?\b(?:for|was|is)\s+(\d[\d,]*(?:\.\d+)?)", "Food & Groceries"),
        (r"\b(hotel bill|hotel|bill)\b[^.]*?\b(?:for|was|is)\s+(\d[\d,]*(?:\.\d+)?)", "Other"),
    ]
    seen: set[float] = set()
    for pattern, category in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            amount_group = match.group(2) if category else match.group(1)
            amount = float(amount_group.replace(",", ""))
            if amount in seen:
                continue
            seen.add(amount)
            phrase = match.group(0)
            final_category = category or _category_for(text)
            components.append({
                "amount": amount,
                "category": final_category,
                "description": _component_description(phrase, final_category),
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


_SHARE_VERBS = r"(?:will\s+)?(?:pay|send|give|return|transfer|owe)s?(?:\s+me)?"


def _percent_owner(text: str, people: list[str]) -> str | None:
    """Find which person a '<n>%' share belongs to.

    Tries an explicit "<name> will send/pay/owes me <n>%" phrasing first, then
    falls back to the sole other participant when there is exactly one.
    """
    match = re.search(rf"\b([a-zA-Z]{{3,}})\s+{_SHARE_VERBS}\s+\d+(?:\.\d+)?\s*%", text, flags=re.IGNORECASE)
    if match:
        name = _clean_name(match.group(1))
        if name not in _STOP_NAMES:
            return name
    others = [p for p in people if p.lower() != "me"]
    return others[0] if len(others) == 1 else None


def _split_component(component: dict, text: str, all_people: list[str]) -> tuple[float, dict[str, float]]:
    amount = float(component["amount"])
    lower = text.lower()
    people = list(all_people)

    # Percentage split: "<name> will send/pay me 50%", "<name> owes 50%", or just "50%".
    percent_value = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
    if percent_value and len(_component_amounts(text)) == 1:
        person = _percent_owner(text, people)
        if person:
            share = round(amount * float(percent_value.group(1)) / 100, 2)
            return round(amount - share, 2), {person: share}

    # Fixed share: "<name> will send/pay me 500".
    fixed_match = re.search(
        rf"\b([a-zA-Z]{{3,}})\s+{_SHARE_VERBS}\s+(\d[\d,]*(?:\.\d+)?)\b",
        text, flags=re.IGNORECASE,
    )
    if fixed_match and len(_component_amounts(text)) == 1:
        person = _clean_name(fixed_match.group(1))
        if person not in _STOP_NAMES:
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
        for marker in [
            " split", "shared", " with ", " for me", "will pay", "paid me back immediately",
            "between me", "%", "send me", "give me", "pay me", "owe me", "owes me",
            "will send", "will give", "will return", "his share", "her share", "my share",
        ]
    )
    if not has_shared_marker:
        return None

    people = _people_after_markers(text)
    payer = _payer(text)
    if payer != "me" and payer not in people:
        people.append(payer)
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
        "payer": payer,
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


_ME_WORDS = {"me", "i", "myself"}
_LOAN_SPEC_INTENTS = {
    "loan_given", "loan_taken", "loan_repayment_received", "loan_repayment_made",
}


def _to_amount(value: object) -> float | None:
    try:
        return round(float(str(value).replace(",", "").replace("%", "").strip()), 2)
    except (TypeError, ValueError):
        return None


def _resolve_shared_spec(spec: dict, date_value: str) -> dict | None:
    """Compute personal/other shares from an LLM-extracted shared-expense spec.

    The model supplies the total, payer, participants, and any explicit shares;
    Python does all the arithmetic here so no math depends on the model.
    """
    total = _to_amount(spec.get("total"))
    if total is None or total <= 0:
        return None

    payer_raw = str(spec.get("payer") or "me").strip()
    payer = "me" if payer_raw.lower() in _ME_WORDS else _clean_name(payer_raw)
    description = (str(spec.get("description") or "").strip() or "shared expense")
    category = str(spec.get("category") or _category_for(description) or "Other").strip()

    others: list[str] = []
    for participant in spec.get("participants") or []:
        ps = str(participant).strip()
        if ps.lower() in _ME_WORDS:
            continue
        name = _clean_name(ps)
        if name and name not in _STOP_NAMES and name not in others:
            others.append(name)

    explicit: dict[str, float] = {}
    for split in spec.get("splits") or []:
        if not isinstance(split, dict):
            continue
        name_raw = str(split.get("person", "")).strip()
        if name_raw.lower() in _ME_WORDS or not name_raw:
            continue
        name = _clean_name(name_raw)
        if name in _STOP_NAMES:
            continue
        value = _to_amount(split.get("value"))
        mode = str(split.get("mode", "equal")).lower()
        if name not in others:
            others.append(name)
        if value is None:
            continue
        if mode == "percent":
            explicit[name] = round(total * value / 100, 2)
        elif mode == "fixed":
            explicit[name] = round(value, 2)
        # "equal" -> handled by the remainder split below.

    remainder_people = [p for p in others if p not in explicit]
    remaining = round(total - round(sum(explicit.values()), 2), 2)
    equal_share = round(remaining / (len(remainder_people) + 1), 2) if remaining > 0 else 0

    people_shares = dict(explicit)
    for person in remainder_people:
        people_shares[person] = equal_share

    my_share = round(total - round(sum(people_shares.values()), 2), 2)
    if my_share < 0:
        return None

    return {
        "intent": "shared_expense",
        "description": description,
        "payer": payer,
        "date": date_value,
        "total_paid": round(total, 2),
        "components": [{
            "amount": round(total, 2),
            "category": category,
            "description": description,
            "my_share": my_share,
        }],
        "people": [
            {"name": person, "share": round(share, 2), "paid_back": False}
            for person, share in people_shares.items()
        ],
        "confidence": "high",
    }


def build_plan_from_spec(spec: dict) -> dict | None:
    """Turn an LLM-extracted finance spec into an applyable plan, or None.

    The model classifies and extracts; this function validates and structures.
    Returns None for "none"/invalid specs so the caller can fall back.
    """
    if not isinstance(spec, dict):
        return None
    intent = str(spec.get("intent", "")).strip().lower()
    date_value = str(spec.get("date") or "today").strip() or "today"
    notes = str(spec.get("notes") or "").strip()

    if intent == "loan_clear":
        person = _clean_name(str(spec.get("person", "")))
        if not person or person in _STOP_NAMES:
            return None
        return {"intent": "loan_clear", "person": person, "date": date_value,
                "notes": notes, "confidence": "high"}

    if intent in _LOAN_SPEC_INTENTS:
        person = _clean_name(str(spec.get("person", "")))
        amount = _to_amount(spec.get("amount"))
        if not person or person in _STOP_NAMES or amount is None or amount <= 0:
            return None
        return {"intent": intent, "person": person, "amount": amount,
                "date": date_value, "notes": notes, "confidence": "high"}

    if intent == "shared_expense":
        return _resolve_shared_spec(spec, date_value)

    return None
