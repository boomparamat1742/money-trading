"""Indicator Engine (design §4.4) — pure Python, no numpy/pandas required.

The engine stores a bounded window of closed candles and recomputes the
snapshot on each update. It is deterministic: same candles in → same values out
(design N8). Signals are withheld until `ready` (minimum lookback met).

If numpy is later installed you can swap the internals for incremental math;
the public IndicatorEngine.update(candle) -> IndicatorSnapshot contract stays.
"""
from __future__ import annotations

from collections import deque
from statistics import fmean, pstdev
from typing import Optional

from .models import Candle, IndicatorSnapshot

MIN_LOOKBACK = 60  # enough to warm EMA50/ADX/Bollinger meaningfully


def sma(xs: list[float], n: int) -> Optional[float]:
    if len(xs) < n:
        return None
    return fmean(xs[-n:])


def ema_series(xs: list[float], n: int) -> list[float]:
    """EMA seeded with the SMA of the first n values."""
    if len(xs) < n:
        return []
    k = 2 / (n + 1)
    out = [fmean(xs[:n])]
    for x in xs[n:]:
        out.append(x * k + out[-1] * (1 - k))
    return out


def ema(xs: list[float], n: int) -> Optional[float]:
    s = ema_series(xs, n)
    return s[-1] if s else None


def rsi(closes: list[float], n: int = 14) -> Optional[float]:
    if len(closes) < n + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    # Wilder smoothing
    avg_g = fmean(gains[:n])
    avg_l = fmean(losses[:n])
    for i in range(n, len(gains)):
        avg_g = (avg_g * (n - 1) + gains[i]) / n
        avg_l = (avg_l * (n - 1) + losses[i]) / n
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return 100 - (100 / (1 + rs))


def macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9):
    ef, es = ema_series(closes, fast), ema_series(closes, slow)
    if not ef or not es:
        return None, None, None
    # align tails
    m = min(len(ef), len(es))
    macd_line = [ef[-m + i] - es[-m + i] for i in range(m)]
    sig = ema_series(macd_line, signal)
    if not sig:
        return macd_line[-1], None, None
    return macd_line[-1], sig[-1], macd_line[-1] - sig[-1]


def bollinger(closes: list[float], n: int = 20, k: float = 2.0):
    if len(closes) < n:
        return None, None, None
    window = closes[-n:]
    mid = fmean(window)
    sd = pstdev(window)
    return mid + k * sd, mid, mid - k * sd


def true_ranges(highs, lows, closes) -> list[float]:
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))
    return trs


def _wilder(xs: list[float], n: int) -> Optional[float]:
    if len(xs) < n:
        return None
    val = fmean(xs[:n])
    for x in xs[n:]:
        val = (val * (n - 1) + x) / n
    return val


def atr(highs, lows, closes, n: int = 14) -> Optional[float]:
    trs = true_ranges(highs, lows, closes)
    return _wilder(trs, n)


def adx(highs, lows, closes, n: int = 14):
    """Returns (adx, plus_di, minus_di)."""
    if len(closes) < 2 * n:
        return None, None, None
    plus_dm, minus_dm, trs = [], [], []
    for i in range(1, len(closes)):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm.append(up if (up > down and up > 0) else 0.0)
        minus_dm.append(down if (down > up and down > 0) else 0.0)
        trs.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))
    atr_s = _wilder(trs, n)
    p_dm = _wilder(plus_dm, n)
    m_dm = _wilder(minus_dm, n)
    if not atr_s or atr_s == 0:
        return None, None, None
    plus_di = 100 * p_dm / atr_s
    minus_di = 100 * m_dm / atr_s
    denom = plus_di + minus_di
    dx = 100 * abs(plus_di - minus_di) / denom if denom else 0.0
    # single-point DX used as ADX proxy for MVP; full ADX = Wilder avg of DX series
    return dx, plus_di, minus_di


SESSION_MS = 86_400_000   # คริปโตเทรด 24/7 → ใช้วัน UTC เป็น session (ตรงกับ TradingView)
VWAP_MIN_BARS = 8         # ต้นวันข้อมูลน้อยเกินไป VWAP จะเหวี่ยงและเกาะราคา — รอก่อน


