"""Candle Builder (design §4.3) — aggregates lower-timeframe closed candles into
higher timeframes (e.g. 15m → 1h) for multi-timeframe confirmation.

Only emits a higher-TF candle when it is fully closed.
"""
from __future__ import annotations

from typing import Optional

from .models import Candle
from .data_quality import TIMEFRAME_MS


class TimeframeAggregator:
    """Aggregate a base timeframe into a larger one (larger must be a multiple)."""

    def __init__(self, base_tf: str, target_tf: str):
        self.base_ms = TIMEFRAME_MS[base_tf]
        self.target_ms = TIMEFRAME_MS[target_tf]
        self.target_tf = target_tf
        assert self.target_ms % self.base_ms == 0, "target must be a multiple of base"
        self._bucket: Optional[Candle] = None

    def add(self, c: Candle) -> Optional[Candle]:
        """Add a base candle; return a completed target candle when the bucket
        rolls over, else None."""
        bucket_open = c.open_time - (c.open_time % self.target_ms)
        emitted: Optional[Candle] = None

        if self._bucket is None or self._bucket.open_time != bucket_open:
            if self._bucket is not None:
                self._bucket.is_closed = True
                emitted = self._bucket
            self._bucket = Candle(
                exchange=c.exchange, symbol=c.symbol, timeframe=self.target_tf,
                open_time=bucket_open, open=c.open, high=c.high, low=c.low,
                close=c.close, volume=c.volume, is_closed=False,
            )
        else:
            b = self._bucket
            b.high = max(b.high, c.high)
            b.low = min(b.low, c.low)
            b.close = c.close
            b.volume += c.volume
        return emitted
