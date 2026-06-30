"""VoiceTrack desktop UI — CustomTkinter app."""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import filedialog, messagebox
import csv
import datetime

import customtkinter as ctk
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

import voicetrack.db as db
import voicetrack.extractor as extractor
import voicetrack.charts as charts
import voicetrack.assistant as assistant
from voicetrack.db import CATEGORIES

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ── Panel IDs ────────────────────────────────────────────
_PANEL_DASHBOARD = "Dashboard"
_PANEL_ADD       = "Add Entry"
_PANEL_HISTORY   = "History"
_PANEL_REPORTS   = "Reports"
_PANEL_LOANS     = "Loans"
_PANEL_SHARED    = "Shared"
_PANEL_ASSISTANT = "Assistant"

PAGE_SIZE = 50

# ── Theme palettes ───────────────────────────────────────
_DARK = {
    "bg":        "#0b0d14",
    "surface":   "#12151f",
    "surface2":  "#1a1d2e",
    "surface3":  "#22263a",
    "border":    "#2a2d45",
    "accent":    "#6366f1",
    "accent_h":  "#4f52d4",
    "accent_dim":"#1e2060",
    "success":   "#22c55e",
    "success_bg":"#0b2e18",
    "danger":    "#ef4444",
    "danger_bg": "#2e0b0b",
    "warning":   "#f59e0b",
    "text":      "#e2e8f0",
    "text2":     "#94a3b8",
    "text3":     "#4b5675",
    "income_bg": "#0b2e18",
    "expense_bg":"#2e0b0b",
    "income_fg": "#22c55e",
    "expense_fg":"#f87171",
    "sidebar":   "#0d0f1a",
    "sidebar2":  "#13162a",
    "card":      "#13162a",
    "card2":     "#1a1d30",
    "hover":     "#1e2140",
    "input_bg":  "#0b0d14",
}
_LIGHT = {
    "bg":        "#f0f2f8",
    "surface":   "#ffffff",
    "surface2":  "#f4f6fb",
    "surface3":  "#e8ecf5",
    "border":    "#d1d8ef",
    "accent":    "#6366f1",
    "accent_h":  "#4f52d4",
    "accent_dim":"#e0e1fc",
    "success":   "#16a34a",
    "success_bg":"#dcfce7",
    "danger":    "#dc2626",
    "danger_bg": "#fee2e2",
    "warning":   "#d97706",
    "text":      "#0f172a",
    "text2":     "#475569",
    "text3":     "#94a3b8",
    "income_bg": "#dcfce7",
    "expense_bg":"#fee2e2",
    "income_fg": "#16a34a",
    "expense_fg":"#dc2626",
    "sidebar":   "#ffffff",
    "sidebar2":  "#f4f6fb",
    "card":      "#ffffff",
    "card2":     "#f4f6fb",
    "hover":     "#eef0fb",
    "input_bg":  "#f4f6fb",
}

_dark_mode = True

def C(key: str) -> str:
    return (_DARK if _dark_mode else _LIGHT)[key]

CAT_COLORS = {
    "Food & Groceries": "#f59e0b",
    "Transport":        "#3b82f6",
    "Utilities":        "#8b5cf6",
    "Health":           "#10b981",
    "Shopping":         "#ec4899",
    "Education":        "#06b6d4",
    "Rent":             "#fb923c",
    "Salary":           "#22c55e",
    "Freelance":        "#a78bfa",
    "Entertainment":    "#f97316",
    "Other":            "#64748b",
}


def _embed_figure(fig, parent):
    canvas = FigureCanvasTkAgg(fig, master=parent)
    canvas.draw()
    return canvas.get_tk_widget()


# ── Widget helpers ───────────────────────────────────────

def _card(parent, radius=14, **kw) -> ctk.CTkFrame:
    return ctk.CTkFrame(parent, corner_radius=radius,
                        fg_color=C("card"), **kw)

def _surface(parent, radius=10, **kw) -> ctk.CTkFrame:
    return ctk.CTkFrame(parent, corner_radius=radius,
                        fg_color=C("surface2"), **kw)

def _lbl(parent, text, size=13, weight="normal", color=None, **kw):
    font = ctk.CTkFont(size=size, weight=weight)
    kw2 = {"text_color": color or C("text")}
    return ctk.CTkLabel(parent, text=text, font=font, **kw2, **kw)

def _btn(parent, text, command=None, width=110, height=34,
         style="primary", **kw) -> ctk.CTkButton:
    if style == "primary":
        fg, hover, tc = C("accent"), C("accent_h"), "#ffffff"
    elif style == "ghost":
        fg, hover, tc = "transparent", C("hover"), C("text2")
    elif style == "danger":
        fg, hover, tc = C("danger_bg"), C("danger"), C("danger")
    else:
        fg, hover, tc = C("surface2"), C("surface3"), C("text2")
    return ctk.CTkButton(parent, text=text, command=command,
                         width=width, height=height, corner_radius=8,
                         fg_color=fg, hover_color=hover,
                         text_color=tc, font=ctk.CTkFont(size=13),
                         **kw)

def _divider(parent):
    ctk.CTkFrame(parent, fg_color=C("border"), height=1,
                 corner_radius=0).pack(fill="x", padx=16, pady=4)


# ════════════════════════════════════════════════════════
#  App
# ════════════════════════════════════════════════════════

class VoiceTrackApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("VoiceTrack")
        self.minsize(980, 640)
        self.geometry("1200x720")
        self.configure(fg_color=C("bg"))

        db.init_db()
        extractor.warmup_async()   # load model into Ollama RAM immediately

        self._panels: dict[str, ctk.CTkFrame] = {}
        self._tx_offset    = 0
        self._voice_recorder = None
        self._recording    = False
        self._spinner_active = False
        self._spinner_tick = 0
        self._dash_period  = "month"
        self._current_panel = _PANEL_DASHBOARD
        self._hist_type_filter = ""

        self._build_sidebar()
        self._build_content_area()
        self._show_panel(_PANEL_DASHBOARD)

    # ── Sidebar ──────────────────────────────────────────

    def _build_sidebar(self):
        global _sb_frame
        self._sb = ctk.CTkFrame(self, width=52, corner_radius=0,
                                 fg_color=C("sidebar"))
        self._sb.pack(side="left", fill="y")
        self._sb.pack_propagate(False)

        # Accent top bar
        ctk.CTkFrame(self._sb, fg_color=C("accent"),
                     height=3, corner_radius=0).pack(fill="x")

        # Logo
        logo_wrap = ctk.CTkFrame(self._sb, fg_color="transparent")
        logo_wrap.pack(fill="x", padx=7, pady=(18, 18))

        logo_ic = ctk.CTkFrame(logo_wrap, fg_color=C("accent_dim"),
                               corner_radius=12, width=38, height=38)
        logo_ic.pack(side="left")
        logo_ic.pack_propagate(False)
        _lbl(logo_ic, "🎙", size=18).pack(expand=True)

        name_col = ctk.CTkFrame(logo_wrap, fg_color="transparent")
        name_col.pack_forget()
        _lbl(name_col, "VoiceTrack", size=15, weight="bold",
             color=C("text")).pack(anchor="w")
        _lbl(name_col, "Finance Tracker", size=10,
             color=C("text3")).pack(anchor="w")

        _divider(self._sb)

        # Nav items
        self._nav_btns: dict[str, ctk.CTkButton] = {}
        nav = [
            ("📊", "Dashboard",  _PANEL_DASHBOARD),
            ("➕", "Add Entry",  _PANEL_ADD),
            ("📋", "History",    _PANEL_HISTORY),
            ("📈", "Reports",    _PANEL_REPORTS),
        ]
        nav.insert(3, ("L", "Loans", _PANEL_LOANS))
        nav.insert(4, ("S", "Shared", _PANEL_SHARED))
        nav.append(("💬", "Assistant", _PANEL_ASSISTANT))
        for icon, label, panel in nav:
            btn = ctk.CTkButton(
                self._sb,
                text=icon,
                anchor="center",
                corner_radius=10,
                width=36,
                height=36,
                fg_color="transparent",
                hover_color=C("hover"),
                text_color=C("text2"),
                font=ctk.CTkFont(size=13),
                command=lambda p=panel: self._show_panel(p),
            )
            btn.pack(padx=8, pady=5)
            self._nav_btns[panel] = btn

        # Bottom: theme toggle + settings
        bottom = ctk.CTkFrame(self._sb, fg_color="transparent")
        bottom.pack(side="bottom", fill="x", padx=10, pady=12)

        _divider(self._sb)

        # Theme toggle
        tog_row = ctk.CTkFrame(self._sb, fg_color="transparent")
        tog_row.pack(side="bottom", fill="x", padx=6, pady=(0, 8))
        self._theme_sw = ctk.CTkSwitch(
            tog_row, text="", width=40,
            button_color=C("accent"), button_hover_color=C("accent_h"),
            progress_color=C("accent_dim"),
            command=self._toggle_theme,
        )
        self._theme_sw.pack(anchor="center")

        ctk.CTkButton(
            self._sb, text="⚙",
            anchor="center", corner_radius=10, width=36, height=36,
            fg_color="transparent", hover_color=C("hover"),
            text_color=C("text3"), font=ctk.CTkFont(size=12),
        ).pack(side="bottom", padx=8, pady=(0, 8))

    def _set_active_nav(self, panel: str):
        for p, btn in self._nav_btns.items():
            if p == panel:
                btn.configure(fg_color=C("accent_dim"),
                              text_color=C("accent"))
            else:
                btn.configure(fg_color="transparent",
                              text_color=C("text2"))

    # ── Theme toggle ─────────────────────────────────────

    def _toggle_theme(self):
        global _dark_mode
        _dark_mode = not _dark_mode
        ctk.set_appearance_mode("dark" if _dark_mode else "light")

        # Update sidebar
        self._sb.configure(fg_color=C("sidebar"))
        self.configure(fg_color=C("bg"))

        # Rebuild content area with new colours
        self._content.destroy()
        self._panels.clear()
        self._build_content_area()
        self._show_panel(self._current_panel)

    # ── Content area ─────────────────────────────────────

    def _build_content_area(self):
        self._content = ctk.CTkFrame(self, corner_radius=0,
                                     fg_color=C("bg"))
        self._content.pack(side="left", fill="both", expand=True)

        self._panels[_PANEL_DASHBOARD] = self._build_dashboard_panel()
        self._panels[_PANEL_ADD]       = self._build_add_panel()
        self._panels[_PANEL_HISTORY]   = self._build_history_panel()
        self._panels[_PANEL_LOANS]     = self._build_loans_panel()
        self._panels[_PANEL_SHARED]    = self._build_shared_panel()
        self._panels[_PANEL_REPORTS]   = self._build_reports_panel()
        self._panels[_PANEL_ASSISTANT] = self._build_assistant_panel()

    def _show_panel(self, name: str):
        self._current_panel = name
        for p in self._panels.values():
            p.pack_forget()
        self._panels[name].pack(fill="both", expand=True)
        self._set_active_nav(name)
        if name == _PANEL_DASHBOARD:
            self._refresh_dashboard()
        elif name == _PANEL_HISTORY:
            self._tx_offset = 0
            self._refresh_history()
        elif name == _PANEL_LOANS:
            self._refresh_loans()
        elif name == _PANEL_SHARED:
            self._refresh_shared()
        elif name == _PANEL_REPORTS:
            self._refresh_reports()
        elif name == _PANEL_ASSISTANT:
            self._input_chat.focus_set()

    # ── Dashboard ────────────────────────────────────────

    def _build_dashboard_panel(self) -> ctk.CTkFrame:
        panel = ctk.CTkFrame(self._content, fg_color="transparent")

        # ── Header ──
        hdr = ctk.CTkFrame(panel, fg_color="transparent")
        hdr.pack(fill="x", padx=28, pady=(22, 0))
        _lbl(hdr, "Dashboard", size=22, weight="bold").pack(side="left")
        right_hdr = ctk.CTkFrame(hdr, fg_color="transparent")
        right_hdr.pack(side="right")
        self._date_label = _lbl(right_hdr, "", size=12, color=C("text2"))
        self._date_label.pack(side="left", padx=(0, 12))
        _btn(right_hdr, "⟳  Refresh", command=self._refresh_dashboard,
             width=100, height=30, style="ghost").pack(side="left")

        # ── Summary cards ──
        cards_row = ctk.CTkFrame(panel, fg_color="transparent")
        cards_row.pack(fill="x", padx=28, pady=(16, 8))

        self._sum_labels: dict[str, ctk.CTkLabel] = {}
        card_defs = [
            ("income_month",  "Income",       "↑", C("income_fg"),  C("income_bg")),
            ("expense_month", "Expenses",     "↓", C("expense_fg"), C("expense_bg")),
            ("net_worth",     "Net Worth",  "◈", C("warning"),     C("accent_dim")),
        ]
        for key, title, icon, color, bg in card_defs:
            c = _card(cards_row, height=118)
            c.pack(side="left", expand=True, fill="both", padx=5)
            c.pack_propagate(False)

            # Coloured left accent stripe
            stripe = ctk.CTkFrame(c, fg_color=color, width=4,
                                  corner_radius=0)
            stripe.pack(side="left", fill="y", padx=(0, 0))
            stripe.pack_propagate(False)

            inner = ctk.CTkFrame(c, fg_color="transparent")
            inner.pack(fill="both", expand=True, padx=14, pady=14)

            top_row = ctk.CTkFrame(inner, fg_color="transparent")
            top_row.pack(fill="x")
            _lbl(top_row, title, size=11, color=C("text2")).pack(side="left")

            ic_f = ctk.CTkFrame(top_row, fg_color=bg, corner_radius=8,
                                width=28, height=28)
            ic_f.pack(side="right")
            ic_f.pack_propagate(False)
            _lbl(ic_f, icon, size=14, color=color).pack(expand=True)

            lbl = _lbl(inner, "PKR 0", size=22, weight="bold", color=color)
            lbl.pack(anchor="w", pady=(6, 0))
            self._sum_labels[key] = lbl

            subtitle = "Cash + receivables - payables" if key == "net_worth" else "This period"
            _lbl(inner, subtitle, size=10, color=C("text3")).pack(anchor="w")

        # ── Period tabs (pill style) ──
        finance_row = ctk.CTkFrame(panel, fg_color="transparent")
        finance_row.pack(fill="x", padx=28, pady=(0, 12))
        self._finance_labels: dict[str, ctk.CTkLabel] = {}
        for key, title, color in [
            ("cash_outflow", "Cash Outflow", C("expense_fg")),
            ("personal_expenses", "Personal Expenses", "#f59e0b"),
            ("outstanding_receivables", "Receivables", C("income_fg")),
            ("outstanding_payables", "Payables", C("danger")),
            ("net_cash", "Available Cash", C("accent")),
        ]:
            mini = _surface(finance_row, radius=10, height=82)
            mini.configure(border_width=1, border_color=color)
            mini.pack(side="left", fill="x", expand=True, padx=5)
            mini.pack_propagate(False)
            _lbl(mini, title, size=12, color=C("text2")).pack(anchor="w", padx=14, pady=(14, 0))
            lbl = _lbl(mini, "PKR 0", size=18, weight="bold", color=color)
            lbl.pack(anchor="w", padx=14, pady=(2, 12))
            self._finance_labels[key] = lbl

        tab_bg = _surface(panel, radius=10)
        tab_bg.pack(fill="x", padx=28, pady=(0, 14))
        tab_bg.configure(fg_color=C("surface2"))
        inner_tabs = ctk.CTkFrame(tab_bg, fg_color="transparent")
        inner_tabs.pack(padx=6, pady=6)

        self._period_btns: dict[str, ctk.CTkButton] = {}
        for label, key in [("Today","today"),("This Week","week"),
                            ("This Month","month"),("All Time","all")]:
            b = ctk.CTkButton(
                inner_tabs, text=label, width=100, height=28,
                corner_radius=7,
                fg_color="transparent",
                hover_color=C("hover"),
                text_color=C("text2"),
                font=ctk.CTkFont(size=12),
                command=lambda k=key: self._set_period(k),
            )
            b.pack(side="left", padx=2)
            self._period_btns[key] = b

        # ── Body ──
        body = ctk.CTkFrame(panel, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=28, pady=(0, 20))

        # Left — Spending by category
        left = _card(body)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))

        lhdr = ctk.CTkFrame(left, fg_color="transparent")
        lhdr.pack(fill="x", padx=16, pady=(16, 6))
        _lbl(lhdr, "Spending by Category", size=13,
             weight="bold").pack(side="left")

        self._cat_chart_frame = ctk.CTkScrollableFrame(
            left, fg_color="transparent",
            scrollbar_button_color=C("border"),
            scrollbar_button_hover_color=C("surface3"))
        self._cat_chart_frame.pack(fill="both", expand=True,
                                   padx=10, pady=(0, 12))

        # Right — Recent transactions
        right = _card(body)
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))

        rhdr = ctk.CTkFrame(right, fg_color="transparent")
        rhdr.pack(fill="x", padx=16, pady=(16, 6))
        _lbl(rhdr, "Recent Transactions", size=13,
             weight="bold").pack(side="left")

        self._recent_frame = ctk.CTkScrollableFrame(
            right, fg_color="transparent",
            scrollbar_button_color=C("border"),
            scrollbar_button_hover_color=C("surface3"))
        self._recent_frame.pack(fill="both", expand=True,
                                padx=6, pady=(0, 12))

        return panel

    def _set_period(self, key: str):
        self._dash_period = key
        self._refresh_dashboard()

    def _refresh_dashboard(self):
        today = datetime.date.today()
        days = ["Monday","Tuesday","Wednesday","Thursday",
                "Friday","Saturday","Sunday"]
        months = ["Jan","Feb","Mar","Apr","May","Jun",
                  "Jul","Aug","Sep","Oct","Nov","Dec"]
        self._date_label.configure(
            text=f"{days[today.weekday()]}, {today.day} {months[today.month-1]} {today.year}")

        for k, b in self._period_btns.items():
            if k == self._dash_period:
                b.configure(fg_color=C("accent"), text_color="#ffffff")
            else:
                b.configure(fg_color="transparent", text_color=C("text2"))

        all_txs = db.get_transactions(limit=100000)

        def _in_period(tx):
            d = tx.get("date", "")
            if not d:
                return True
            try:
                dt = datetime.date.fromisoformat(d)
            except ValueError:
                return True
            if self._dash_period == "today":
                return dt == today
            elif self._dash_period == "week":
                return (today - dt).days <= 6
            elif self._dash_period == "month":
                return dt.year == today.year and dt.month == today.month
            return True

        txs = [t for t in all_txs if _in_period(t)]
        income  = sum(
            t["amount"] for t in txs
            if t["type"] == "income" and (t.get("kind") in (None, "standard"))
        )
        expense = sum(
            t["amount"] for t in txs
            if t["type"] == "expense" and (t.get("kind") in (None, "standard", "shared_expense"))
        )
        cash_balance = sum(
            float(t["cash_flow"]) if t.get("cash_flow") is not None
            else (float(t["amount"]) if t["type"] == "income" else -float(t["amount"]))
            for t in txs
        )
        finance = db.get_finance_summary()

        self._sum_labels["income_month"].configure(text=f"PKR {income:,.0f}")
        self._sum_labels["expense_month"].configure(text=f"PKR {expense:,.0f}")
        net_worth = finance.get("net_worth", cash_balance)
        worth_color = C("income_fg") if net_worth >= 0 else C("expense_fg")
        self._sum_labels["net_worth"].configure(
            text=f"PKR {net_worth:,.0f}", text_color=worth_color)

        for key, label in getattr(self, "_finance_labels", {}).items():
            value = finance.get(key, 0)
            label.configure(text=f"PKR {value:,.0f}")

        self._render_cat_bars(txs)
        self._render_recent(txs)

    def _render_cat_bars(self, txs):
        for w in self._cat_chart_frame.winfo_children():
            w.destroy()

        totals: dict[str, float] = {}
        for t in txs:
            if t["type"] == "expense" and (t.get("kind") in (None, "standard", "shared_expense")):
                cat = t.get("category", "Other")
                totals[cat] = totals.get(cat, 0) + t["amount"]

        if not totals:
            _lbl(self._cat_chart_frame, "No expense data for this period",
                 color=C("text3")).pack(pady=30)
            return

        max_val = max(totals.values())
        for cat, val in sorted(totals.items(), key=lambda x: -x[1]):
            row = ctk.CTkFrame(self._cat_chart_frame,
                               fg_color="transparent")
            row.pack(fill="x", pady=5)

            _lbl(row, cat, size=11, color=C("text2"),
                 width=110, anchor="w").pack(side="left", padx=(4, 8))

            track = ctk.CTkFrame(row, fg_color=C("surface3"),
                                 corner_radius=5, height=8)
            track.pack(side="left", fill="x", expand=True)
            track.pack_propagate(False)

            color = CAT_COLORS.get(cat, "#64748b")
            ratio = val / max_val if max_val else 0
            fill = ctk.CTkFrame(track, fg_color=color,
                                corner_radius=5, height=8)
            fill.place(relx=0, rely=0, relwidth=max(ratio, 0.03), relheight=1)

            _lbl(row, f"{val:,.0f}", size=11,
                 color=C("text3")).pack(side="left", padx=(8, 4))

    def _render_recent(self, txs):
        for w in self._recent_frame.winfo_children():
            w.destroy()

        recent = sorted(
            txs,
            key=lambda t: (t.get("date") or "", t.get("created_at") or ""),
            reverse=True,
        )[:12]

        if not recent:
            _lbl(self._recent_frame, "No transactions yet",
                 color=C("text3")).pack(pady=30)
            return

        for t in recent:
            row = ctk.CTkFrame(self._recent_frame, fg_color=C("card2"),
                               corner_radius=10)
            row.pack(fill="x", pady=3, padx=4)

            cat   = t.get("category", "Other")
            color = CAT_COLORS.get(cat, "#64748b")

            ic = ctk.CTkFrame(row, fg_color=C("surface3"),
                              corner_radius=18, width=34, height=34)
            ic.pack(side="left", padx=(10, 0), pady=8)
            ic.pack_propagate(False)
            _lbl(ic, cat[0].upper(), size=12, weight="bold",
                 color=color).pack(expand=True)

            info = ctk.CTkFrame(row, fg_color="transparent")
            info.pack(side="left", fill="x", expand=True, padx=10, pady=8)
            desc = str(t.get("description", ""))[:24] or cat
            _lbl(info, desc, size=12, weight="bold").pack(anchor="w")
            _lbl(info, t.get("date", ""), size=10,
                 color=C("text3")).pack(anchor="w")

            sign      = "+" if t["type"] == "income" else "-"
            amt_color = C("income_fg") if t["type"] == "income" else C("expense_fg")
            _lbl(row, f"{sign}{t['amount']:,.0f}",
                 size=13, weight="bold",
                 color=amt_color).pack(side="right", padx=12)

    # ── History ───────────────────────────────────────────

    def _build_history_panel(self) -> ctk.CTkFrame:
        panel = ctk.CTkFrame(self._content, fg_color="transparent")

        # Header
        hdr = ctk.CTkFrame(panel, fg_color="transparent")
        hdr.pack(fill="x", padx=28, pady=(22, 14))
        _lbl(hdr, "History", size=22, weight="bold").pack(side="left")

        # Search bar
        sb = ctk.CTkFrame(panel, fg_color=C("card"),
                          corner_radius=10)
        sb.pack(fill="x", padx=28, pady=(0, 10))
        _lbl(sb, "🔍", size=13, color=C("text3")).pack(
            side="left", padx=(14, 6), pady=10)
        self._search_var = ctk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._refresh_history())
        ctk.CTkEntry(
            sb, placeholder_text="Search transactions…",
            textvariable=self._search_var,
            border_width=0, fg_color="transparent",
            text_color=C("text"),
            placeholder_text_color=C("text3"),
            font=ctk.CTkFont(size=13),
        ).pack(fill="x", pady=10, padx=(0, 12))

        # Filter bar
        fb = ctk.CTkFrame(panel, fg_color="transparent")
        fb.pack(fill="x", padx=28, pady=(0, 10))

        # Type pills
        pill_bg = ctk.CTkFrame(fb, fg_color=C("card"),
                               corner_radius=9)
        pill_bg.pack(side="left")
        self._type_filter_btns: dict[str, ctk.CTkButton] = {}
        for label, val in [("All", ""), ("Income", "income"), ("Expense", "expense")]:
            b = ctk.CTkButton(
                pill_bg, text=label, width=72, height=28,
                corner_radius=7,
                fg_color="transparent",
                hover_color=C("hover"),
                text_color=C("text2"),
                font=ctk.CTkFont(size=12),
                command=lambda v=val: self._set_type_filter(v),
            )
            b.pack(side="left", padx=3, pady=3)
            self._type_filter_btns[val] = b

        # Category dropdown
        self._filter_cat = ctk.CTkOptionMenu(
            fb, values=["All categories"] + CATEGORIES,
            width=155, height=34, corner_radius=9,
            fg_color=C("card"), button_color=C("card"),
            button_hover_color=C("hover"),
            text_color=C("text2"),
            dropdown_fg_color=C("surface2"),
            dropdown_text_color=C("text"),
            dropdown_hover_color=C("hover"),
            command=lambda _: self._refresh_history(),
        )
        self._filter_cat.pack(side="left", padx=8)

        # Date range
        date_frame = ctk.CTkFrame(fb, fg_color=C("card"), corner_radius=9)
        date_frame.pack(side="left", padx=(0, 8))
        _lbl(date_frame, "From", size=11, color=C("text3")).pack(
            side="left", padx=(12, 4))
        self._filter_from = ctk.CTkEntry(
            date_frame, width=100, height=28,
            placeholder_text="YYYY-MM-DD",
            fg_color="transparent", border_width=0,
            text_color=C("text"),
            placeholder_text_color=C("text3"),
        )
        self._filter_from.pack(side="left")
        _lbl(date_frame, "–", size=12, color=C("text3")).pack(
            side="left", padx=4)
        self._filter_to = ctk.CTkEntry(
            date_frame, width=100, height=28,
            placeholder_text="YYYY-MM-DD",
            fg_color="transparent", border_width=0,
            text_color=C("text"),
            placeholder_text_color=C("text3"),
        )
        self._filter_to.pack(side="left", padx=(0, 8))

        _btn(fb, "Apply", command=self._apply_hist_filters,
             width=70, height=34).pack(side="left")

        # Set initial button state without triggering refresh
        for v, b in self._type_filter_btns.items():
            if v == "":
                b.configure(fg_color=C("accent"), text_color="#ffffff")
            else:
                b.configure(fg_color="transparent", text_color=C("text2"))

        # Table header
        th = ctk.CTkFrame(panel, fg_color=C("surface2"), corner_radius=0,
                          height=36)
        th.pack(fill="x", padx=28, pady=(4, 0))
        th.pack_propagate(False)
        for col, w in [("DATE",95),("TYPE",85),("CATEGORY",130),
                       ("DESCRIPTION",0),("AMOUNT",100),("",44)]:
            kw = {"width": w} if w else {}
            _lbl(th, col, size=10, weight="bold",
                 color=C("text3"), anchor="w", **kw).pack(
                side="left", padx=10, pady=10)

        # Scrollable rows
        self._hist_scroll = ctk.CTkScrollableFrame(
            panel, fg_color="transparent",
            scrollbar_button_color=C("border"),
            scrollbar_button_hover_color=C("surface3"))
        self._hist_scroll.pack(fill="both", expand=True, padx=28, pady=4)

        # Pager
        pager = ctk.CTkFrame(panel, fg_color=C("surface2"),
                             corner_radius=10, height=44)
        pager.pack(fill="x", padx=28, pady=(0, 16))
        pager.pack_propagate(False)
        _btn(pager, "← Prev", command=self._tx_prev,
             width=80, height=28, style="ghost").pack(
            side="left", padx=10, pady=8)
        self._page_label = _lbl(pager, "Page 1", size=12, color=C("text2"))
        self._page_label.pack(side="left", padx=8)
        _btn(pager, "Next →", command=self._tx_next,
             width=80, height=28, style="ghost").pack(side="left")

        return panel

    def _set_type_filter(self, val: str):
        self._hist_type_filter = val
        for v, b in self._type_filter_btns.items():
            if v == val:
                b.configure(fg_color=C("accent"), text_color="#ffffff")
            else:
                b.configure(fg_color="transparent", text_color=C("text2"))
        self._tx_offset = 0
        self._refresh_history()

    def _apply_hist_filters(self):
        self._tx_offset = 0
        self._refresh_history()

    def _refresh_history(self):
        if not hasattr(self, "_hist_scroll"):
            return
        for w in self._hist_scroll.winfo_children():
            w.destroy()

        cat = self._filter_cat.get()
        rows = db.get_transactions(
            limit=PAGE_SIZE, offset=self._tx_offset,
            category=None if cat == "All categories" else cat,
            tx_type=self._hist_type_filter or None,
            date_from=self._filter_from.get() or None,
            date_to=self._filter_to.get() or None,
        )
        search = self._search_var.get().lower()
        if search:
            rows = [r for r in rows if
                    search in str(r.get("description", "")).lower() or
                    search in str(r.get("category", "")).lower()]

        self._page_label.configure(
            text=f"Page {self._tx_offset // PAGE_SIZE + 1}")

        if not rows:
            _lbl(self._hist_scroll, "No transactions found",
                 color=C("text3")).pack(pady=40)
            return

        for i, row in enumerate(rows):
            self._add_hist_row(row, alt=i % 2 == 1)

    def _add_hist_row(self, row: dict, alt=False):
        bg = C("surface2") if alt else "transparent"
        r = ctk.CTkFrame(self._hist_scroll, fg_color=bg,
                         corner_radius=8, height=46)
        r.pack(fill="x", pady=1)
        r.pack_propagate(False)

        _lbl(r, row.get("date", ""), size=12, color=C("text2"),
             width=95, anchor="w").pack(side="left", padx=10)

        tx_type = row.get("type", "")
        kind = row.get("kind") or "standard"
        kind_labels = {
            "loan_given": "Loan Given",
            "loan_taken": "Loan Taken",
            "loan_repayment_received": "Repay In",
            "loan_repayment_made": "Repay Out",
            "shared_expense": "Shared",
        }
        display_type = kind_labels.get(kind, tx_type.capitalize())
        if tx_type == "income":
            badge_bg, badge_fg = C("income_bg"), C("income_fg")
        else:
            badge_bg, badge_fg = C("expense_bg"), C("expense_fg")

        badge = ctk.CTkFrame(r, fg_color=badge_bg, corner_radius=6,
                             width=92, height=24)
        badge.pack(side="left", padx=4)
        badge.pack_propagate(False)
        _lbl(badge, display_type, size=10, weight="bold",
             color=badge_fg).pack(expand=True)

        cat = row.get("category", "Other")
        cat_color = CAT_COLORS.get(cat, "#64748b")
        _lbl(r, cat, size=12, weight="bold", color=cat_color,
             width=120, anchor="w").pack(side="left", padx=8)

        desc = str(row.get("description", ""))[:38]
        _lbl(r, desc, size=12, color=C("text2"),
             anchor="w").pack(side="left", fill="x", expand=True, padx=4)

        sign = "+" if tx_type == "income" else "-"
        amt_color = C("income_fg") if tx_type == "income" else C("expense_fg")
        _lbl(r, f"{sign}{row.get('amount', 0):,.0f}",
             size=13, weight="bold", color=amt_color,
             width=100, anchor="e").pack(side="left", padx=8)

        ctk.CTkButton(
            r, text="🗑", width=34, height=28,
            fg_color="transparent", hover_color=C("danger_bg"),
            text_color=C("danger"), font=ctk.CTkFont(size=14),
            command=lambda rid=row["id"]: self._delete_tx(rid),
        ).pack(side="left", padx=6)

    def _delete_tx(self, tx_id: int):
        db.delete_transaction(tx_id)
        self._refresh_history()

    def _tx_prev(self):
        if self._tx_offset >= PAGE_SIZE:
            self._tx_offset -= PAGE_SIZE
            self._refresh_history()

    def _tx_next(self):
        self._tx_offset += PAGE_SIZE
        self._refresh_history()

    # ── Add Entry ─────────────────────────────────────────

    def _build_add_panel(self) -> ctk.CTkFrame:
        panel = ctk.CTkFrame(self._content, fg_color="transparent")

        hdr = ctk.CTkFrame(panel, fg_color="transparent")
        hdr.pack(fill="x", padx=28, pady=(22, 0))
        _lbl(hdr, "Add Entry", size=22, weight="bold").pack(side="left")

        # Input card
        card = _card(panel)
        card.pack(fill="x", padx=28, pady=(28, 14))

        top_card = ctk.CTkFrame(card, fg_color="transparent")
        top_card.pack(fill="x", padx=14, pady=(14, 4))
        _lbl(top_card, "Describe your transaction",
             size=13, weight="bold").pack(side="left")
        _lbl(top_card, "Examples: loans, shared bills, income, expenses",
             size=11, color=C("text3")).pack(side="right")

        # Text input with accent border on focus
        input_wrap = ctk.CTkFrame(card, fg_color=C("input_bg"),
                                  corner_radius=10,
                                  border_width=1,
                                  border_color=C("border"))
        input_wrap.pack(fill="x", padx=12, pady=12)

        self._input_box = ctk.CTkEntry(
            input_wrap, height=42, corner_radius=10,
            fg_color="transparent", border_width=0,
            text_color=C("text"),
            placeholder_text="Type naturally, e.g. 'I lent Ali 5000' or 'cab 500 and dinner 900 split equally'",
            placeholder_text_color=C("text3"),
            font=ctk.CTkFont(size=14),
        )
        self._input_box.pack(fill="x", padx=8, pady=4)
        self._input_placeholder = _lbl(
            input_wrap,
            "Type naturally, e.g. “I lent Ali 5000” or “cab 500 and dinner 900 split equally”",
            size=12,
            color=C("text3"),
        )
        self._input_placeholder.place_forget()
        self._input_box.bind("<KeyRelease>", self._sync_input_placeholder)
        self._input_box.bind("<FocusIn>", self._sync_input_placeholder)
        self._input_box.bind("<FocusOut>", self._sync_input_placeholder)
        self._input_placeholder.bind("<Button-1>", self._focus_input_box)
        self._input_placeholder.configure(
            text="Type naturally, e.g. 'I lent Ali 5000' or 'cab 500 and dinner 900 split equally'"
        )

        hint_row = ctk.CTkFrame(card, fg_color="transparent")
        hint_row.pack(fill="x", padx=14, pady=(0, 8))
        hints = [
            ('🛒', '"spent 500 on groceries yesterday"'),
            ('💰', '"received salary 50000 last month"'),
            ('🎬', '"movie ticket 1200 and cab 300 today"'),
        ]
        for icon, text in hints:
            chip = ctk.CTkFrame(hint_row, fg_color=C("surface2"),
                                corner_radius=6)
            chip.pack(side="left", padx=(0, 6), pady=(0, 10))
            _lbl(chip, f"{icon} {text}", size=10,
                 color=C("text3")).pack(padx=8, pady=4)

        help_text = (
            "You can enter normal expenses, income, loans, repayments, "
            "or shared bills in one sentence."
        )
        _lbl(card, help_text, size=11, color=C("text3")).pack(
            anchor="w", padx=16, pady=(0, 8))

        # Buttons
        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(fill="x", padx=12, pady=(0, 12))

        self._process_btn = ctk.CTkButton(
            btn_row, text="→", width=48, height=34,
            corner_radius=10,
            fg_color=C("accent"), hover_color=C("accent_h"),
            text_color="#ffffff",
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._process_input,
        )
        self._process_btn.pack(side="right", padx=(8, 0))

        self._spinner_label = _lbl(btn_row, "", size=13, color=C("accent"))
        self._spinner_label.pack(side="left")

        self._mic_btn = ctk.CTkButton(
            btn_row, text="Mic", width=70, height=34,
            corner_radius=10,
            fg_color=C("surface2"), hover_color=C("surface3"),
            text_color=C("text2"),
            command=self._toggle_mic,
        )
        self._mic_btn.pack(side="left", padx=(0, 8))

        self._voice_status = _lbl(btn_row, "", size=11, color=C("text3"))
        self._voice_status.pack(side="left")

        self._error_label = _lbl(panel, "", size=12, color=C("danger"))
        self._error_label.pack(anchor="w", padx=28, pady=(0, 4))

        self._preview_container = ctk.CTkScrollableFrame(
            panel, fg_color="transparent",
            scrollbar_button_color=C("border"),
            scrollbar_button_hover_color=C("surface3"))
        self._preview_container.pack(fill="both", expand=True,
                                     padx=28, pady=(0, 16))

        self._init_voice()
        self.bind("<Control-Return>", lambda _e: self._process_input())
        return panel

    def _sync_input_placeholder(self, _event=None):
        return

    def _focus_input_box(self, _event=None):
        self._input_box.focus_set()

    def _init_voice(self):
        try:
            from voicetrack.voice import VoiceRecorder, VOICE_AVAILABLE
            if VOICE_AVAILABLE:
                from voicetrack.config import VOSK_MODEL_PATH
                self._voice_recorder = VoiceRecorder(
                    VOSK_MODEL_PATH, self._on_voice_result)
                if not self._voice_recorder.is_available():
                    self._mic_btn.configure(state="disabled")
                    self._voice_status.configure(
                        text="(voice packages missing)")
            else:
                self._mic_btn.configure(state="disabled")
                self._voice_status.configure(text="(voice not available)")
        except Exception:
            self._mic_btn.configure(state="disabled")
            self._voice_status.configure(text="(voice not available)")

    def _toggle_mic(self):
        if self._voice_recorder is None:
            return
        if not self._recording:
            self._voice_recorder.start()
            self._recording = True
            self._mic_btn.configure(
                text="Stop",
                fg_color=C("danger_bg"), text_color=C("danger"))
        else:
            self._voice_recorder.stop()
            self._recording = False
            self._mic_btn.configure(
                text="Mic",
                fg_color=C("surface2"), text_color=C("text2"))

    def _on_voice_result(self, text: str):
        def _u():
            self._input_box.delete(0, "end")
            self._input_box.insert(0, text)
            self._process_input()
        self.after(0, _u)

    # ── Spinner ───────────────────────────────────────────

    def _start_spinner(self):
        _f = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]
        self._spinner_active = True
        self._spinner_tick = 0

        def _tick():
            if not self._spinner_active:
                return
            self._spinner_label.configure(
                text=f"{_f[self._spinner_tick % len(_f)]}  Processing…")
            self._spinner_tick += 1
            self.after(80, _tick)
        _tick()

    def _stop_spinner(self):
        self._spinner_active = False
        self._spinner_label.configure(text="")

    # ── Process & auto-save ───────────────────────────────

    def _process_input(self):
        raw = self._input_box.get().strip()
        if not raw:
            return
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        if not lines:
            return

        self._process_btn.configure(state="disabled")
        self._start_spinner()
        self._error_label.configure(text="")
        for w in self._preview_container.winfo_children():
            w.destroy()

        def _run():
            results, errors = [], []
            for line in lines:
                res = extractor.extract(line)
                if "error" in res:
                    errors.append(res["error"])
                else:
                    results.append(res)
            self.after(0, lambda: self._on_extract_done(results, errors))

        threading.Thread(target=_run, daemon=True).start()

    def _on_extract_done(self, results: list, errors: list):
        self._process_btn.configure(state="normal")
        self._stop_spinner()

        all_txs = []
        finance_plans = []
        for res in results:
            if res.get("intent") in {
                "loan_given",
                "loan_taken",
                "loan_repayment_received",
                "loan_repayment_made",
                "loan_clear",
                "shared_expense",
            }:
                finance_plans.append(res)
            else:
                all_txs.extend(extractor.normalize_transactions(res))

        if errors and not all_txs and not finance_plans:
            self._error_label.configure(text=errors[0])
            return
        if not all_txs and not finance_plans:
            self._error_label.configure(
                text="Could not parse any transaction.")
            return

        inserted_txs = []
        for tx in all_txs:
            tx_id = db.insert_transaction(tx)
            inserted_txs.append(db.get_transaction(tx_id))
        for plan in finance_plans:
            try:
                inserted_txs.extend(db.apply_finance_plan(plan))
            except ValueError as exc:
                errors.append(str(exc))

        count = len(inserted_txs)
        if count == 0:
            self._error_label.configure(
                text=errors[0] if errors else "Could not save this transaction."
            )
            return

        # Success card
        scard = ctk.CTkFrame(self._preview_container,
                             fg_color=C("success_bg"),
                             corner_radius=12,
                             border_width=1,
                             border_color=C("success"))
        scard.pack(fill="x", pady=6)

        inner = ctk.CTkFrame(scard, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=14)

        ic = ctk.CTkFrame(inner, fg_color=C("success"),
                          corner_radius=20, width=36, height=36)
        ic.pack(side="left", padx=(0, 14))
        ic.pack_propagate(False)
        _lbl(ic, "✓", size=16, weight="bold",
             color="#ffffff").pack(expand=True)

        info = ctk.CTkFrame(inner, fg_color="transparent")
        info.pack(side="left", fill="x", expand=True)

        msg = (f"Successfully added {count} "
               f"transaction{'s' if count > 1 else ''}!")
        if errors:
            msg += f"  ({len(errors)} line(s) skipped)"
        _lbl(info, msg, size=13, weight="bold",
             color=C("success")).pack(anchor="w")

        detail = "  ·  ".join(
            f"{'+'if t['type']=='income' else '-'}"
            f"{t['amount']:,.0f}  {t.get('category','')}"
            for t in inserted_txs
        )
        _lbl(info, detail, size=11, color=C("text2")).pack(
            anchor="w", pady=(2, 0))

        self._input_box.delete(0, "end")
        self.after(4000, scard.destroy)

    # ── Reports ───────────────────────────────────────────

    def _build_loans_panel(self) -> ctk.CTkFrame:
        panel = ctk.CTkFrame(self._content, fg_color="transparent")
        hdr = ctk.CTkFrame(panel, fg_color="transparent")
        hdr.pack(fill="x", padx=28, pady=(22, 0))
        _lbl(hdr, "Loan Management", size=22, weight="bold").pack(side="left")
        _btn(hdr, "Refresh", command=self._refresh_loans,
             width=100, height=32, style="ghost").pack(side="right")

        self._loans_scroll = ctk.CTkScrollableFrame(
            panel, fg_color="transparent",
            scrollbar_button_color=C("border"),
            scrollbar_button_hover_color=C("surface3"))
        self._loans_scroll.pack(fill="both", expand=True, padx=28, pady=14)
        return panel

    def _refresh_loans(self):
        if not hasattr(self, "_loans_scroll"):
            return
        for w in self._loans_scroll.winfo_children():
            w.destroy()

        accounts = db.get_loan_accounts()
        if not accounts:
            _lbl(self._loans_scroll, "No loan accounts yet",
                 color=C("text3")).pack(pady=40)
            return

        for account in accounts:
            card = _card(self._loans_scroll)
            card.pack(fill="x", pady=6)
            top = ctk.CTkFrame(card, fg_color="transparent")
            top.pack(fill="x", padx=16, pady=(14, 4))
            _lbl(top, account["person_name"], size=15,
                 weight="bold").pack(side="left")
            status_color = C("success") if account["status"] == "Paid" else C("warning")
            _lbl(top, account["status"], size=12, weight="bold",
                 color=status_color).pack(side="right")

            loan_label = "Money Owed To Me" if account["loan_type"] == "owed_to_me" else "Money I Owe"
            body = ctk.CTkFrame(card, fg_color="transparent")
            body.pack(fill="x", padx=16, pady=(0, 12))
            _lbl(body, loan_label, size=11, color=C("text2")).pack(anchor="w")
            amount_color = C("income_fg") if account["loan_type"] == "owed_to_me" else C("expense_fg")
            _lbl(body, f"Outstanding Balance: PKR {account['current_balance']:,.0f}",
                 size=18, weight="bold", color=amount_color).pack(anchor="w", pady=(2, 0))
            _lbl(body, f"Last activity: {account['last_activity']}",
                 size=10, color=C("text3")).pack(anchor="w", pady=(2, 0))

            actions = ctk.CTkFrame(card, fg_color="transparent")
            actions.pack(fill="x", padx=16, pady=(0, 12))
            is_settled = (
                account["status"] == "Paid"
                or round(float(account["current_balance"]), 2) == 0
            )
            if is_settled:
                _btn(actions, "Remove",
                     command=lambda aid=account["id"]: self._remove_loan(aid),
                     width=90, height=30, style="danger").pack(side="right")
            else:
                _btn(actions, "Mark as Paid",
                     command=lambda aid=account["id"]: self._settle_loan(aid),
                     width=120, height=30, style="primary").pack(side="right")

    def _settle_loan(self, account_id: int):
        try:
            db.settle_loan_account(account_id)
        except ValueError as exc:
            messagebox.showerror("VoiceTrack", str(exc))
        self._refresh_loans()
        self._refresh_dashboard()

    def _remove_loan(self, account_id: int):
        if not messagebox.askyesno(
            "Remove loan",
            "Remove this settled loan from the list? Its cash history is kept.",
        ):
            return
        try:
            db.delete_loan_account(account_id)
        except ValueError as exc:
            messagebox.showerror("VoiceTrack", str(exc))
        self._refresh_loans()
        self._refresh_dashboard()

    def _build_shared_panel(self) -> ctk.CTkFrame:
        panel = ctk.CTkFrame(self._content, fg_color="transparent")
        hdr = ctk.CTkFrame(panel, fg_color="transparent")
        hdr.pack(fill="x", padx=28, pady=(22, 0))
        _lbl(hdr, "Shared Expenses", size=22, weight="bold").pack(side="left")
        _btn(hdr, "Refresh", command=self._refresh_shared,
             width=100, height=32, style="ghost").pack(side="right")

        self._shared_scroll = ctk.CTkScrollableFrame(
            panel, fg_color="transparent",
            scrollbar_button_color=C("border"),
            scrollbar_button_hover_color=C("surface3"))
        self._shared_scroll.pack(fill="both", expand=True, padx=28, pady=14)
        return panel

    def _refresh_shared(self):
        if not hasattr(self, "_shared_scroll"):
            return
        for w in self._shared_scroll.winfo_children():
            w.destroy()

        groups = db.get_shared_expense_groups()
        if not groups:
            _lbl(self._shared_scroll, "No shared expenses yet",
                 color=C("text3")).pack(pady=40)
            return

        for group in groups:
            card = _card(self._shared_scroll)
            card.pack(fill="x", pady=6)
            top = ctk.CTkFrame(card, fg_color="transparent")
            top.pack(fill="x", padx=16, pady=(14, 4))
            _lbl(top, group.get("description") or "Shared expense",
                 size=15, weight="bold").pack(side="left")
            _lbl(top, group["date"], size=11,
                 color=C("text3")).pack(side="right")

            body = ctk.CTkFrame(card, fg_color="transparent")
            body.pack(fill="x", padx=16, pady=(0, 12))
            summary = (
                f"Total paid PKR {group['total_paid']:,.0f}  |  "
                f"My share PKR {group['my_share']:,.0f}  |  "
                f"Receivable PKR {group['others_share']:,.0f}"
            )
            _lbl(body, summary, size=12, color=C("text2")).pack(anchor="w")
            participants = db.get_shared_expense_participants(group["id"])
            if participants:
                text = "  |  ".join(
                    f"{p['person_name']}: PKR {p['share_amount']:,.0f}"
                    f" ({p['status']})"
                    for p in participants
                )
                _lbl(body, text, size=11, color=C("text3")).pack(anchor="w", pady=(4, 0))

            actions = ctk.CTkFrame(card, fg_color="transparent")
            actions.pack(fill="x", padx=16, pady=(0, 12))
            outstanding = db.shared_group_outstanding(group["id"])
            if outstanding != 0:
                _btn(actions, "Mark Settled",
                     command=lambda gid=group["id"]: self._settle_shared(gid),
                     width=120, height=30, style="primary").pack(side="right")
            else:
                _btn(actions, "Remove",
                     command=lambda gid=group["id"]: self._remove_shared(gid),
                     width=90, height=30, style="danger").pack(side="right")

    def _settle_shared(self, group_id: int):
        try:
            db.settle_shared_group(group_id)
        except ValueError as exc:
            messagebox.showerror("VoiceTrack", str(exc))
        self._refresh_shared()
        self._refresh_loans()
        self._refresh_dashboard()

    def _remove_shared(self, group_id: int):
        if not messagebox.askyesno(
            "Remove shared expense",
            "Remove this settled shared expense from the list? Your expense history is kept.",
        ):
            return
        try:
            db.delete_shared_group(group_id)
        except ValueError as exc:
            messagebox.showerror("VoiceTrack", str(exc))
        self._refresh_shared()
        self._refresh_dashboard()

    def _build_assistant_panel(self) -> ctk.CTkFrame:
        panel = ctk.CTkFrame(self._content, fg_color="transparent")

        hdr = ctk.CTkFrame(panel, fg_color="transparent")
        hdr.pack(fill="x", padx=28, pady=(22, 0))
        _lbl(hdr, "💬  Assistant", size=22, weight="bold").pack(side="left")
        _lbl(hdr, "Ask about your money — answers come straight from your data",
             size=11, color=C("text3")).pack(side="left", padx=(12, 0), pady=(8, 0))

        self._chat_scroll = ctk.CTkScrollableFrame(
            panel, fg_color="transparent",
            scrollbar_button_color=C("border"),
            scrollbar_button_hover_color=C("surface3"))
        self._chat_scroll.pack(fill="both", expand=True, padx=28, pady=14)

        # Quick-question chips
        chips = ctk.CTkFrame(panel, fg_color="transparent")
        chips.pack(fill="x", padx=28, pady=(0, 6))
        for text in ["Who owes me money?", "How much loan do I need to pay?",
                     "How much did I spend on food this month?", "What is my net worth?"]:
            _btn(chips, text, command=lambda t=text: self._send_chat(t),
                 height=30, width=len(text) * 7 + 24, style="surface").pack(side="left", padx=(0, 6))

        # Input row
        row = ctk.CTkFrame(panel, fg_color="transparent")
        row.pack(fill="x", padx=28, pady=(0, 20))
        self._input_chat = ctk.CTkEntry(
            row, placeholder_text="Ask a question…", height=40,
            fg_color=C("surface2"), border_color=C("border"))
        self._input_chat.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._input_chat.bind("<Return>", lambda _e: self._send_chat())
        _btn(row, "Send", command=self._send_chat, width=90, height=40).pack(side="left")

        self._add_chat_bubble(
            "Hi! Ask me things like \"how much did I spend on food this month?\", "
            "\"who owes me money?\", or \"how much loan do I need to pay?\".",
            sender="bot")
        return panel

    def _add_chat_bubble(self, text: str, sender: str):
        rowf = ctk.CTkFrame(self._chat_scroll, fg_color="transparent")
        rowf.pack(fill="x", pady=4)
        is_user = sender == "user"
        bubble = ctk.CTkFrame(
            rowf, corner_radius=12,
            fg_color=C("accent_dim") if is_user else C("surface2"))
        bubble.pack(side="right" if is_user else "left", padx=4)
        _lbl(bubble, text, size=12,
             color=C("text") if is_user else C("text2"),
             justify="left", anchor="w", wraplength=560).pack(
                 padx=14, pady=10, anchor="w")
        # Scroll to the newest message.
        try:
            self.update_idletasks()
            self._chat_scroll._parent_canvas.yview_moveto(1.0)
        except Exception:
            pass

    def _send_chat(self, text: str | None = None):
        question = text if text is not None else self._input_chat.get().strip()
        if not question:
            return
        self._add_chat_bubble(question, sender="user")
        self._input_chat.delete(0, "end")
        try:
            reply = assistant.answer(question)
        except Exception as exc:
            reply = f"Something went wrong reading your data: {exc}"
        self._add_chat_bubble(reply, sender="bot")

    def _build_reports_panel(self) -> ctk.CTkFrame:
        panel = ctk.CTkFrame(self._content, fg_color="transparent")

        hdr = ctk.CTkFrame(panel, fg_color="transparent")
        hdr.pack(fill="x", padx=28, pady=(22, 0))
        _lbl(hdr, "Reports", size=22, weight="bold").pack(side="left")
        for text in ["This Year", "Last 3 Months", "This Month"]:
            _btn(hdr, text, width=105, height=30,
                 style="ghost").pack(side="right", padx=(6, 0))
        _btn(hdr, "⬇  Export CSV", command=self._export_csv,
             width=120, height=32, style="ghost").pack(side="right")

        self._report_scroll = ctk.CTkScrollableFrame(
            panel, fg_color="transparent",
            scrollbar_button_color=C("border"),
            scrollbar_button_hover_color=C("surface3"))
        self._report_scroll.pack(fill="both", expand=True,
                                 padx=28, pady=14)
        return panel

    def _refresh_reports(self):
        for w in self._report_scroll.winfo_children():
            w.destroy()
        monthly = db.get_monthly_totals(6)
        categories = db.get_category_totals("expense")

        row = ctk.CTkFrame(self._report_scroll, fg_color="transparent")
        row.pack(fill="both", expand=True)

        left = _card(row)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8), pady=8)
        _lbl(left, "Spending by category", size=13,
             weight="bold").pack(anchor="w", padx=18, pady=(16, 0))
        total = sum(float(c["total"]) for c in categories)
        _lbl(left, f"This period · total PKR {total:,.0f}", size=10,
             color=C("text3")).pack(anchor="w", padx=18, pady=(0, 8))
        donut = _embed_figure(charts.report_spending_donut(categories), left)
        donut.pack(fill="both", expand=True, padx=12, pady=(0, 14))

        right = _card(row)
        right.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        _lbl(right, "Income vs Expense", size=13,
             weight="bold").pack(anchor="w", padx=18, pady=(16, 0))
        _lbl(right, "Last 6 months", size=10,
             color=C("text3")).pack(anchor="w", padx=18, pady=(0, 8))
        bars = _embed_figure(charts.report_income_expense_bars(monthly), right)
        bars.pack(fill="both", expand=True, padx=12, pady=(0, 14))

        footer = ctk.CTkFrame(self._report_scroll, fg_color="transparent")
        footer.pack(fill="x", pady=(4, 8))
        _btn(footer, "Export to CSV", command=self._export_csv,
             width=130, height=34, style="ghost").pack(side="right")
        return
        for fn, title in [
            (lambda: charts.balance_trend(monthly), "Balance Trend — Last 6 Months"),
            (lambda: charts.monthly_income_vs_expense(monthly),
             "Income vs Expense — Last 6 Months"),
        ]:
            c = _card(self._report_scroll)
            c.pack(fill="x", pady=8)
            _lbl(c, title, size=13, weight="bold").pack(
                anchor="w", padx=18, pady=(16, 4))
            ctk.CTkFrame(c, fg_color=C("border"), height=1).pack(
                fill="x", padx=18, pady=(0, 8))
            w = _embed_figure(fn(), c)
            w.pack(fill="x", padx=12, pady=(0, 14))

    def _export_csv(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            title="Export Transactions",
        )
        if not path:
            return
        rows = db.get_transactions(limit=100000)
        fields = ["id","type","amount","category","description",
                  "date","time","confidence","created_at"]
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)


def run_app():
    app = VoiceTrackApp()
    app.mainloop()
