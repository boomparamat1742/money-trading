"""Strategy B: Breakout / Breakdown (design §4.6) — v1.2 symmetric long & short.

v1.1 was long-only. On futures (perps) a breakdown is tradable the same way a
breakout is, so the rules are mirrored: break the recent HIGH with the 1h+4h
trend UP → long; break the recent LOW with the trend DOWN → short. The quality
filters (volume conviction, healthy volatility, not-choppy) are identical.
"""
from __future__ import annotations

from typing import Optional

from ..models import Direction, StrategyResult
from .base import Strategy, StrategyContext


# --- quality filter thresholds (v1.3: relaxed so the strategy actually fires;
# v1.2 values were 1.3 / 0.2-0.9 / 20 and produced 1 signal in 30,000 bars) ---
MIN_VOL_RATIO = 1.1      # volume vs its 20-bar MA
ATR_PCTL_MIN = 0.15      # not a dead market
ATR_PCTL_MAX = 0.95      # not pure chaos
MIN_ADX = 15.0           # not choppy


class Breakout(Strategy):
    name = "breakout"
    version = "1.3.0"
    # trend-aligned breaks, plus range breaks out of a sideway market
    allowed_regimes = ("uptrend", "downtrend", "sideway")

    def evaluate(self, ctx: StrategyContext) -> Optional[StrategyResult]:
        v = ctx.snapshot.values
        close = v.get("close")
        vol_ratio = v.get("vol_ratio", 0.0)
        atr_pctl = v.get("atr_percentile", 0.5)
        adx = v.get("adx", 0.0)
        if close is None:
            return None

        # (1) HARD gate: direction must follow the confirmed 1h+4h trend
        if ctx.htf_trend > 0:
            direction = Direction.LONG
            lvl20, lvl50 = v.get("high20"), v.get("high50")
            if lvl20 is None:
                return None
            broke = close >= lvl20 or (lvl50 is not None and close >= lvl50)
            break_reason = "close_above_recent_high"
            trend_reason = "htf_up"
        elif ctx.htf_trend < 0:
            direction = Direction.SHORT
            lvl20, lvl50 = v.get("low20"), v.get("low50")
            if lvl20 is None:
                return None
            broke = close <= lvl20 or (lvl50 is not None and close <= lvl50)
            break_reason = "close_below_recent_low"
            trend_reason = "htf_down"
        else:
            return None  # 1h/4h disagree → stand aside

        if not broke:
            return None

        # (2) quality filters (avoid weak / chaotic / choppy breaks)
        if vol_ratio < MIN_VOL_RATIO:
            return None                      # need some volume conviction
        if not (ATR_PCTL_MIN <= atr_pctl <= ATR_PCTL_MAX):
            return None                      # not dead, not chaotic
        if adx < MIN_ADX:
            return None                      # not choppy

        reasons = [trend_reason, break_reason, "volume_above_average", "healthy_volatility"]
        raw = min(100.0, 48 + 10 * len(reasons) + min((vol_ratio - 1) * 15, 20))
        return StrategyResult(self.name, self.version, direction, raw, reasons, [])
