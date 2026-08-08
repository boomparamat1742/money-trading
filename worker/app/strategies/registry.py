"""Strategy registry — selects strategies applicable to the current regime and
returns the best-scoring candidate (design F6)."""
from __future__ import annotations

from typing import Optional

from ..models import StrategyResult
from .base import Strategy, StrategyContext
from .breakout import Breakout
from .mean_reversion import MeanReversion
from .trend_following import TrendFollowing

ALL_STRATEGIES: list[Strategy] = [TrendFollowing(), Breakout(), MeanReversion()]


def best_signal(ctx: StrategyContext, strategies: Optional[list[Strategy]] = None) -> Optional[StrategyResult]:
    """Run every applicable strategy; keep the highest raw_score with no
    invalidations and a non-zero score."""
    strategies = strategies or ALL_STRATEGIES
    best: Optional[StrategyResult] = None
    for s in strategies:
        if not s.applicable(ctx):
            continue
        res = s.evaluate(ctx)
        if not res or res.raw_score <= 0 or res.invalidations:
            continue
        if best is None or res.raw_score > best.raw_score:
            best = res
    return best
