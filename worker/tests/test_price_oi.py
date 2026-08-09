"""price + OI confirmation — กลไก long/short ตาม new money (Technical Paper §15)"""
from research.lab.core import DAY_MS
from research.lab.hypotheses import PriceOIConfirm, _oi_confirmed_daily

D0 = 1_700_000_000_000 // DAY_MS * DAY_MS


def _series(vals):
    return {D0 + i * DAY_MS: v for i, v in enumerate(vals)}, [D0 + i * DAY_MS for i in range(len(vals))]


def test_price_up_oi_up_goes_long_and_profits():
    # ราคาขึ้นต่อเนื่อง + OI ขึ้นต่อเนื่อง → long → กำไร
    px, dates = _series([100, 101, 102, 103, 104, 105, 106, 107])
    oi = {d: 1000 + i * 10 for i, d in enumerate(dates)}
    r, pos = _oi_confirmed_daily(px, oi, dates, L=2, cost=0.0)
    assert sum(r) > 0
    assert any(p == 1.0 for p in pos)


def test_price_down_oi_up_goes_short_and_profits():
    # ราคาลงต่อเนื่อง + OI ขึ้น (new shorts) → short → กำไรจากขาลง
    px, dates = _series([107, 106, 105, 104, 103, 102, 101, 100])
    oi = {d: 1000 + i * 10 for i, d in enumerate(dates)}
    r, pos = _oi_confirmed_daily(px, oi, dates, L=2, cost=0.0)
    assert sum(r) > 0                       # ชนะแม้ตลาดขาลง (จุดที่ user อยากได้)


def test_falling_oi_stands_aside():
    # ราคาขึ้น แต่ OI ลง (short covering ไม่ใช่ money ใหม่) → flat
    px, dates = _series([100, 101, 102, 103, 104, 105])
    oi = {d: 2000 - i * 10 for i, d in enumerate(dates)}
    r, pos = _oi_confirmed_daily(px, oi, dates, L=2, cost=0.0)
    assert all(p == 0.0 for p in pos)
    assert sum(r) == 0.0


def test_registered_and_directional():
    from research.lab.hypotheses import REGISTRY
    assert REGISTRY["price_oi_confirm"] is PriceOIConfirm
    assert PriceOIConfirm().neutral is False
