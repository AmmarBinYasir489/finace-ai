import re

# Common misspelling → canonical form
_SPELLING_FIXES = [
    (r'\breciev\w*\b', 'received'),
    (r'\breciv\w*\b',  'received'),
    (r'\brecev\w*\b',  'received'),
    (r'\bgoten\b',     'gotten'),
    (r'\bgott\b',      'got'),
    (r'\bpaid\b',      'paid'),
    (r'\bpayd\b',      'paid'),
    (r'\bspend\b',     'spent'),
    (r'\bspended\b',   'spent'),
    (r'\bbought\b',    'bought'),
    (r'\bbuy\b',       'bought'),
]

INCOME_PHRASES = [
    "gifted me", "gift me", "gave me", "give me",
    "sent me", "send me", "transferred me", "transfer me",
    "paid me", "pay me", "paying me",
    "returned my", "returned me", "he returned", "she returned", "owed me",
    "paid back", "paid me back",
    "lent me", "loan to me", "given me",
    "received", "got paid", "i got", "i received",
    "bonus received", "bonus given", "salary received",
    "deposited", "refund", "cashback", "reimbursed",
    "won", "earned", "commission received",
    "client paid", "customer paid", "payment received",
    "freelance payment", "project payment",
    "salary", "income",
]

EXPENSE_PHRASES = [
    "i gave", "i paid", "i sent", "i transferred",
    "i bought", "i spent", "i lent", "i loaned",
    "gave to", "paid to", "sent to", "transferred to",
    "gave my", "paid my", "gave him", "gave her",
    "gave them", "paid him", "paid her", "paid them",
    "lent to", "loaned to",
    "bought", "spent", "purchased", "paid for",
    "done shopping", "did shopping", "gone shopping",
    "shopping of", "fare was", "fare is", "cost was", "cost is",
]

CATEGORY_KEYWORDS = [
    (["movie", "cinema", "ticket", "film", "netflix", "spotify", "game",
      "entertainment", "concert", "watched", "watch"], "Entertainment"),
    (["cab", "uber", "careem", "rickshaw", "bus", "train", "fare", "ride",
      "fuel", "transport", "auto", "taxi", "metro", "murree transportation"], "Transport"),
    (["food", "grocery", "groceries", "lunch", "dinner", "breakfast", "meal",
      "restaurant", "pizza", "burger", "kfc", "mcdonalds", "chai", "tea",
      "coffee", "snack", "biryani", "karahi", "bbq", "banana", "fruit",
      "vegetables", "meat"], "Food & Groceries"),
    (["electricity", "gas", "water", "bill", "internet", "wifi", "utility",
      "utilities", "bijli", "sui gas"], "Utilities"),
    (["doctor", "medicine", "hospital", "pharmacy", "health", "clinic",
      "medical", "dawa", "dawai"], "Health"),
    (["school", "college", "university", "tuition", "course", "education",
      "fee", "fees", "books", "challan"], "Education"),
    (["rent", "house rent", "apartment", "kiraya"], "Rent"),
    (["salary", "paycheck", "monthly pay", "wages"], "Salary"),
    (["freelance", "project payment", "client payment", "client paid",
      "upwork", "fiverr", "commission"], "Freelance"),
    (["shopping", "clothes", "amazon", "daraz", "mall", "shoes", "shirt",
      "t-shirt", "tshirt", "dress", "purchased"], "Shopping"),
]

# Conjunction/separator patterns that often split multi-transaction sentences
_SPLIT_PATTERNS = [
    # "and paid/bought/spent/done..." — verb-led second clause
    r'\s+and\s+(?=(?:paid|bought|spent|gave|sent|received|got|purchased|done|did)\s)',
    r'\s*,\s*(?=(?:paid|bought|spent|gave|sent|received|got|purchased|done|did)\s)',
    # "and <number>" — amount-led: "ticket 2000 and 500 for cab"
    r'\s+and\s+(?=\d)',
    # "and <1-4 words> <number>" — "shopping 3000 and cab fare 500"
    r'\s+and\s+(?=(?:\w+\s+){1,4}\d)',
]


