import argparse
import logging
import os
import sys
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import List

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from config.settings import (
    SYMBOLS, BACKTEST_START, BACKTEST_END, BACKTEST_TIMEFRAME,
    BACKTEST_SPREAD_PIPS, BACKTEST_SLIPPAGE_PIPS, BACKTEST_COMMISSION_USD,
    ACCOUNT_EQUITY,
)
from datetime import timedelta
from backtest.data_loader import load_history, generate_synthetic_data
from backtest.simulator import Simulator, TradeResult, PIP_SIZE
from backtest.report import BacktestResult
from backtest.charts import generate_all_charts
from strategy.scalper import Scalper
from strategy.hawk import HawkFilter
from strategy.base import FilterResult
from capital.allocator import calc_lot_size, calc_pip_value
from capital.profit_guard import ProfitGuard

logger = logging.getLogger("astra.backtest_engine")


def _default_symbol_info(symbol: str) -> dict:
    # Realistic XAU values: tick_value=1.0 per tick_size=0.1 → pip_value = $100/lot
    # This matches typical MT5 XAU broker configs where 1 pip (0.1) = $1 for 0.01 lot
    return {
        "trade_tick_value": 1.0,
        "trade_tick_size": 0.1,
        "volume_min": 0.01,
        "volume_max": 100.0,
        "volume_step": 0.01,
    }


WARMUP_CANDLES = 200


def run_symbol_backtest(
    symbol: str,
    df: pd.DataFrame,
    trade_start_idx: int,
    initial_equity: float,
    symbol_info: dict,
    profit_guard: ProfitGuard,
    spread_pips: float,
    slippage_pips: float,
    commission_usd: float,
) -> List[TradeResult]:
    scalper = Scalper(symbol, mode="backtest")
    hawk = HawkFilter(symbol, mode="backtest")
    simulator = Simulator(spread_pips, slippage_pips, commission_usd)
    pip_value = calc_pip_value(symbol_info)

    trades = []
    equity = initial_equity
    signal_log_count = 0
    skip_until_idx = 0
    current_date = None

    # Start from trade_start_idx (first candle in the requested date range)
    # but never earlier than index 50 to have minimal lookback
    loop_start = max(trade_start_idx, 50)

    for i in range(loop_start, len(df)):
        # Daily reset of profit guard (simulates daily_init in live)
        candle_date = df.iloc[i]["time"].date() if hasattr(df.iloc[i]["time"], "date") else None
        if candle_date and candle_date != current_date:
            current_date = candle_date
            # Don't reset if account is breached (10% total DD)
            if profit_guard.global_status == "ACCOUNT_BREACH":
                break
            profit_guard.realized_pnl[symbol] = 0.0
            profit_guard.daily_pips[symbol] = 0.0
            profit_guard.status[symbol] = "ACTIVE"
            profit_guard.global_status = "ACTIVE"
            profit_guard.start_new_day(equity)

        if not profit_guard.is_global_active():
            break

        # Skip candles that fall within a previous trade's duration
        if i < skip_until_idx:
            continue

        candle_time = df.iloc[i]["time"]
        guard_check = profit_guard.can_trade(symbol, current_time=candle_time)
        if not guard_check["allowed"]:
            continue

        # Large window for H4 zone detection (need ~60 H4 candles = 960 M15 candles)
        window = df.iloc[max(0, i - 999):i + 1].copy().reset_index(drop=True)

        signal = scalper.generate_signal(window)
        if signal is None:
            continue

        hawk_result = hawk.evaluate(window, signal)
        if signal_log_count < 5:
            signal_log_count += 1
            logger.info(
                f"  [{symbol}] Signal #{signal_log_count}: {signal.direction} @ {signal.entry_price:.2f} "
                f"(conf={signal.confidence:.2f}) -> Hawk: {hawk_result.action} ({hawk_result.reason})"
            )
        if hawk_result.action != FilterResult.CONFIRM:
            continue

        active_syms = [s for s in profit_guard.symbols if profit_guard.is_symbol_active(s)]
        lot = calc_lot_size(equity, symbol_info, signal.sl_pips, active_syms)

        if lot is None:
            continue

        result = simulator.execute_trade(
            df, i, signal.direction, lot,
            signal.sl_price, signal.tp_price, symbol, pip_value
        )

        trades.append(result)
        equity += result.pnl_usd
        profit_guard.update_equity(equity)
        profit_guard.update_realized(symbol, result.pnl_usd, abs(result.pips),
                                     trade_time=result.exit_time)

        # Check Funding Pips drawdown limits
        dd_check = profit_guard.check_drawdown(equity)
        if dd_check["breach"]:
            logger.warning(f"{symbol}: {dd_check['type']} at equity ${equity:,.2f} — stopping")
            break

        # Skip forward past this trade's exit candle
        exit_matches = df.index[df["time"] >= result.exit_time]
        if len(exit_matches) > 0:
            skip_until_idx = exit_matches[0] + 1

    return trades


