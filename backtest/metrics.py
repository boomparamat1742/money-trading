"""Edge metrics (design §8.2). These decide whether a strategy has an edge —
Profit Factor, Expectancy and Max Drawdown matter more than Win Rate."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from worker.app.models import PaperTrade, TradeStatus


@dataclass
class Metrics:
    trade_count: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    net_profit: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    profit_factor: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    expectancy: float = 0.0        # avg pnl per trade (quote currency)
    max_drawdown: float = 0.0
    max_consecutive_losses: int = 0
    total_fees: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_metrics(trades: list[PaperTrade], starting_equity: float) -> Metrics:
    closed = [t for t in trades if t.status in (TradeStatus.HIT_TP, TradeStatus.HIT_SL, TradeStatus.EXPIRED)]
    m = Metrics(trade_count=len(closed))
    if not closed:
        return m

    pnls = [t.pnl_amount or 0.0 for t in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    m.wins, m.losses = len(wins), len(losses)
    m.win_rate = round(len(wins) / len(closed) * 100, 2)
    m.gross_profit = round(sum(wins), 2)
    m.gross_loss = round(sum(losses), 2)
    m.net_profit = round(sum(pnls), 2)
    m.profit_factor = round(m.gross_profit / abs(m.gross_loss), 3) if m.gross_loss else float("inf")
    m.avg_win = round(sum(wins) / len(wins), 2) if wins else 0.0
    m.avg_loss = round(sum(losses) / len(losses), 2) if losses else 0.0
    m.expectancy = round(m.net_profit / len(closed), 4)
    m.total_fees = round(sum((t.entry_fee + t.exit_fee) for t in closed), 4)

    # equity curve + max drawdown
    equity = starting_equity
    peak = equity
    max_dd = 0.0
    streak = 0
    max_streak = 0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        dd = (peak - equity) / peak * 100 if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
        if p <= 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    m.max_drawdown = round(max_dd, 2)
    m.max_consecutive_losses = max_streak
    return m


def format_report(m: Metrics, title: str = "Backtest Result") -> str:
    pf = "∞" if m.profit_factor == float("inf") else f"{m.profit_factor}"
    edge = "✅ มี edge (expectancy > 0)" if m.expectancy > 0 else "❌ ยังไม่มี edge (expectancy ≤ 0)"
    return "\n".join([
        f"── {title} ──",
        f"Trades              : {m.trade_count}  (win {m.wins} / loss {m.losses})",
        f"Win rate            : {m.win_rate}%",
        f"Net profit          : {m.net_profit}",
        f"Profit factor       : {pf}",
        f"Expectancy / trade  : {m.expectancy}",
        f"Avg win / avg loss  : {m.avg_win} / {m.avg_loss}",
        f"Max drawdown        : {m.max_drawdown}%",
        f"Max consec. losses  : {m.max_consecutive_losses}",
        f"Total fees          : {m.total_fees}",
        f"→ {edge}",
    ])
