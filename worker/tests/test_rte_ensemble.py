"""RTE ensemble — unit + regression parity กับ hypothesis ที่ผ่าน walk-forward.

parity (สเปก §23): production `ensemble.decide()` ต้องให้ target weights ตรงกับ
สูตรใน research/lab/hypotheses.py::RobustTrendEnsemble เป๊ะ (tol 1e-9) บนข้อมูลจริง
ชุดเดียวกัน — ถ้า drift แปลว่า runtime เทรดคนละกฎกับที่ทดสอบ
"""
import math
import os

import pytest

from worker.app.rte.config import RTEConfig
from worker.app.rte import ensemble as ens


# ---------- unit: สูตรพื้นฐาน ----------
def test_seeded_ema_matches_manual():
    closes = [1.0, 2.0, 3.0, 4.0, 5.0]
    e = ens.seeded_ema(closes, 3)
    assert e[0] is None and e[1] is None
    assert e[2] == pytest.approx(2.0)                 # seed = mean(1,2,3)
    a = 2 / 4
    assert e[3] == pytest.approx(a * 4 + (1 - a) * 2.0)
    assert e[4] == pytest.approx(a * 5 + (1 - a) * e[3])


def test_pstdev_is_population():
    xs = [1.0, 2.0, 3.0]
    assert ens.pstdev(xs) == pytest.approx(math.sqrt(2 / 3))   # population, ไม่ใช่ sample (1.0)


def _synthetic(sym, n, start=100.0, step=1.0):
    from worker.app.models import Candle
    out = []
    for i in range(n):
        px = start + i * step
        out.append(Candle(exchange="binance", symbol=sym, timeframe="4h",
                          open_time=i * 14_400_000, open=px, high=px, low=px,
                          close=px, volume=1.0, is_closed=True))
    return out


def test_cash_when_data_insufficient():
    cfg = RTEConfig()
    candles = {s: _synthetic(s, 10) for s in cfg.symbols}
    d = ens.decide(candles, cfg)
    assert d.to_cash and "ไม่พอ" in d.reason


def test_uptrend_all_selects_top_n():
    cfg = RTEConfig()
    # ราคาขึ้นตลอด → ทุกเหรียญ score 6, breadth 1.0, เลือก top 4
    candles = {s: _synthetic(s, 200, start=100.0 + i, step=0.5)
               for i, s in enumerate(cfg.symbols)}
    d = ens.decide(candles, cfg)
    assert d.btc_trend_ok and d.crash_filter_ok and not d.to_cash
    assert len(d.selected) == cfg.top_n
    assert d.breadth == pytest.approx(1.0)
    assert sum(d.target_weights.values()) <= cfg.max_gross_exposure + 1e-9
    for w in d.target_weights.values():
        assert 0 <= w <= 1


# ---------- regression: parity กับ hypothesis บนข้อมูลจริง ----------
def _load_real_bars(coins):
    from backtest.synthetic import load_csv
    bars = {}
    for coin in coins:
        path = f"data/{coin}USDT_4h.csv"
        if not os.path.exists(path):
            return None
        bars[coin] = load_csv(path, symbol=f"{coin}USDT", timeframe="4h")
    return bars


def test_parity_with_walkforward_hypothesis():
    from research.lab.hypotheses import (RobustTrendEnsemble, _seeded_ema, _pstdev)
    cfg = RTEConfig()
    coins = [s[:-4] for s in cfg.symbols]
    bars = _load_real_bars(coins)
    if bars is None:
        pytest.skip("ไม่มี data/*USDT_4h.csv — รัน fetch ก่อน (ทดสอบ parity ต้องใช้ข้อมูลจริง)")

    # สร้างโครงภายในแบบเดียวกับ hypothesis.run()
    closes, tsmap, ema, rets = {}, {}, {}, {}
    for coin, candles in bars.items():
        sym = coin + "USDT"
        cs = sorted(candles, key=lambda c: c.open_time)
        cl = [c.close for c in cs]
        closes[sym] = cl
        tsmap[sym] = {c.open_time: i for i, c in enumerate(cs)}
        ema[sym] = {n: _seeded_ema(cl, n) for n in set(cfg.ema_horizons) | {cfg.btc_ema_bars}}
        r = [0.0] * len(cl)
        for i in range(1, len(cl)):
            r[i] = cl[i] / cl[i - 1] - 1 if cl[i - 1] else 0.0
        rets[sym] = r

    # hypothesis ใช้ชื่อ coin (ไม่มี USDT) ใน UNIVERSE — map ให้ตรง
    h = RobustTrendEnsemble()
    h.UNIVERSE = [s[:-4] for s in cfg.symbols]
    hclose = {c[:-4] if c.endswith("USDT") else c: v for c, v in closes.items()}
    htsmap = {c[:-4] if c.endswith("USDT") else c: v for c, v in tsmap.items()}
    hema = {c[:-4] if c.endswith("USDT") else c: v for c, v in ema.items()}
    hrets = {c[:-4] if c.endswith("USDT") else c: v for c, v in rets.items()}
    warm = max(max(cfg.ema_horizons), cfg.btc_ema_bars, cfg.volatility_lookback_bars,
               cfg.crash_lookback_bars)
    ann = math.sqrt(cfg.annualization_bars)

    btc_ts = [c.open_time for c in sorted(bars["BTC"], key=lambda c: c.open_time)]
    # timestamps ทดสอบ: กระจายทั่วช่วง (index >= warmup ทั้ง production และ hypothesis)
    idxs = range(cfg.warmup_bars + 10, len(btc_ts) - 1, 137)
    checked = 0
    for j in idxs:
        t = btc_ts[j]
        # hypothesis weights (coin keys)
        hw = h._target_weights(t, hclose, htsmap, hema, hrets, warm, ann)
        # production weights (symbol keys) — slice candles ถึงเวลา t
        sliced = {}
        ok = True
        for coin, candles in bars.items():
            cs = [c for c in sorted(candles, key=lambda c: c.open_time) if c.open_time <= t]
            if not cs or cs[-1].open_time != t:
                ok = False
                break
            sliced[coin + "USDT"] = cs
        if not ok:
            continue
        d = ens.decide(sliced, cfg)
        pw = {sym[:-4]: w for sym, w in d.target_weights.items()}
        assert set(pw) == set(hw), f"เหรียญที่เลือกต่างกันที่ t={t}: prod={set(pw)} hyp={set(hw)}"
        for coin in hw:
            assert pw[coin] == pytest.approx(hw[coin], abs=1e-9), \
                f"weight ต่างกันที่ t={t} {coin}: prod={pw[coin]} hyp={hw[coin]}"
        checked += 1
    assert checked >= 5, f"ตรวจ parity ได้แค่ {checked} จุด (ข้อมูลอาจสั้นไป)"
