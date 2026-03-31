import logging
import threading
from datetime import datetime, timedelta

import plotly.graph_objects as go
from plotly.subplots import make_subplots
from dash import Dash, html, dcc
from dash.dependencies import Input, Output

from config.settings import SYMBOLS, DAILY_CAP, DAILY_FLOOR
from capital.profit_guard import ProfitGuard
from state.manager import load_all

logger = logging.getLogger("astra.live_chart")


class LiveChart:
    def __init__(self, profit_guard: ProfitGuard = None, symbols: list = None, port: int = 8050):
        self.symbols = symbols or SYMBOLS
        self.profit_guard = profit_guard
        self.port = port
        self.app = Dash(__name__)
        self._setup_layout()
        self._setup_callbacks()
        self._thread = None

    def _setup_layout(self):
        symbol_cards = []
        for sym in self.symbols:
            symbol_cards.append(
                html.Div([
                    html.H3(sym, style={"color": "#00d4ff", "margin": "0"}),
                    html.Div(id=f"status-{sym}", children="ACTIVE",
                             style={"fontSize": "14px", "color": "#00ff88"}),
                    html.Div(id=f"pnl-{sym}", children="$0.00",
                             style={"fontSize": "20px", "fontWeight": "bold"}),
                    html.Div(id=f"pips-{sym}", children="0.0 pips",
                             style={"fontSize": "12px", "color": "#888"}),
                ], style={
                    "background": "#16213e", "borderRadius": "8px",
                    "padding": "15px", "textAlign": "center", "flex": "1",
                    "margin": "0 10px",
                })
            )

        charts = []
        for sym in self.symbols:
            charts.append(
                html.Div([
                    dcc.Graph(id=f"chart-{sym}", style={"height": "400px"}),
                ], style={"flex": "1", "minWidth": "300px"})
            )

        self.app.layout = html.Div([
            html.Div([
                html.H1("ASTRA XAU v2 — Live Dashboard",
                         style={"color": "#00d4ff", "textAlign": "center", "margin": "10px 0"}),
            ]),
            html.Div(symbol_cards, style={
                "display": "flex", "justifyContent": "center", "margin": "10px 0",
            }),
            html.Div([
                html.Div(id="global-stats", style={
                    "textAlign": "center", "padding": "10px",
                    "background": "#16213e", "borderRadius": "8px",
                    "margin": "10px 20px", "fontSize": "16px",
                }),
            ]),
            html.Div(charts, style={
                "display": "flex", "flexWrap": "wrap", "margin": "10px",
            }),
            dcc.Interval(id="interval", interval=15000, n_intervals=0),
        ], style={
            "background": "#1a1a2e", "color": "#e0e0e0",
            "fontFamily": "Segoe UI, sans-serif", "minHeight": "100vh",
            "padding": "10px",
        })

    def _setup_callbacks(self):
        outputs = []
        for sym in self.symbols:
            outputs.extend([
                Output(f"status-{sym}", "children"),
                Output(f"status-{sym}", "style"),
                Output(f"pnl-{sym}", "children"),
                Output(f"pnl-{sym}", "style"),
                Output(f"pips-{sym}", "children"),
                Output(f"chart-{sym}", "figure"),
            ])
        outputs.append(Output("global-stats", "children"))

        @self.app.callback(outputs, [Input("interval", "n_intervals")])
        def update_all(n):
            result = []
            for sym in self.symbols:
                data = self._get_symbol_data(sym)
                status_text = data["status"]
                status_style = {"fontSize": "14px"}
                if status_text == "FROZEN":
                    status_style["color"] = "#ffd700"
                elif status_text == "GLOBAL_CAP":
                    status_style["color"] = "#ff4444"
                else:
                    status_style["color"] = "#00ff88"

                pnl = data["realized"]
                pnl_text = f"${pnl:,.2f}"
                pnl_style = {"fontSize": "20px", "fontWeight": "bold"}
                pnl_style["color"] = "#00ff88" if pnl >= 0 else "#ff4444"

                pips_text = f"{data['pips']:.1f} pips"
                fig = self._build_chart(sym)

                result.extend([status_text, status_style, pnl_text, pnl_style, pips_text, fig])

            summary = self._get_summary()
            total = summary["total_realized"]
            cap_left = DAILY_CAP - total
            floor_status = "HIT" if total >= DAILY_FLOOR else f"${DAILY_FLOOR - total:.0f} left"

            global_text = (
                f"Total PnL: ${total:,.2f}  |  "
                f"Cap distance: ${cap_left:,.2f}  |  "
                f"Floor ($500): {floor_status}"
            )
            result.append(global_text)
            return result

    def _build_chart(self, symbol: str) -> go.Figure:
        fig = go.Figure()

        try:
            candles = self._fetch_candles(symbol)
            if candles is not None and len(candles) > 0:
                fig.add_trace(go.Candlestick(
                    x=candles["time"],
                    open=candles["open"],
                    high=candles["high"],
                    low=candles["low"],
                    close=candles["close"],
                    name=symbol,
                ))
        except Exception as e:
            logger.debug(f"Chart candle fetch error {symbol}: {e}")

        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor="#1a1a2e",
            plot_bgcolor="#1a1a2e",
            title=f"{symbol} M15",
            xaxis_rangeslider_visible=False,
            margin=dict(l=40, r=20, t=40, b=30),
            font=dict(color="#e0e0e0"),
        )
        return fig

    def _fetch_candles(self, symbol: str):
        try:
            from core.market import fetch_candles_live
            return fetch_candles_live(symbol, "M15", 50)
        except Exception:
            return None

    def _get_symbol_data(self, symbol: str) -> dict:
        if self.profit_guard:
            return {
                "status": self.profit_guard.status.get(symbol, "ACTIVE"),
                "realized": self.profit_guard.realized_pnl.get(symbol, 0),
                "pips": self.profit_guard.daily_pips.get(symbol, 0),
            }
        state = load_all([symbol])
        s = state.get(symbol, {})
        return {
            "status": s.get("status", "ACTIVE"),
            "realized": s.get("realized_pnl", 0),
            "pips": s.get("daily_pips", 0),
        }

    def _get_summary(self) -> dict:
        if self.profit_guard:
            return self.profit_guard.get_summary()
        return {"total_realized": 0, "global_status": "ACTIVE"}

    def start(self):
        self._thread = threading.Thread(
            target=self._run_server,
            daemon=True,
            name="dash-server",
        )
        self._thread.start()
        logger.info(f"Live chart server started at http://localhost:{self.port}")

    def _run_server(self):
        self.app.run(host="0.0.0.0", port=self.port, debug=False, use_reloader=False)

    def run_blocking(self):
        logger.info(f"Live chart server at http://localhost:{self.port}")
        self.app.run(host="0.0.0.0", port=self.port, debug=False)


if __name__ == "__main__":
    guard = ProfitGuard()
    guard.update_realized("XAUUSD", 250, 50)
    guard.update_realized("XAUEUR", 180, 35)

    chart = LiveChart(profit_guard=guard)
    print(f"Starting live chart at http://localhost:{chart.port}")
    chart.run_blocking()
