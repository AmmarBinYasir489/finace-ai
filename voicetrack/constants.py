"""Shared constants used by the database, LLM prompt, and user interface."""

CATEGORIES = [
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
]

TRANSACTION_TYPES = ["expense", "income"]

DARK_THEME = {
    "background": "#0F1117",
    "surface": "#1A1D27",
    "border": "#2D3148",
    "text": "#F1F5F9",
    "muted": "#94A3B8",
    "input": "#111622",
    "row": "#131722",
    "nav_hover": "#202636",
    "danger_hover": "#3A1A22",
    "blue": "#3B82F6",
    "green": "#22C55E",
    "red": "#EF4444",
}

LIGHT_THEME = {
    "background": "#F6F8FB",
    "surface": "#FFFFFF",
    "border": "#D9E1EE",
    "text": "#0F172A",
    "muted": "#64748B",
    "input": "#F8FAFC",
    "row": "#F1F5F9",
    "nav_hover": "#E8EEF8",
    "danger_hover": "#FEE2E2",
    "blue": "#2563EB",
    "green": "#16A34A",
    "red": "#DC2626",
}

THEME = DARK_THEME.copy()
