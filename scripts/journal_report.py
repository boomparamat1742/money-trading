"""สรุปผลจาก trade journal — ระบบทำอะไรไปบ้าง และผลจริงเป็นยังไง.

    python -m scripts.journal_report              # สรุป + 20 ไม้ล่าสุด
    python -m scripts.journal_report 50           # 50 ไม้ล่าสุด
    JOURNAL_DB=data/other.db python -m scripts.journal_report
"""
from __future__ import annotations

import os
import sys
import time

from worker.app.store import backend_name, open_journal


def main(argv: list[str]) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    limit = int(argv[1]) if len(argv) > 1 else 20
    if backend_name() == "sqlite":
        path = os.environ.get("JOURNAL_DB", "data/journal.db")
        if not os.path.exists(path):
            print(f"ยังไม่มี journal ที่ {path}")
            print("รันระบบก่อน:  python -m worker.app.main")
            return
    j = open_journal()
    s = j.stats()

    print(f"\n📓 Trade Journal — {j.path}\n")
    print(f"  สัญญาณที่บันทึก : {s['signals']}")
    print(f"  ไม้ที่ปิดแล้ว    : {s['trades_closed']}   (เปิดค้าง {s['trades_open']})")
    if s["trades_closed"]:
        pf = "∞" if s["profit_factor"] == float("inf") else s["profit_factor"]
        print(f"  ชนะ/แพ้         : {s['wins']}/{s['losses']}  ({s['win_rate']}%)")
        print(f"  Net PnL         : {s['net_pnl']}")
        print(f"  Profit factor   : {pf}")
        print(f"  Expectancy/ไม้   : {s['expectancy']}")
        print(f"  กำไรเฉลี่ย/ขาดทุนเฉลี่ย : {s['avg_win']} / {s['avg_loss']}")
        verdict = "✅ expectancy เป็นบวก" if s["expectancy"] > 0 else "❌ expectancy ติดลบ"
        print(f"  → {verdict} (ต้องมีไม้พอสมควรถึงจะเชื่อได้)")
    else:
        print("  (ยังไม่มีไม้ปิด — รอสัญญาณและผลลัพธ์ก่อน)")

    reasons = j.exit_reasons()
    if reasons:
        print("\n  ปิดด้วยสาเหตุอะไรบ้าง (เรียงตามจำนวน):")
        print(f"  {'สาเหตุ':<14}{'รูปแบบ':<15}{'ไม้':>5}{'PnL รวม':>12}{'RR เฉลี่ย':>10}"
              f"{'แท่ง':>7}{'MFE(R)':>9}")
        for r in reasons:
            rr = "-" if r["avg_rr"] is None else f"{r['avg_rr']:.2f}"
            bars = "-" if r["avg_bars"] is None else f"{r['avg_bars']:.1f}"
            mfe = "-" if r["avg_mfe_r"] is None else f"{r['avg_mfe_r']:.2f}"
            print(f"  {str(r['exit_reason'] or '-'):<14}{str(r['pattern'] or '-'):<15}"
                  f"{r['n']:>5}{r['net_pnl']:>12.4f}{rr:>10}{bars:>7}{mfe:>9}")
        print("\n  อ่านยังไง:")
        print("    never_worked มาก → ปัญหาอยู่ที่จังหวะเข้า/ฟิลเตอร์ (เข้าผิดตั้งแต่แรก)")
        print("    gave_back มาก    → ปัญหาอยู่ที่จุดออก (เคยกำไรแล้วคืนหมด)")
        print("    trail_locked มาก → trailing ทำงานดี แต่อาจแคบไป ลองขยับดู")
        print("    timeout มาก      → TP ไกลเกินไป หรือเข้าตอนตลาดนิ่ง")
        fast = sum(r["fast_stops"] for r in reasons)
        if fast:
            print(f"    ⚠️ โดน SL ภายใน 2 แท่ง {fast} ไม้ — สัญญาณอาจมาช้ากว่าตลาด")

    triggers = j.sl_by_trigger()
    if triggers:
        print("\n  เงื่อนไขที่จุดชนวนการเข้า → จบด้วย SL กี่ % (เรียงจากแย่สุด):")
        print(f"  {'เงื่อนไข':<24}{'ไม้':>5}{'โดน SL':>9}{'%':>8}{'PnL รวม':>12}")
        for t in triggers:
            print(f"  {t['trigger']:<24}{t['n']:>5}{t['sl']:>9}{t['sl_rate']:>7.1f}%{t['net_pnl']:>12.4f}")
        print("\n  ⚠️ ไม้หนึ่งมีหลายเงื่อนไขพร้อมกัน ตัวเลขนี้จึงอ่านว่า")
        print("     \"เมื่อเงื่อนไขนี้อยู่ในชุดที่จุดชนวน ผลเป็นยังไง\"")
        print("     ไม่ใช่ \"เงื่อนไขนี้ทำให้แพ้\" — แยกอิทธิพลรายตัวออกจากกันไม่ได้")
        print("     และต้องมีไม้หลักสิบขึ้นไปต่อเงื่อนไขถึงจะเริ่มเชื่อได้")

    rows = j.recent_trades(limit)
    if rows:
        print(f"\n  {limit} ไม้ล่าสุด:")
        print(f"  {'เวลาเปิด':<17}{'symbol':<10}{'side':<7}{'status':<9}{'PnL':>10}{'RR':>7}")
        for r in rows:
            ts = time.strftime("%Y-%m-%d %H:%M", time.gmtime((r["opened_at"] or 0) / 1000))
            pnl = "-" if r["pnl_amount"] is None else f"{r['pnl_amount']:.4f}"
            rr = "-" if r["actual_rr"] is None else f"{r['actual_rr']:.2f}"
            print(f"  {ts:<17}{r['symbol']:<10}{r['side']:<7}{r['status']:<9}{pnl:>10}{rr:>7}")
    j.close()
    print("\n⚠️ ผล paper trading — ไม่ใช่ผลเทรดจริง และสัญญาณยังไม่มี edge พิสูจน์แล้ว")


if __name__ == "__main__":
    main(sys.argv)
