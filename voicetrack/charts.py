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
