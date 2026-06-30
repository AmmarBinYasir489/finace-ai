"""Finance assistant — the read-only Insight reporter.

Answers natural-language questions about the user's money. Question intent is
matched deterministically (keyword rules, works fully offline); every number in
the answer is computed from the database, never invented. This module reads the
database only — it never writes.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import voicetrack.db as db

# Map spoken words to the canonical categories used in the DB.
_CATEGORY_SYNONYMS = {
    "Food & Groceries": ["food", "grocery", "groceries", "eat", "eating", "dining",
                          "restaurant", "lunch", "dinner", "breakfast", "meal"],
    "Transport": ["transport", "travel", "cab", "taxi", "uber", "careem", "fuel",
                  "petrol", "ride", "fare", "bus", "train", "rickshaw"],
    "Utilities": ["utilities", "utility", "bill", "bills", "electricity", "gas",
                  "water", "internet", "wifi"],
    "Health": ["health", "medical", "doctor", "medicine", "hospital", "pharmacy"],
    "Education": ["education", "school", "college", "university", "tuition", "fees",
                  "course", "books"],
    "Shopping": ["shopping", "clothes", "shoes", "shirt", "dress", "mall"],
    "Entertainment": ["entertainment", "movie", "cinema", "netflix", "game", "concert"],
    "Rent": ["rent", "kiraya"],
    "Salary": ["salary", "paycheck", "wages"],
    "Freelance": ["freelance", "client", "project", "upwork", "fiverr", "commission"],
    "Other": ["other", "miscellaneous", "misc"],
}


def _pkr(value: float) -> str:
    return f"PKR {float(value):,.0f}"


def _period(question: str):
    """Return (label, date_from, date_to) ISO strings (or None) for time words."""
    today = date.today()
    if "today" in question:
        iso = today.isoformat()
        return "today", iso, iso
    if "last week" in question:
        # The previous Monday–Sunday calendar week.
        this_monday = today - timedelta(days=today.weekday())
        last_monday = this_monday - timedelta(days=7)
        last_sunday = last_monday + timedelta(days=6)
        return "last week", last_monday.isoformat(), last_sunday.isoformat()
    if "this week" in question or "past week" in question:
        this_monday = today - timedelta(days=today.weekday())
        return "this week", this_monday.isoformat(), today.isoformat()
    if "last month" in question:
        first_this = today.replace(day=1)
        last_month_end = first_this - timedelta(days=1)
        return "last month", last_month_end.replace(day=1).isoformat(), last_month_end.isoformat()
    if "this month" in question:
        return "this month", today.replace(day=1).isoformat(), today.isoformat()
    if "this year" in question:
        return "this year", today.replace(month=1, day=1).isoformat(), today.isoformat()
    return "all time", None, None


def _detect_category(question: str) -> str | None:
    for category, words in _CATEGORY_SYNONYMS.items():
        if any(re.search(rf"\b{re.escape(w)}\b", question) for w in words):
            return category
    return None


def _category_spend(category: str, date_from: str | None, date_to: str | None, path=None) -> float:
    rows = db.get_transactions(
        limit=1_000_000, category=category, tx_type="expense",
        date_from=date_from, date_to=date_to, path=path,
    )
    # Loan movements never carry a real spending category, so a category filter
    # already excludes them; sum the personal/shared expense rows.
    return round(sum(float(r["amount"]) for r in rows), 2)


def _named_person(question: str, accounts: list[dict]) -> dict | None:
    for account in accounts:
        if re.search(rf"\b{re.escape(account['person_name'].lower())}\b", question):
            return account
    return None


def _receivables(accounts: list[dict]) -> list[dict]:
    return [a for a in accounts
            if a["loan_type"] == "owed_to_me" and round(float(a["current_balance"]), 2) > 0]


def _payables(accounts: list[dict]) -> list[dict]:
    return [a for a in accounts
            if a["loan_type"] == "i_owe" and round(float(a["current_balance"]), 2) > 0]


_HELP = (
    "I can answer questions about your money, for example:\n"
    "  • How much did I spend on food this month?\n"
    "  • Who owes me money?\n"
    "  • How much loan do I need to pay?\n"
    "  • How much does Ahmed owe me?\n"
    "  • What is my net worth / available cash?\n"
    "  • How much did I earn this month?"
)


def answer(question: str, path=None) -> str:
    """Return a plain-text answer computed from the database."""
    q = (question or "").lower().strip()
    if not q:
        return _HELP

    period_label, date_from, date_to = _period(q)
    accounts = db.get_loan_accounts(path=path)

    # --- Person-specific loan questions (check before generic loan questions) ---
    person = _named_person(q, accounts) if "owe" in q or "loan" in q or "pay" in q else None
    if person:
        bal = round(float(person["current_balance"]), 2)
        name = person["person_name"]
        if bal == 0:
            return f"You're all settled with {name} — no outstanding balance."
        if person["loan_type"] == "owed_to_me":
            return f"{name} owes you {_pkr(bal)}."
        return f"You owe {name} {_pkr(bal)}."

    # --- Who owes me money (receivables) ---
    if "owe" in q and ("me" in q or "owes me" in q) and "i owe" not in q and "do i owe" not in q:
        recv = _receivables(accounts)
        if not recv:
            return "No one currently owes you money."
        total = sum(float(a["current_balance"]) for a in recv)
        lines = "\n".join(f"  • {a['person_name']}: {_pkr(a['current_balance'])}" for a in recv)
        return f"You are owed {_pkr(total)} in total:\n{lines}"

    # --- How much do I owe / loans I need to pay (payables) ---
    if (("how much" in q and "owe" in q) or "i owe" in q or "do i owe" in q
            or ("loan" in q and ("pay" in q or "repay" in q or "need" in q))
            or "pay back" in q):
        pay = _payables(accounts)
        if not pay:
            return "You don't owe anyone money right now."
        total = sum(float(a["current_balance"]) for a in pay)
        lines = "\n".join(f"  • {a['person_name']}: {_pkr(a['current_balance'])}" for a in pay)
        return f"You need to pay back {_pkr(total)} in total:\n{lines}"

    # --- Net worth ---
    if "net worth" in q or "networth" in q or "worth" in q:
        finance = db.get_finance_summary(path=path)
        return (f"Your net worth is {_pkr(finance['net_worth'])} "
                f"(cash {_pkr(finance['cash'])} + receivables {_pkr(finance['outstanding_receivables'])} "
                f"− payables {_pkr(finance['outstanding_payables'])}).")

    # --- Available cash / balance ---
    if ("cash" in q or "balance" in q
            or ("how much" in q and "money" in q and "have" in q)):
        finance = db.get_finance_summary(path=path)
        return f"Your available cash is {_pkr(finance['cash'])}."

    # --- Income / earnings ---
    if "earn" in q or "income" in q or "salary" in q or "made" in q:
        rows = db.get_transactions(limit=1_000_000, tx_type="income",
                                   date_from=date_from, date_to=date_to, path=path)
        total = round(sum(float(r["amount"]) for r in rows
                          if r.get("kind") in (None, "standard")), 2)
        return f"You earned {_pkr(total)} ({period_label})."

    # --- Spending (category-specific or total) ---
    if "spend" in q or "spent" in q or "expense" in q or "cost" in q:
        category = _detect_category(q)
        if category:
            amount = _category_spend(category, date_from, date_to, path=path)
            return f"You spent {_pkr(amount)} on {category} ({period_label})."
        rows = db.get_transactions(limit=1_000_000, tx_type="expense",
                                   date_from=date_from, date_to=date_to, path=path)
        total = round(sum(float(r["amount"]) for r in rows
                          if r.get("kind") in (None, "standard", "shared_expense")), 2)
        return f"You spent {_pkr(total)} in total ({period_label})."

    # --- Bare category mention ("food", "transport") treated as a spend query ---
    category = _detect_category(q)
    if category:
        amount = _category_spend(category, date_from, date_to, path=path)
        return f"You spent {_pkr(amount)} on {category} ({period_label})."

    return "Sorry, I didn't understand that.\n\n" + _HELP
