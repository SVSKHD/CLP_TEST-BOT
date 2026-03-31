import logging
import time
from datetime import datetime

from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text

from config.settings import SYMBOLS, DAILY_CAP, DAILY_FLOOR
from capital.profit_guard import ProfitGuard
from state.manager import load_all

logger = logging.getLogger("astra.dashboard")


class Dashboard:
    def __init__(self, profit_guard: ProfitGuard = None, symbols: list = None):
        self.symbols = symbols or SYMBOLS
        self.profit_guard = profit_guard
        self.console = Console()
        self._running = False

    def run(self, interval: float = 5.0):
        self._running = True
        logger.info("Dashboard started")

        try:
            with Live(self._render(), console=self.console, refresh_per_second=1) as live:
                while self._running:
                    live.update(self._render())
                    time.sleep(interval)
        except KeyboardInterrupt:
            self._running = False

    def stop(self):
        self._running = False

    def _render(self) -> Panel:
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="symbols", size=9),
            Layout(name="footer", size=4),
        )

        layout["header"].update(self._header())
        layout["symbols"].update(self._symbol_cards())
        layout["footer"].update(self._footer())

        return Panel(layout, title="ASTRA XAU v2", border_style="cyan")

    def _header(self) -> Panel:
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        equity = self._get_equity()
        return Panel(
            Text(f"  ASTRA XAU v2  |  {now} UTC  |  Equity: ${equity:,.2f}",
                 style="bold cyan"),
            style="cyan",
        )

    def _symbol_cards(self) -> Table:
        table = Table(show_header=True, header_style="bold cyan", expand=True,
                      show_edge=False, pad_edge=False)

        for sym in self.symbols:
            table.add_column(sym, justify="center", ratio=1)

        row = []
        for sym in self.symbols:
            data = self._get_symbol_data(sym)
            status = data["status"]

            if status == "FROZEN":
                status_text = f"[yellow]FROZEN ■[/yellow]"
            elif status == "GLOBAL_CAP":
                status_text = f"[red bold]CAP HIT ✗[/red bold]"
            else:
                status_text = f"[green]ACTIVE ●[/green]"

            pnl = data["realized"]
            pnl_color = "green" if pnl >= 300 else ("yellow" if pnl >= 100 else "red")

            cell = (
                f"{status_text}\n"
                f"  [{pnl_color}]PnL: ${pnl:.2f}[/{pnl_color}]\n"
                f"  Pips: {data['pips']:.1f}\n"
                f"  Trades: {data['trades']}"
            )
            row.append(cell)

        table.add_row(*row)
        return table

    def _footer(self) -> Panel:
        summary = self._get_summary()
        total = summary["total_realized"]
        cap_dist = DAILY_CAP - total
        floor_check = "✓" if total >= DAILY_FLOOR else "✗"
        floor_color = "green" if total >= DAILY_FLOOR else "red"

        global_status = summary["global_status"]
        if global_status == "GLOBAL_CAP":
            status_text = "[red bold blink]*** GLOBAL CAP HIT — ALL TRADING STOPPED ***[/red bold blink]"
        else:
            status_text = (
                f"  Total PnL: [bold]${total:,.2f}[/bold]  |  "
                f"Cap: ${DAILY_CAP} (${cap_dist:,.2f} left)  |  "
                f"Floor: ${DAILY_FLOOR} [{floor_color}]{floor_check}[/{floor_color}]"
            )

        return Panel(Text.from_markup(status_text), style="dim")

    def _get_equity(self) -> float:
        if self.profit_guard:
            from config.settings import ACCOUNT_EQUITY
            return ACCOUNT_EQUITY + self.profit_guard.total_realized()
        return 50000.0

    def _get_symbol_data(self, symbol: str) -> dict:
        if self.profit_guard:
            return {
                "status": self.profit_guard.status.get(symbol, "ACTIVE"),
                "realized": self.profit_guard.realized_pnl.get(symbol, 0),
                "pips": self.profit_guard.daily_pips.get(symbol, 0),
                "trades": self.profit_guard.trade_count.get(symbol, 0),
            }

        state = load_all([symbol])
        s = state.get(symbol, {})
        return {
            "status": s.get("status", "ACTIVE"),
            "realized": s.get("realized_pnl", 0),
            "pips": s.get("daily_pips", 0),
            "trades": s.get("trade_count", 0),
        }

    def _get_summary(self) -> dict:
        if self.profit_guard:
            return self.profit_guard.get_summary()
        return {"total_realized": 0, "global_status": "ACTIVE"}


if __name__ == "__main__":
    guard = ProfitGuard()
    guard.update_realized("XAUUSD", 312, 62)
    guard.update_realized("XAUEUR", 304, 58)
    guard.update_realized("XAUGBP", 186, 41)

    dashboard = Dashboard(profit_guard=guard)
    print("Dashboard demo (Ctrl+C to exit):")
    dashboard.run(interval=2.0)
