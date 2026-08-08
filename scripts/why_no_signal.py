"""ทำไมระบบถึงไม่ออกสัญญาณ (หรือออกแต่ข้างเดียว) — ไล่ทีละด่านว่าตกตรงไหน

    python -m scripts.why_no_signal                 # 3 เหรียญ ~30 วันล่าสุด
    python -m scripts.why_no_signal ETHUSDT 60      # เหรียญเดียว 60 วัน

ใช้ pipeline ตัวเดียวกับระบบจริงเป๊ะๆ (SignalPipeline + MultiTimeframeTrend)
จึงตอบได้ว่าเป็นเพราะ "ตลาดไม่เข้าเงื่อนไข" หรือ "โค้ดมีอคติข้างเดียว"
"""
from __future__ import annotations

import sys
from collections import Counter

from worker.app.config import load_settings
from worker.app.htf import MultiTimeframeTrend
from worker.app.main import confirm_tfs
from worker.app.models import Candle, Direction
from worker.app.pipeline import SignalPipeline
from worker.app.regime import detect_regime
from worker.app.risk import PortfolioState
from worker.app.strategies.base import StrategyContext, is_choppy
from worker.app.strategies.registry import best_signal

BARS_PER_DAY = 96          # 15m


def analyse(symbol: str, days: int, s) -> dict:
    from backtest.fetch_binance import fetch

    tf = s.primary_timeframe
    rows = fetch(symbol, tf, days * BARS_PER_DAY)
    pipeline = SignalPipeline(s.risk)
    htf = MultiTimeframeTrend(tf, confirm_tfs(s))
    portfolio = PortfolioState()

    regimes = Counter()
    htf_states = Counter()
    gate = Counter()
    fired = Counter()
    approved = Counter()
    bars = 0

    for r in rows[:-1]:                       # ตัดแท่งที่ยังไม่ปิด
        c = Candle(s.exchange, symbol, tf, int(r[0]), float(r[1]), float(r[2]),
                   float(r[3]), float(r[4]), float(r[5]))
        trend = htf.update(c)
        snap = pipeline.ind.update(c)
        if not snap.ready:
            continue
        bars += 1
        reg = detect_regime(snap)
        regimes[reg.regime] += 1
        htf_states[{1: "ขึ้น", -1: "ลง", 0: "ไม่ตรงกัน"}[trend]] += 1

        # ด่านที่ 1: regime + HTF ต้องตรงทิศเดียวกัน (ประตูหลักของ trend_following)
        if reg.regime == "uptrend" and trend > 0:
            gate["LONG ผ่านด่าน regime+HTF"] += 1
            side = Direction.LONG
        elif reg.regime == "downtrend" and trend < 0:
            gate["SHORT ผ่านด่าน regime+HTF"] += 1
            side = Direction.SHORT
        else:
            gate["ไม่ผ่าน (ทิศไม่ตรงกัน)"] += 1
            side = None

        # ด่านที่ 2: anti-chop
        if side and is_choppy(snap.values):
            gate[f"{side.value.upper()} ตกที่ anti-chop"] += 1

        # ด่านที่ 3+4: กลยุทธ์ยิงจริง แล้วผ่านคะแนน+risk ไหม
        res = best_signal(StrategyContext(snapshot=snap, regime=reg, htf_trend=trend))
        if res:
            fired[res.direction.value] += 1
        sig = pipeline.process(c, portfolio, htf_trend=trend)
        if sig and sig.status == "approved":
            approved[sig.direction.value] += 1

    return {"symbol": symbol, "bars": bars, "regimes": regimes, "htf": htf_states,
            "gate": gate, "fired": fired, "approved": approved}


def main(argv: list[str]) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    s = load_settings()
    args = [a for a in argv[1:] if not a.startswith("-")]
    symbols = [a for a in args if not a.isdigit()] or s.symbols[:s.max_symbols]
    days = next((int(a) for a in args if a.isdigit()), 30)

    print(f"\nวิเคราะห์ {', '.join(symbols)} · {days} วันล่าสุด @ {s.primary_timeframe}")
    print(f"ยืนยันด้วย {'+'.join(confirm_tfs(s))} · เกณฑ์คะแนน {s.risk.signal_score_threshold}\n")

    tot_fired, tot_appr = Counter(), Counter()
    for sym in symbols:
        r = analyse(sym, days, s)
        tot_fired.update(r["fired"])
        tot_appr.update(r["approved"])
        print(f"── {sym}  ({r['bars']} แท่งที่อินดิเคเตอร์พร้อม)")
        print(f"   สภาพตลาด : " + ", ".join(f"{k} {v}" for k, v in r["regimes"].most_common()))
        print(f"   ทิศ HTF   : " + ", ".join(f"{k} {v}" for k, v in r["htf"].most_common()))
        for k, v in r["gate"].most_common():
            print(f"   • {k}: {v}")
        print(f"   กลยุทธ์ยิง : {dict(r['fired']) or 'ไม่มี'}")
        print(f"   ผ่าน risk  : {dict(r['approved']) or 'ไม่มี'}\n")

    print("รวมทุกเหรียญ")
    print(f"   กลยุทธ์ยิง : LONG {tot_fired['long']}  SHORT {tot_fired['short']}")
    print(f"   ผ่าน risk  : LONG {tot_appr['long']}  SHORT {tot_appr['short']}")
    if tot_fired["short"] == 0 and tot_fired["long"] > 0:
        print("\n   → ไม่มี SHORT เพราะเงื่อนไขขาลงไม่เคยครบ ไม่ใช่เพราะโค้ดไม่รองรับ")
    print()


if __name__ == "__main__":
    main(sys.argv)
