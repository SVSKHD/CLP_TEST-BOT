import os
import logging
import base64
from io import BytesIO
from datetime import datetime
from typing import List

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from backtest.simulator import TradeResult
from config.settings import DAILY_FLOOR, DAILY_CAP

logger = logging.getLogger("astra.charts")

RESULTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "backtest_results")
BG_COLOR = "#1a1a2e"
TEXT_COLOR = "#e0e0e0"
GRID_COLOR = "#2a2a4e"


def _setup_style():
    plt.rcParams.update({
        "figure.facecolor": BG_COLOR,
        "axes.facecolor": BG_COLOR,
        "axes.edgecolor": GRID_COLOR,
        "axes.labelcolor": TEXT_COLOR,
        "text.color": TEXT_COLOR,
        "xtick.color": TEXT_COLOR,
        "ytick.color": TEXT_COLOR,
        "grid.color": GRID_COLOR,
        "grid.alpha": 0.3,
    })


def _fig_to_base64(fig) -> str:
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight", facecolor=BG_COLOR)
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode("utf-8")
    plt.close(fig)
    return b64


def _save_fig(fig, name: str) -> str:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    path = os.path.join(RESULTS_DIR, name)
    fig.savefig(path, dpi=120, bbox_inches="tight", facecolor=BG_COLOR)
    return path


def chart_equity_curve(equity_curve: list, trades: List[TradeResult],
                       start: str, end: str, symbols: list) -> str:
    _setup_style()
    fig, ax = plt.subplots(figsize=(14, 6))

    total_eq = np.array(equity_curve)
    x = range(len(total_eq))
    ax.plot(x, total_eq, color="#00d4ff", linewidth=2, label="Total Equity", zorder=5)

    peak = np.maximum.accumulate(total_eq)
    ax.fill_between(x, total_eq, peak, where=(total_eq < peak),
                    color="#ff4444", alpha=0.3, label="Drawdown")

    if trades:
        df = pd.DataFrame([vars(t) for t in trades])
        df["exit_time"] = pd.to_datetime(df["exit_time"])
        df = df.sort_values("exit_time")

        colors = {"XAUUSD": "#ffd700", "XAUEUR": "#00ff88", "XAUGBP": "#ff69b4"}
        for sym in symbols:
            sym_trades = df[df["symbol"] == sym]
            if len(sym_trades) == 0:
                continue
            cum_pnl = sym_trades["pnl_usd"].cumsum() + equity_curve[0]
            sym_x = [list(df.index).index(i) + 1 for i in sym_trades.index]
            ax.plot(sym_x, cum_pnl.values, color=colors.get(sym, "#888"),
                    linewidth=1, alpha=0.7, label=sym)

    init_eq = equity_curve[0]
    for pct, style in [(0, "--"), (0.10, ":"), (0.20, ":")]:
        level = init_eq * (1 + pct)
        ax.axhline(y=level, color="#555", linestyle=style, alpha=0.5)

    ax.set_title(f"Equity Curve — {start} to {end}", fontsize=14, color="#00d4ff")
    ax.set_xlabel("Trade #")
    ax.set_ylabel("Equity (USD)")
    ax.legend(loc="upper left", facecolor=BG_COLOR, edgecolor=GRID_COLOR)
    ax.grid(True, alpha=0.2)

    _save_fig(fig, "equity_curve.png")
    b64 = _fig_to_base64(fig)
    return f'<div class="chart-container"><h2>Equity Curve</h2><img src="data:image/png;base64,{b64}"></div>'


