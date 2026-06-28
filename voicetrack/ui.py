"""CustomTkinter interface for VoiceTrack."""

from __future__ import annotations

import os
import threading
import tkinter as tk
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

_mpl_cache = Path.cwd() / ".voicetrack_cache" / "matplotlib"
_mpl_cache.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(_mpl_cache))

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from .config import load_settings
from .constants import CATEGORIES, DARK_THEME, LIGHT_THEME, THEME
from .db import Database, DateRange, period_to_range
from .extractors import OllamaExtractor
from .pipeline import ExtractionError, TransactionPipeline
from .speech import listen_until_stopped, missing_voice_dependencies


def money(value: float) -> str:
    """Format money without forcing a currency symbol."""
    return f"{value:,.0f}"


def format_created_at(value: str | None) -> str:
    """Format SQLite/Python created_at values for row details."""
    if not value:
        return "unknown"
    raw = value.replace("T", " ").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(raw[:19], fmt).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            continue
    return raw[:16]


def category_label(name: str) -> str:
    """Short display name for the app's fixed category set."""
    aliases = {
        "Food & Groceries": "Food",
        "Transport": "Transportation",
        "Utilities": "Utilities",
        "Health": "Health",
        "Education": "Education",
        "Shopping": "Shopping",
        "Entertainment": "Entertainment",
        "Rent": "Housing",
        "Salary": "Salary",
        "Freelance": "Freelance",
        "Other": "Other",
    }
    return aliases.get(name, name)


