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


def insert_transaction(tx: dict, path=None) -> int:
    tx = dict(tx)
    tx["date"] = _resolve_date(tx.get("date", "today"))
    amount = float(tx["amount"])
    cash_flow = tx.get("cash_flow")
    if cash_flow is None:
        cash_flow = amount if tx["type"] == "income" else -amount
    con = _connect(path)
    try:
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
        con.commit()
        return cur.lastrowid
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


def record_loan_movement(
    action: str,
    person: str,
    amount: float,
    date_value: str = "today",
    notes: str = "",
    confidence: str = "high",
    path=None,
) -> dict:
    """Record a loan movement and its linked normal transaction."""
    action = action.strip().lower()
    amount = round(float(amount), 2)
    tx_date = _resolve_date(date_value)
    definitions = {
        "loan_given": ("owed_to_me", "expense", "Loan Given", -amount, amount),
        "loan_taken": ("i_owe", "income", "Loan Taken", amount, -amount),
        "loan_repayment_received": ("owed_to_me", "income", "Loan Repayment", amount, -amount),
        "loan_repayment_made": ("i_owe", "expense", "Loan Repayment", -amount, amount),
    }
    if action not in definitions:
        raise ValueError(f"Unsupported loan action: {action}")
    default_loan_type, tx_type, category, cash_flow, signed_delta = definitions[action]

    con = _connect(path)
    try:
        account_id = _get_or_create_loan_account(con, person, default_loan_type, tx_date, notes)
        con.commit()
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
        }
        tx_id = insert_transaction(
            {
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
            },
            path=path,
        )

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
        con.commit()
        return get_transaction(tx_id, path=path)
    finally:
        con.close()


def get_transaction(tx_id: int, path=None) -> dict:
    con = _connect(path)
    try:
        row = con.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
        return dict(row) if row else {}
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


def record_shared_expense(plan: dict, path=None) -> list[dict]:
    """Record personal shares as expenses and other shares as receivable loans."""
    tx_date = _resolve_date(plan.get("date", "today"))
    components = plan.get("components", [])
    people = plan.get("people", [])
    description = plan.get("description", "Shared expense")
    total_paid = round(float(plan.get("total_paid", 0)), 2)
    my_share = round(sum(float(c.get("my_share", 0)) for c in components), 2)
    others_share = round(total_paid - my_share, 2)

    con = _connect(path)
    try:
        cur = con.execute(
            """
            INSERT INTO shared_expense_groups(description, total_paid, my_share, others_share, date)
            VALUES (?, ?, ?, ?, ?)
            """,
            (description, total_paid, my_share, others_share, tx_date),
        )
        group_id = cur.lastrowid
        con.commit()
    finally:
        con.close()

    inserted: list[dict] = []
    for component in components:
        if float(component.get("my_share", 0)) > 0:
            tx_id = insert_transaction(
                {
                    "type": "expense",
                    "amount": round(float(component["my_share"]), 2),
                    "category": component.get("category", "Other"),
                    "description": component.get("description", description),
                    "date": tx_date,
                    "time": None,
                    "confidence": plan.get("confidence", "high"),
                    "kind": "shared_expense",
                    "shared_group_id": group_id,
                    "cash_flow": -round(float(component["my_share"]), 2),
                },
                path=path,
            )
            inserted.append(get_transaction(tx_id, path=path))

    for person in people:
        share = round(float(person.get("share", 0)), 2)
        if share <= 0:
            continue
        loan_tx = record_loan_movement(
            "loan_given",
            person["name"],
            share,
            date_value=tx_date,
            notes=f"Shared expense: {description}",
            confidence=plan.get("confidence", "high"),
            path=path,
        )
        inserted.append(loan_tx)
        account_id = loan_tx.get("loan_account_id")
        con = _connect(path)
        try:
            con.execute(
                """
                INSERT INTO shared_expense_participants(group_id, person_name, share_amount, status, loan_account_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (group_id, person["name"].title(), share, "Paid" if person.get("paid_back") else "Open", account_id),
            )
            con.commit()
        finally:
            con.close()

        if person.get("paid_back"):
            repay_tx = record_loan_movement(
                "loan_repayment_received",
                person["name"],
                share,
                date_value=tx_date,
                notes=f"Immediate shared expense repayment: {description}",
                confidence=plan.get("confidence", "high"),
                path=path,
            )
            inserted.append(repay_tx)

    return inserted


def apply_finance_plan(plan: dict, path=None) -> list[dict]:
    """Apply a parsed loan/shared-expense plan and return inserted transactions."""
    intent = plan.get("intent")
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
        cash_outflow = sum(
            abs(float(r["cash_flow"])) for r in rows
            if r["cash_flow"] is not None and float(r["cash_flow"]) < 0
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
