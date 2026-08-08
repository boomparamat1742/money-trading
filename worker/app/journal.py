"""Trade Journal — SQLite persistence for signals and paper trades (design §7).

Why this matters: without it every signal vanishes the moment it scrolls past,
and a restart forgets which paper trades were open. With it you can answer the
only question that matters — "what did the system ACTUALLY do, and did it
work?" — from recorded data instead of memory.

Uses the stdlib `sqlite3`, so there is nothing to install and nothing to host.
Schema mirrors migrations/schema.sql; move to Postgres later by swapping this
class, not the callers.

    journal = Journal("data/journal.db")
    sid = journal.record_signal(sig)          # idempotent per candle
    journal.open_trade(trade, sid)            # sets trade.db_id
    journal.close_trade(trade)                # on TP/SL/expiry
    journal.load_open_trades("BTCUSDT")       # restore after a restart
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from .models import Direction, PaperTrade, Signal, TradeStatus

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    candle_open_time INTEGER NOT NULL,
    strategy_name TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    direction TEXT NOT NULL,
    signal_score REAL NOT NULL,
    score_breakdown TEXT,
    market_regime TEXT,
    entry_price REAL, stop_loss REAL, take_profit REAL, expected_rr REAL,
    position_size REAL, risk_amount REAL, risk_pct REAL,
    risk_status TEXT, rejection_reason TEXT,
    indicators TEXT, trigger_reasons TEXT,
    status TEXT NOT NULL,
    ai_context TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (exchange, symbol, timeframe, strategy_name, strategy_version, candle_open_time)
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER REFERENCES signals(id),
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_entry REAL, filled_entry REAL,
    stop_loss REAL, take_profit REAL,
    position_size REAL, risk_amount REAL, risk_pct REAL,
    entry_fee REAL, exit_fee REAL, slippage REAL,
    exit_price REAL, pnl_amount REAL, pnl_pct REAL, actual_rr REAL,
    mfe REAL, mae REAL,
    bars_held INTEGER, init_risk REAL, extreme REAL,
    opened_at INTEGER, closed_at INTEGER,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_signals_symbol_time ON signals(symbol, candle_open_time);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Journal:
    def __init__(self, path: str = "data/journal.db"):
        self.path = path
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ---------- signals ----------
    def record_signal(self, sig: Signal) -> Optional[int]:
        """Insert a signal. Returns its id, or the EXISTING id when this candle
        was already recorded (idempotent — design §6.3), so replaying a candle
        can never duplicate history."""
        key = (sig.exchange, sig.symbol, sig.timeframe, sig.strategy_name,
               sig.strategy_version, sig.candle_open_time)
        cur = self.conn.execute(
            """INSERT OR IGNORE INTO signals
               (exchange, symbol, timeframe, candle_open_time, strategy_name,
                strategy_version, direction, signal_score, score_breakdown,
                market_regime, entry_price, stop_loss, take_profit, expected_rr,
                position_size, risk_amount, risk_pct, risk_status,
                rejection_reason, indicators, trigger_reasons, status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (*key[:6],
             sig.direction.value if isinstance(sig.direction, Direction) else sig.direction,
             sig.signal_score, json.dumps(sig.score_breakdown),
             json.dumps(sig.market_regime), sig.entry_price, sig.stop_loss,
             sig.take_profit, sig.expected_rr, sig.position_size, sig.risk_amount,
             sig.risk_pct, sig.risk_status, sig.rejection_reason,
             json.dumps(sig.indicators), json.dumps(sig.trigger_reasons),
             sig.status, _now()))
        self.conn.commit()
        if cur.lastrowid:
            return cur.lastrowid
        row = self.conn.execute(
            """SELECT id FROM signals WHERE exchange=? AND symbol=? AND timeframe=?
               AND strategy_name=? AND strategy_version=? AND candle_open_time=?""",
            key).fetchone()
        return row["id"] if row else None

    def attach_ai_context(self, signal_id: int, ctx: dict[str, Any]) -> None:
        self.conn.execute("UPDATE signals SET ai_context=? WHERE id=?",
                          (json.dumps(ctx, ensure_ascii=False), signal_id))
        self.conn.commit()

    # ---------- trades ----------
    def open_trade(self, t: PaperTrade, signal_id: Optional[int] = None) -> int:
        cur = self.conn.execute(
            """INSERT INTO trades
               (signal_id, symbol, side, status, requested_entry, filled_entry,
                stop_loss, take_profit, position_size, risk_amount, risk_pct,
                entry_fee, exit_fee, slippage, mfe, mae, bars_held, init_risk,
                extreme, opened_at, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (signal_id, t.symbol, t.side.value, t.status.value, t.requested_entry,
             t.filled_entry, t.stop_loss, t.take_profit, t.position_size,
             t.risk_amount, t.risk_pct, t.entry_fee, t.exit_fee, t.slippage,
             t.max_favorable_excursion, t.max_adverse_excursion, t.bars_held,
             t.init_risk, t.extreme, t.opened_at, _now(), _now()))
        self.conn.commit()
        t.db_id = cur.lastrowid
        return cur.lastrowid

    def update_trade(self, t: PaperTrade) -> None:
        """Persist in-flight state (trailing stop, bars held, excursions) so a
        restart resumes the trade exactly where it left off."""
        if t.db_id is None:
            return
        self.conn.execute(
            """UPDATE trades SET status=?, stop_loss=?, exit_price=?, exit_fee=?,
               pnl_amount=?, pnl_pct=?, actual_rr=?, mfe=?, mae=?, bars_held=?,
               extreme=?, closed_at=?, updated_at=? WHERE id=?""",
            (t.status.value, t.stop_loss, t.exit_price, t.exit_fee, t.pnl_amount,
             t.pnl_pct, t.actual_rr, t.max_favorable_excursion,
             t.max_adverse_excursion, t.bars_held, t.extreme, t.closed_at,
             _now(), t.db_id))
        self.conn.commit()

    close_trade = update_trade  # same write; named for intent at the call site

    def load_open_trades(self, symbol: Optional[str] = None) -> list[PaperTrade]:
        """Restore trades still marked open (after a process restart)."""
        sql = "SELECT * FROM trades WHERE status='open'"
        args: tuple = ()
        if symbol:
            sql += " AND symbol=?"
            args = (symbol,)
        out = []
        for r in self.conn.execute(sql + " ORDER BY id", args):
            t = PaperTrade(
                signal_id=r["signal_id"], symbol=r["symbol"],
                side=Direction(r["side"]), status=TradeStatus(r["status"]),
                requested_entry=r["requested_entry"], stop_loss=r["stop_loss"],
                take_profit=r["take_profit"], position_size=r["position_size"],
                risk_amount=r["risk_amount"], risk_pct=r["risk_pct"],
                filled_entry=r["filled_entry"], entry_fee=r["entry_fee"] or 0.0,
                exit_fee=r["exit_fee"] or 0.0, slippage=r["slippage"] or 0.0,
                max_favorable_excursion=r["mfe"] or 0.0,
                max_adverse_excursion=r["mae"] or 0.0, opened_at=r["opened_at"],
            )
            t.bars_held = r["bars_held"] or 0
            t.init_risk = r["init_risk"] or 0.0
            t.extreme = r["extreme"] or 0.0
            t.db_id = r["id"]
            out.append(t)
        return out

    # ---------- reporting ----------
    def stats(self) -> dict[str, Any]:
        closed = self.conn.execute(
            "SELECT pnl_amount, actual_rr FROM trades WHERE status IN ('hit_tp','hit_sl','expired')"
        ).fetchall()
        pnls = [r["pnl_amount"] or 0.0 for r in closed]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gross_win, gross_loss = sum(wins), abs(sum(losses))
        n_signals = self.conn.execute("SELECT COUNT(*) c FROM signals").fetchone()["c"]
        n_open = self.conn.execute("SELECT COUNT(*) c FROM trades WHERE status='open'").fetchone()["c"]
        return {
            "signals": n_signals,
            "trades_closed": len(closed),
            "trades_open": n_open,
            "wins": len(wins), "losses": len(losses),
            "win_rate": round(len(wins) / len(closed) * 100, 2) if closed else 0.0,
            "net_pnl": round(sum(pnls), 4),
            "profit_factor": round(gross_win / gross_loss, 3) if gross_loss else (float("inf") if gross_win else 0.0),
            "expectancy": round(sum(pnls) / len(closed), 4) if closed else 0.0,
            "avg_win": round(sum(wins) / len(wins), 4) if wins else 0.0,
            "avg_loss": round(sum(losses) / len(losses), 4) if losses else 0.0,
        }

    def recent_trades(self, limit: int = 20) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)))

    def close(self) -> None:
        self.conn.close()
