import sqlite3
import os
from datetime import date, timedelta

from voicetrack.config import DB_PATH

CATEGORIES = [
    "Food & Groceries", "Transport", "Utilities", "Health", "Education",
    "Shopping", "Entertainment", "Rent", "Salary", "Freelance", "Other",
]

_db_path = DB_PATH


def _connect(path=None):
    p = path or _db_path
    os.makedirs(os.path.dirname(p), exist_ok=True)
    con = sqlite3.connect(p)
    con.row_factory = sqlite3.Row
    # Enforce declared foreign keys (off by default in SQLite) and use WAL so a
    # crash mid-write cannot leave a torn database file.
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    return con


def init_db(path=None) -> None:
    global _db_path
    if path:
        _db_path = path
    con = _connect(path)
    try:
        con.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                type        TEXT NOT NULL CHECK(type IN ('expense','income')),
                amount      REAL NOT NULL,
                category    TEXT NOT NULL,
                description TEXT,
                date        TEXT NOT NULL,
                time        TEXT,
                confidence  TEXT CHECK(confidence IN ('high','low')),
                kind        TEXT DEFAULT 'standard',
                person      TEXT,
                loan_account_id INTEGER,
                shared_group_id INTEGER,
                cash_flow   REAL,
                created_at  TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS loan_accounts (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                person_name     TEXT UNIQUE NOT NULL COLLATE NOCASE,
                current_balance REAL NOT NULL DEFAULT 0,
                loan_type       TEXT NOT NULL CHECK(loan_type IN ('owed_to_me','i_owe')),
                status          TEXT NOT NULL DEFAULT 'Active',
                created_date    TEXT NOT NULL,
                last_activity   TEXT NOT NULL,
                notes           TEXT
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS loan_transactions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                loan_account_id INTEGER NOT NULL,
                transaction_id  INTEGER,
                action          TEXT NOT NULL,
                amount          REAL NOT NULL,
                balance_after   REAL NOT NULL,
                date            TEXT NOT NULL,
                notes           TEXT,
                created_at      TEXT DEFAULT (datetime('now','localtime')),
                FOREIGN KEY(loan_account_id) REFERENCES loan_accounts(id),
                FOREIGN KEY(transaction_id) REFERENCES transactions(id)
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS shared_expense_groups (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                description     TEXT,
                total_paid      REAL NOT NULL,
                my_share        REAL NOT NULL,
                others_share    REAL NOT NULL,
                date            TEXT NOT NULL,
                created_at      TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS shared_expense_participants (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id        INTEGER NOT NULL,
                person_name     TEXT NOT NULL,
                share_amount    REAL NOT NULL,
                status          TEXT NOT NULL DEFAULT 'Open',
                loan_account_id INTEGER,
                FOREIGN KEY(group_id) REFERENCES shared_expense_groups(id),
                FOREIGN KEY(loan_account_id) REFERENCES loan_accounts(id)
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                id    INTEGER PRIMARY KEY AUTOINCREMENT,
                name  TEXT UNIQUE NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS event_trace (
                request_id          TEXT PRIMARY KEY,
                raw_input           TEXT NOT NULL,
                normalized_input    TEXT,
                route               TEXT,
                parser_output       TEXT,
                auditor_output      TEXT,
                final_event         TEXT,
                transaction_ids     TEXT,
                confidence          REAL,
                model               TEXT,
                prompt_version      TEXT,
                latency_ms          INTEGER,
                fallback_used       INTEGER DEFAULT 0,
                needs_clarification INTEGER DEFAULT 0,
                errors              TEXT,
                created_at          TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        # Indexes for the analytics queries that currently full-scan transactions.
        con.execute("CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(date)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_tx_kind ON transactions(kind)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_tx_loan ON transactions(loan_account_id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_loantx_acct ON loan_transactions(loan_account_id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_part_group ON shared_expense_participants(group_id)")
        con.executemany(
            "INSERT OR IGNORE INTO categories(name) VALUES (?)",
            [(c,) for c in CATEGORIES],
        )
        con.commit()
        # Additive migrations for existing local databases.
        cols = [r[1] for r in con.execute("PRAGMA table_info(transactions)").fetchall()]
        if "confidence" not in cols:
            con.execute("ALTER TABLE transactions ADD COLUMN confidence TEXT")
        for name, ddl in {
            "kind": "ALTER TABLE transactions ADD COLUMN kind TEXT DEFAULT 'standard'",
            "person": "ALTER TABLE transactions ADD COLUMN person TEXT",
            "loan_account_id": "ALTER TABLE transactions ADD COLUMN loan_account_id INTEGER",
            "shared_group_id": "ALTER TABLE transactions ADD COLUMN shared_group_id INTEGER",
            "cash_flow": "ALTER TABLE transactions ADD COLUMN cash_flow REAL",
        }.items():
            if name not in cols:
                con.execute(ddl)
        con.commit()
    finally:
        con.close()


def _resolve_date(value: str) -> str:
    today = date.today()
    v = (value or "today").lower().strip()
    if v == "today":
        return today.isoformat()
    if v == "yesterday":
        return (today - timedelta(days=1)).isoformat()
    if v in ("last week", "lastweek"):
        return (today - timedelta(days=7)).isoformat()
    if v in ("last month", "lastmonth"):
        d = today.replace(day=1)
        if d.month == 1:
            d = d.replace(year=d.year - 1, month=12)
        else:
            d = d.replace(month=d.month - 1)
        return d.isoformat()
    return value


def _insert_tx(con, tx: dict) -> int:
    """Insert one transaction row on an existing connection (no commit)."""
    tx = dict(tx)
    tx["date"] = _resolve_date(tx.get("date", "today"))
    amount = float(tx["amount"])
    cash_flow = tx.get("cash_flow")
    if cash_flow is None:
        cash_flow = amount if tx["type"] == "income" else -amount
    cur = con.execute(
        """
        INSERT INTO transactions(
            type, amount, category, description, date, time, confidence,
            kind, person, loan_account_id, shared_group_id, cash_flow
        )
        VALUES (
            :type, :amount, :category, :description, :date, :time, :confidence,
            :kind, :person, :loan_account_id, :shared_group_id, :cash_flow
        )
        """,
        {
            "type": tx["type"],
            "amount": amount,
            "category": tx.get("category", "Other"),
            "description": tx.get("description", ""),
            "date": tx["date"],
            "time": tx.get("time"),
            "confidence": tx.get("confidence"),
            "kind": tx.get("kind", "standard"),
            "person": tx.get("person"),
            "loan_account_id": tx.get("loan_account_id"),
            "shared_group_id": tx.get("shared_group_id"),
            "cash_flow": cash_flow,
        },
    )
    return cur.lastrowid


def insert_transaction(tx: dict, path=None) -> int:
    con = _connect(path)
    try:
        tx_id = _insert_tx(con, tx)
        con.commit()
        return tx_id
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def get_transactions(
    limit: int = 100,
    offset: int = 0,
    category: str = None,
    tx_type: str = None,
    date_from: str = None,
    date_to: str = None,
    path=None,
) -> list:
    clauses = []
    params = []
    if category:
        clauses.append("category = ?")
        params.append(category)
    if tx_type:
        clauses.append("type = ?")
        params.append(tx_type)
    if date_from:
        clauses.append("date >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("date <= ?")
        params.append(date_to)

    sql = "SELECT * FROM transactions"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY date DESC, id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    con = _connect(path)
    try:
        return [dict(r) for r in con.execute(sql, params).fetchall()]
    finally:
        con.close()


def delete_transaction(tx_id: int, path=None) -> None:
    con = _connect(path)
    try:
        con.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))
        con.commit()
    finally:
        con.close()


def _get_or_create_loan_account(con, person: str, loan_type: str, activity_date: str, notes: str = "") -> int:
    person = person.strip().title()
    row = con.execute(
        "SELECT id, loan_type FROM loan_accounts WHERE person_name = ? COLLATE NOCASE",
        (person,),
    ).fetchone()
    if row:
        return row["id"]
    cur = con.execute(
        """
        INSERT INTO loan_accounts(person_name, current_balance, loan_type, status, created_date, last_activity, notes)
        VALUES (?, 0, ?, 'Active', ?, ?, ?)
        """,
        (person, loan_type, activity_date, activity_date, notes),
    )
    return cur.lastrowid


_LOAN_DEFINITIONS = {
    # action: (default_loan_type, tx_type, category, cash_flow_sign, signed_delta_sign)
    "loan_given": ("owed_to_me", "expense", "Loan Given", -1, +1),
    "loan_taken": ("i_owe", "income", "Loan Taken", +1, -1),
    "loan_repayment_received": ("owed_to_me", "income", "Loan Repayment", +1, -1),
    "loan_repayment_made": ("i_owe", "expense", "Loan Repayment", -1, +1),
    "shared_payable": ("i_owe", "expense", "Shared Payable", 0, -1),
}


def _record_loan_movement(con, action: str, person: str, amount: float,
                          date_value: str = "today", notes: str = "",
                          confidence: str = "high") -> dict:
    """Record a loan movement + its linked transaction on one connection (no commit)."""
    action = action.strip().lower()
    amount = round(float(amount), 2)
    tx_date = _resolve_date(date_value)
    if action not in _LOAN_DEFINITIONS:
        raise ValueError(f"Unsupported loan action: {action}")
    default_loan_type, tx_type, category, cf_sign, delta_sign = _LOAN_DEFINITIONS[action]
    cash_flow = cf_sign * amount
    signed_delta = delta_sign * amount

    account_id = _get_or_create_loan_account(con, person, default_loan_type, tx_date, notes)
    account = con.execute(
        "SELECT current_balance, loan_type FROM loan_accounts WHERE id = ?",
        (account_id,),
    ).fetchone()
    signed_balance = float(account["current_balance"])
    if account["loan_type"] == "i_owe":
        signed_balance *= -1
    signed_balance = round(signed_balance + signed_delta, 2)
    new_balance = abs(signed_balance)
    if signed_balance > 0:
        loan_type = "owed_to_me"
    elif signed_balance < 0:
        loan_type = "i_owe"
    else:
        loan_type = account["loan_type"]
    status = "Paid" if new_balance == 0 else "Active"

    description_map = {
        "loan_given": f"Loan Given -> {person.title()}",
        "loan_taken": f"Loan Taken -> {person.title()}",
        "loan_repayment_received": f"Loan Repayment Received -> {person.title()}",
        "loan_repayment_made": f"Loan Repayment Made -> {person.title()}",
        "shared_payable": f"Shared Payable -> {person.title()}",
    }
    tx_id = _insert_tx(con, {
        "type": tx_type,
        "amount": amount,
        "category": category,
        "description": description_map[action],
        "date": tx_date,
        "time": None,
        "confidence": confidence,
        "kind": action,
        "person": person.title(),
        "loan_account_id": account_id,
        "cash_flow": cash_flow,
    })
    con.execute(
        """
        UPDATE loan_accounts
        SET current_balance = ?, status = ?, last_activity = ?, loan_type = ?
        WHERE id = ?
        """,
        (new_balance, status, tx_date, loan_type, account_id),
    )
    con.execute(
        """
        INSERT INTO loan_transactions(loan_account_id, transaction_id, action, amount, balance_after, date, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (account_id, tx_id, action, amount, new_balance, tx_date, notes),
    )
    return _get_tx(con, tx_id)


def record_loan_movement(
    action: str,
    person: str,
    amount: float,
    date_value: str = "today",
    notes: str = "",
    confidence: str = "high",
    path=None,
) -> dict:
    """Record a loan movement and its linked transaction atomically."""
    con = _connect(path)
    try:
        result = _record_loan_movement(con, action, person, amount, date_value, notes, confidence)
        con.commit()
        return result
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def _get_tx(con, tx_id: int) -> dict:
    row = con.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    return dict(row) if row else {}


def get_transaction(tx_id: int, path=None) -> dict:
    con = _connect(path)
    try:
        return _get_tx(con, tx_id)
    finally:
        con.close()


_TRACE_FIELDS = (
    "request_id", "raw_input", "normalized_input", "route", "parser_output",
    "auditor_output", "final_event", "transaction_ids", "confidence", "model",
    "prompt_version", "latency_ms", "fallback_used", "needs_clarification", "errors",
)


def record_trace(trace: dict, path=None) -> None:
    """Append (or replace by request_id) one EventTrace row.

    Tracing must never break the main flow, so failures here are swallowed after
    a best-effort write — the financial write already succeeded by this point.
    """
    if not trace or not trace.get("request_id"):
        return
    row = {k: trace.get(k) for k in _TRACE_FIELDS}
    for flag in ("fallback_used", "needs_clarification"):
        row[flag] = int(bool(row.get(flag)))
    con = _connect(path)
    try:
        con.execute(
            f"INSERT OR REPLACE INTO event_trace({', '.join(_TRACE_FIELDS)}) "
            f"VALUES ({', '.join(':' + f for f in _TRACE_FIELDS)})",
            row,
        )
        con.commit()
    except Exception:
        con.rollback()
    finally:
        con.close()


def get_traces(limit: int = 100, path=None) -> list[dict]:
    con = _connect(path)
    try:
        rows = con.execute(
            "SELECT * FROM event_trace ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def get_loan_account_by_name(person: str, path=None) -> dict | None:
    """Return a person's loan account (case-insensitive), or None."""
    con = _connect(path)
    try:
        row = con.execute(
            "SELECT * FROM loan_accounts WHERE person_name = ? COLLATE NOCASE",
            (person.strip().title(),),
        ).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def get_loan_accounts(path=None) -> list[dict]:
    con = _connect(path)
    try:
        rows = con.execute(
            "SELECT * FROM loan_accounts ORDER BY status, person_name"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def get_loan_transactions(account_id: int | None = None, path=None) -> list[dict]:
    con = _connect(path)
    try:
        if account_id:
            rows = con.execute(
                "SELECT * FROM loan_transactions WHERE loan_account_id = ? ORDER BY date DESC, id DESC",
                (account_id,),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM loan_transactions ORDER BY date DESC, id DESC"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def get_shared_expense_groups(path=None) -> list[dict]:
    con = _connect(path)
    try:
        rows = con.execute(
            "SELECT * FROM shared_expense_groups ORDER BY date DESC, id DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def get_shared_expense_participants(group_id: int | None = None, path=None) -> list[dict]:
    con = _connect(path)
    try:
        if group_id:
            rows = con.execute(
                "SELECT * FROM shared_expense_participants WHERE group_id = ? ORDER BY person_name",
                (group_id,),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM shared_expense_participants ORDER BY group_id DESC, person_name"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def _record_shared_expense(con, plan: dict) -> list[dict]:
    """Record a shared expense on one connection (no commit). All-or-nothing."""
    tx_date = _resolve_date(plan.get("date", "today"))
    components = plan.get("components", [])
    people = plan.get("people", [])
    payer = (plan.get("payer") or "me").strip().title()
    payer_is_me = payer.lower() == "me"
    description = plan.get("description", "Shared expense")
    confidence = plan.get("confidence", "high")
    total_paid = round(float(plan.get("total_paid", 0)), 2)
    my_share = round(sum(float(c.get("my_share", 0)) for c in components), 2)
    others_share = round(total_paid - my_share, 2) if payer_is_me else 0

    cur = con.execute(
        """
        INSERT INTO shared_expense_groups(description, total_paid, my_share, others_share, date)
        VALUES (?, ?, ?, ?, ?)
        """,
        (description, total_paid, my_share, others_share, tx_date),
    )
    group_id = cur.lastrowid

    inserted: list[dict] = []
    for component in components:
        share = round(float(component.get("my_share", 0)), 2)
        if share > 0:
            tx_id = _insert_tx(con, {
                "type": "expense",
                "amount": share,
                "category": component.get("category", "Other"),
                "description": component.get("description", description),
                "date": tx_date,
                "time": None,
                "confidence": confidence,
                "kind": "shared_expense",
                "shared_group_id": group_id,
                "cash_flow": -share if payer_is_me else 0,
            })
            inserted.append(_get_tx(con, tx_id))

    if not payer_is_me:
        loan_tx = _record_loan_movement(
            con, "shared_payable", payer, my_share, date_value=tx_date,
            notes=f"Shared expense paid by {payer}: {description}", confidence=confidence,
        )
        inserted.append(loan_tx)
        con.execute(
            """
            INSERT INTO shared_expense_participants(group_id, person_name, share_amount, status, loan_account_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (group_id, payer, my_share, "I owe", loan_tx.get("loan_account_id")),
        )
        return inserted

    for person in people:
        share = round(float(person.get("share", 0)), 2)
        if share <= 0:
            continue
        loan_tx = _record_loan_movement(
            con, "loan_given", person["name"], share, date_value=tx_date,
            notes=f"Shared expense: {description}", confidence=confidence,
        )
        inserted.append(loan_tx)
        con.execute(
            """
            INSERT INTO shared_expense_participants(group_id, person_name, share_amount, status, loan_account_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (group_id, person["name"].title(), share,
             "Paid" if person.get("paid_back") else "Open", loan_tx.get("loan_account_id")),
        )
        if person.get("paid_back"):
            inserted.append(_record_loan_movement(
                con, "loan_repayment_received", person["name"], share, date_value=tx_date,
                notes=f"Immediate shared expense repayment: {description}", confidence=confidence,
            ))

    return inserted


def record_shared_expense(plan: dict, path=None) -> list[dict]:
    """Record personal shares as expenses and other shares as loans, atomically."""
    con = _connect(path)
    try:
        result = _record_shared_expense(con, plan)
        con.commit()
        return result
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def apply_finance_plan(plan: dict, path=None) -> list[dict]:
    """Apply a parsed loan/shared-expense plan and return inserted transactions.

    Every plan passes through the Auditor first, which corrects repayment
    direction from the ledger and raises ClarificationNeeded when the correct
    interpretation cannot be determined.
    """
    from voicetrack import auditor

    plan = auditor.audit_finance_plan(plan, path=path)
    intent = plan.get("intent")
    if intent == "loan_clear":
        person = plan["person"].strip().title()
        con = _connect(path)
        try:
            account = con.execute(
                """
                SELECT current_balance, loan_type
                FROM loan_accounts
                WHERE person_name = ? COLLATE NOCASE
                """,
                (person,),
            ).fetchone()
        finally:
            con.close()
        if not account or float(account["current_balance"]) <= 0:
            raise ValueError(f"No active loan balance found for {person}")
        action = (
            "loan_repayment_received"
            if account["loan_type"] == "owed_to_me"
            else "loan_repayment_made"
        )
        return [
            record_loan_movement(
                action,
                person,
                float(account["current_balance"]),
                date_value=plan.get("date", "today"),
                notes=plan.get("notes", ""),
                confidence=plan.get("confidence", "high"),
                path=path,
            )
        ]

    if intent in {
        "loan_given",
        "loan_taken",
        "loan_repayment_received",
        "loan_repayment_made",
    }:
        return [
            record_loan_movement(
                intent,
                plan["person"],
                plan["amount"],
                date_value=plan.get("date", "today"),
                notes=plan.get("notes", ""),
                confidence=plan.get("confidence", "high"),
                path=path,
            )
        ]
    if intent == "shared_expense":
        return record_shared_expense(plan, path=path)
    raise ValueError(f"Unsupported finance plan: {intent}")


def settle_loan_account(account_id: int, date_value: str = "today",
                        notes: str = "Marked as paid", path=None) -> dict:
    """Settle a loan's full outstanding balance and mark it Paid.

    This records the matching repayment (so cash and receivables/payables update
    correctly) rather than just flipping a status flag. Net worth stays consistent.
    """
    con = _connect(path)
    try:
        account = con.execute(
            "SELECT person_name, current_balance, loan_type FROM loan_accounts WHERE id = ?",
            (account_id,),
        ).fetchone()
    finally:
        con.close()
    if not account:
        raise ValueError("Loan account not found")

    balance = round(float(account["current_balance"]), 2)
    if balance <= 0:
        con = _connect(path)
        try:
            con.execute("UPDATE loan_accounts SET status = 'Paid' WHERE id = ?", (account_id,))
            con.commit()
        finally:
            con.close()
        return {}

    action = (
        "loan_repayment_received"
        if account["loan_type"] == "owed_to_me"
        else "loan_repayment_made"
    )
    return record_loan_movement(
        action, account["person_name"], balance,
        date_value=date_value, notes=notes, path=path,
    )


def delete_loan_account(account_id: int, path=None) -> None:
    """Remove a fully-settled loan account record.

    Refuses to delete a loan that still has an outstanding balance, so receivables
    and payables can never be silently dropped. The underlying cash-history
    transactions are kept (their loan link is just nulled).
    """
    con = _connect(path)
    try:
        account = con.execute(
            "SELECT current_balance FROM loan_accounts WHERE id = ?", (account_id,)
        ).fetchone()
        if not account:
            raise ValueError("Loan account not found")
        if round(float(account["current_balance"]), 2) != 0:
            raise ValueError(
                "Cannot remove a loan with an outstanding balance. Mark it as paid first."
            )
        con.execute("DELETE FROM loan_transactions WHERE loan_account_id = ?", (account_id,))
        con.execute("UPDATE transactions SET loan_account_id = NULL WHERE loan_account_id = ?", (account_id,))
        con.execute("DELETE FROM loan_accounts WHERE id = ?", (account_id,))
        con.commit()
    finally:
        con.close()


def settle_shared_group(group_id: int, date_value: str = "today", path=None) -> list[dict]:
    """Settle every participant share in a shared expense and mark the group Paid."""
    con = _connect(path)
    try:
        participants = [
            dict(r) for r in con.execute(
                "SELECT * FROM shared_expense_participants WHERE group_id = ?", (group_id,)
            ).fetchall()
        ]
    finally:
        con.close()
    if not participants:
        raise ValueError("Shared expense not found")

    settled: list[dict] = []
    for participant in participants:
        if participant["status"] != "Paid" and participant.get("loan_account_id"):
            tx = settle_loan_account(
                participant["loan_account_id"],
                date_value=date_value,
                notes="Shared expense settled",
                path=path,
            )
            if tx:
                settled.append(tx)

    con = _connect(path)
    try:
        con.execute(
            "UPDATE shared_expense_participants SET status = 'Paid' WHERE group_id = ?",
            (group_id,),
        )
        con.commit()
    finally:
        con.close()
    return settled


def shared_group_outstanding(group_id: int, path=None) -> float:
    """Return the total still-open balance across a shared group's participants."""
    con = _connect(path)
    try:
        rows = con.execute(
            """
            SELECT la.current_balance AS balance, p.status AS status
            FROM shared_expense_participants p
            LEFT JOIN loan_accounts la ON la.id = p.loan_account_id
            WHERE p.group_id = ?
            """,
            (group_id,),
        ).fetchall()
    finally:
        con.close()
    return round(sum(
        float(r["balance"]) for r in rows
        if r["balance"] is not None and r["status"] != "Paid"
    ), 2)


def delete_shared_group(group_id: int, path=None) -> None:
    """Remove a fully-settled shared expense group record.

    Refuses while any participant share is still outstanding. The user's own
    expense transactions for the group are kept (their group link is nulled).
    """
    if shared_group_outstanding(group_id, path=path) != 0:
        raise ValueError("Settle the shared expense before removing it.")
    con = _connect(path)
    try:
        if not con.execute(
            "SELECT 1 FROM shared_expense_groups WHERE id = ?", (group_id,)
        ).fetchone():
            raise ValueError("Shared expense not found")
        con.execute("DELETE FROM shared_expense_participants WHERE group_id = ?", (group_id,))
        con.execute("UPDATE transactions SET shared_group_id = NULL WHERE shared_group_id = ?", (group_id,))
        con.execute("DELETE FROM shared_expense_groups WHERE id = ?", (group_id,))
        con.commit()
    finally:
        con.close()


def get_summary(path=None) -> dict:
    today = date.today()
    month_start = today.replace(day=1).isoformat()
    con = _connect(path)
    try:
        rows = con.execute("SELECT type, amount, kind FROM transactions").fetchall()
        total_income = sum(
            r["amount"] for r in rows
            if r["type"] == "income" and r["kind"] in (None, "standard")
        )
        total_expense = sum(
            r["amount"] for r in rows
            if r["type"] == "expense" and r["kind"] in (None, "standard", "shared_expense")
        )
        month_rows = con.execute(
            """
            SELECT amount FROM transactions
            WHERE type='expense' AND date >= ? AND (kind IS NULL OR kind IN ('standard','shared_expense'))
            """,
            (month_start,),
        ).fetchall()
        this_month_expense = sum(r["amount"] for r in month_rows)
        return {
            "total_income": total_income,
            "total_expense": total_expense,
            "balance": total_income - total_expense,
            "this_month_expense": this_month_expense,
        }
    finally:
        con.close()


def get_finance_summary(path=None) -> dict:
    """Return cash, personal expense, loan, and net-worth metrics."""
    con = _connect(path)
    try:
        rows = con.execute("SELECT type, amount, kind, cash_flow FROM transactions").fetchall()
        accounts = con.execute("SELECT loan_type, current_balance FROM loan_accounts").fetchall()
        cash = sum(
            float(r["cash_flow"]) if r["cash_flow"] is not None
            else (float(r["amount"]) if r["type"] == "income" else -float(r["amount"]))
            for r in rows
        )
        personal_expenses = sum(
            float(r["amount"]) for r in rows
            if r["type"] == "expense" and r["kind"] in (None, "standard", "shared_expense")
        )
        cash_flows = [
            float(r["cash_flow"]) if r["cash_flow"] is not None
            else (float(r["amount"]) if r["type"] == "income" else -float(r["amount"]))
            for r in rows
        ]
        cash_outflow = sum(
            abs(flow) for flow in cash_flows
            if flow < 0
        )
        receivables = sum(float(a["current_balance"]) for a in accounts if a["loan_type"] == "owed_to_me")
        payables = sum(float(a["current_balance"]) for a in accounts if a["loan_type"] == "i_owe")
        loans_given = sum(float(r["amount"]) for r in rows if r["kind"] == "loan_given")
        loans_taken = sum(float(r["amount"]) for r in rows if r["kind"] == "loan_taken")
        loans_repaid = sum(float(r["amount"]) for r in rows if r["kind"] == "loan_repayment_received")
        return {
            "cash": cash,
            "cash_outflow": cash_outflow,
            "personal_expenses": personal_expenses,
            "outstanding_receivables": receivables,
            "outstanding_payables": payables,
            "loans_given": loans_given,
            "loans_taken": loans_taken,
            "loans_repaid": loans_repaid,
            "net_cash": cash,
            "net_worth": cash + receivables - payables,
        }
    finally:
        con.close()


def get_category_totals(tx_type: str = "expense", path=None) -> list:
    con = _connect(path)
    try:
        rows = con.execute(
            """
            SELECT category, SUM(amount) as total
            FROM transactions
            WHERE type = ? AND (kind IS NULL OR kind IN ('standard','shared_expense'))
            GROUP BY category
            ORDER BY total DESC
            """,
            (tx_type,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def get_monthly_totals(months: int = 6, path=None) -> list:
    today = date.today()
    result = []
    cursor = today.replace(day=1)
    for _ in range(months):
        month_str = cursor.strftime("%Y-%m")
        con = _connect(path)
        try:
            rows = con.execute(
                """
                SELECT type, amount, kind FROM transactions
                WHERE date LIKE ? AND (kind IS NULL OR kind IN ('standard','shared_expense'))
                """,
                (f"{month_str}%",),
            ).fetchall()
        finally:
            con.close()
        income = sum(r["amount"] for r in rows if r["type"] == "income")
        expense = sum(r["amount"] for r in rows if r["type"] == "expense")
        result.append({"month": month_str, "income": income, "expense": expense})
        if cursor.month == 1:
            cursor = cursor.replace(year=cursor.year - 1, month=12)
        else:
            cursor = cursor.replace(month=cursor.month - 1)
    result.reverse()
    return result
