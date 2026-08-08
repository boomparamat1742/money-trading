"""Market Regime Detector (design §4.5).

Classifies the market so the Strategy Engine only runs strategies suited to
current conditions. Pure function of the indicator snapshot → reproducible.
"""
from __future__ import annotations

from .models import IndicatorSnapshot, RegimeResult

ADX_TREND = 20.0        # ADX above → trending
ADX_STRONG = 30.0
ATR_HIGH_PCTL = 0.85    # ATR percentile above → high volatility
VOL_LOW_RATIO = 0.4     # volume below 40% of its MA → thin liquidity


def detect_regime(snap: IndicatorSnapshot) -> RegimeResult:
    v = snap.values
    ema20 = v.get("ema20")
    ema50 = v.get("ema50")
    close = v.get("close")
    adx = v.get("adx", 0.0)
    atr_pctl = v.get("atr_percentile", 0.5)
    vol_ratio = v.get("vol_ratio", 1.0)

    volatility_state = "high" if atr_pctl >= ATR_HIGH_PCTL else ("low" if atr_pctl <= 0.15 else "normal")
    liquidity_state = "low" if vol_ratio < VOL_LOW_RATIO else "normal"

    if not snap.ready or ema20 is None or ema50 is None or close is None:
        return RegimeResult("unsafe", 0.0, volatility_state, liquidity_state)

    # strength scales with how decisive ADX is
    strength = max(0.0, min(1.0, (adx - ADX_TREND) / (ADX_STRONG - ADX_TREND))) if adx >= ADX_TREND else 0.0

    if volatility_state == "high":
        regime = "high_volatility"
    elif adx >= ADX_TREND and ema20 > ema50 and close > ema20:
        regime = "uptrend"
    elif adx >= ADX_TREND and ema20 < ema50 and close < ema20:
        regime = "downtrend"
    else:
        regime = "sideway"
        strength = max(strength, 0.3)

    return RegimeResult(regime, round(strength, 3), volatility_state, liquidity_state)
