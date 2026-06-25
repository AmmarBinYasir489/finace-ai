"""SQLite data access layer for VoiceTrack."""

from __future__ import annotations

import csv
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

from .constants import CATEGORIES


@dataclass(frozen=True)
class DateRange:
    """Inclusive date range used by SQL filters."""

    start: str | None
    end: str | None


def period_to_range(period: str, today: date | None = None) -> DateRange:
    """Convert a dashboard period into an inclusive SQL date range."""
    today = today or date.today()
    if period == "today":
        value = today.isoformat()
        return DateRange(value, value)
    if period == "week":
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
        return DateRange(start.isoformat(), end.isoformat())
    if period == "month":
        start = today.replace(day=1)
        if start.month == 12:
            next_month = start.replace(year=start.year + 1, month=1)
        else:
            next_month = start.replace(month=start.month + 1)
        end = next_month - timedelta(days=1)
        return DateRange(start.isoformat(), end.isoformat())
    return DateRange(None, None)


class Database:
    """Small wrapper around sqlite3 so the rest of the app stays readable."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    @contextmanager
    def connect(self):
        """Open a connection that returns rows as dictionaries."""
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def init_schema(self) -> None:
        """Create tables and seed the default categories on first run."""
        with self.connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL,
                    amount REAL NOT NULL,
                    category TEXT NOT NULL,
                    description TEXT,
                    date TEXT NOT NULL,
                    time TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL
                )
                """
            )
            con.executemany(
                "INSERT OR IGNORE INTO categories(name) VALUES (?)",
                [(name,) for name in CATEGORIES],
            )

    def add_transaction(self, item: dict) -> int:
        """Insert one validated transaction and return its row id."""
        with self.connect() as con:
            cur = con.execute(
                """
                INSERT INTO transactions(type, amount, category, description, date, time)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    item["type"],
                    float(item["amount"]),
                    item["category"],
                    item.get("description", ""),
                    item["date"],
                    item.get("time"),
                ),
            )
            return int(cur.lastrowid)

    def delete_transaction(self, transaction_id: int) -> None:
        """Delete a transaction by id."""
        with self.connect() as con:
            con.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))

    def list_transactions(
        self,
        *,
        date_range: DateRange | None = None,
        search: str = "",
        tx_type: str = "all",
        category: str = "All",
        limit: int | None = None,
    ) -> list[dict]:
        """Return transactions newest first using only SQL/Python filtering."""
        clauses: list[str] = []
        params: list[object] = []
        if date_range and date_range.start:
            clauses.append("date >= ?")
            params.append(date_range.start)
        if date_range and date_range.end:
            clauses.append("date <= ?")
            params.append(date_range.end)
        if search:
            clauses.append("(description LIKE ? OR category LIKE ?)")
            needle = f"%{search}%"
            params.extend([needle, needle])
        if tx_type in {"income", "expense"}:
            clauses.append("type = ?")
            params.append(tx_type)
        if category != "All":
            clauses.append("category = ?")
            params.append(category)

        sql = "SELECT * FROM transactions"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY date DESC, COALESCE(time, '') DESC, id DESC"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)

        with self.connect() as con:
            return [dict(row) for row in con.execute(sql, params).fetchall()]

    def totals(self, date_range: DateRange | None = None) -> dict:
        """Calculate income, expense, and net balance in Python."""
        rows = self.list_transactions(date_range=date_range)
        income = sum(float(row["amount"]) for row in rows if row["type"] == "income")
        expense = sum(float(row["amount"]) for row in rows if row["type"] == "expense")
        return {"income": income, "expense": expense, "net": income - expense}

    def category_breakdown(self, date_range: DateRange | None = None) -> list[dict]:
        """Group expenses by category for dashboard and report charts."""
        rows = self.list_transactions(date_range=date_range, tx_type="expense")
        totals: dict[str, float] = {}
        for row in rows:
            totals[row["category"]] = totals.get(row["category"], 0.0) + float(row["amount"])
        return [
            {"category": name, "amount": amount}
            for name, amount in sorted(totals.items(), key=lambda item: item[1], reverse=True)
        ]

    def monthly_income_expense(self, months: int = 6, today: date | None = None) -> list[dict]:
        """Return grouped monthly income and expense totals for reports."""
        today = today or date.today()
        month_starts: list[date] = []
        cursor = today.replace(day=1)
        for _ in range(months):
            month_starts.append(cursor)
            if cursor.month == 1:
                cursor = cursor.replace(year=cursor.year - 1, month=12)
            else:
                cursor = cursor.replace(month=cursor.month - 1)
        month_starts.reverse()

        result: list[dict] = []
        for start in month_starts:
            if start.month == 12:
                next_month = start.replace(year=start.year + 1, month=1)
            else:
                next_month = start.replace(month=start.month + 1)
            end = next_month - timedelta(days=1)
            totals = self.totals(DateRange(start.isoformat(), end.isoformat()))
            result.append({"label": start.strftime("%b"), **totals})
        return result

    def export_csv(self, output_path: Path, rows: Iterable[dict] | None = None) -> Path:
        """Write transactions to CSV for offline reports."""
        rows = list(rows) if rows is not None else self.list_transactions()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["id", "type", "amount", "category", "description", "date", "time", "created_at"],
            )
            writer.writeheader()
            writer.writerows(rows)
        return output_path