def chart_trade_scatter(trades: List[TradeResult], price_data: dict, symbol: str) -> str:
    _setup_style()
    sym_trades = [t for t in trades if t.symbol == symbol]
    if not sym_trades:
        return ""

    fig, ax = plt.subplots(figsize=(14, 7))

    if symbol in price_data and price_data[symbol] is not None:
        df = price_data[symbol]
        dates = pd.to_datetime(df["time"])
        ax.plot(dates, df["close"], color="#555", linewidth=0.5, alpha=0.5)
        ax.fill_between(dates, df["low"], df["high"], color="#333", alpha=0.3)

    for t in sym_trades:
        entry_color = "#00ff88" if t.direction == "BUY" else "#ff4444"
        marker = "^" if t.direction == "BUY" else "v"
        ax.scatter(t.entry_time, t.entry_price, color=entry_color, marker=marker,
                   s=50, zorder=5, edgecolors="white", linewidths=0.5)

        exit_color = "#00ff88" if t.result == "WIN" else "#ff4444"
        ax.scatter(t.exit_time, t.exit_price, color=exit_color, marker="o",
                   s=30, zorder=5, edgecolors="white", linewidths=0.5)

        ax.plot([t.entry_time, t.exit_time], [t.sl_price, t.sl_price],
                color="#ff4444", linestyle="--", linewidth=0.5, alpha=0.5)
        ax.plot([t.entry_time, t.exit_time], [t.tp_price, t.tp_price],
                color="#00ff88", linestyle="--", linewidth=0.5, alpha=0.5)

    ax.set_title(f"{symbol} — All Backtest Trades", fontsize=14, color="#00d4ff")
    ax.set_xlabel("Time")
    ax.set_ylabel("Price")
    ax.grid(True, alpha=0.2)

    _save_fig(fig, f"trades_{symbol}.png")
    b64 = _fig_to_base64(fig)
    return f'<div class="chart-container"><h2>{symbol} Trades</h2><img src="data:image/png;base64,{b64}"></div>'


