"""RTE runner — rebalance timing, signal classify, offline replay ของ process_bar."""
import asyncio

import pytest

from worker.app.models import Candle
from worker.app.rte.config import RTEConfig
from worker.app.rte.portfolio import PaperPortfolio
from worker.app.rte import runner as rt

FOUR_H = 14_400_000


def _cfg():
    return RTEConfig()


def test_is_rebalance_bar_anchored_midnight():
    cfg = _cfg()
    assert rt.is_rebalance_bar(0, cfg)                    # epoch 0 = 00:00 UTC
    assert rt.is_rebalance_bar(6 * FOUR_H, cfg)           # +24h = 00:00 UTC ถัดไป
    assert not rt.is_rebalance_bar(1 * FOUR_H, cfg)       # 04:00 UTC
    assert not rt.is_rebalance_bar(3 * FOUR_H, cfg)       # 12:00 UTC


def test_classify_enter_and_exit():
    cfg = _cfg()

    class D:  # decision จำลอง
        to_cash = False
        target_weights = {"BTCUSDT": 0.4, "ETHUSDT": 0.3}
    ev = rt.classify({}, D(), cfg)
    assert {(s, a) for s, a, _, _ in ev} == {("BTCUSDT", "ENTER"), ("ETHUSDT", "ENTER")}

    class C:
        to_cash = True
        target_weights = {}
    ev2 = rt.classify({"BTCUSDT": 0.4}, C(), cfg)
    assert ev2 == [("BTCUSDT", "EXIT", 0.4, 0.0)]


class _FakeStore:
    def __init__(self):
        self.rebals, self.states, self.poslogs = [], [], []

    def load_state(self, ch):
        return None

    def record_rebalance(self, ch, dec, eq, dd, halted):
        self.rebals.append((dec.bar_close_time, eq, dd, halted))

    def save_state(self, ch, pf, w):
        self.states.append((pf.to_dict(), dict(w)))

    def record_positions(self, ch, bar_time, rows):
        self.poslogs.append((bar_time, rows))


class _FakeNotifier:
    def __init__(self):
        self.msgs = []

    async def send(self, text, priority="normal"):
        self.msgs.append((priority, text))


def _series(sym, n, start, step):
    return [Candle(exchange="binance", symbol=sym, timeframe="4h", open_time=i * FOUR_H,
                   open=start + i * step, high=start + i * step, low=start + i * step,
                   close=start + i * step, volume=1.0, is_closed=True) for i in range(n)]


def test_process_bar_replay_allocates_and_persists():
    cfg = _cfg()
    eng = rt.RTEEngine(cfg, _FakeStore(), _FakeNotifier())
    # 8 เหรียญขาขึ้น (ต่างความชันเล็กน้อยให้ ranking ไม่เสมอ)
    for i, s in enumerate(cfg.symbols):
        eng.window[s] = _series(s, 200, start=100.0 + i, step=0.5 + i * 0.01)
    eng.portfolio = PaperPortfolio.new(cfg, ts=0)

    bar = 192 * FOUR_H          # 192 % 6 == 0 → รอบ rebalance, มีประวัติ 193 แท่ง
    assert rt.is_rebalance_bar(bar, cfg)
    res = asyncio.run(eng.process_bar(bar, funding={}))
    assert res is not None
    dec, fills, events = res
    assert not dec.to_cash                       # ขาขึ้นทั้งตลาด → ลงทุน
    assert len(dec.selected) == cfg.top_n
    assert fills and all(f.side == "buy" for f in fills)
    assert eng.store.rebals and eng.store.states  # persist แล้ว
    marks = {s: eng.window[s][192].close for s in cfg.symbols}
    assert eng.portfolio.gross_exposure(marks) > 0
    # แจ้งเตือนออกเพราะมี ENTER events
    assert eng.notifier.msgs
    # A3: log P&L รายเหรียญ — ต้องมี 1 แถวต่อเหรียญที่ถือ
    assert eng.store.poslogs
    bar_time, rows = eng.store.poslogs[-1]
    assert {r["symbol"] for r in rows} == set(dec.selected)
    for r in rows:
        assert r["qty"] > 0 and r["notional"] > 0 and "unrealized_pnl" in r


def test_process_bar_skips_non_rebalance():
    cfg = _cfg()
    eng = rt.RTEEngine(cfg, _FakeStore(), _FakeNotifier())
    eng.portfolio = PaperPortfolio.new(cfg, ts=0)
    assert asyncio.run(eng.process_bar(1 * FOUR_H, funding={})) is None   # ไม่ใช่รอบ


def test_process_bar_applies_funding():
    cfg = _cfg()
    eng = rt.RTEEngine(cfg, _FakeStore(), _FakeNotifier())
    for i, s in enumerate(cfg.symbols):
        eng.window[s] = _series(s, 200, start=100.0 + i, step=0.5)
    eng.portfolio = PaperPortfolio.new(cfg, ts=0)
    asyncio.run(eng.process_bar(192 * FOUR_H, funding={}))     # เข้าสถานะก่อน
    before = eng.portfolio.cumulative_funding
    # รอบถัดไป (198) มี funding บวก → long จ่าย → cumulative_funding ลดลง
    asyncio.run(eng.process_bar(198 * FOUR_H, funding={s: 0.01 for s in cfg.symbols}))
    assert eng.portfolio.cumulative_funding < before
