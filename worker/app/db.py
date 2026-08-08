"""Persistence (design §4.8/§7) — SCAFFOLD.

schema.sql holds the DDL (signals, paper_trades, llm_calls, strategy_metrics).
Repository writes go through asyncpg in Phase 1+. For backtests, persistence is
skipped — results are aggregated in-memory by the metrics module.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .models import PaperTrade, Signal

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "migrations" / "schema.sql"


class Repository:
    """TODO(Phase 1): asyncpg pool + INSERTs keyed by the design's UNIQUE
    constraints / idempotency keys (§6.3), so re-processing a candle can't
    create a duplicate signal."""

    def __init__(self, dsn: Optional[str]):
        self.dsn = dsn

    async def insert_signal(self, sig: Signal) -> Optional[int]:  # pragma: no cover
        raise NotImplementedError("wire up in Phase 1")

    async def insert_trade(self, trade: PaperTrade) -> Optional[int]:  # pragma: no cover
        raise NotImplementedError("wire up in Phase 1")