def session_vwap(candles: list[Candle], min_bars: int = VWAP_MIN_BARS) -> Optional[dict]:
    """Session VWAP (รีเซ็ต 00:00 UTC) พร้อม σ bands — แบบเดียวกับที่ TradingView
    และ Binance แสดงเป็นค่า default สำหรับคริปโต.

        VWAP = Σ(typical_price × volume) / Σ(volume),  typical = (H+L+C)/3

    คืน None จนกว่าจะมีอย่างน้อย min_bars แท่งในวันนั้น เพราะ VWAP ตอนต้น session
    คำนวณจากไม่กี่แท่ง ค่าจะติดราคาแทบตลอดจนไม่มีความหมาย
    """
    if not candles:
        return None
    day = candles[-1].open_time // SESSION_MS
    todays = [c for c in candles if c.open_time // SESSION_MS == day]
    if len(todays) < min_bars:
        return None
    total_vol = sum(c.volume for c in todays)
    if total_vol <= 0:
        return None
    typicals = [((c.high + c.low + c.close) / 3, c.volume) for c in todays]
    vwap = sum(tp * vol for tp, vol in typicals) / total_vol
    # ส่วนเบี่ยงเบนแบบถ่วงน้ำหนักด้วย volume (ใช้ทำ band เหมือน TradingView)
    variance = sum(vol * (tp - vwap) ** 2 for tp, vol in typicals) / total_vol
    return {"vwap": vwap, "sd": variance ** 0.5, "bars": len(todays)}


def roc(closes: list[float], n: int = 10) -> Optional[float]:
    if len(closes) < n + 1 or closes[-n - 1] == 0:
        return None
    return (closes[-1] - closes[-n - 1]) / closes[-n - 1] * 100


class IndicatorEngine:
    """Maintains a rolling window per symbol/timeframe and emits snapshots."""

    def __init__(self, maxlen: int = 320):  # ≥ EMA200 warmup; smaller = faster recompute
        self._c: deque[Candle] = deque(maxlen=maxlen)
        self._atr_hist: deque[float] = deque(maxlen=200)  # for ATR percentile

    @property
    def count(self) -> int:
        return len(self._c)

    def update(self, candle: Candle) -> IndicatorSnapshot:
        self._c.append(candle)
        closes = [c.close for c in self._c]
        highs = [c.high for c in self._c]
        lows = [c.low for c in self._c]
        vols = [c.volume for c in self._c]

        snap = IndicatorSnapshot(ready=len(self._c) >= MIN_LOOKBACK)
        v = snap.values
        v["close"] = closes[-1]

        for n in (20, 50, 200):
            e = ema(closes, n)
            if e is not None:
                v[f"ema{n}"] = e
        r = rsi(closes)
        if r is not None:
            v["rsi"] = r
        m, sig, hist = macd(closes)
        if m is not None:
            v["macd"] = m
        if sig is not None:
            v["macd_signal"] = sig
        if hist is not None:
            v["macd_hist"] = hist
        ub, mb, lb = bollinger(closes)
        if ub is not None:
            v["bb_upper"], v["bb_mid"], v["bb_lower"] = ub, mb, lb
        a = atr(highs, lows, closes)
        if a is not None:
            v["atr"] = a
            self._atr_hist.append(a)
            if len(self._atr_hist) >= 20:
                below = sum(1 for x in self._atr_hist if x <= a)
                v["atr_percentile"] = below / len(self._atr_hist)
        adx_v, pdi, mdi = adx(highs, lows, closes)
        if adx_v is not None:
            v["adx"], v["plus_di"], v["minus_di"] = adx_v, pdi, mdi
        rc = roc(closes)
        if rc is not None:
            v["roc"] = rc
        vma = sma(vols, 20)
        if vma is not None:
            v["vol_ma20"] = vma
            v["vol_ratio"] = (vols[-1] / vma) if vma else 0.0
        vw = session_vwap(list(self._c))
        if vw:
            v["vwap"] = vw["vwap"]
            v["vwap_sd"] = vw["sd"]
            v["vwap_bars"] = float(vw["bars"])
            v["vwap_upper1"] = vw["vwap"] + vw["sd"]
            v["vwap_lower1"] = vw["vwap"] - vw["sd"]
            v["vwap_upper2"] = vw["vwap"] + 2 * vw["sd"]
            v["vwap_lower2"] = vw["vwap"] - 2 * vw["sd"]
            if vw["vwap"]:
                v["vwap_dist_pct"] = (closes[-1] - vw["vwap"]) / vw["vwap"] * 100
        hh = max(highs[-20:]) if len(highs) >= 20 else None
        ll = min(lows[-20:]) if len(lows) >= 20 else None
        if hh is not None:
            v["high20"], v["low20"] = hh, ll
        if len(highs) >= 50:
            v["high50"], v["low50"] = max(highs[-50:]), min(lows[-50:])
        return snap
