"""Strategy A: Trend Following (design §4.6) — v1.1 with hard HTF gate + anti-chop."""
from __future__ import annotations

from typing import Optional

from ..models import Direction, StrategyResult
from .base import Strategy, StrategyContext, is_choppy


class TrendFollowing(Strategy):
    name = "trend_following"
    version = "1.1.0"
    allowed_regimes = ("uptrend", "downtrend")

    def evaluate(self, ctx: StrategyContext) -> Optional[StrategyResult]:
        v = ctx.snapshot.values
        ema20, ema50, close = v.get("ema20"), v.get("ema50"), v.get("close")
        adx, macd_hist, atr = v.get("adx", 0.0), v.get("macd_hist", 0.0), v.get("atr")
        if None in (ema20, ema50, close, atr):
            return None

        # (1) HARD higher-timeframe gate: trade only WITH the 1h+4h trend
        if ctx.regime.regime == "uptrend" and ctx.htf_trend > 0:
            direction = Direction.LONG
        elif ctx.regime.regime == "downtrend" and ctx.htf_trend < 0:
            direction = Direction.SHORT
        else:
            return None  # no HTF alignment → stand aside

        # (2) anti-chop: need a real, separated trend
        if is_choppy(v):
            return None

        reasons = ["htf_aligned", "adx_trending", "ema_separated"]
        if direction == Direction.LONG and close > ema20 and macd_hist > 0:
            reasons += ["close_above_ema20", "macd_positive"]
        elif direction == Direction.SHORT and close < ema20 and macd_hist < 0:
            reasons += ["close_below_ema20", "macd_negative"]

        raw = min(100.0, 45 + 8 * len(reasons) + min(adx, 40) * 0.5)
        return StrategyResult(self.name, self.version, direction, raw, reasons, [])
