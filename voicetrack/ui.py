"""VoiceTrack desktop UI — CustomTkinter app."""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import filedialog
import csv
import datetime

import customtkinter as ctk
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

import voicetrack.db as db
import voicetrack.extractor as extractor
import voicetrack.charts as charts
from voicetrack.db import CATEGORIES

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ── panel names ──────────────────────────────
_PANEL_DASHBOARD    = "Dashboard"
_PANEL_ADD          = "Add Entry"
_PANEL_HISTORY      = "History"
_PANEL_REPORTS      = "Reports"

PAGE_SIZE = 50

# ── category accent colours ──────────────────
CAT_COLORS = {
    "Food & Groceries": "#f59e0b",
    "Transport": "#3b82f6",
    "Utilities": "#8b5cf6",
    "Health": "#10b981",
    "Shopping": "#ec4899",
    "Education": "#a78bfa",
    "Rent": "#fb923c",
    "Salary": "#22c55e",
    "Freelance": "#06b6d4",
    "Entertainment": "#f97316",
    "Other": "#94a3b8",
}

TYPE_COLORS = {"income": "#22c55e", "expense": "#ef4444"}


def _embed_figure(fig, parent):
    canvas = FigureCanvasTkAgg(fig, master=parent)
    canvas.draw()
    return canvas.get_tk_widget()


# ─────────────────────────────────────────────────────────
#  Helper widgets
# ─────────────────────────────────────────────────────────

def _card(parent, **kw) -> ctk.CTkFrame:
    return ctk.CTkFrame(parent, corner_radius=12,
                        fg_color="#1e2130", **kw)


def _label(parent, text, size=13, weight="normal", color=None, **kw):
    font = ctk.CTkFont(size=size, weight=weight)
    kw2 = {"text_color": color} if color else {}
    return ctk.CTkLabel(parent, text=text, font=font, **kw2, **kw)


# ─────────────────────────────────────────────────────────
#  Main app
# ─────────────────────────────────────────────────────────

class VoiceTrackApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("VoiceTrack")
        self.minsize(960, 620)
        self.geometry("1160x700")
        self.configure(fg_color="#12141c")

        db.init_db()

        self._panels: dict[str, ctk.CTkFrame] = {}
        self._tx_offset = 0
        self._tx_filters: dict = {}
        self._voice_recorder = None
        self._recording = False
        self._spinner_active = False
        self._spinner_frame = 0
        self._dash_period = "month"  # today / week / month / all

        self._build_sidebar()
        self._build_content_area()
        self._show_panel(_PANEL_DASHBOARD)

    # ── Sidebar ──────────────────────────────────────────

    def _build_sidebar(self):
        sb = ctk.CTkFrame(self, width=190, corner_radius=0,
                          fg_color="#161824")
        sb.pack(side="left", fill="y")
        sb.pack_propagate(False)

        # Logo area
        logo_frame = ctk.CTkFrame(sb, fg_color="#1e2130", corner_radius=14,
                                  width=48, height=48)
        logo_frame.pack(pady=(24, 6))
        logo_frame.pack_propagate(False)
        _label(logo_frame, "🎙", size=22).pack(expand=True)

        _label(sb, "VoiceTrack", size=16, weight="bold").pack(pady=(0, 20))

        self._nav_btns: dict[str, ctk.CTkButton] = {}
        nav_items = [
            ("Dashboard", "📊", _PANEL_DASHBOARD),
            ("Add Entry",  "➕", _PANEL_ADD),
            ("History",    "📋", _PANEL_HISTORY),
            ("Reports",    "📈", _PANEL_REPORTS),
        ]
        for label, icon, panel in nav_items:
            btn = ctk.CTkButton(
                sb, text=f"  {icon}  {label}",
                anchor="w", corner_radius=10,
                fg_color="transparent", hover_color="#252840",
                text_color="#8892a4",
                font=ctk.CTkFont(size=13),
                command=lambda p=panel: self._show_panel(p),
            )
            btn.pack(fill="x", padx=10, pady=2)
            self._nav_btns[panel] = btn

        # Settings at bottom
        ctk.CTkFrame(sb, fg_color="#2a2d3e", height=1).pack(
            fill="x", padx=14, side="bottom", pady=(0, 8))
        ctk.CTkButton(
            sb, text="  ⚙️  Settings", anchor="w",
            corner_radius=10, fg_color="transparent",
            hover_color="#252840", text_color="#8892a4",
            font=ctk.CTkFont(size=13),
        ).pack(fill="x", padx=10, pady=4, side="bottom")

    def _set_active_nav(self, panel: str):
        for p, btn in self._nav_btns.items():
            if p == panel:
                btn.configure(fg_color="#252840", text_color="#ffffff")
            else:
                btn.configure(fg_color="transparent", text_color="#8892a4")

    # ── Content area ─────────────────────────────────────

    def _build_content_area(self):
        self._content = ctk.CTkFrame(self, corner_radius=0,
                                     fg_color="#12141c")
        self._content.pack(side="left", fill="both", expand=True)

        self._panels[_PANEL_DASHBOARD] = self._build_dashboard_panel()
        self._panels[_PANEL_ADD]       = self._build_add_panel()
        self._panels[_PANEL_HISTORY]   = self._build_history_panel()
        self._panels[_PANEL_REPORTS]   = self._build_reports_panel()

    def _show_panel(self, name: str):
        for p in self._panels.values():
            p.pack_forget()
        self._panels[name].pack(fill="both", expand=True)
        self._set_active_nav(name)
        if name == _PANEL_DASHBOARD:
            self._refresh_dashboard()
        elif name == _PANEL_HISTORY:
            self._tx_offset = 0
            self._refresh_history()
        elif name == _PANEL_REPORTS:
            self._refresh_reports()

    # ── Dashboard ────────────────────────────────────────

    def _build_dashboard_panel(self) -> ctk.CTkFrame:
        panel = ctk.CTkFrame(self._content, fg_color="transparent")

        # Header
        hdr = ctk.CTkFrame(panel, fg_color="transparent")
        hdr.pack(fill="x", padx=24, pady=(20, 0))
        _label(hdr, "Dashboard", size=20, weight="bold").pack(side="left")
        self._date_label = _label(hdr, "", size=12, color="#8892a4")
        self._date_label.pack(side="right")

        # Summary cards
        cards_frame = ctk.CTkFrame(panel, fg_color="transparent")
        cards_frame.pack(fill="x", padx=24, pady=16)

        self._sum_labels: dict[str, ctk.CTkLabel] = {}
        card_defs = [
            ("income_month",   "Income this month",    "⬆", "#22c55e", "#0d2b1a"),
            ("expense_month",  "Expenses this month",  "⬇", "#ef4444", "#2b0d0d"),
            ("balance",        "Net balance",           "💳", "#3b82f6", "#0d1a2b"),
        ]
        for key, title, icon, color, icon_bg in card_defs:
            c = _card(cards_frame)
            c.pack(side="left", expand=True, fill="both", padx=6)

            top = ctk.CTkFrame(c, fg_color="transparent")
            top.pack(fill="x", padx=16, pady=(14, 4))
            _label(top, title, size=11, color="#8892a4").pack(side="left")

            icon_frame = ctk.CTkFrame(top, fg_color=icon_bg,
                                      corner_radius=8, width=28, height=28)
            icon_frame.pack(side="right")
            icon_frame.pack_propagate(False)
            _label(icon_frame, icon, size=13).pack(expand=True)

            lbl = _label(c, "PKR 0", size=20, weight="bold", color=color)
            lbl.pack(anchor="w", padx=16, pady=(0, 14))
            self._sum_labels[key] = lbl

        # Period tabs
        tab_frame = ctk.CTkFrame(panel, fg_color="transparent")
        tab_frame.pack(fill="x", padx=24, pady=(0, 12))
        self._period_btns: dict[str, ctk.CTkButton] = {}
        for label, key in [("Today","today"),("This Week","week"),
                            ("This Month","month"),("All Time","all")]:
            b = ctk.CTkButton(
                tab_frame, text=label, width=90, height=28,
                corner_radius=8, fg_color="transparent",
                hover_color="#252840", text_color="#8892a4",
                font=ctk.CTkFont(size=12),
                command=lambda k=key: self._set_period(k),
            )
            b.pack(side="left", padx=3)
            self._period_btns[key] = b

        # Body: spending chart + recent transactions
        body = ctk.CTkFrame(panel, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=24, pady=(0, 16))

        # Left — spending by category
        left = _card(body)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))
        _label(left, "Spending by category", size=13,
               weight="bold").pack(anchor="w", padx=16, pady=(14, 10))
        self._cat_chart_frame = ctk.CTkScrollableFrame(
            left, fg_color="transparent")
        self._cat_chart_frame.pack(fill="both", expand=True, padx=12,
                                   pady=(0, 12))

        # Right — recent transactions
        right = _card(body)
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))
        _label(right, "Recent transactions", size=13,
               weight="bold").pack(anchor="w", padx=16, pady=(14, 10))
        self._recent_frame = ctk.CTkScrollableFrame(
            right, fg_color="transparent")
        self._recent_frame.pack(fill="both", expand=True, padx=4,
                                pady=(0, 12))

        return panel

    def _set_period(self, key: str):
        self._dash_period = key
        self._refresh_dashboard()

    def _refresh_dashboard(self):
        today = datetime.date.today()
        self._date_label.configure(
            text=today.strftime("%A, %d %B %Y"))

        # active period button highlight
        for k, b in self._period_btns.items():
            if k == self._dash_period:
                b.configure(fg_color="#3b82f6", text_color="#ffffff")
            else:
                b.configure(fg_color="transparent", text_color="#8892a4")

        all_txs = db.get_transactions(limit=100000)

        def _in_period(tx):
            d_str = tx.get("date", "")
            if not d_str:
                return True
            try:
                d = datetime.date.fromisoformat(d_str)
            except ValueError:
                return True
            if self._dash_period == "today":
                return d == today
            elif self._dash_period == "week":
                return (today - d).days <= 6
            elif self._dash_period == "month":
                return d.year == today.year and d.month == today.month
            return True  # all

        txs = [t for t in all_txs if _in_period(t)]

        income  = sum(t["amount"] for t in txs if t["type"] == "income")
        expense = sum(t["amount"] for t in txs if t["type"] == "expense")
        balance = income - expense

        self._sum_labels["income_month"].configure(
            text=f"PKR {income:,.0f}")
        self._sum_labels["expense_month"].configure(
            text=f"PKR {expense:,.0f}")
        bal_color = "#22c55e" if balance >= 0 else "#ef4444"
        self._sum_labels["balance"].configure(
            text=f"PKR {balance:,.0f}", text_color=bal_color)

        self._render_cat_bars(txs)
        self._render_recent(txs)

    def _render_cat_bars(self, txs):
        for w in self._cat_chart_frame.winfo_children():
            w.destroy()

        totals: dict[str, float] = {}
        for t in txs:
            if t["type"] == "expense":
                cat = t.get("category", "Other")
                totals[cat] = totals.get(cat, 0) + t["amount"]

        if not totals:
            _label(self._cat_chart_frame,
                   "No expense data", color="#8892a4").pack(pady=20)
            return

        max_val = max(totals.values())
        for cat, val in sorted(totals.items(), key=lambda x: -x[1]):
            row = ctk.CTkFrame(self._cat_chart_frame, fg_color="transparent")
            row.pack(fill="x", pady=4)
            _label(row, cat, size=12, color="#c8cdd8",
                   width=90, anchor="e").pack(side="left", padx=(0, 8))

            bar_bg = ctk.CTkFrame(row, fg_color="#252840",
                                  corner_radius=4, height=10)
            bar_bg.pack(side="left", fill="x", expand=True)
            bar_bg.pack_propagate(False)

            color = CAT_COLORS.get(cat, "#94a3b8")
            ratio = val / max_val if max_val else 0
            bar_bg.update_idletasks()

            inner = ctk.CTkFrame(bar_bg, fg_color=color,
                                 corner_radius=4, height=10)
            inner.place(relx=0, rely=0, relwidth=max(ratio, 0.03), relheight=1)

            _label(row, f"{val:,.0f}", size=11,
                   color="#8892a4").pack(side="left", padx=8)

    def _render_recent(self, txs):
        for w in self._recent_frame.winfo_children():
            w.destroy()

        recent = sorted(
            txs, key=lambda t: (t.get("date") or "", t.get("created_at") or ""),
            reverse=True
        )[:10]

        if not recent:
            _label(self._recent_frame,
                   "No transactions", color="#8892a4").pack(pady=20)
            return

        for t in recent:
            row = ctk.CTkFrame(self._recent_frame, fg_color="transparent")
            row.pack(fill="x", pady=5, padx=8)

            # icon circle
            cat = t.get("category", "Other")
            color = CAT_COLORS.get(cat, "#94a3b8")
            ic = ctk.CTkFrame(row, fg_color="#252840",
                              corner_radius=20, width=36, height=36)
            ic.pack(side="left", padx=(0, 10))
            ic.pack_propagate(False)
            _label(ic, cat[0], size=13, weight="bold",
                   color=color).pack(expand=True)

            info = ctk.CTkFrame(row, fg_color="transparent")
            info.pack(side="left", fill="x", expand=True)
            desc = str(t.get("description", ""))[:28] or cat
            _label(info, desc, size=12, weight="bold").pack(anchor="w")
            date_str = t.get("date", "")
            time_str = t.get("time", "") or ""
            _label(info, f"{date_str}  {time_str}".strip(),
                   size=10, color="#8892a4").pack(anchor="w")

            sign = "+" if t["type"] == "income" else "-"
            amt_color = TYPE_COLORS.get(t["type"], "#fff")
            _label(row, f"{sign}{t['amount']:,.0f}",
                   size=13, weight="bold", color=amt_color).pack(
                side="right", padx=4)

    # ── History panel ─────────────────────────────────────

    def _build_history_panel(self) -> ctk.CTkFrame:
        panel = ctk.CTkFrame(self._content, fg_color="transparent")

        hdr = ctk.CTkFrame(panel, fg_color="transparent")
        hdr.pack(fill="x", padx=24, pady=(20, 12))
        _label(hdr, "History", size=20, weight="bold").pack(side="left")

        # Search
        search_frame = ctk.CTkFrame(panel, fg_color="#1e2130",
                                    corner_radius=10)
        search_frame.pack(fill="x", padx=24, pady=(0, 10))
        _label(search_frame, "🔍", size=13,
               color="#8892a4").pack(side="left", padx=10)
        self._search_var = ctk.StringVar()
        self._search_var.trace_add("write",
                                   lambda *_: self._refresh_history())
        ctk.CTkEntry(
            search_frame, placeholder_text="Search transactions…",
            textvariable=self._search_var,
            border_width=0, fg_color="transparent",
            font=ctk.CTkFont(size=13),
        ).pack(fill="x", pady=6, padx=(0, 10))

        # Filter row
        filter_row = ctk.CTkFrame(panel, fg_color="transparent")
        filter_row.pack(fill="x", padx=24, pady=(0, 8))

        self._type_filter_btns: dict[str, ctk.CTkButton] = {}
        for label, val in [("All",""), ("Income","income"), ("Expense","expense")]:
            b = ctk.CTkButton(
                filter_row, text=label, width=72, height=28,
                corner_radius=8, fg_color="transparent",
                hover_color="#252840", text_color="#8892a4",
                font=ctk.CTkFont(size=12),
                command=lambda v=val: self._set_type_filter(v),
            )
            b.pack(side="left", padx=2)
            self._type_filter_btns[val] = b

        self._filter_cat = ctk.CTkOptionMenu(
            filter_row, values=["All categories"] + CATEGORIES,
            width=150, height=28, corner_radius=8,
            fg_color="#1e2130", button_color="#1e2130",
            command=lambda _: self._refresh_history(),
        )
        self._filter_cat.pack(side="left", padx=8)

        _label(filter_row, "From:", size=12,
               color="#8892a4").pack(side="left", padx=(8, 4))
        self._filter_from = ctk.CTkEntry(
            filter_row, width=110, height=28,
            placeholder_text="YYYY-MM-DD",
            fg_color="#1e2130", border_color="#2a2d3e")
        self._filter_from.pack(side="left", padx=(0, 4))

        _label(filter_row, "–", size=12,
               color="#8892a4").pack(side="left", padx=2)
        self._filter_to = ctk.CTkEntry(
            filter_row, width=110, height=28,
            placeholder_text="YYYY-MM-DD",
            fg_color="#1e2130", border_color="#2a2d3e")
        self._filter_to.pack(side="left", padx=(4, 8))
        ctk.CTkButton(
            filter_row, text="Apply", width=60, height=28,
            corner_radius=8, command=self._apply_hist_filters,
        ).pack(side="left", padx=2)

        self._hist_type_filter = ""
        # highlight "All" button as default without triggering a refresh
        for v, b in self._type_filter_btns.items():
            if v == "":
                b.configure(fg_color="#3b82f6", text_color="#fff")
            else:
                b.configure(fg_color="transparent", text_color="#8892a4")

        # Table header
        col_defs = [("DATE",90),("TYPE",80),("CATEGORY",120),
                    ("DESCRIPTION",0),("AMOUNT",90),("",36)]
        th = ctk.CTkFrame(panel, fg_color="#1e2130", corner_radius=0,
                          height=32)
        th.pack(fill="x", padx=24)
        th.pack_propagate(False)
        for col, w in col_defs:
            kw = {"width": w} if w else {}
            _label(th, col, size=10, color="#8892a4",
                   anchor="w", **kw).pack(
                side="left", padx=8, pady=6)
            if not w:
                th.pack_propagate(False)

        # Scrollable rows
        self._hist_scroll = ctk.CTkScrollableFrame(
            panel, fg_color="transparent")
        self._hist_scroll.pack(fill="both", expand=True, padx=24, pady=4)

        # Pager
        pager = ctk.CTkFrame(panel, fg_color="transparent")
        pager.pack(fill="x", padx=24, pady=(0, 12))
        ctk.CTkButton(pager, text="← Prev", width=80, height=28,
                      command=self._tx_prev).pack(side="left", padx=4)
        self._page_label = _label(pager, "Page 1", color="#8892a4")
        self._page_label.pack(side="left", padx=8)
        ctk.CTkButton(pager, text="Next →", width=80, height=28,
                      command=self._tx_next).pack(side="left", padx=4)

        return panel

    def _set_type_filter(self, val: str):
        self._hist_type_filter = val
        for v, b in self._type_filter_btns.items():
            if v == val:
                b.configure(fg_color="#3b82f6", text_color="#fff")
            else:
                b.configure(fg_color="transparent", text_color="#8892a4")
        self._tx_offset = 0
        self._refresh_history()

    def _apply_hist_filters(self):
        self._tx_offset = 0
        self._refresh_history()

    def _refresh_history(self):
        for w in self._hist_scroll.winfo_children():
            w.destroy()

        cat = self._filter_cat.get()
        rows = db.get_transactions(
            limit=PAGE_SIZE,
            offset=self._tx_offset,
            category=None if cat == "All categories" else cat,
            tx_type=self._hist_type_filter or None,
            date_from=self._filter_from.get() or None,
            date_to=self._filter_to.get() or None,
        )

        search = self._search_var.get().lower()
        if search:
            rows = [r for r in rows if
                    search in str(r.get("description","")).lower() or
                    search in str(r.get("category","")).lower()]

        page = self._tx_offset // PAGE_SIZE + 1
        self._page_label.configure(text=f"Page {page}")

        if not rows:
            _label(self._hist_scroll,
                   "No transactions found", color="#8892a4").pack(pady=32)
            return

        for row in rows:
            self._add_hist_row(row)

    def _add_hist_row(self, row: dict):
        r = ctk.CTkFrame(self._hist_scroll, fg_color="transparent",
                         height=44)
        r.pack(fill="x", pady=1)
        r.pack_propagate(False)

        # DATE
        _label(r, row.get("date",""), size=12, color="#c8cdd8",
               width=90, anchor="w").pack(side="left", padx=8)

        # TYPE badge
        tx_type = row.get("type","")
        badge_color = "#052e16" if tx_type == "income" else "#450a0a"
        badge_text_color = "#22c55e" if tx_type == "income" else "#f87171"
        badge = ctk.CTkFrame(r, fg_color=badge_color,
                             corner_radius=6, width=68, height=22)
        badge.pack(side="left", padx=4)
        badge.pack_propagate(False)
        _label(badge, tx_type.capitalize(), size=11,
               weight="bold", color=badge_text_color).pack(expand=True)

        # CATEGORY (colored)
        cat = row.get("category","Other")
        cat_color = CAT_COLORS.get(cat, "#94a3b8")
        _label(r, cat, size=12, weight="bold", color=cat_color,
               width=110, anchor="w").pack(side="left", padx=8)

        # DESCRIPTION
        desc = str(row.get("description",""))[:36]
        _label(r, desc, size=12, color="#8892a4",
               anchor="w").pack(side="left", fill="x", expand=True, padx=4)

        # AMOUNT
        sign = "+" if tx_type == "income" else "-"
        amt_color = "#22c55e" if tx_type == "income" else "#ef4444"
        _label(r, f"{sign}{row.get('amount',0):,.0f}",
               size=13, weight="bold", color=amt_color,
               width=90, anchor="e").pack(side="left", padx=8)

        # Delete (trash icon)
        ctk.CTkButton(
            r, text="🗑", width=32, height=28,
            fg_color="transparent", hover_color="#3a1010",
            text_color="#ef4444", font=ctk.CTkFont(size=14),
            command=lambda rid=row["id"]: self._delete_tx(rid),
        ).pack(side="left", padx=4)

    def _delete_tx(self, tx_id: int):
        db.delete_transaction(tx_id)
        self._refresh_history()
        self._refresh_dashboard()

    def _tx_prev(self):
        if self._tx_offset >= PAGE_SIZE:
            self._tx_offset -= PAGE_SIZE
            self._refresh_history()

    def _tx_next(self):
        self._tx_offset += PAGE_SIZE
        self._refresh_history()

    # ── Add Entry panel ───────────────────────────────────

    def _build_add_panel(self) -> ctk.CTkFrame:
        panel = ctk.CTkFrame(self._content, fg_color="transparent")

        hdr = ctk.CTkFrame(panel, fg_color="transparent")
        hdr.pack(fill="x", padx=24, pady=(20, 0))
        _label(hdr, "Add Entry", size=20, weight="bold").pack(side="left")

        body = _card(panel)
        body.pack(fill="x", padx=24, pady=16)

        _label(body, "Describe your transaction",
               size=12, color="#8892a4").pack(anchor="w", padx=16, pady=(14, 4))

        self._input_box = ctk.CTkTextbox(
            body, height=80, corner_radius=10,
            fg_color="#12141c", border_color="#2a2d3e", border_width=1,
            font=ctk.CTkFont(size=13),
        )
        self._input_box.pack(fill="x", padx=16, pady=(0, 10))

        hint = ctk.CTkFrame(body, fg_color="transparent")
        hint.pack(fill="x", padx=16, pady=(0, 4))
        _label(hint, 'e.g. "spent 500 on groceries today"  or  "received 50000 salary"',
               size=11, color="#555e73").pack(side="left")
        _label(hint, "Ctrl+Enter to process",
               size=11, color="#555e73").pack(side="right")

        btn_row = ctk.CTkFrame(body, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(0, 16))

        self._process_btn = ctk.CTkButton(
            btn_row, text="▶  Process", width=130, height=36,
            corner_radius=10, font=ctk.CTkFont(size=13, weight="bold"),
            command=self._process_input,
        )
        self._process_btn.pack(side="left", padx=(0, 12))

        self._spinner_frame_widget = ctk.CTkFrame(
            btn_row, fg_color="transparent")
        self._spinner_frame_widget.pack(side="left")
        self._spinner_label = _label(
            self._spinner_frame_widget, "", size=13, color="#3b82f6")
        self._spinner_label.pack(side="left")

        self._mic_btn = ctk.CTkButton(
            btn_row, text="🎙  Microphone", width=130, height=36,
            corner_radius=10, fg_color="#1e2130",
            hover_color="#252840", text_color="#c8cdd8",
            command=self._toggle_mic,
        )
        self._mic_btn.pack(side="left", padx=8)

        self._voice_status = _label(btn_row, "", size=11, color="#8892a4")
        self._voice_status.pack(side="left")

        self._error_label = _label(panel, "", size=12, color="#f87171")
        self._error_label.pack(anchor="w", padx=24, pady=(0, 4))

        self._preview_container = ctk.CTkScrollableFrame(
            panel, fg_color="transparent")
        self._preview_container.pack(fill="both", expand=True,
                                     padx=24, pady=(0, 16))

        self._init_voice()
        self.bind("<Control-Return>", lambda _: self._process_input())
        return panel

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
                text="⏹  Stop", fg_color="#450a0a", text_color="#f87171")
        else:
            self._voice_recorder.stop()
            self._recording = False
            self._mic_btn.configure(
                text="🎙  Microphone", fg_color="#1e2130",
                text_color="#c8cdd8")

    def _on_voice_result(self, text: str):
        def _update():
            self._input_box.delete("1.0", "end")
            self._input_box.insert("1.0", text)
            self._process_input()
        self.after(0, _update)

    # ── Spinner ───────────────────────────────────────────

    def _start_spinner(self):
        _frames = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]
        self._spinner_active = True
        self._spinner_frame = 0

        def _tick():
            if not self._spinner_active:
                return
            self._spinner_label.configure(
                text=f"{_frames[self._spinner_frame % len(_frames)]}  Processing…")
            self._spinner_frame += 1
            self.after(80, _tick)
        _tick()

    def _stop_spinner(self):
        self._spinner_active = False
        self._spinner_label.configure(text="")

    # ── Process + auto-save ───────────────────────────────

    def _process_input(self):
        raw_text = self._input_box.get("1.0", "end").strip()
        if not raw_text:
            return
        lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
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
        for res in results:
            all_txs.extend(extractor.normalize_transactions(res))

        if errors and not all_txs:
            self._error_label.configure(text=errors[0])
            return
        if not all_txs:
            self._error_label.configure(
                text="Could not parse any transaction.")
            return

        for tx in all_txs:
            db.insert_transaction(tx)

        count = len(all_txs)
        msg = (f"✓  Successfully added {count} "
               f"transaction{'s' if count > 1 else ''}.")
        if errors:
            msg += f"  ({len(errors)} line(s) skipped.)"

        card = _card(self._preview_container)
        card.pack(fill="x", pady=6)

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=14)

        ic = ctk.CTkFrame(row, fg_color="#052e16", corner_radius=20,
                          width=36, height=36)
        ic.pack(side="left", padx=(0, 12))
        ic.pack_propagate(False)
        _label(ic, "✓", size=16, weight="bold",
               color="#22c55e").pack(expand=True)

        info = ctk.CTkFrame(row, fg_color="transparent")
        info.pack(side="left")
        _label(info, msg, size=13, weight="bold",
               color="#22c55e").pack(anchor="w")
        _label(info,
               "  ·  ".join(
                   f"{'+' if t['type']=='income' else '-'}"
                   f"{t['amount']:,.0f} {t.get('category','')}"
                   for t in all_txs),
               size=11, color="#8892a4").pack(anchor="w", pady=(2, 0))

        self._input_box.delete("1.0", "end")
        self.after(4000, card.destroy)

    # ── Reports panel ─────────────────────────────────────

    def _build_reports_panel(self) -> ctk.CTkFrame:
        panel = ctk.CTkFrame(self._content, fg_color="transparent")

        hdr = ctk.CTkFrame(panel, fg_color="transparent")
        hdr.pack(fill="x", padx=24, pady=(20, 0))
        _label(hdr, "Reports", size=20, weight="bold").pack(side="left")
        ctk.CTkButton(hdr, text="Export CSV", width=110, height=32,
                      command=self._export_csv).pack(side="right")

        self._report_scroll = ctk.CTkScrollableFrame(
            panel, fg_color="transparent")
        self._report_scroll.pack(fill="both", expand=True,
                                 padx=24, pady=12)
        return panel

    def _refresh_reports(self):
        for w in self._report_scroll.winfo_children():
            w.destroy()

        monthly = db.get_monthly_totals(6)

        for fig_fn, title in [
            (lambda: charts.balance_trend(monthly), "Balance Trend"),
            (lambda: charts.monthly_income_vs_expense(monthly),
             "Income vs Expense"),
        ]:
            c = _card(self._report_scroll)
            c.pack(fill="x", pady=8)
            _label(c, title, size=13, weight="bold").pack(
                anchor="w", padx=16, pady=(12, 4))
            w = _embed_figure(fig_fn(), c)
            w.pack(fill="x", padx=8, pady=(0, 12))

    def _export_csv(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files","*.csv"),("All files","*.*")],
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
