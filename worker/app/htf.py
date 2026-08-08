"""Multi-timeframe trend confirmation (design §4.6 higher-TF confirmation).

Aggregates the base timeframe into one or more higher timeframes (e.g. 1h, 4h)
and reports a combined trend that is +1 only when ALL confirm timeframes agree
up, -1 when all agree down, else 0 (unknown/mixed). Using agreement as a hard
gate means the system stands aside when timeframes disagree.
"""
from __future__ import annotations

from .candle_builder import TimeframeAggregator
from .data_quality import TIMEFRAME_MS
from .indicators import IndicatorEngine
from .models import Candle


class MultiTimeframeTrend:
    def __init__(self, base_tf: str, confirm_tfs: list[str]):
        self.aggs: list[TimeframeAggregator] = []
        self.engines: list[IndicatorEngine] = []
        self.trends: list[int] = []
        for tf in confirm_tfs:
            if TIMEFRAME_MS.get(tf, 0) > TIMEFRAME_MS.get(base_tf, 0):
                self.aggs.append(TimeframeAggregator(base_tf, tf))
                self.engines.append(IndicatorEngine())
                self.trends.append(0)

    def update(self, candle: Candle) -> int:
        for i, agg in enumerate(self.aggs):
            closed = agg.add(candle)
            if closed is not None:
                snap = self.engines[i].update(closed)
                e20, e50 = snap.get("ema20"), snap.get("ema50")
                if e20 == e20 and e50 == e50:  # not NaN
                    self.trends[i] = 1 if e20 > e50 else -1
        if not self.trends:
            return 0
        if all(t > 0 for t in self.trends):
            return 1
        if all(t < 0 for t in self.trends):
            return -1
        return 0