def _fix_spelling(text: str) -> str:
    for pattern, replacement in _SPELLING_FIXES:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def _resolve_date_str(lowered: str, original: str) -> str:
    explicit = re.search(r'\d{4}-\d{2}-\d{2}', original)
    if explicit:
        return explicit.group()
    if "yesterday" in lowered:
        return "yesterday"
    if re.search(r'last\s+week', lowered):
        return "last week"
    if re.search(r'last\s+month', lowered):
        return "last month"
    if "tomorrow" in lowered:
        # store as tomorrow's date offset; db resolves "today" only,
        # so we return the ISO string computed here
        import datetime
        return (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    return "today"


def _detect_type(lowered: str) -> str:
    # Check expense phrases first (more specific — "i paid", "i gave" etc.)
    for phrase in EXPENSE_PHRASES:
        if phrase in lowered:
            return "expense"
    # Then income phrases
    for phrase in INCOME_PHRASES:
        if phrase in lowered:
            return "income"
    return "expense"  # safe default


def _detect_category(lowered: str) -> str:
    for keywords, cat in CATEGORY_KEYWORDS:
        if any(kw in lowered for kw in keywords):
            return cat
    return "Other"


_FILLER = re.compile(
    r'\b(i|the|a|an|on|for|of|to|at|in|by|with|my|his|her|their|'
    r'some|just|also|and|paid|spent|bought|received|got|gave|'
    r'yesterday|today|tomorrow|last|week|month)\b',
    re.IGNORECASE,
)
_TRAILING_JUNK = re.compile(r'[\s\-–,;:\.]+$')


def _make_description(text: str, amount: float) -> str:
    """Build a clean short description from the raw text."""
    # Remove the amount (number) so description doesn't end with "for"
    desc = re.sub(r'[\d,]+(?:\.\d+)?', '', text)
    # Remove common filler words to get the core meaning
    desc = _FILLER.sub(' ', desc)
    # Collapse spaces and strip trailing punctuation/prepositions
    desc = re.sub(r'\s+', ' ', desc).strip()
    desc = _TRAILING_JUNK.sub('', desc).strip()
    # Capitalise and cap length
    return (desc[:50].capitalize() or text[:40]) if desc else text[:40]


def _parse_single(text: str) -> dict | None:
    """Parse one transaction sentence. Returns None if no amount found."""
    fixed = _fix_spelling(text)
    lowered = fixed.lower()

    match = re.search(r'[\d,]+(?:\.\d+)?', fixed)
    if not match:
        return None
    amount = float(match.group().replace(',', ''))

    tx_type  = _detect_type(lowered)
    category = _detect_category(lowered)

    # Salary/Freelance always income
    if category in ("Salary", "Freelance") and tx_type == "expense":
        tx_type = "income"

    date        = _resolve_date_str(lowered, fixed)
    description = _make_description(fixed, amount)

    return {
        "type":        tx_type,
        "amount":      amount,
        "category":    category,
        "description": description,
        "date":        date,
        "time":        None,
        "confidence":  "low",
    }


def parse(user_input: str) -> dict:
    """
    Try to split multi-transaction input, parse each part.
    Returns a single transaction dict or a {'transactions': [...]} dict.
    The date is resolved from the full sentence and applied to all parts.
    """
    fixed = _fix_spelling(user_input)
    lowered = fixed.lower()

    # Resolve date once from the full sentence so split parts inherit it
    global_date = _resolve_date_str(lowered, fixed)

    # Try splitting on conjunctions
    parts = [fixed]
    for pattern in _SPLIT_PATTERNS:
        chunks = re.split(pattern, fixed, flags=re.IGNORECASE)
        if len(chunks) > 1:
            parts = chunks
            break

    results = []
    for p in parts:
        r = _parse_single(p)
        if r is not None:
            # Override date with the global date from the full sentence
            r["date"] = global_date
            results.append(r)

    if not results:
        r = _parse_single(user_input)
        if r:
            r["date"] = global_date
            return r
        return {"type": "expense", "amount": 0, "category": "Other",
                "description": user_input[:60], "date": global_date,
                "time": None, "confidence": "low"}

    if len(results) == 1:
        return results[0]

    return {"transactions": results}
