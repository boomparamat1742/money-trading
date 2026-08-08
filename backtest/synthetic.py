"""Synthetic OHLCV generator so the backtest runs with zero external data.

Produces a deterministic (seeded) price series with alternating trend and range
regimes, so all three strategies get a chance to fire. This is for wiring/demo
only — real edge validation needs real historical data + walk-forward (§8.1).
"""
from __future__ import annotations

import math
import random

from worker.app.models import Candle


def generate(symbol: str = "BTCUSDT", timeframe: str = "15m",
             bars: int = 3000, seed: int = 42, start_price: float = 60_000.0,
             exchange: str = "binance") -> list[Candle]:
    rng = random.Random(seed)
    step_ms = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000}[timeframe]
    t0 = 1_700_000_000_000
    price = start_price
    candles: list[Candle] = []
    for i in range(bars):
        # regime cycles: ~300 bars trending up, 200 ranging, 300 trending down, 200 ranging
        phase = i % 1000
        if phase < 300:
            drift = 0.0009
        elif phase < 500:
            drift = 0.0
        elif phase < 800:
            drift = -0.0009
        else:
            drift = 0.0
        vol = 0.004 + 0.002 * abs(math.sin(i / 50))
        ret = drift + rng.gauss(0, vol)
        open_p = price
        close_p = max(1.0, open_p * (1 + ret))
        hi = max(open_p, close_p) * (1 + abs(rng.gauss(0, vol / 2)))
        lo = min(open_p, close_p) * (1 - abs(rng.gauss(0, vol / 2)))
        base_vol = 100 + 400 * abs(math.sin(i / 37))
        volume = base_vol * (1 + abs(rng.gauss(0, 0.5)))
        candles.append(Candle(
            exchange=exchange, symbol=symbol, timeframe=timeframe,
            open_time=t0 + i * step_ms, open=round(open_p, 2), high=round(hi, 2),
            low=round(lo, 2), close=round(close_p, 2), volume=round(volume, 4),
            is_closed=True,
        ))
        price = close_p
    return candles


def load_csv(path: str, symbol: str, timeframe: str, exchange: str = "binance") -> list[Candle]:
    """Load candles from a CSV with header: open_time,open,high,low,close,volume
    (open_time in ms epoch). Use this for real historical data."""
    import csv

    out: list[Candle] = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            out.append(Candle(
                exchange=exchange, symbol=symbol, timeframe=timeframe,
                open_time=int(float(row["open_time"])),
                open=float(row["open"]), high=float(row["high"]),
                low=float(row["low"]), close=float(row["close"]),
                volume=float(row["volume"]), is_closed=True,
            ))
    return out
