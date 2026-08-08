"""Strategy C: Mean Reversion (design §4.6) — v1.3, relaxed so it actually fires.

v1.2 required htf_trend == 0 (1h and 4h had to disagree exactly), regime sideway
AND adx < 20 AND a Bollinger touch AND an RSI extreme — the combination produced
ZERO signals in 30,000 bars. v1.3 keeps the core idea (fade extremes inside a
range) but relaxes the gates:

  • no longer requires the higher timeframes to disagree; a sideway regime is
    enough (the regime detector already implies weak trend)
  • ADX ceiling 20 → 25, RSI extremes 30/70 → 35/65
  • still refuses to fade a STRONGLY confirmed higher-timeframe trend
"""
from __future__ import annotations

from typing import Optional

from ..models import Direction, StrategyResult
from .base import Strategy, StrategyContext

MAX_ADX = 25.0        # above this the market trends too well to fade
RSI_LOW = 35.0
RSI_HIGH = 65.0


class MeanReversion(Strategy):
    name = "mean_reversion"
    version = "1.3.0"
    allowed_regimes = ("sideway",)

    def evaluate(self, ctx: StrategyContext) -> Optional[StrategyResult]:
        v = ctx.snapshot.values
        close = v.get("close")
        bb_upper, bb_lower = v.get("bb_upper"), v.get("bb_lower")
        rsi = v.get("rsi")
        adx = v.get("adx", 100.0)
        if None in (close, bb_upper, bb_lower, rsi):
            return None
        if adx >= MAX_ADX:
            return None  # trending enough to be dangerous for mean reversion

        if close <= bb_lower and rsi <= RSI_LOW:
            direction, reasons = Direction.LONG, ["touch_lower_band", "rsi_oversold", "range_market"]
        elif close >= bb_upper and rsi >= RSI_HIGH:
            direction, reasons = Direction.SHORT, ["touch_upper_band", "rsi_overbought", "range_market"]
        else:
            return None

        # don't fade a strongly confirmed higher-timeframe trend
        if ctx.htf_trend > 0 and direction == Direction.SHORT:
            return None
        if ctx.htf_trend < 0 and direction == Direction.LONG:
            return None
        if ctx.htf_trend == 0:
            reasons.append("htf_flat")

        raw = min(100.0, 40 + 12 * len(reasons))
        return StrategyResult(self.name, self.version, direction, raw, reasons, [])
