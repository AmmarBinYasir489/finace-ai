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
    "background": "#343B40",
    "surface": "#1F2528",
    "panel": "#202629",
    "card": "#252B2F",
    "border": "#394149",
    "text": "#F7F9FC",
    "muted": "#B8C0CA",
    "input": "#1B2024",
    "row": "#22282C",
    "header": "#343B40",
    "nav_hover": "#2C3338",
    "danger_hover": "#3A1A22",
    "blue": "#4895FF",
    "cyan": "#4CC9F0",
    "yellow": "#FFD166",
    "purple": "#8B5CF6",
    "teal": "#4ECDC4",
    "pink": "#F75C8A",
    "green": "#22C55E",
    "red": "#FF3B45",
}

LIGHT_THEME = {
    "background": "#E9ECEF",
    "surface": "#F8F9FA",
    "panel": "#F8F9FA",
    "card": "#FFFFFF",
    "border": "#CFD6DE",
    "text": "#1F2933",
    "muted": "#65717D",
    "input": "#FFFFFF",
    "row": "#FFFFFF",
    "header": "#E9ECEF",
    "nav_hover": "#E6ECFF",
    "danger_hover": "#FEE2E2",
    "blue": "#4361EE",
    "cyan": "#4CC9F0",
    "yellow": "#FFD166",
    "purple": "#8B5CF6",
    "teal": "#4ECDC4",
    "pink": "#F75C8A",
    "green": "#16A34A",
    "red": "#FF3B45",
}

THEME = DARK_THEME.copy()