def chart_daily_pnl(trades: List[TradeResult], symbols: list) -> str:
    _setup_style()
    if not trades:
        return ""

    df = pd.DataFrame([vars(t) for t in trades])
    df["exit_time"] = pd.to_datetime(df["exit_time"])
    df["date"] = df["exit_time"].dt.date

    daily = df.groupby(["date", "symbol"])["pnl_usd"].sum().unstack(fill_value=0)
    daily_total = daily.sum(axis=1)

    fig, ax = plt.subplots(figsize=(14, 6))

    colors_map = {"XAUUSD": "#ffd700", "XAUEUR": "#00ff88", "XAUGBP": "#ff69b4"}
    bottom = np.zeros(len(daily))
    dates = range(len(daily))

    for sym in symbols:
        if sym in daily.columns:
            vals = daily[sym].values
            ax.bar(dates, vals, bottom=bottom, color=colors_map.get(sym, "#888"),
                   label=sym, alpha=0.8, width=0.8)
            bottom += vals

    ax.axhline(y=DAILY_FLOOR, color="#ffd700", linestyle="--", alpha=0.7, label=f"Floor (${DAILY_FLOOR})")
    ax.axhline(y=DAILY_CAP, color="#ff4444", linestyle="--", alpha=0.7, label=f"Cap (${DAILY_CAP})")

    for i, total in enumerate(daily_total):
        if total >= DAILY_FLOOR:
            color = "#00ff88"
        elif total >= PER_SYMBOL_DAILY_TARGET:
            color = "#ffd700"
        else:
            color = "#ff4444"

    ax.set_title("Daily PnL", fontsize=14, color="#00d4ff")
    ax.set_xlabel("Trading Day")
    ax.set_ylabel("PnL (USD)")
    ax.set_xticks(dates[::max(1, len(dates) // 20)])
    ax.set_xticklabels([str(d) for d in list(daily.index)[::max(1, len(dates) // 20)]],
                       rotation=45, fontsize=8)
    ax.legend(loc="upper left", facecolor=BG_COLOR, edgecolor=GRID_COLOR)
    ax.grid(True, alpha=0.2)

    _save_fig(fig, "daily_pnl.png")
    b64 = _fig_to_base64(fig)
    return f'<div class="chart-container"><h2>Daily PnL</h2><img src="data:image/png;base64,{b64}"></div>'


from config.settings import PER_SYMBOL_DAILY_TARGET


def chart_drawdown(equity_curve: list, start: str, end: str) -> str:
    _setup_style()
    fig, ax = plt.subplots(figsize=(14, 4))

    eq = np.array(equity_curve)
    peak = np.maximum.accumulate(eq)
    dd_pct = (eq - peak) / peak * 100

    x = range(len(dd_pct))
    ax.fill_between(x, dd_pct, 0, color="#ff4444", alpha=0.4)
    ax.plot(x, dd_pct, color="#ff4444", linewidth=1)

    ax.axhline(y=-3, color="#ffd700", linestyle="--", alpha=0.7, label="3% Warning")
    ax.axhline(y=-5, color="#ff0000", linestyle="--", alpha=0.7, label="5% Daily Limit")

    ax.set_title(f"Drawdown — {start} to {end}", fontsize=14, color="#00d4ff")
    ax.set_xlabel("Trade #")
    ax.set_ylabel("Drawdown %")
    ax.legend(loc="lower left", facecolor=BG_COLOR, edgecolor=GRID_COLOR)
    ax.grid(True, alpha=0.2)

    _save_fig(fig, "drawdown.png")
    b64 = _fig_to_base64(fig)
    return f'<div class="chart-container"><h2>Drawdown</h2><img src="data:image/png;base64,{b64}"></div>'


def chart_win_loss_distribution(trades: List[TradeResult]) -> str:
    _setup_style()
    if not trades:
        return ""

    wins = [t.pips for t in trades if t.pnl_usd > 0]
    losses = [t.pips for t in trades if t.pnl_usd <= 0]

    fig, ax = plt.subplots(figsize=(10, 5))

    if wins:
        ax.hist(wins, bins=20, color="#00ff88", alpha=0.6, label=f"Wins ({len(wins)})")
        mean_win = np.mean(wins)
        ax.axvline(x=mean_win, color="#00ff88", linestyle="--", linewidth=2,
                   label=f"Mean Win: {mean_win:.1f} pips")
    if losses:
        ax.hist(losses, bins=20, color="#ff4444", alpha=0.6, label=f"Losses ({len(losses)})")
        mean_loss = np.mean(losses)
        ax.axvline(x=mean_loss, color="#ff4444", linestyle="--", linewidth=2,
                   label=f"Mean Loss: {mean_loss:.1f} pips")

    gross_w = sum(t.pnl_usd for t in trades if t.pnl_usd > 0)
    gross_l = abs(sum(t.pnl_usd for t in trades if t.pnl_usd <= 0))
    pf = gross_w / gross_l if gross_l > 0 else float("inf")
    ax.annotate(f"Profit Factor: {pf:.2f}", xy=(0.95, 0.95), xycoords="axes fraction",
                ha="right", va="top", fontsize=12, color="#00d4ff",
                bbox=dict(boxstyle="round", fc=BG_COLOR, ec=GRID_COLOR))

    ax.set_title("Win/Loss Distribution (Pips)", fontsize=14, color="#00d4ff")
    ax.set_xlabel("Pips")
    ax.set_ylabel("Frequency")
    ax.legend(loc="upper left", facecolor=BG_COLOR, edgecolor=GRID_COLOR)
    ax.grid(True, alpha=0.2)

    _save_fig(fig, "win_loss_dist.png")
    b64 = _fig_to_base64(fig)
    return f'<div class="chart-container"><h2>Win/Loss Distribution</h2><img src="data:image/png;base64,{b64}"></div>'


def generate_all_charts(trades: List[TradeResult], equity_curve: list,
                        price_data: dict, symbols: list,
                        start: str, end: str) -> str:
    html_parts = []
    html_parts.append(chart_equity_curve(equity_curve, trades, start, end, symbols))
    for sym in symbols:
        chart = chart_trade_scatter(trades, price_data, sym)
        if chart:
            html_parts.append(chart)
    html_parts.append(chart_daily_pnl(trades, symbols))
    html_parts.append(chart_drawdown(equity_curve, start, end))
    html_parts.append(chart_win_loss_distribution(trades))
    return "\n".join(html_parts)


if __name__ == "__main__":
    from backtest.simulator import TradeResult
    from datetime import timedelta

    trades = []
    base_time = datetime(2025, 1, 2, 8, 0)
    for i in range(30):
        pnl = 150 if i % 3 != 0 else -100
        pips = 60 if pnl > 0 else -40
        trades.append(TradeResult(
            symbol=["XAUUSD", "XAUEUR", "XAUGBP"][i % 3],
            direction="BUY" if i % 2 == 0 else "SELL",
            entry_time=base_time + timedelta(hours=i * 3),
            exit_time=base_time + timedelta(hours=i * 3 + 1),
            entry_price=2000 + i * 0.5,
            exit_price=2000 + i * 0.5 + (6 if pnl > 0 else -4),
            sl_price=2000 + i * 0.5 - 4,
            tp_price=2000 + i * 0.5 + 6,
            lot=0.10,
            pips=pips,
            pnl_usd=pnl,
            result="WIN" if pnl > 0 else "LOSS",
            exit_reason="TP_HIT" if pnl > 0 else "SL_HIT",
        ))

    equity = [50000]
    for t in trades:
        equity.append(equity[-1] + t.pnl_usd)

    charts_html = generate_all_charts(
        trades, equity, {}, ["XAUUSD", "XAUEUR", "XAUGBP"],
        "2025-01-01", "2025-01-10"
    )
    print(f"Generated {len(charts_html)} chars of HTML with embedded charts")
