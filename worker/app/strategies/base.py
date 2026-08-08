"""Base strategy contract."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..models import IndicatorSnapshot, RegimeResult, StrategyResult

# --- shared anti-chop filter thresholds (v1.1) ---
CHOP_ADX_MIN = 23.0     # below this ADX = choppy/rangebound → no trend/breakout trade
EMA_SEP_ATR = 0.5       # EMA20/50 must be at least this many ATRs apart (real separation)


def is_choppy(v: dict, adx_min: float = CHOP_ADX_MIN, sep_atr: float = EMA_SEP_ATR) -> bool:
    """True when the market looks like chop for trend strategies."""
    adx = v.get("adx", 0.0)
    ema20, ema50, atr = v.get("ema20"), v.get("ema50"), v.get("atr")
    if adx < adx_min:
        return True
    if ema20 is not None and ema50 is not None and atr:
        if abs(ema20 - ema50) < sep_atr * atr:
            return True
    return False


@dataclass
class StrategyContext:
    """Everything a strategy is allowed to see. Higher-timeframe trend is an
    optional confirmation hint (+1 up, -1 down, 0 unknown)."""
    snapshot: IndicatorSnapshot
    regime: RegimeResult
    htf_trend: int = 0


class Strategy:
    name: str = "base"
    version: str = "0.0.0"
    # regimes this strategy is allowed to fire in (design F6 — regime gating)
    allowed_regimes: tuple[str, ...] = ()

    def applicable(self, ctx: StrategyContext) -> bool:
        return (not self.allowed_regimes) or ctx.regime.regime in self.allowed_regimes

    def evaluate(self, ctx: StrategyContext) -> Optional[StrategyResult]:  # pragma: no cover
        raise NotImplementedError
