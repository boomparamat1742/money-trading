from worker.app.data_quality import DataQualityChecker
from worker.app.models import Candle

STEP = 900_000
T0 = 1_700_000_000_000


def _c(i, o=100, h=101, l=99, c=100, v=10.0):
    return Candle("binance", "BTCUSDT", "15m", T0 + i * STEP, o, h, l, c, v)


def test_accepts_clean_sequence():
    q = DataQualityChecker()
    assert q.check(_c(0)).ok
    assert q.check(_c(1)).ok


def test_rejects_duplicate():
    q = DataQualityChecker()
    q.check(_c(0))
    assert not q.check(_c(0)).ok


def test_rejects_backwards_timestamp():
    q = DataQualityChecker()
    q.check(_c(5))
    r = q.check(_c(4))
    assert not r.ok and r.reason == "timestamp_backwards"


def test_detects_gap():
    q = DataQualityChecker()
    q.check(_c(0))
    r = q.check(_c(3))  # skipped bars 1,2
    assert r.ok and r.gap_bars == 2


def test_rejects_illogical_ohlc():
    q = DataQualityChecker()
    r = q.check(_c(0, o=100, h=90, l=95, c=100))  # high < low/open
    assert not r.ok and r.reason == "ohlc_illogical"


def test_rejects_negative_volume():
    q = DataQualityChecker()
    assert not q.check(_c(0, v=-1)).ok
