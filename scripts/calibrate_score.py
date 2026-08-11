"""Calibration: signal_score → ผลจริง (win-rate / expectancy) จาก backtest

ตอบคำถามที่เอกสารตั้งไว้ — "Score 85 = ชนะ 85% จริงไหม?" ด้วยข้อมูลหลายปี/หลายเหรียญ
(หลายพันไม้ ไม่ใช่ 40 ไม้สด) เป็นการ **วัด** ว่าคะแนนมีความหมายแค่ไหน ไม่ใช่การจูน

    python -m scripts.calibrate_score                 # majors + smallcaps, 15m
    python -m scripts.calibrate_score BTCUSDT ETHUSDT # เจาะจง

⚠️ นี่คือ diagnostic บนข้อมูลย้อนหลัง (in-sample) — บอกว่า "คะแนนเคยหมายถึงอะไร"
ถ้าช่วงไหนดูดี ต้องทดสอบ OOS ก่อนเอาไปใช้กรอง ไม่ใช่ auto-tune ทันที
"""
from __future__ import annotations

import sys

# ช่วงคะแนน (5 แต้ม/ช่อง) — เก็บตั้งแต่ 40 เพื่อเห็นทั้งช่วง
EDGES = [40, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 100]
UNIVERSE = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SUIUSDT", "INJUSDT", "TIAUSDT",
            "ARBUSDT", "OPUSDT", "SEIUSDT", "APTUSDT", "XRPUSDT", "ADAUSDT",
            "SOLUSDT", "DOGEUSDT", "LINKUSDT", "AVAXUSDT"]


def bucket(pairs: list[tuple[float, float]], edges=EDGES) -> list[dict]:
    """pairs = [(score, rr)] → สถิติต่อช่วงคะแนน (n, win%, expectancy R)"""
    out = []
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        rrs = [rr for s, rr in pairs if lo <= s < hi or (hi == 100 and s == 100)]
        if not rrs:
            out.append({"lo": lo, "hi": hi, "n": 0})
            continue
        wins = sum(1 for r in rrs if r > 0)
        out.append({"lo": lo, "hi": hi, "n": len(rrs),
                    "win": round(wins / len(rrs) * 100, 1),
                    "exp": round(sum(rrs) / len(rrs), 3),          # expectancy เป็น R
                    "avg_win": round(sum(r for r in rrs if r > 0) / wins, 2) if wins else 0.0,
                    "avg_loss": round(sum(r for r in rrs if r <= 0) / (len(rrs) - wins), 2)
                    if len(rrs) - wins else 0.0})
    return out


def _collect(symbols: list[str]) -> list[tuple[float, float]]:
    from backtest.fetch_binance import PERP_BASE, fetch
    from backtest.run_backtest import run as run_bt
    from backtest.synthetic import load_csv
    from worker.app.config import Fees, RiskPolicy
    from worker.app.models import Candle, TradeStatus

    policy = RiskPolicy()
    object.__setattr__(policy, "signal_score_threshold", 40.0)  # เก็บทุกช่วงคะแนน
    object.__setattr__(policy, "max_open_trades", 50)           # ไม่ให้ risk limit ตัดตัวอย่างทิ้ง
    fees = Fees()
    pairs: list[tuple[float, float]] = []
    for sym in symbols:
        print(f"  ▶ {sym} ...", flush=True)
        try:
            rows = fetch(sym, "15m", 12000, base=PERP_BASE)     # ~125 วัน/เหรียญ
        except SystemExit:
            print(f"    ข้าม {sym} (fetch fail)")
            continue
        candles = [Candle("binance", sym, "15m", int(r[0]), float(r[1]), float(r[2]),
                          float(r[3]), float(r[4]), float(r[5])) for r in rows[:-1]]
        out = run_bt(candles, policy, fees, confirm_tfs=("1h", "4h"))
        for t in out.trades:
            if t.status in (TradeStatus.HIT_TP, TradeStatus.HIT_SL, TradeStatus.EXPIRED):
                score = (t.entry_context or {}).get("score")
                if score is not None and t.actual_rr is not None:
                    pairs.append((float(score), float(t.actual_rr)))
    return pairs


def main(argv: list[str]) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    symbols = [a.upper() for a in argv[1:] if a.upper().endswith("USDT")] or UNIVERSE
    print(f"\nเก็บ (score, ผลจริง) จาก {len(symbols)} เหรียญ @ 15m ...\n")
    pairs = _collect(symbols)
    if not pairs:
        print("ไม่ได้ไม้เลย")
        return
    stats = bucket(pairs)
    print(f"\nได้ {len(pairs)} ไม้ทั้งหมด\n")
    print(f"  {'ช่วงคะแนน':<12}{'ไม้':>6}{'ชนะ%':>8}{'expectancy':>12}{'กำไรเฉลี่ย':>11}{'ขาดทุนเฉลี่ย':>13}")
    for b in stats:
        if b["n"] == 0:
            print(f"  {b['lo']}-{b['hi']:<8}{'0':>6}{'—':>8}{'—':>12}")
            continue
        print(f"  {str(b['lo'])+'-'+str(b['hi']):<12}{b['n']:>6}{b['win']:>7.1f}%"
              f"{b['exp']:>+12.3f}{b['avg_win']:>+11.2f}{b['avg_loss']:>+13.2f}")
    overall = sum(rr for _, rr in pairs) / len(pairs)
    print(f"\n  รวมทุกช่วง: expectancy {overall:+.3f}R ต่อไม้ (n={len(pairs)})")
    print("\n  อ่านยังไง: ถ้าคะแนนมีความหมาย → ช่วงสูงควร expectancy สูงกว่าอย่างเป็นระบบ")
    print("  ถ้า expectancy เท่าๆ กัน/สุ่มทุกช่วง = คะแนนไม่ได้ทำนายผล (ยืนยันสิ่งที่เจอมา)")
    print("\n⚠️ in-sample diagnostic — ถ้าช่วงไหนดี ต้องทดสอบ OOS ก่อนใช้กรอง")


if __name__ == "__main__":
    main(sys.argv)
