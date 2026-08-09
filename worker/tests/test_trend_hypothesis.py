"""สมมติฐาน trend_follow_4h — รันกลยุทธ์จริงบน 4h แล้วแปลงเป็น return รายวัน

จุดที่เสี่ยงพลาดสุดคือตัวแปลงไม้→รายวัน (bucket วัน, exposure, จัดแกนให้ตรง)
เทสต์ชุดนี้ล็อกไว้ด้วยไม้จำลองที่คำนวณมือได้ ไม่แตะเน็ต
"""
from types import SimpleNamespace

from research.lab.core import DAY_MS
from research.lab.hypotheses import REGISTRY, TrendFollowHTF, _trades_to_daily

D0 = 1_700_000_000_000 // DAY_MS * DAY_MS
DAYS = [D0 + i * DAY_MS for i in range(5)]


def _trade(opened_day, closed_day, pnl):
    return SimpleNamespace(opened_at=DAYS[opened_day] + 3_600_000,
                           closed_at=DAYS[closed_day] + 3_600_000, pnl_amount=pnl)


def test_pnl_is_bucketed_into_the_close_day_as_a_fraction_of_equity():
    # ไม้กำไร +100 บนทุน 10,000 → +1% ในวันที่ปิด
    trades = [_trade(0, 2, 100.0)]
    _, ret, _ = _trades_to_daily(trades, DAYS, equity=10_000.0)
    assert ret == [0.0, 0.0, 0.01, 0.0, 0.0]


def test_same_day_pnl_adds_up():
    trades = [_trade(0, 1, 100.0), _trade(1, 1, -50.0)]
    _, ret, _ = _trades_to_daily(trades, DAYS, equity=10_000.0)
    assert ret[1] == (100.0 - 50.0) / 10_000.0


def test_exposure_marks_every_day_a_trade_was_open():
    """ไม้เปิดวัน 0 ปิดวัน 2 → ถือครองวัน 0,1,2 (ไม่ใช่แค่วันปิด)"""
    _, _, pos = _trades_to_daily([_trade(0, 2, 10.0)], DAYS, equity=10_000.0)
    assert pos == [1.0, 1.0, 1.0, 0.0, 0.0]


def test_returns_align_to_the_given_date_grid():
    _, ret, pos = _trades_to_daily([], DAYS, equity=10_000.0)
    assert len(ret) == len(DAYS) == len(pos)
    assert ret == [0.0] * 5 and pos == [0.0] * 5


def test_pnl_outside_the_grid_is_ignored_not_crashed():
    """ไม้ที่ปิดนอกช่วง (แท่ง 4h ล้ำหน้า daily) ต้องถูกข้าม ไม่ระเบิด"""
    stray = SimpleNamespace(opened_at=DAYS[-1] + 10 * DAY_MS,
                            closed_at=DAYS[-1] + 12 * DAY_MS, pnl_amount=999.0)
    _, ret, _ = _trades_to_daily([stray], DAYS, equity=10_000.0)
    assert sum(ret) == 0.0


def test_registry_includes_the_new_hypothesis():
    assert "trend_follow_4h" in REGISTRY
    assert REGISTRY["trend_follow_4h"] is TrendFollowHTF


def test_param_grid_is_small_and_declared_upfront():
    """grid ใหญ่ = จูนจนหลอกตัวเอง — ล็อกไว้ว่าต้องเล็ก"""
    grid = TrendFollowHTF().param_grid()
    assert 1 <= len(grid) <= 6
    assert all("sl" in p and "tp" in p for p in grid)
    # ต้องคร่อมค่าที่ระบบใช้จริง (sl 1.5 / tp 3.0)
    assert {"sl": 1.5, "tp": 3.0} in grid
