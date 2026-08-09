"""funding_carry — market-neutral carry ต้องวัดผลแบบไม่หลอกตัวเอง

บั๊กเดิม: บันทึก return เฉพาะวันที่ถือสถานะ → Sharpe เว่อร์ (นับแต่วันได้กำไร)
และ n น้อยจนตัดสินไม่ได้ กลยุทธ์ market-neutral จริงต้องนิ่ง (0) ในวันที่ไม่ถือ
"""
from research.lab.core import DAY_MS, DataBundle
from research.lab.hypotheses import FundingCarry

D0 = 1_700_000_000_000 // DAY_MS * DAY_MS


def _funding(pattern: dict[str, list[float]], spot=None, perp=None) -> DataBundle:
    """pattern: sym -> รายการ funding รายวันเรียงตามวัน (spot/perp เป็น closes รายวัน)"""
    n = max(len(v) for v in pattern.values())
    days = [D0 + i * DAY_MS for i in range(n)]
    funding = {sym: {days[i]: v[i] for i in range(len(v))} for sym, v in pattern.items()}
    # ค่าเริ่มต้น: spot=perp=ราคาคงที่ → basis ไม่ขยับ (แยกผลของ funding ล้วน)
    prices = {sym: {days[i]: (spot[sym][i] if spot else 100.0) for i in range(n)} for sym in pattern}
    perps = {sym: {days[i]: (perp[sym][i] if perp else 100.0) for i in range(n)} for sym in pattern}
    return DataBundle(funding=funding, prices=prices, perp=perps)


def test_emits_a_return_every_day_including_flat_ones():
    """วันที่ไม่มีเหรียญไหน funding เกิน floor → ต้องได้ 0 ไม่ใช่ข้ามทิ้ง"""
    # 10 วัน: ครึ่งแรก funding บวกสูง ครึ่งหลังศูนย์ (ไม่มีอะไรให้ถือ)
    data = _funding({"BTC": [0.01] * 5 + [0.0] * 5,
                     "ETH": [0.01] * 5 + [0.0] * 5})
    h = FundingCarry()
    t, r, pos = h.run(data, {"lookback": 2, "top_n": 2, "floor": 0.0005})
    assert len(t) == len(data.dates)          # ปล่อยครบทุกวัน ไม่ข้าม
    assert len(r) == len(t) == len(pos)
    # ช่วงหลังไม่ถือ → return เป็น 0 (ไม่ใช่หายไป) และ exposure = 0
    assert any(p == 0.0 for p in pos)
    assert r[-1] == 0.0


def test_idle_days_drag_the_sharpe_down_to_reality():
    """ถ้าข้ามวันเฉยๆ Sharpe จะสูงลวง — นับครบแล้วต้องต่ำลงอย่างมีนัย"""
    from research.lab.core import compute_stats
    # funding บวกคงที่ 20 วัน แล้วนิ่ง 200 วัน
    data = _funding({"BTC": [0.005] * 20 + [0.0] * 200,
                     "ETH": [0.005] * 20 + [0.0] * 200})
    h = FundingCarry()
    t, r, pos = h.run(data, {"lookback": 2, "top_n": 2, "floor": 0.0005})
    full = compute_stats(r, positions=pos)
    only_active = compute_stats([x for x in r if x != 0.0])
    # การนับแต่วัน active ทำให้ Sharpe สูงกว่าความจริงมาก — นี่คือกับดักที่แก้แล้ว
    assert only_active.sharpe > full.sharpe
    assert full.exposure < 0.2               # ถืออยู่จริงไม่ถึง 20% ของเวลา


def test_turnover_cost_is_charged_even_when_closing_to_flat():
    """วันที่ปิดสถานะจนว่าง ต้องยังโดน cost ปิด ไม่ใช่ 0 เฉยๆ"""
    data = _funding({"BTC": [0.01, 0.01, 0.01, 0.0, 0.0],
                     "ETH": [0.01, 0.01, 0.01, 0.0, 0.0]})
    h = FundingCarry()
    t, r, pos = h.run(data, {"lookback": 1, "top_n": 2, "floor": 0.0005})
    # วันที่เปลี่ยนจากถือ→ว่าง ต้องมี cost ติดลบอย่างน้อยหนึ่งวัน
    assert any(x < 0 for x in r)


def test_basis_move_against_us_eats_into_funding():
    """perp วิ่งขึ้นแรงกว่า spot (basis กว้างขึ้น) → เราขาดทุนจากขา short perp
    ต้องหักออกจาก funding ที่ได้ ไม่ใช่เก็บ funding เต็มๆ แบบไม่มีความเสี่ยง"""
    # 3 วัน funding +0.001/วัน; วันที่ 2→3 perp +2% แต่ spot +0% → Δbasis = +2%
    spot = {"BTC": [100.0, 100.0, 100.0], "ETH": [100.0, 100.0, 100.0]}
    perp = {"BTC": [100.0, 100.0, 102.0], "ETH": [100.0, 100.0, 102.0]}
    data = _funding({"BTC": [0.001, 0.001, 0.001], "ETH": [0.001, 0.001, 0.001]},
                    spot=spot, perp=perp)
    h = FundingCarry()
    _, r, _ = h.run(data, {"lookback": 1, "top_n": 2, "floor": 0.0})
    # วันสุดท้าย: funding +0.001 − Δbasis 0.02 = −0.019 (ขาดทุน แม้ funding บวก)
    assert r[-1] < 0
    assert abs(r[-1] - (0.001 - 0.02)) < 1e-6


def test_flat_basis_leaves_pure_funding():
    """spot กับ perp ขยับเท่ากัน → Δbasis = 0 → เหลือ funding ล้วน (คุมได้ว่า basis
    ไม่เพี้ยนตอนราคาขยับพร้อมกัน)"""
    spot = {"BTC": [100.0, 110.0, 121.0], "ETH": [100.0, 110.0, 121.0]}
    perp = {"BTC": [100.0, 110.0, 121.0], "ETH": [100.0, 110.0, 121.0]}
    data = _funding({"BTC": [0.001, 0.001, 0.001], "ETH": [0.001, 0.001, 0.001]},
                    spot=spot, perp=perp)
    h = FundingCarry()
    _, r, _ = h.run(data, {"lookback": 1, "top_n": 2, "floor": 0.0})
    assert abs(r[-1] - 0.001) < 1e-6        # funding ล้วน ไม่มี basis รบกวน


def test_neutral_benchmark_is_cash():
    assert FundingCarry().neutral is True
