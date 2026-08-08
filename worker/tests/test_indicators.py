from worker.app.indicators import (
    ema, rsi, atr, sma, IndicatorEngine, MIN_LOOKBACK,
)
from worker.app.models import Candle


def test_sma_and_ema_basic():
    xs = [1, 2, 3, 4, 5]
    assert sma(xs, 5) == 3
    # EMA of a constant series equals the constant
    assert abs(ema([7.0] * 30, 10) - 7.0) < 1e-9


def test_rsi_all_gains_is_100():
    closes = [float(i) for i in range(1, 40)]  # strictly rising
    assert rsi(closes) == 100.0


def test_rsi_all_losses_is_zero():
    closes = [float(i) for i in range(40, 1, -1)]  # strictly falling
    assert rsi(closes) == 0.0


def test_atr_positive():
    highs = [10 + i for i in range(30)]
    lows = [8 + i for i in range(30)]
    closes = [9 + i for i in range(30)]
    a = atr(highs, lows, closes)
    assert a is not None and a > 0


def _mk(i, price):
    return Candle("binance", "BTCUSDT", "15m", 1_700_000_000_000 + i * 900_000,
                  price, price * 1.01, price * 0.99, price, 100.0)


def test_engine_ready_after_min_lookback():
    eng = IndicatorEngine()
    snap = None
    for i in range(MIN_LOOKBACK + 5):
        snap = eng.update(_mk(i, 100 + i))
    assert snap is not None and snap.ready
    assert "ema20" in snap.values and "rsi" in snap.values
