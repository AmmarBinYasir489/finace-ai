import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.figure import Figure


def _base_fig(nrows=1, ncols=1, figsize=(6, 3.5)):
    fig, ax = plt.subplots(nrows, ncols, figsize=figsize)
    fig.patch.set_facecolor("none")
    if isinstance(ax, (list, tuple)):
        for a in (ax if nrows > 1 or ncols > 1 else [ax]):
            a.set_facecolor("none")
            a.spines["top"].set_visible(False)
            a.spines["right"].set_visible(False)
    else:
        ax.set_facecolor("none")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
    return fig, ax


def spending_by_category(data: list) -> Figure:
    fig, ax = _base_fig(figsize=(6, max(2.5, len(data) * 0.45 + 0.5)))
    if not data:
        ax.text(0.5, 0.5, "No data", ha="center", va="center", fontsize=9,
                transform=ax.transAxes)
        ax.axis("off")
        fig.tight_layout()
        return fig

    cats = [d["category"] for d in data]
    totals = [d["total"] for d in data]
    colors = plt.cm.Pastel1.colors[: len(cats)]
    bars = ax.barh(cats, totals, color=colors)
    ax.bar_label(bars, fmt="%.0f", padding=4, fontsize=9)
    ax.set_title("Spending by Category", fontsize=10)
    ax.tick_params(labelsize=9)
    ax.invert_yaxis()
    fig.tight_layout()
    return fig


def monthly_income_vs_expense(data: list) -> Figure:
    fig, ax = _base_fig(figsize=(6, 3.5))
    if not data:
        ax.text(0.5, 0.5, "No data", ha="center", va="center", fontsize=9,
                transform=ax.transAxes)
        ax.axis("off")
        fig.tight_layout()
        return fig

    import numpy as np
    months = [d["month"] for d in data]
    incomes = [d["income"] for d in data]
    expenses = [d["expense"] for d in data]
    x = np.arange(len(months))
    w = 0.35
    ax.bar(x - w / 2, incomes, w, label="Income", color="#7fbf7f")
    ax.bar(x + w / 2, expenses, w, label="Expense", color="#bf7f7f")
    ax.set_xticks(x)
    ax.set_xticklabels(months, fontsize=9)
    ax.tick_params(labelsize=9)
    ax.set_title("Monthly Income vs Expense", fontsize=10)
    ax.legend(fontsize=9)
    fig.tight_layout()
    return fig


def balance_trend(data: list) -> Figure:
    fig, ax = _base_fig(figsize=(6, 3.5))
    if not data:
        ax.text(0.5, 0.5, "No data", ha="center", va="center", fontsize=9,
                transform=ax.transAxes)
        ax.axis("off")
        fig.tight_layout()
        return fig

    months = [d["month"] for d in data]
    cumulative = []
    running = 0.0
    for d in data:
        running += d["income"] - d["expense"]
        cumulative.append(running)

    color = "#5a9"
    ax.plot(months, cumulative, marker="o", color=color, linewidth=2)
    ax.fill_between(months, cumulative, alpha=0.15, color=color)
    ax.axhline(0, color="#aaa", linewidth=0.8, linestyle="--")
    ax.set_title("Balance Trend", fontsize=10)
    ax.tick_params(labelsize=9)
    fig.tight_layout()
    return fig


def report_spending_donut(data: list) -> Figure:
    """Dark dashboard donut chart for report cards."""
    fig, ax = _base_fig(figsize=(5.2, 3.2))
    fig.patch.set_facecolor("#111827")
    ax.set_facecolor("#111827")
    ax.axis("equal")
    ax.axis("off")
    if not data:
        ax.text(0.5, 0.5, "No spending data", ha="center", va="center",
                fontsize=9, color="#8aa4c8", transform=ax.transAxes)
        fig.tight_layout()
        return fig

    labels = [d["category"] for d in data[:6]]
    values = [float(d["total"]) for d in data[:6]]
    total = sum(values)
    colors = ["#38bdf8", "#34d399", "#a78bfa", "#fbbf24", "#f472b6", "#94a3b8"]
    ax.pie(
        values,
        startangle=90,
        counterclock=False,
        colors=colors[: len(values)],
        wedgeprops={"width": 0.38, "edgecolor": "#111827", "linewidth": 2},
    )
    ax.text(0, 0.08, "TOTAL", ha="center", va="center", fontsize=7,
            color="#547296")
    ax.text(0, -0.08, f"{total:,.0f}", ha="center", va="center",
            fontsize=12, fontweight="bold", color="#ffffff")
    legend = [
        f"{label}   {value / total * 100:.0f}%   {value:,.0f}"
        for label, value in zip(labels, values)
    ]
    ax.legend(
        legend,
        loc="center left",
        bbox_to_anchor=(1.0, 0.5),
        frameon=False,
        fontsize=8,
        labelcolor="#dbeafe",
        handlelength=0.8,
        handletextpad=0.6,
    )
    fig.tight_layout()
    return fig


def report_income_expense_bars(data: list) -> Figure:
    """Dark grouped bar chart for income-vs-expense reports."""
    fig, ax = _base_fig(figsize=(5.2, 3.2))
    fig.patch.set_facecolor("#111827")
    ax.set_facecolor("#111827")
    if not data:
        ax.text(0.5, 0.5, "No monthly data", ha="center", va="center",
                fontsize=9, color="#8aa4c8", transform=ax.transAxes)
        ax.axis("off")
        fig.tight_layout()
        return fig

    import numpy as np
    rows = data[-6:]
    months = [d["month"][-2:] for d in rows]
    labels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug",
              "Sep", "Oct", "Nov", "Dec"]
    months = [labels[int(m) - 1] for m in months]
    income = [float(d["income"]) for d in rows]
    expense = [float(d["expense"]) for d in rows]
    x = np.arange(len(rows))
    width = 0.24
    ax.bar(x - width / 2, income, width, label="Income", color="#34d399")
    ax.bar(x + width / 2, expense, width, label="Expense", color="#fb7185")
    ax.set_xticks(x)
    ax.set_xticklabels(months, color="#c7d2fe", fontsize=8)
    ax.tick_params(axis="y", colors="#94a3b8", labelsize=8)
    ax.grid(axis="y", color="#334155", alpha=0.55, linewidth=0.7)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.legend(loc="upper right", frameon=False, fontsize=8,
              labelcolor="#dbeafe")
    fig.tight_layout()
    return fig