class VoiceTrackApp(ctk.CTk):
    """Main desktop window with dashboard, entry, history, and reports."""

    def __init__(self, database: Database, pipeline: TransactionPipeline, vosk_model_path: Path | None = None):
        super().__init__()
        self.database = database
        self.pipeline = pipeline
        self.vosk_model_path = vosk_model_path or Path("models/vosk-model-small-en-us-0.15")
        self.pipeline.on_saved = self._on_saved
        self.current_screen = "dashboard"
        self.current_period = "month"
        self.preview_item: dict | None = None
        self.field_vars: dict[str, tk.StringVar] = {}
        self.theme_mode = "dark"
        self.theme_switch_var = tk.BooleanVar(value=False)
        self.processing_entry = False
        self.recording_voice = False
        self.recording_stop_event: threading.Event | None = None
        self.mic_button: ctk.CTkButton | None = None

        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")
        self.title("VoiceTrack")
        self.geometry("1280x780")
        self.minsize(1100, 680)
        self.configure(fg_color=THEME["background"])

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.sidebar: ctk.CTkFrame | None = None
        self.logo: ctk.CTkLabel | None = None
        self.db_label: ctk.CTkLabel | None = None
        self._build_sidebar()
        self.main = ctk.CTkFrame(self, fg_color=THEME["background"], corner_radius=0)
        self.main.grid(row=0, column=1, sticky="nsew")
        self.show_dashboard()

    def _build_sidebar(self) -> None:
        """Build the left navigation column from the HTML reference."""
        sidebar = ctk.CTkFrame(self, width=300, fg_color=THEME["background"], corner_radius=0)
        self.sidebar = sidebar
        sidebar.grid(row=0, column=0, sticky="ns")
        sidebar.grid_propagate(False)

        panel = ctk.CTkFrame(sidebar, fg_color=THEME["surface"], corner_radius=8)
        panel.pack(fill="both", expand=True, padx=(24, 18), pady=(96, 24))

        avatar = ctk.CTkLabel(
            panel,
            text="VT",
            width=78,
            height=78,
            fg_color=THEME["blue"],
            corner_radius=39,
            text_color="#FFFFFF",
            font=("Segoe UI", 24, "bold"),
        )
        self.logo = avatar
        avatar.pack(pady=(24, 12))
        ctk.CTkLabel(panel, text="Welcome!", font=("Segoe UI", 22, "bold"), text_color=THEME["text"]).pack(pady=(0, 26))

        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        for key, label, command in [
            ("dashboard", "Dashboard", self.show_dashboard),
            ("add_entry", "Add Entry", self.show_add_entry),
            ("history", "Transactions", self.show_history),
            ("reports", "Reports", self.show_reports),
        ]:
            button = ctk.CTkButton(
                panel,
                text=label,
                anchor="w",
                height=48,
                corner_radius=8,
                fg_color="transparent",
                hover_color=THEME["nav_hover"],
                text_color=THEME["muted"],
                font=("Segoe UI", 16, "bold"),
                command=command,
            )
            button.pack(fill="x", padx=22, pady=5)
            self.nav_buttons[key] = button

        ctk.CTkButton(
            panel,
            text="Export Data",
            height=36,
            fg_color="transparent",
            border_width=1,
            border_color=THEME["border"],
            hover_color=THEME["nav_hover"],
            text_color=THEME["text"],
            command=self._export_csv,
        ).pack(side="bottom", fill="x", padx=22, pady=(8, 12))

        self.db_label = ctk.CTkLabel(
            panel,
            text=f"Database\n{self.database.path}",
            text_color=THEME["muted"],
            font=("Segoe UI", 11),
            justify="left",
            wraplength=210,
        )
        self.db_label.pack(side="bottom", padx=22, pady=(8, 0), fill="x")

    def _set_active_nav(self, key: str) -> None:
        """Highlight the active navigation item."""
        self.current_screen = key
        for name, button in self.nav_buttons.items():
            active = name == key
            button.configure(
                fg_color=THEME["blue"] if active else "transparent",
                text_color="#FFFFFF" if active else THEME["muted"],
                hover_color=THEME["nav_hover"],
            )

    def _toggle_theme(self) -> None:
        """Switch between dark and light mode and redraw the current screen."""
        self.theme_mode = "light" if self.theme_switch_var.get() else "dark"
        THEME.clear()
        THEME.update(LIGHT_THEME if self.theme_mode == "light" else DARK_THEME)
        ctk.set_appearance_mode("Light" if self.theme_mode == "light" else "Dark")
        self.configure(fg_color=THEME["background"])
        if self.sidebar:
            self.sidebar.destroy()
        self._build_sidebar()
        self.main.configure(fg_color=THEME["background"])
        self._redraw_current_screen()

    def _redraw_current_screen(self) -> None:
        """Open the same page again after a theme change or refresh."""
        if self.current_screen == "add_entry":
            self.show_add_entry()
        elif self.current_screen == "history":
            self.show_history()
        elif self.current_screen == "reports":
            self.show_reports()
        else:
            self.show_dashboard()

    def _clear_main(self) -> None:
        """Remove widgets before drawing the next screen."""
        self._stop_recording()
        for child in self.main.winfo_children():
            child.destroy()
        self.main.grid_columnconfigure(0, weight=1)
        self.main.grid_rowconfigure(0, weight=1)

    def _screen_header(self, parent: ctk.CTkFrame, title: str) -> None:
        """Draw a reusable page header."""
        header = ctk.CTkFrame(parent, fg_color="transparent")
        header.pack(fill="x", pady=(0, 26))
        brand = ctk.CTkFrame(header, fg_color="transparent")
        brand.pack(side="left")
        brand_row = ctk.CTkFrame(brand, fg_color="transparent")
        brand_row.pack(anchor="w")
        ctk.CTkLabel(
            brand_row,
            text="VT",
            width=34,
            height=28,
            fg_color=THEME["blue"],
            corner_radius=6,
            text_color="#FFFFFF",
            font=("Segoe UI", 12, "bold"),
        ).pack(side="left", padx=(0, 10))
        ctk.CTkLabel(
            brand_row,
            text="Income and Expense Tracker",
            font=("Segoe UI", 30, "bold"),
            text_color=THEME["blue"],
        ).pack(side="left")
        ctk.CTkLabel(
            brand,
            text=f"Take control of your finances  /  {title}",
            text_color=THEME["muted"],
            font=("Segoe UI", 14),
        ).pack(anchor="w", pady=(4, 0))
        ctk.CTkSwitch(
            header,
            text="Light mode",
            variable=self.theme_switch_var,
            command=self._toggle_theme,
            progress_color=THEME["blue"],
            button_color=THEME["surface"],
            text_color=THEME["muted"],
        ).pack(side="right", padx=(14, 0))
        ctk.CTkLabel(
            header,
            text=date.today().strftime("%A, %d %B %Y"),
            text_color=THEME["muted"],
            font=("Segoe UI", 13),
        ).pack(side="right")

    def show_dashboard(self) -> None:
        """Render dashboard metrics, filter buttons, chart, and recent rows."""
        self._set_active_nav("dashboard")
        self._clear_main()
        page = ctk.CTkFrame(self.main, fg_color=THEME["background"], corner_radius=0)
        page.pack(fill="both", expand=True, padx=(8, 28), pady=24)
        self._screen_header(page, "Dashboard")

        month_range = period_to_range("month")
        month_totals = self.database.totals(month_range)
        all_totals = self.database.totals()
        savings_rate = (month_totals["net"] / month_totals["income"] * 100) if month_totals["income"] else 0.0

        cards = ctk.CTkFrame(page, fg_color="transparent")
        cards.pack(fill="x", pady=(0, 18))
        cards.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="metric")
        self._metric_card(cards, 0, "Total Balance", all_totals["net"], THEME["text"])
        self._metric_card(cards, 1, "Monthly Income", month_totals["income"], THEME["cyan"])
        self._metric_card(cards, 2, "Monthly Expenses", month_totals["expense"], THEME["red"])
        self._metric_card(cards, 3, "Savings Rate", savings_rate, THEME["text"], suffix="%")

        filters = ctk.CTkFrame(page, fg_color="transparent")
        filters.pack(fill="x", pady=(0, 16))
        for key, label in [("today", "Today"), ("week", "This Week"), ("month", "This Month"), ("all", "All")]:
            ctk.CTkButton(
                filters,
                text=label,
                width=110,
                height=34,
                corner_radius=8,
                fg_color=THEME["blue"] if self.current_period == key else THEME["surface"],
                border_color=THEME["border"],
                border_width=0 if self.current_period == key else 1,
                command=lambda value=key: self._change_period(value),
            ).pack(side="left", padx=(0, 8))

        body = ctk.CTkFrame(page, fg_color="transparent")
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure((0, 1), weight=1, uniform="dashboard")
        body.grid_rowconfigure(0, weight=1)

        category_card = self._card(body)
        category_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        ctk.CTkLabel(category_card, text="Spending by Category", font=("Segoe UI", 20, "bold"), text_color=THEME["blue"]).pack(
            anchor="w", padx=18, pady=(16, 0)
        )
        self._draw_category_donut(category_card, self.database.category_breakdown(month_range))

        overview_card = self._card(body)
        overview_card.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        ctk.CTkLabel(
            overview_card,
            text=self._dashboard_chart_title(self.current_period),
            font=("Segoe UI", 20, "bold"),
            text_color=THEME["blue"],
        ).pack(
            anchor="w", padx=18, pady=(16, 0)
        )
        self._draw_dashboard_spending_chart(overview_card, self._dashboard_spending_rows(self.current_period))

    def _metric_card(self, parent: ctk.CTkFrame, col: int, title: str, value: float, color: str, suffix: str = "") -> None:
        """Render one dashboard metric."""
        card = self._card(parent)
        card.grid(row=0, column=col, sticky="ew", padx=(0 if col == 0 else 8, 0 if col == 3 else 8))
        ctk.CTkLabel(card, text=title, text_color=THEME["muted"], font=("Segoe UI", 15, "bold")).pack(anchor="w", padx=18, pady=(18, 8))
        display_value = f"{value:.1f}{suffix}" if suffix else money(value)
        ctk.CTkLabel(card, text=display_value, text_color=color, font=("Segoe UI", 24, "bold")).pack(anchor="w", padx=18, pady=(0, 18))

    def _card(self, parent: ctk.CTkFrame) -> ctk.CTkFrame:
        """Create a standard surface card."""
        return ctk.CTkFrame(parent, fg_color=THEME["surface"], border_color=THEME["border"], border_width=1, corner_radius=8)

    def _change_period(self, period: str) -> None:
        """Update dashboard period and redraw."""
        self.current_period = period
        self.show_dashboard()

    def _draw_category_bar(self, parent: ctk.CTkFrame, rows: list[dict]) -> None:
        """Draw expense category totals with matplotlib."""
        fig = Figure(figsize=(6.2, 3.8), dpi=100, facecolor=THEME["surface"])
        ax = fig.add_subplot(111)
        ax.set_facecolor(THEME["surface"])
        if rows:
            labels = [row["category"] for row in rows]
            values = [row["amount"] for row in rows]
            ax.bar(labels, values, color=THEME["blue"])
            ax.tick_params(axis="x", rotation=25, colors=THEME["muted"], labelsize=8)
            ax.tick_params(axis="y", colors=THEME["muted"])
        else:
            ax.text(0.5, 0.5, "No expenses yet", ha="center", va="center", color=THEME["muted"], transform=ax.transAxes)
            ax.set_xticks([])
            ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_color(THEME["border"])
        ax.grid(axis="y", color=THEME["border"], alpha=0.4)
        fig.tight_layout()
        canvas = FigureCanvasTkAgg(fig, master=parent)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True, padx=12, pady=12)

    def _draw_category_donut(self, parent: ctk.CTkFrame, rows: list[dict]) -> None:
        """Draw a reference-style donut chart for category spending."""
        fig = Figure(figsize=(5.6, 4.5), dpi=100, facecolor=THEME["surface"])
        ax = fig.add_subplot(111)
        ax.set_facecolor(THEME["surface"])
        if rows:
            values = [row["amount"] for row in rows]
            labels = [f"{category_label(row['category'])}  {money(float(row['amount']))}" for row in rows]
            colors = [THEME["pink"], THEME["cyan"], THEME["yellow"], THEME["teal"], THEME["purple"], THEME["blue"]]
            slice_colors = [colors[index % len(colors)] for index in range(len(values))]
            wedges, _ = ax.pie(
                values,
                labels=None,
                colors=slice_colors,
                startangle=90,
                counterclock=False,
                wedgeprops={"width": 0.42, "edgecolor": THEME["surface"]},
            )
            total = sum(values)
            ax.text(0, 0.04, "Total", ha="center", va="center", color=THEME["muted"], fontsize=10)
            ax.text(0, -0.08, money(total), ha="center", va="center", color=THEME["text"], fontsize=15, fontweight="bold")
            ax.legend(
                wedges,
                labels,
                loc="center left",
                bbox_to_anchor=(0.94, 0.5),
                frameon=False,
                labelcolor=THEME["text"],
                fontsize=8,
                handlelength=1.1,
                handletextpad=0.6,
            )
        else:
            ax.text(0.5, 0.5, "No category spending yet", ha="center", va="center", color=THEME["muted"], transform=ax.transAxes)
        ax.set_aspect("equal")
        fig.subplots_adjust(left=0.04, right=0.74, top=0.94, bottom=0.06)
        canvas = FigureCanvasTkAgg(fig, master=parent)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True, padx=12, pady=12)

    def _dashboard_chart_title(self, period: str) -> str:
        """Return the chart title for the current dashboard filter."""
        if period == "week":
            return "This week vs previous week"
        if period == "month":
            return "This month vs previous month"
        if period == "today":
            return "Today's spending"
        return "Spending by month"

    def _dashboard_spending_rows(self, period: str) -> list[dict]:
        """Build chart rows for the selected dashboard filter."""
        today = date.today()
        if period == "week":
            current = period_to_range("week", today)
            start = date.fromisoformat(current.start)
            end = date.fromisoformat(current.end)
            previous = DateRange((start - timedelta(days=7)).isoformat(), (end - timedelta(days=7)).isoformat())
            return [
                {"label": f"Previous week\n{previous.start[-5:]} to {previous.end[-5:]}", "amount": self.database.totals(previous)["expense"]},
                {"label": f"This week\n{current.start[-5:]} to {current.end[-5:]}", "amount": self.database.totals(current)["expense"]},
            ]

        if period == "month":
            current_start = today.replace(day=1)
            previous_end = current_start - timedelta(days=1)
            previous_start = previous_end.replace(day=1)
            current = period_to_range("month", today)
            previous = DateRange(previous_start.isoformat(), previous_end.isoformat())
            return [
                {"label": f"Previous month\n{previous_start.strftime('%b %Y')}", "amount": self.database.totals(previous)["expense"]},
                {"label": f"This month\n{current_start.strftime('%b %Y')}", "amount": self.database.totals(current)["expense"]},
            ]

        range_ = period_to_range(period)
        rows = self.database.list_transactions(date_range=range_, tx_type="expense")
        totals: dict[str, float] = {}
        for row in rows:
            key = row["date"][:7] if period == "all" else row["date"]
            totals[key] = totals.get(key, 0.0) + float(row["amount"])

        if period == "week" and range_.start and range_.end:
            start = date.fromisoformat(range_.start)
            end = date.fromisoformat(range_.end)
            days = []
            cursor = start
            while cursor <= end:
                key = cursor.isoformat()
                days.append({"label": cursor.strftime("%a %d"), "amount": totals.get(key, 0.0)})
                cursor += timedelta(days=1)
            return days

        if period == "month" and range_.start and range_.end:
            start = date.fromisoformat(range_.start)
            end = date.fromisoformat(range_.end)
            days = []
            cursor = start
            while cursor <= end:
                key = cursor.isoformat()
                days.append({"label": cursor.strftime("%d"), "amount": totals.get(key, 0.0)})
                cursor += timedelta(days=1)
            return days

        if period == "today":
            today_key = date.today().isoformat()
            return [{"label": date.today().strftime("%d %b"), "amount": totals.get(today_key, 0.0)}]

        return [{"label": key, "amount": totals[key]} for key in sorted(totals)]

    def _draw_dashboard_spending_chart(self, parent: ctk.CTkFrame, rows: list[dict]) -> None:
        """Draw dashboard expense bars for the selected period."""
        fig = Figure(figsize=(6.2, 3.8), dpi=100, facecolor=THEME["surface"])
        ax = fig.add_subplot(111)
        ax.set_facecolor(THEME["surface"])
        visible_rows = rows if len(rows) <= 2 else ([row for row in rows if row["amount"] > 0] or rows)
        if visible_rows:
            labels = [row["label"] for row in visible_rows]
            values = [row["amount"] for row in visible_rows]
            colors = [THEME["muted"], THEME["blue"]] if len(labels) == 2 else THEME["blue"]
            ax.bar(labels, values, color=colors)
            ax.tick_params(axis="x", rotation=35, colors=THEME["muted"], labelsize=8)
            ax.tick_params(axis="y", colors=THEME["muted"])
            ax.set_ylabel("Expense", color=THEME["muted"])
        else:
            ax.text(0.5, 0.5, "No expenses in this period", ha="center", va="center", color=THEME["muted"], transform=ax.transAxes)
            ax.set_xticks([])
            ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_color(THEME["border"])
        ax.grid(axis="y", color=THEME["border"], alpha=0.4)
        fig.tight_layout()
        canvas = FigureCanvasTkAgg(fig, master=parent)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True, padx=12, pady=12)

    def _transaction_rows(self, parent: ctk.CTkFrame, rows: list[dict], compact: bool = False) -> None:
        """Render transaction rows used by dashboard and history."""
        if not rows:
            ctk.CTkLabel(parent, text="No transactions yet", text_color=THEME["muted"]).pack(pady=30)
            return
        for row in rows:
            frame = ctk.CTkFrame(parent, fg_color=THEME["row"], corner_radius=8)
            frame.pack(fill="x", pady=4)
            sign = "+" if row["type"] == "income" else "-"
            color = THEME["green"] if row["type"] == "income" else THEME["red"]
            left = ctk.CTkFrame(frame, fg_color="transparent")
            left.pack(side="left", fill="x", expand=True, padx=12, pady=9)
            ctk.CTkLabel(left, text=row["description"], anchor="w", font=("Segoe UI", 13, "bold")).pack(anchor="w")
            transaction_time = f" {row.get('time')}" if row.get("time") else ""
            detail = f"{row['category']}  |  Transaction: {row['date']}{transaction_time}  |  Entered: {format_created_at(row.get('created_at'))}"
            ctk.CTkLabel(left, text=detail, anchor="w", text_color=THEME["muted"], font=("Segoe UI", 11)).pack(anchor="w")
            ctk.CTkLabel(frame, text=f"{sign}{money(float(row['amount']))}", text_color=color, width=90).pack(
                side="right", padx=8
            )
            if not compact:
                ctk.CTkButton(
                    frame,
                    text="Delete",
                    width=70,
                    height=28,
                    fg_color="transparent",
                    hover_color=THEME["danger_hover"],
                    text_color=THEME["red"],
                    command=lambda row_id=row["id"]: self._delete_transaction(row_id),
                ).pack(side="right", padx=(0, 10))

    def show_add_entry(self) -> None:
        """Render natural language entry and editable AI preview."""
        self._set_active_nav("add_entry")
        self._clear_main()
        page = ctk.CTkFrame(self.main, fg_color=THEME["background"], corner_radius=0)
        page.pack(fill="both", expand=True, padx=24, pady=24)
        self._screen_header(page, "Add Entry")

        input_card = self._card(page)
        input_card.pack(fill="x", pady=(0, 14))
        ctk.CTkLabel(input_card, text="Speak or type a transaction", font=("Segoe UI", 15, "bold")).pack(
            anchor="w", padx=18, pady=(16, 8)
        )
        self.input_text = ctk.CTkTextbox(input_card, height=92, fg_color=THEME["input"], border_color=THEME["border"], border_width=1)
        self.input_text.pack(fill="x", padx=18, pady=(0, 12))
        actions = ctk.CTkFrame(input_card, fg_color="transparent")
        actions.pack(fill="x", padx=18, pady=(0, 16))
        self.process_button = ctk.CTkButton(actions, text="Process", width=120, command=self._process_entry)
        self.process_button.pack(side="left")
        self.mic_button = ctk.CTkButton(
            actions,
            text="Microphone",
            width=130,
            fg_color=THEME["blue"],
            command=self._toggle_recording,
        )
        self.mic_button.pack(side="left", padx=8)
        self.status_label = ctk.CTkLabel(actions, text="", text_color=THEME["muted"])
        self.status_label.pack(side="left", padx=12)

        self.preview_card = self._card(page)
        self.preview_card.pack(fill="both", expand=True)
        self._draw_empty_preview()

    def _draw_empty_preview(self) -> None:
        """Show a placeholder before the first extraction."""
        for child in self.preview_card.winfo_children():
            child.destroy()
        ctk.CTkLabel(
            self.preview_card,
            text="AI suggestion preview will appear here.",
            text_color=THEME["muted"],
            font=("Segoe UI", 14),
        ).pack(pady=40)

    def _process_entry(self) -> None:
        """Ask Ollama for structured fields without blocking the UI."""
        if self.processing_entry:
            return
        text = self.input_text.get("1.0", "end").strip()
        self.processing_entry = True
        self.process_button.configure(state="disabled", text="Working...")
        self.status_label.configure(text="Asking local Ollama...", text_color=THEME["muted"])

        def worker() -> None:
            try:
                item = self.pipeline.preview(text)
            except ExtractionError as exc:
                self.after(0, lambda message=str(exc): self._finish_process(error=message))
                return
            self.after(0, lambda result=item: self._finish_process(item=result))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_process(self, item: dict | None = None, error: str | None = None) -> None:
        """Receive extraction results from the background thread."""
        self.processing_entry = False
        self.process_button.configure(state="normal", text="Process")
        if error:
            self.status_label.configure(text=f"Warning: {error}", text_color=THEME["red"])
            return
        if not item:
            self.status_label.configure(text="Warning: Could not extract a transaction.", text_color=THEME["red"])
            return
        self.preview_item = item
        if item.get("confidence") == "low":
            self.status_label.configure(text="Low-confidence fallback. Please review before saving.", text_color=THEME["red"])
        else:
            self.status_label.configure(text="Review the fields, then confirm.", text_color=THEME["muted"])
        self._draw_preview_form(self.preview_item)

    def _draw_preview_form(self, item: dict) -> None:
        """Create editable fields for user confirmation."""
        for child in self.preview_card.winfo_children():
            child.destroy()
        ctk.CTkLabel(self.preview_card, text="AI suggestion", font=("Segoe UI", 15, "bold")).pack(anchor="w", padx=18, pady=(16, 8))
        if item.get("confidence") == "low":
            ctk.CTkLabel(
                self.preview_card,
                text="Low confidence: please check this carefully before saving.",
                text_color=THEME["red"],
            ).pack(anchor="w", padx=18, pady=(0, 8))

        form = ctk.CTkFrame(self.preview_card, fg_color="transparent")
        form.pack(fill="x", padx=18, pady=6)
        for col in range(4):
            form.grid_columnconfigure(col, weight=1)

        self.field_vars = {
            "type": tk.StringVar(value=item["type"]),
            "amount": tk.StringVar(value=str(item["amount"])),
            "category": tk.StringVar(value=item["category"]),
            "description": tk.StringVar(value=item["description"]),
            "date": tk.StringVar(value=item["date"]),
            "time": tk.StringVar(value=item["time"]),
        }
        self._field(form, "Type", ctk.CTkOptionMenu, 0, 0, variable=self.field_vars["type"], values=["expense", "income"])
        self._field(form, "Amount", ctk.CTkEntry, 0, 1, textvariable=self.field_vars["amount"])
        self._field(form, "Category", ctk.CTkOptionMenu, 0, 2, variable=self.field_vars["category"], values=CATEGORIES)
        self._field(form, "Date", ctk.CTkEntry, 0, 3, textvariable=self.field_vars["date"])
        self._field(form, "Description", ctk.CTkEntry, 1, 0, colspan=2, textvariable=self.field_vars["description"])
        self._field(form, "Time", ctk.CTkEntry, 1, 2, textvariable=self.field_vars["time"])

        actions = ctk.CTkFrame(self.preview_card, fg_color="transparent")
        actions.pack(fill="x", padx=18, pady=16)
        ctk.CTkButton(actions, text="Confirm", width=130, fg_color=THEME["green"], command=self._confirm_preview).pack(side="left")
        ctk.CTkButton(actions, text="Cancel", width=110, fg_color=THEME["surface"], command=self._draw_empty_preview).pack(
            side="left", padx=8
        )

    def _field(self, parent, label: str, widget_cls, row: int, col: int, colspan: int = 1, **kwargs) -> None:
        """Draw one labeled editable field."""
        wrap = ctk.CTkFrame(parent, fg_color="transparent")
        wrap.grid(row=row, column=col, columnspan=colspan, sticky="ew", padx=5, pady=8)
        ctk.CTkLabel(wrap, text=label, text_color=THEME["muted"], font=("Segoe UI", 11)).pack(anchor="w")
        widget = widget_cls(wrap, height=34, **kwargs)
        widget.pack(fill="x", pady=(4, 0))

    def _confirm_preview(self) -> None:
        """Validate edited fields and save the transaction."""
        try:
            item = {key: var.get() for key, var in self.field_vars.items()}
            item["confidence"] = (self.preview_item or {}).get("confidence", "high")
            saved = self.pipeline.save(item)
        except ExtractionError as exc:
            messagebox.showerror("Cannot save", str(exc))
            return
        self.input_text.delete("1.0", "end")
        self._draw_empty_preview()
        self.status_label.configure(text=f"Saved #{saved['id']}", text_color=THEME["green"])

    def _toggle_recording(self) -> None:
        """Start or stop microphone recording from one WhatsApp-style button."""
        if self.recording_voice:
            self._stop_recording()
            return
        self._start_recording()

    def _start_recording(self) -> None:
        """Start listening in a background thread and keep the UI responsive."""
        missing = missing_voice_dependencies()
        if missing:
            self._set_status(
                "Microphone needs: " + ", ".join(missing) + ". Install requirements-voice.txt.",
                THEME["red"],
            )
            return
        if not self.vosk_model_path.exists():
            self._set_status(f"Microphone model missing: {self.vosk_model_path}", THEME["red"])
            return
        self.recording_voice = True
        self.recording_stop_event = threading.Event()
        if self.mic_button:
            self.mic_button.configure(text="Stop", fg_color=THEME["red"])
        self._set_status("Listening... speak now, then press Stop.", THEME["green"])

        def on_text(text: str) -> None:
            self.after(0, lambda value=text: self._append_voice_text(value))

        def on_status(message: str) -> None:
            self.after(0, lambda value=message: self._set_status(value, THEME["muted"]))

        def worker() -> None:
            try:
                listen_until_stopped(self.recording_stop_event, on_text, on_status, self.vosk_model_path)
            except Exception as exc:
                self.after(0, lambda message=str(exc): self._recording_finished(error=message))
                return
            self.after(0, self._recording_finished)

        threading.Thread(target=worker, daemon=True).start()

    def _stop_recording(self) -> None:
        """Ask the background recording loop to stop."""
        if not self.recording_voice:
            return
        if self.recording_stop_event:
            self.recording_stop_event.set()
        if self.mic_button and self.mic_button.winfo_exists():
            self.mic_button.configure(text="Stopping...", state="disabled")
        self._set_status("Stopping microphone...", THEME["muted"])

    def _recording_finished(self, error: str | None = None) -> None:
        """Return the microphone controls to their normal state."""
        self.recording_voice = False
        self.recording_stop_event = None
        if self.mic_button and self.mic_button.winfo_exists():
            self.mic_button.configure(text="Microphone", fg_color=THEME["blue"], state="normal")
        if error:
            self._set_status(error, THEME["red"])
        else:
            self._set_status("Microphone stopped. Press Process to extract.", THEME["muted"])

    def _append_voice_text(self, text: str) -> None:
        """Append recognized speech to the natural-language input box."""
        if not text.strip() or not self.input_text.winfo_exists():
            return
        current = self.input_text.get("1.0", "end").strip()
        next_text = f"{current} {text.strip()}".strip()
        self.input_text.delete("1.0", "end")
        self.input_text.insert("1.0", next_text)
        self._set_status(f'Captured: "{text.strip()}"', THEME["green"])

    def _set_status(self, text: str, color: str | None = None) -> None:
        """Safely update the Add Entry status label if it exists."""
        if hasattr(self, "status_label") and self.status_label.winfo_exists():
            self.status_label.configure(text=text, text_color=color or THEME["muted"])

    def show_history(self) -> None:
        """Render searchable transaction history."""
        self._set_active_nav("history")
        self._clear_main()
        page = ctk.CTkFrame(self.main, fg_color=THEME["background"], corner_radius=0)
        page.pack(fill="both", expand=True, padx=(8, 28), pady=24)
        self._screen_header(page, "Transactions")

        filters = ctk.CTkFrame(page, fg_color="transparent")
        filters.pack(fill="x", pady=(0, 12))
        self.history_search = tk.StringVar()
        self.history_type = tk.StringVar(value="all")
        self.history_category = tk.StringVar(value="All")
        self.history_period = tk.StringVar(value="all")
        ctk.CTkEntry(filters, textvariable=self.history_search, placeholder_text="Search", width=240).pack(side="left", padx=(0, 8))
        ctk.CTkOptionMenu(filters, variable=self.history_type, values=["all", "income", "expense"], width=120).pack(
            side="left", padx=8
        )
        ctk.CTkOptionMenu(filters, variable=self.history_category, values=["All", *CATEGORIES], width=180).pack(side="left", padx=8)
        ctk.CTkOptionMenu(filters, variable=self.history_period, values=["all", "today", "week", "month"], width=130).pack(
            side="left", padx=8
        )
        ctk.CTkButton(filters, text="Apply", width=90, command=self._refresh_history).pack(side="left", padx=8)

        self.history_list = ctk.CTkScrollableFrame(page, fg_color=THEME["surface"], corner_radius=8)
        self.history_list.pack(fill="both", expand=True)
        self._refresh_history()

    def _refresh_history(self) -> None:
        """Reload history rows from SQLite."""
        for child in self.history_list.winfo_children():
            child.destroy()
        rows = self.database.list_transactions(
            date_range=period_to_range(self.history_period.get()),
            search=self.history_search.get(),
            tx_type=self.history_type.get(),
            category=self.history_category.get(),
        )
        self._transaction_table(self.history_list, rows)

    def _transaction_table(self, parent: ctk.CTkFrame, rows: list[dict]) -> None:
        """Render history as a table similar to the reference interface."""
        if not rows:
            ctk.CTkLabel(parent, text="No transactions found", text_color=THEME["muted"]).pack(pady=30)
            return

        table = ctk.CTkFrame(parent, fg_color="transparent")
        table.pack(fill="x", padx=16, pady=14)
        widths = [0, 1, 2, 3, 4, 5]
        for col in widths:
            table.grid_columnconfigure(col, weight=1 if col in {1, 2} else 0)

        header = ctk.CTkFrame(table, fg_color=THEME["border"], corner_radius=0)
        header.grid(row=0, column=0, columnspan=6, sticky="ew")
        for col, text, width in [
            (0, "Date", 150),
            (1, "Description", 230),
            (2, "Category", 180),
            (3, "Type", 120),
            (4, "Amount", 130),
            (5, "Actions", 110),
        ]:
            ctk.CTkLabel(
                header,
                text=text,
                width=width,
                anchor="w",
                text_color=THEME["blue"],
                font=("Segoe UI", 14, "bold"),
            ).grid(row=0, column=col, padx=14, pady=12, sticky="w")

        for row_index, row in enumerate(rows, start=1):
            grid_row = (row_index * 2) - 1
            frame = ctk.CTkFrame(table, fg_color="transparent", corner_radius=0)
            frame.grid(row=grid_row, column=0, columnspan=6, sticky="ew")
            frame.grid_columnconfigure(1, weight=1)
            frame.grid_columnconfigure(2, weight=1)
            amount_color = THEME["green"] if row["type"] == "income" else THEME["red"]
            sign = "+" if row["type"] == "income" else "-"
            transaction_time = f" {row.get('time')}" if row.get("time") else ""
            date_text = f"{row['date']}{transaction_time}\nEntered {format_created_at(row.get('created_at'))}"
            values = [
                (date_text, 150, THEME["text"]),
                (row["description"], 230, THEME["text"]),
                (category_label(row["category"]), 180, THEME["text"]),
                (row["type"].title(), 120, THEME["green"] if row["type"] == "income" else THEME["red"]),
                (f"{sign}{money(float(row['amount']))}", 130, amount_color),
            ]
            for col, (text, width, color) in enumerate(values):
                ctk.CTkLabel(
                    frame,
                    text=text,
                    width=width,
                    anchor="w",
                    justify="left",
                    text_color=color,
                    font=("Segoe UI", 13, "bold" if col in {1, 3, 4} else "normal"),
                ).grid(row=0, column=col, padx=14, pady=10, sticky="w")
            ctk.CTkButton(
                frame,
                text="Delete",
                width=80,
                height=28,
                fg_color="transparent",
                hover_color=THEME["danger_hover"],
                text_color=THEME["red"],
                command=lambda row_id=row["id"]: self._delete_transaction(row_id),
            ).grid(row=0, column=5, padx=14, pady=10, sticky="w")
            ctk.CTkFrame(table, height=1, fg_color=THEME["border"]).grid(row=grid_row + 1, column=0, columnspan=6, sticky="ew")

    def _delete_transaction(self, row_id: int) -> None:
        """Ask before deleting a row."""
        if not messagebox.askyesno("Delete transaction", "Delete this transaction?"):
            return
        self.database.delete_transaction(row_id)
        if self.current_screen == "history":
            self._refresh_history()
        else:
            self.show_dashboard()

    def show_reports(self) -> None:
        """Render monthly bars, category pie chart, and CSV export."""
        self._set_active_nav("reports")
        self._clear_main()
        page = ctk.CTkFrame(self.main, fg_color=THEME["background"], corner_radius=0)
        page.pack(fill="both", expand=True, padx=(8, 28), pady=24)
        self._screen_header(page, "Reports")

        ctk.CTkLabel(page, text="Category Spending", font=("Segoe UI", 20, "bold"), text_color=THEME["blue"]).pack(
            anchor="w", pady=(0, 10)
        )
        self._category_spending_cards(page, self.database.category_breakdown(period_to_range("month")))

        body = ctk.CTkFrame(page, fg_color="transparent")
        body.pack(fill="both", expand=True, pady=(14, 0))
        body.grid_columnconfigure((0, 1), weight=1, uniform="reports")
        body.grid_rowconfigure(0, weight=1)

        pie_card = self._card(body)
        pie_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        ctk.CTkLabel(pie_card, text="Spending by category", font=("Segoe UI", 15, "bold")).pack(anchor="w", padx=18, pady=(16, 0))
        self._draw_category_pie(pie_card, self.database.category_breakdown(period_to_range("month")))

        bar_card = self._card(body)
        bar_card.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        ctk.CTkLabel(bar_card, text="Income vs expense", font=("Segoe UI", 15, "bold")).pack(anchor="w", padx=18, pady=(16, 0))
        self._draw_monthly_bar(bar_card, self.database.monthly_income_expense(6))

        ctk.CTkButton(page, text="Export to CSV", width=150, command=self._export_csv).pack(anchor="e", pady=(14, 0))

    def _category_spending_cards(self, parent: ctk.CTkFrame, rows: list[dict]) -> None:
        """Show category spending as small data cards."""
        wrap = ctk.CTkFrame(parent, fg_color="transparent")
        wrap.pack(fill="x")
        for col in range(4):
            wrap.grid_columnconfigure(col, weight=1, uniform="category")
        display_rows = rows[:8] or [{"category": "No spending", "amount": 0.0}]
        colors = [THEME["pink"], THEME["cyan"], THEME["yellow"], THEME["teal"], THEME["purple"], THEME["blue"]]
        for index, row in enumerate(display_rows):
            card = self._card(wrap)
            card.grid(row=index // 4, column=index % 4, sticky="ew", padx=(0 if index % 4 == 0 else 8, 8), pady=6)
            accent = colors[index % len(colors)]
            ctk.CTkFrame(card, width=38, height=38, fg_color=accent, corner_radius=19).pack(side="left", padx=14, pady=16)
            text = ctk.CTkFrame(card, fg_color="transparent")
            text.pack(side="left", fill="x", expand=True, pady=12)
            ctk.CTkLabel(text, text=category_label(row["category"]), text_color=THEME["text"], font=("Segoe UI", 15, "bold")).pack(anchor="w")
            ctk.CTkLabel(text, text=f"Spent: {money(float(row['amount']))}", text_color=THEME["muted"], font=("Segoe UI", 12)).pack(anchor="w")

    def _draw_category_pie(self, parent: ctk.CTkFrame, rows: list[dict]) -> None:
        """Draw a category pie chart for the current month."""
        fig = Figure(figsize=(4.8, 3.8), dpi=100, facecolor=THEME["surface"])
        ax = fig.add_subplot(111)
        ax.set_facecolor(THEME["surface"])
        if rows:
            values = [row["amount"] for row in rows]
            labels = [row["category"] for row in rows]
            ax.pie(values, labels=labels, textprops={"color": THEME["text"], "fontsize": 8})
        else:
            ax.text(0.5, 0.5, "No expenses this month", ha="center", va="center", color=THEME["muted"], transform=ax.transAxes)
        fig.tight_layout()
        canvas = FigureCanvasTkAgg(fig, master=parent)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True, padx=12, pady=12)

    def _draw_monthly_bar(self, parent: ctk.CTkFrame, rows: list[dict]) -> None:
        """Draw grouped monthly income and expense bars."""
        fig = Figure(figsize=(4.8, 3.8), dpi=100, facecolor=THEME["surface"])
        ax = fig.add_subplot(111)
        labels = [row["label"] for row in rows]
        xs = list(range(len(labels)))
        width = 0.35
        ax.set_facecolor(THEME["surface"])
        ax.bar([x - width / 2 for x in xs], [row["income"] for row in rows], width, color=THEME["green"], label="Income")
        ax.bar([x + width / 2 for x in xs], [row["expense"] for row in rows], width, color=THEME["red"], label="Expense")
        ax.set_xticks(xs)
        ax.set_xticklabels(labels, color=THEME["muted"])
        ax.tick_params(axis="y", colors=THEME["muted"])
        ax.legend(facecolor=THEME["surface"], labelcolor=THEME["text"])
        for spine in ax.spines.values():
            spine.set_color(THEME["border"])
        ax.grid(axis="y", color=THEME["border"], alpha=0.4)
        fig.tight_layout()
        canvas = FigureCanvasTkAgg(fig, master=parent)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True, padx=12, pady=12)

    def _export_csv(self) -> None:
        """Ask for a destination and export all transactions."""
        path = filedialog.asksaveasfilename(
            title="Export transactions",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
            initialfile="voicetrack-transactions.csv",
        )
        if not path:
            return
        saved = self.database.export_csv(Path(path))
        messagebox.showinfo("Export complete", f"Saved {saved}")

    def _on_saved(self, _item: dict) -> None:
        """Refresh the active screen after a save."""
        if self.current_screen == "dashboard":
            self.show_dashboard()


def run_app() -> None:
    """Create dependencies and start the desktop GUI."""
    settings = load_settings()
    database = Database(settings.db_path)
    extractor = OllamaExtractor(settings)
    pipeline = TransactionPipeline(database, extractor)
    app = VoiceTrackApp(database, pipeline, settings.vosk_model_path)
    app.mainloop()
