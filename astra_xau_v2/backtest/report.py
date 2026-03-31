import logging
import os
from datetime import datetime
from typing import List

import numpy as np
import pandas as pd

from backtest.simulator import TradeResult
from config.settings import PER_SYMBOL_DAILY_TARGET, DAILY_FLOOR, DAILY_CAP

logger = logging.getLogger("astra.report")

RESULTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "backtest_results")


class BacktestResult:
    def __init__(self, trades: List[TradeResult], initial_equity: float,
                 start: str, end: str, symbols: list):
        self.trades = trades
        self.initial_equity = initial_equity
        self.start = start
        self.end = end
        self.symbols = symbols
        self.metrics = self._compute_metrics()

    def _compute_metrics(self) -> dict:
        if not self.trades:
            return self._empty_metrics()

        df = pd.DataFrame([vars(t) for t in self.trades])
        df["entry_time"] = pd.to_datetime(df["entry_time"])
        df["exit_time"] = pd.to_datetime(df["exit_time"])
        df["date"] = df["exit_time"].dt.date

        total_trades = len(df)

        # Core metrics — computed directly from trade pnl_usd
        winning_trades = [t for t in self.trades if t.pnl_usd > 0]
        losing_trades = [t for t in self.trades if t.pnl_usd <= 0]
        win_rate = len(winning_trades) / total_trades * 100

        gross_profit = sum(t.pnl_usd for t in winning_trades)
        gross_loss = abs(sum(t.pnl_usd for t in losing_trades))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        total_pnl = sum(t.pnl_usd for t in self.trades)

        # Verify pnl consistency
        df_pnl_sum = df["pnl_usd"].sum()
        if abs(total_pnl - df_pnl_sum) > 0.01:
            raise ValueError(
                f"PnL mismatch: list sum={total_pnl:.2f} vs df sum={df_pnl_sum:.2f}"
            )

        # Mean win / mean loss for RR analysis
        mean_win = np.mean([t.pnl_usd for t in winning_trades]) if winning_trades else 0
        mean_loss = np.mean([t.pnl_usd for t in losing_trades]) if losing_trades else 0
        mean_win_pips = np.mean([t.pips for t in winning_trades]) if winning_trades else 0
        mean_loss_pips = np.mean([t.pips for t in losing_trades]) if losing_trades else 0

        # Equity curve and drawdown — from sorted trade sequence
        sorted_trades = sorted(self.trades, key=lambda t: t.exit_time)
        equity_curve = [self.initial_equity]
        for t in sorted_trades:
            equity_curve.append(equity_curve[-1] + t.pnl_usd)
        equity_arr = np.array(equity_curve)
        peak = np.maximum.accumulate(equity_arr)
        drawdown = equity_arr - peak
        max_dd_usd = abs(drawdown.min())
        dd_idx = np.argmin(drawdown)
        max_dd_pct = max_dd_usd / peak[dd_idx] * 100 if peak[dd_idx] > 0 else 0

        daily_pnl = df.groupby("date")["pnl_usd"].sum()
        daily_returns = daily_pnl / self.initial_equity
        sharpe = (daily_returns.mean() / daily_returns.std() * np.sqrt(252)
                  if daily_returns.std() > 0 else 0)

        annual_return = total_pnl / self.initial_equity * (365 / max(len(daily_pnl), 1))
        calmar = annual_return / (max_dd_pct / 100) if max_dd_pct > 0 else 0

        durations = (df["exit_time"] - df["entry_time"]).dt.total_seconds()
        avg_duration = durations.mean()
        avg_pips = df["pips"].mean()

        per_symbol = {}
        for sym in self.symbols:
            sym_df = df[df["symbol"] == sym]
            if len(sym_df) == 0:
                per_symbol[sym] = self._empty_symbol_metrics()
                continue
            sym_wins = sym_df[sym_df["pnl_usd"] > 0]
            sym_losses = sym_df[sym_df["pnl_usd"] <= 0]
            sym_gp = sym_wins["pnl_usd"].sum() if len(sym_wins) > 0 else 0
            sym_gl = abs(sym_losses["pnl_usd"].sum()) if len(sym_losses) > 0 else 0
            per_symbol[sym] = {
                "trades": len(sym_df),
                "win_rate": len(sym_wins) / len(sym_df) * 100,
                "total_pnl": sym_df["pnl_usd"].sum(),
                "profit_factor": sym_gp / sym_gl if sym_gl > 0 else float("inf"),
                "avg_pips": sym_df["pips"].mean(),
            }

        sym_daily = df.groupby(["date", "symbol"])["pnl_usd"].sum().unstack(fill_value=0)
        days_target_hit = {}
        for sym in self.symbols:
            if sym in sym_daily.columns:
                days_target_hit[sym] = (sym_daily[sym] >= PER_SYMBOL_DAILY_TARGET).sum()
            else:
                days_target_hit[sym] = 0

        total_days = len(daily_pnl)
        days_floor_hit = (daily_pnl >= DAILY_FLOOR).sum()
        days_cap_hit = (daily_pnl >= DAILY_CAP).sum()

        return {
            "total_trades": total_trades,
            "win_rate": round(win_rate, 1),
            "profit_factor": round(profit_factor, 2),
            "total_pnl": round(total_pnl, 2),
            "max_drawdown_usd": round(max_dd_usd, 2),
            "max_drawdown_pct": round(max_dd_pct, 2),
            "sharpe_ratio": round(sharpe, 2),
            "calmar_ratio": round(calmar, 2),
            "avg_duration_seconds": round(avg_duration, 0),
            "avg_pips_per_trade": round(avg_pips, 1),
            "mean_win_usd": round(mean_win, 2),
            "mean_loss_usd": round(mean_loss, 2),
            "mean_win_pips": round(mean_win_pips, 1),
            "mean_loss_pips": round(mean_loss_pips, 1),
            "per_symbol": per_symbol,
            "total_trading_days": total_days,
            "days_target_per_symbol": days_target_hit,
            "days_floor_hit": int(days_floor_hit),
            "days_floor_pct": round(days_floor_hit / total_days * 100, 1) if total_days > 0 else 0,
            "days_cap_hit": int(days_cap_hit),
            "equity_curve": equity_curve,
        }

    def _empty_metrics(self):
        return {
            "total_trades": 0, "win_rate": 0, "profit_factor": 0,
            "total_pnl": 0, "max_drawdown_usd": 0, "max_drawdown_pct": 0,
            "sharpe_ratio": 0, "calmar_ratio": 0, "avg_duration_seconds": 0,
            "avg_pips_per_trade": 0, "per_symbol": {}, "total_trading_days": 0,
            "days_target_per_symbol": {}, "days_floor_hit": 0,
            "days_floor_pct": 0, "days_cap_hit": 0, "equity_curve": [self.initial_equity],
        }

    def _empty_symbol_metrics(self):
        return {"trades": 0, "win_rate": 0, "total_pnl": 0,
                "profit_factor": 0, "avg_pips": 0}

    def generate_html(self, charts_html: str = "") -> str:
        m = self.metrics
        sym_rows = ""
        for sym, data in m.get("per_symbol", {}).items():
            sym_rows += f"""
            <tr>
                <td>{sym}</td>
                <td>{data['trades']}</td>
                <td>{data['win_rate']:.1f}%</td>
                <td>${data['total_pnl']:.2f}</td>
                <td>{data['profit_factor']:.2f}</td>
                <td>{data['avg_pips']:.1f}</td>
            </tr>"""

        html = f"""<!DOCTYPE html>
<html>
<head>
<title>Astra XAU v2 — Backtest Report</title>
<style>
body {{ background: #1a1a2e; color: #e0e0e0; font-family: 'Segoe UI', sans-serif; margin: 20px; }}
h1 {{ color: #00d4ff; }}
h2 {{ color: #ffd700; border-bottom: 1px solid #333; padding-bottom: 5px; }}
table {{ border-collapse: collapse; width: 100%; margin: 15px 0; }}
th, td {{ border: 1px solid #333; padding: 8px 12px; text-align: right; }}
th {{ background: #16213e; color: #00d4ff; }}
tr:nth-child(even) {{ background: #1a1a3e; }}
.metric-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin: 20px 0; }}
.metric-card {{ background: #16213e; border-radius: 8px; padding: 15px; text-align: center; }}
.metric-card .value {{ font-size: 24px; font-weight: bold; color: #00d4ff; }}
.metric-card .label {{ font-size: 12px; color: #888; margin-top: 5px; }}
.positive {{ color: #00ff88; }}
.negative {{ color: #ff4444; }}
.chart-container {{ margin: 20px 0; text-align: center; }}
.chart-container img {{ max-width: 100%; border-radius: 8px; }}
</style>
</head>
<body>
<h1>Astra XAU v2 — Backtest Report</h1>
<p>{self.start} to {self.end} | Initial equity: ${self.initial_equity:,.2f}</p>

<div class="metric-grid">
  <div class="metric-card">
    <div class="value">{m['total_trades']}</div>
    <div class="label">Total Trades</div>
  </div>
  <div class="metric-card">
    <div class="value">{m['win_rate']}%</div>
    <div class="label">Win Rate</div>
  </div>
  <div class="metric-card">
    <div class="value {'positive' if m['total_pnl'] >= 0 else 'negative'}">${m['total_pnl']:,.2f}</div>
    <div class="label">Total PnL</div>
  </div>
  <div class="metric-card">
    <div class="value">{m['profit_factor']}</div>
    <div class="label">Profit Factor</div>
  </div>
  <div class="metric-card">
    <div class="value negative">${m['max_drawdown_usd']:,.2f}</div>
    <div class="label">Max Drawdown</div>
  </div>
  <div class="metric-card">
    <div class="value negative">{m['max_drawdown_pct']}%</div>
    <div class="label">Max DD %</div>
  </div>
  <div class="metric-card">
    <div class="value">{m['sharpe_ratio']}</div>
    <div class="label">Sharpe Ratio</div>
  </div>
  <div class="metric-card">
    <div class="value">{m['calmar_ratio']}</div>
    <div class="label">Calmar Ratio</div>
  </div>
</div>

<h2>Performance Summary</h2>
<table>
<tr><th>Metric</th><th>Value</th></tr>
<tr><td>Avg Trade Duration</td><td>{m['avg_duration_seconds']/60:.0f} min</td></tr>
<tr><td>Avg Pips/Trade</td><td>{m['avg_pips_per_trade']}</td></tr>
<tr><td>Trading Days</td><td>{m['total_trading_days']}</td></tr>
<tr><td>Days $500 Floor Hit</td><td>{m['days_floor_hit']} ({m['days_floor_pct']}%)</td></tr>
<tr><td>Days $3000 Cap Hit</td><td>{m['days_cap_hit']}</td></tr>
</table>

<h2>Per-Symbol Breakdown</h2>
<table>
<tr><th>Symbol</th><th>Trades</th><th>Win Rate</th><th>PnL</th><th>Profit Factor</th><th>Avg Pips</th></tr>
{sym_rows}
</table>

{charts_html}

<footer style="margin-top:40px;color:#555;text-align:center;">
Generated by Astra XAU v2 at {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC
</footer>
</body>
</html>"""
        return html

    def save_report(self, charts_html: str = "") -> str:
        os.makedirs(RESULTS_DIR, exist_ok=True)
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(RESULTS_DIR, f"report_{timestamp}.html")
        html = self.generate_html(charts_html)
        with open(path, "w") as f:
            f.write(html)
        logger.info(f"Report saved: {path}")
        return path


