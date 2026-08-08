"""สรุปผลจาก trade journal — ระบบทำอะไรไปบ้าง และผลจริงเป็นยังไง.

    python -m scripts.journal_report              # สรุป + 20 ไม้ล่าสุด
    python -m scripts.journal_report 50           # 50 ไม้ล่าสุด
    JOURNAL_DB=data/other.db python -m scripts.journal_report
"""
from __future__ import annotations

import os
import sys
import time

from worker.app.journal import Journal


def main(argv: list[str]) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    path = os.environ.get("JOURNAL_DB", "data/journal.db")
    if not os.path.exists(path):
        print(f"ยังไม่มี journal ที่ {path}")
        print("รันระบบก่อน:  python -m worker.app.main")
        return
    limit = int(argv[1]) if len(argv) > 1 else 20
    j = Journal(path)
    s = j.stats()

    print(f"\n📓 Trade Journal — {path}\n")
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
