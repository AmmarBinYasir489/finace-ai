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
                created_at  TEXT DEFAULT (datetime('now','localtime'))
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
        # migrate: add confidence column if missing
        cols = [r[1] for r in con.execute("PRAGMA table_info(transactions)").fetchall()]
        if "confidence" not in cols:
            con.execute("ALTER TABLE transactions ADD COLUMN confidence TEXT")
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
    con = _connect(path)
    try:
        cur = con.execute(
            """
            INSERT INTO transactions(type, amount, category, description, date, time, confidence)
            VALUES (:type, :amount, :category, :description, :date, :time, :confidence)
            """,
            {
                "type": tx["type"],
                "amount": float(tx["amount"]),
                "category": tx.get("category", "Other"),
                "description": tx.get("description", ""),
                "date": tx["date"],
                "time": tx.get("time"),
                "confidence": tx.get("confidence"),
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


def get_summary(path=None) -> dict:
    today = date.today()
    month_start = today.replace(day=1).isoformat()
    con = _connect(path)
    try:
        rows = con.execute("SELECT type, amount FROM transactions").fetchall()
        total_income = sum(r["amount"] for r in rows if r["type"] == "income")
        total_expense = sum(r["amount"] for r in rows if r["type"] == "expense")
        month_rows = con.execute(
            "SELECT amount FROM transactions WHERE type='expense' AND date >= ?",
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


def get_category_totals(tx_type: str = "expense", path=None) -> list:
    con = _connect(path)
    try:
        rows = con.execute(
            """
            SELECT category, SUM(amount) as total
            FROM transactions
            WHERE type = ?
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
                "SELECT type, amount FROM transactions WHERE date LIKE ?",
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