def run_backtest(
    symbols: list = None,
    start: str = None,
    end: str = None,
    timeframe: str = None,
    initial_equity: float = None,
    save_report: bool = True,
) -> BacktestResult:
    symbols = symbols or SYMBOLS
    start = start or BACKTEST_START
    end = end or BACKTEST_END
    timeframe = timeframe or BACKTEST_TIMEFRAME
    initial_equity = initial_equity or ACCOUNT_EQUITY

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    logger.info(f"Backtest: {symbols} | {start} to {end} | {timeframe} | ${initial_equity:,.2f}")

    try:
        from core.mt5_client import initialize
        initialize()
        logger.info("MT5 connected for backtest data fetch")
    except Exception as e:
        logger.warning(f"MT5 not available, will use CSV fallback: {e}")

    # Calculate warmup start date (extra candles before the requested range)
    warmup_days = 30  # ~200 M15 candles in ~10 trading days, 30 calendar days for safety
    warmup_start_dt = datetime.strptime(start, "%Y-%m-%d") - timedelta(days=warmup_days)
    warmup_start = warmup_start_dt.strftime("%Y-%m-%d")
    logger.info(f"Fetching warmup data from {warmup_start} (requested start: {start})")

    price_data = {}
    trade_start_indices = {}
    trade_start_dt = pd.Timestamp(start)

    for sym in symbols:
        try:
            price_data[sym] = load_history(sym, timeframe, warmup_start, end)
            logger.info(f"Loaded {sym}: {len(price_data[sym])} candles (incl. warmup)")
        except RuntimeError:
            logger.info(f"Generating synthetic data for {sym}")
            base_prices = {"XAUUSD": 2000, "XAUEUR": 1850, "XAUGBP": 1600}
            price_data[sym] = generate_synthetic_data(
                sym, warmup_start, end, timeframe,
                base_price=base_prices.get(sym, 2000)
            )
            logger.info(f"Generated {sym}: {len(price_data[sym])} synthetic candles (incl. warmup)")

        # Find the index where the actual requested date range begins
        df = price_data[sym]
        mask = df["time"] >= trade_start_dt
        if mask.any():
            trade_start_indices[sym] = mask.idxmax()
        else:
            trade_start_indices[sym] = 0

    for sym in symbols:
        df = price_data[sym]
        tsi = trade_start_indices[sym]
        logger.info(
            f"  {sym}: {len(df)} total candles, range {df['time'].iloc[0]} to {df['time'].iloc[-1]}, "
            f"warmup={tsi} candles, trade zone starts at index {tsi}"
        )

    all_trades = []
    symbol_infos = {sym: _default_symbol_info(sym) for sym in symbols}

    # Each symbol gets its own ProfitGuard for daily pip/pnl tracking,
    # but shares the same initial_equity reference for DD limits
    for sym in symbols:
        sym_guard = ProfitGuard([sym], initial_equity=initial_equity)
        try:
            trades = run_symbol_backtest(
                sym, price_data[sym], trade_start_indices[sym],
                initial_equity, symbol_infos[sym],
                sym_guard,
                BACKTEST_SPREAD_PIPS, BACKTEST_SLIPPAGE_PIPS, BACKTEST_COMMISSION_USD,
            )
            all_trades.extend(trades)
            wins = sum(1 for t in trades if t.result == "WIN")
            logger.info(f"{sym}: {len(trades)} trades, {wins} wins")
        except Exception as e:
            logger.error(f"{sym} backtest failed: {e}")

    all_trades.sort(key=lambda t: t.entry_time)

    # Verify PnL consistency before building report
    raw_pnl = sum(t.pnl_usd for t in all_trades)

    bt_result = BacktestResult(all_trades, initial_equity, start, end, symbols)

    m = bt_result.metrics
    if abs(m["total_pnl"] - raw_pnl) > 1.0:
        raise ValueError(
            f"PnL mismatch in report: reported={m['total_pnl']:.2f}, "
            f"actual sum={raw_pnl:.2f}"
        )

    logger.info("=" * 60)
    logger.info(f"Total trades: {m['total_trades']}")
    logger.info(f"Win rate: {m['win_rate']}%")
    logger.info(f"Total PnL: ${m['total_pnl']:,.2f}")
    logger.info(f"Profit factor: {m['profit_factor']}")
    logger.info(f"Max drawdown: ${m['max_drawdown_usd']:,.2f} ({m['max_drawdown_pct']}%)")
    logger.info(f"Sharpe: {m['sharpe_ratio']} | Calmar: {m['calmar_ratio']}")
    logger.info(f"Mean win: ${m['mean_win_usd']:.2f} ({m['mean_win_pips']:.1f}p) | "
                f"Mean loss: ${m['mean_loss_usd']:.2f} ({m['mean_loss_pips']:.1f}p)")
    logger.info("=" * 60)

    if save_report:
        try:
            charts_html = generate_all_charts(
                all_trades, m["equity_curve"], price_data, symbols, start, end
            )
        except Exception as e:
            logger.warning(f"Chart generation failed: {e}")
            charts_html = ""

        report_path = bt_result.save_report(charts_html)
        logger.info(f"Report saved: {report_path}")

        try:
            webbrowser.open(f"file://{os.path.abspath(report_path)}")
        except Exception:
            pass

    return bt_result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Astra XAU v2 Backtester")
    parser.add_argument("--start", default=BACKTEST_START, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default=BACKTEST_END, help="End date (YYYY-MM-DD)")
    parser.add_argument("--timeframe", default=BACKTEST_TIMEFRAME, help="Timeframe (M1/M5/M15/M30/H1/H4/D1)")
    parser.add_argument("--equity", type=float, default=ACCOUNT_EQUITY, help="Initial equity")
    parser.add_argument("--symbols", nargs="+", default=SYMBOLS, help="Symbols to backtest")
    parser.add_argument("--no-report", action="store_true", help="Skip HTML report generation")

    args = parser.parse_args()
    run_backtest(
        symbols=args.symbols,
        start=args.start,
        end=args.end,
        timeframe=args.timeframe,
        initial_equity=args.equity,
        save_report=not args.no_report,
    )
