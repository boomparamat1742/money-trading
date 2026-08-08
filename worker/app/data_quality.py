"""Data Quality Service (design §4.2) — rejects/flags bad candles before they
reach the pipeline. Pure and testable."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .models import Candle

TIMEFRAME_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000,
                "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}


@dataclass
class QualityResult:
    ok: bool
    reason: Optional[str] = None
    gap_bars: int = 0  # missing candles detected before this one


class DataQualityChecker:
    def __init__(self):
        self._last_open: dict[str, int] = {}   # key -> last open_time seen
        self._seen: set[str] = set()

    def check(self, c: Candle) -> QualityResult:
        step = TIMEFRAME_MS.get(c.timeframe)
        if step is None:
            return QualityResult(False, "unknown_timeframe")
        # OHLC sanity
        if not (c.low <= c.open <= c.high and c.low <= c.close <= c.high) or c.high < c.low:
            return QualityResult(False, "ohlc_illogical")
        if c.volume < 0:
            return QualityResult(False, "negative_volume")

        series_key = f"{c.exchange}:{c.symbol}:{c.timeframe}"
        # duplicate candle
        if c.idempotency_key in self._seen:
            return QualityResult(False, "duplicate_candle")

        last = self._last_open.get(series_key)
        gap = 0
        if last is not None:
            if c.open_time <= last:
                return QualityResult(False, "timestamp_backwards")
            delta = c.open_time - last
            if delta % step != 0:
                return QualityResult(False, "misaligned_timestamp")
            gap = int(delta // step) - 1   # missing bars between last and this

        self._seen.add(c.idempotency_key)
        self._last_open[series_key] = c.open_time
        return QualityResult(True, gap_bars=gap)