if __name__ == "__main__":
    from backtest.simulator import TradeResult
    from datetime import timedelta

    trades = []
    base_time = datetime(2025, 1, 2, 8, 0)
    for i in range(20):
        pnl = 60 if i % 3 != 0 else -40
        trades.append(TradeResult(
            symbol=["XAUUSD", "XAUEUR", "XAUGBP"][i % 3],
            direction="BUY" if i % 2 == 0 else "SELL",
            entry_time=base_time + timedelta(hours=i * 4),
            exit_time=base_time + timedelta(hours=i * 4 + 2),
            entry_price=2000 + i,
            exit_price=2000 + i + (6 if pnl > 0 else -4),
            sl_price=2000 + i - 4,
            tp_price=2000 + i + 6,
            lot=0.10,
            pips=60 if pnl > 0 else -40,
            pnl_usd=pnl,
            result="WIN" if pnl > 0 else "LOSS",
            exit_reason="TP_HIT" if pnl > 0 else "SL_HIT",
        ))

    bt = BacktestResult(trades, 50000, "2025-01-01", "2025-01-31", ["XAUUSD", "XAUEUR", "XAUGBP"])
    print(f"Total PnL: ${bt.metrics['total_pnl']:.2f}")
    print(f"Win rate: {bt.metrics['win_rate']}%")
    print(f"Profit factor: {bt.metrics['profit_factor']}")
    print(f"Max DD: ${bt.metrics['max_drawdown_usd']:.2f} ({bt.metrics['max_drawdown_pct']}%)")
