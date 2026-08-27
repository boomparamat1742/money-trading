"""RTE paper portfolio — accounting, funding sign, DD halt, weight cap, persistence."""
import pytest

from worker.app.rte.config import RTEConfig
from worker.app.rte.portfolio import PaperPortfolio, Position


def _cfg(**kw):
    cfg = RTEConfig()
    for k, v in kw.items():
        object.__setattr__(cfg, k, v)   # frozen dataclass
    return cfg


def test_new_starts_all_cash():
    p = PaperPortfolio.new(_cfg(), ts=0)
    assert p.cash == p.cfg.starting_equity
    assert p.equity({}) == p.cfg.starting_equity
    assert p.gross_exposure({}) == 0.0


def test_entry_allocates_target_weight():
    p = PaperPortfolio.new(_cfg(slippage_rate=0.0, fee_rate=0.0), ts=0)
    p.rebalance({"BTCUSDT": 0.5}, {"BTCUSDT": 100.0}, ts=1)
    # 50% ของ 10000 = 5000 ที่ราคา 100 → 50 หน่วย
    assert p.positions["BTCUSDT"].qty == pytest.approx(50.0)
    assert p.cash == pytest.approx(5000.0)
    assert p.equity({"BTCUSDT": 100.0}) == pytest.approx(10000.0)
    assert p.gross_exposure({"BTCUSDT": 100.0}) == pytest.approx(0.5)


def test_fee_and_slippage_reduce_equity():
    p = PaperPortfolio.new(_cfg(slippage_rate=0.0002, fee_rate=0.0005,
                                max_single_symbol_weight=1.0), ts=0)
    p.rebalance({"BTCUSDT": 1.0}, {"BTCUSDT": 100.0}, ts=1)
    eq = p.equity({"BTCUSDT": 100.0})
    # ซื้อเต็มพอร์ต turnover~1 → ต้นทุน ~ (fee+slip) ของ notional ~10000
    assert eq < 10000.0
    assert p.cumulative_fee > 0 and p.cumulative_slippage > 0
    # ขาดทุนเริ่มต้น ~ fee(0.05%) + slippage(0.02%) ของ ~10000 ≈ 7 + นิดหน่อย
    assert 10000.0 - eq == pytest.approx(10000 * (0.0005 + 0.0002), rel=0.1)


def test_funding_sign_long_pays_positive():
    p = PaperPortfolio.new(_cfg(slippage_rate=0.0, fee_rate=0.0), ts=0)
    p.rebalance({"BTCUSDT": 1.0}, {"BTCUSDT": 100.0}, ts=1)
    marks = {"BTCUSDT": 100.0}
    before = p.equity(marks)
    f = p.apply_funding({"BTCUSDT": 0.01}, marks)   # funding บวก → long จ่าย
    assert f < 0
    assert p.equity(marks) < before
    # funding ลบ → long ได้รับ
    f2 = p.apply_funding({"BTCUSDT": -0.01}, marks)
    assert f2 > 0


def test_drawdown_triggers_halt_and_forces_cash():
    p = PaperPortfolio.new(_cfg(slippage_rate=0.0, fee_rate=0.0, max_strategy_drawdown=0.25,
                                max_single_symbol_weight=1.0), ts=0)
    p.rebalance({"BTCUSDT": 1.0}, {"BTCUSDT": 100.0}, ts=1)
    # ราคาร่วง 30% → DD เกิน 25%
    p.rebalance({"BTCUSDT": 1.0}, {"BTCUSDT": 70.0}, ts=2)
    assert p.halted
    # halt แล้ว: rebalance รอบใหม่บังคับล้างพอร์ต ไม่รับ entry
    p.rebalance({"BTCUSDT": 1.0}, {"BTCUSDT": 70.0}, ts=3)
    assert p.positions == {} or all(pos.qty == 0 for pos in p.positions.values())


def test_single_symbol_weight_cap_redistributes():
    p = PaperPortfolio.new(_cfg(slippage_rate=0.0, fee_rate=0.0, max_single_symbol_weight=0.60), ts=0)
    # ขอ 0.9 ให้ BTC, 0.1 ETH → BTC ต้องถูก cap ที่ 0.6, ส่วนเกินไป ETH
    p.rebalance({"BTCUSDT": 0.9, "ETHUSDT": 0.1}, {"BTCUSDT": 100.0, "ETHUSDT": 50.0}, ts=1)
    marks = {"BTCUSDT": 100.0, "ETHUSDT": 50.0}
    wb = p.positions["BTCUSDT"].qty * 100.0 / p.equity(marks)
    assert wb <= 0.60 + 1e-6
    assert p.gross_exposure(marks) <= 1.0 + 1e-6


def test_hold_when_change_below_threshold():
    p = PaperPortfolio.new(_cfg(slippage_rate=0.0, fee_rate=0.0, min_weight_change=0.005), ts=0)
    p.rebalance({"BTCUSDT": 0.5}, {"BTCUSDT": 100.0}, ts=1)
    fills = p.rebalance({"BTCUSDT": 0.502}, {"BTCUSDT": 100.0}, ts=2)  # เปลี่ยน 0.2% < 0.5%
    assert fills == []


def test_to_from_dict_roundtrip():
    p = PaperPortfolio.new(_cfg(), ts=5)
    p.rebalance({"BTCUSDT": 0.4, "ETHUSDT": 0.3}, {"BTCUSDT": 100.0, "ETHUSDT": 50.0}, ts=6)
    p.apply_funding({"BTCUSDT": 0.001}, {"BTCUSDT": 100.0, "ETHUSDT": 50.0})
    d = p.to_dict()
    q = PaperPortfolio.from_dict(p.cfg, d)
    marks = {"BTCUSDT": 110.0, "ETHUSDT": 55.0}
    assert q.equity(marks) == pytest.approx(p.equity(marks))
    assert q.cash == pytest.approx(p.cash)
    assert q.cumulative_funding == pytest.approx(p.cumulative_funding)
    assert set(q.positions) == set(p.positions)
