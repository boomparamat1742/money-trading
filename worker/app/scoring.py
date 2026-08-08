"""Signal Scoring (design §4.7) — an explainable 0..100 score with a per-category
breakdown. NOT an AI confidence and NOT a probability; thresholds must be tuned
by backtest (design §4.7 note)."""
from __future__ import annotations

from .models import Direction, IndicatorSnapshot, RegimeResult, ScoreResult, StrategyResult

MAX = {"trend": 25, "momentum": 20, "volume": 15, "multi_timeframe": 20,
       "volatility_quality": 10, "liquidity_quality": 10}


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def score_signal(snap: IndicatorSnapshot, regime: RegimeResult,
                 strat: StrategyResult, htf_trend: int) -> ScoreResult:
    v = snap.values
    long = strat.direction == Direction.LONG
    b: dict[str, float] = {}

    # Trend: ADX strength + EMA alignment agreeing with direction
    adx = v.get("adx", 0.0)
    ema20, ema50 = v.get("ema20"), v.get("ema50")
    trend = _clamp((adx - 15) / 25, 0, 1) * 0.6  # ADX 15→0, 40→0.6
    if ema20 is not None and ema50 is not None:
        aligned = (ema20 > ema50) if long else (ema20 < ema50)
        trend += 0.4 if aligned else 0.0
    b["trend"] = round(trend * MAX["trend"], 2)

    # Momentum: MACD histogram + RSI in a healthy zone for the direction
    macd_hist = v.get("macd_hist", 0.0)
    rsi = v.get("rsi", 50.0)
    mom = 0.0
    mom += 0.5 if (macd_hist > 0) == long else 0.0
    if long:
        mom += 0.5 * _clamp((rsi - 45) / 25, 0, 1)   # rising into strength
    else:
        mom += 0.5 * _clamp((55 - rsi) / 25, 0, 1)
    b["momentum"] = round(mom * MAX["momentum"], 2)

    # Volume: ratio to its MA
    vol_ratio = v.get("vol_ratio", 1.0)
    b["volume"] = round(_clamp((vol_ratio - 0.8) / 1.2, 0, 1) * MAX["volume"], 2)

    # Multi-timeframe confirmation
    if htf_trend == 0:
        mtf = 0.5
    else:
        mtf = 1.0 if (htf_trend > 0) == long else 0.0
    b["multi_timeframe"] = round(mtf * MAX["multi_timeframe"], 2)

    # Volatility quality: prefer normal (penalise both dead and chaotic)
    atr_pctl = v.get("atr_percentile", 0.5)
    vq = 1.0 - abs(atr_pctl - 0.5) * 2  # peak at 0.5
    b["volatility_quality"] = round(_clamp(vq, 0, 1) * MAX["volatility_quality"], 2)

    # Liquidity quality
    b["liquidity_quality"] = round((0.0 if regime.liquidity_state == "low" else 1.0) * MAX["liquidity_quality"], 2)

    total = round(sum(b.values()), 2)
    return ScoreResult(total=total, breakdown=b)
