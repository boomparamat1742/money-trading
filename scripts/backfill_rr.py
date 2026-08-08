"""คำนวณ actual_rr ของไม้ที่ปิดไปแล้วใหม่ ให้ตรงกับนิยาม R (แก้ผลของบั๊กก่อน 338421c)

สูตรเดิมหารด้วยระยะไปยัง stop **ปัจจุบัน** ซึ่ง trailing ขยับได้ พอ stop ถูกเลื่อน
มาชิดราคาเข้า ตัวหารเกือบศูนย์ แล้ว RR ก็ระเบิด (เคยรายงาน -13.975 กับไม้ที่แทบ
เสมอตัว) R คือความเสี่ยงที่รับไว้ **ตอนเข้า** ซึ่งเก็บอยู่ใน init_risk

    python -m scripts.backfill_rr            # ดูอย่างเดียว ไม่แก้
    python -m scripts.backfill_rr --apply    # เขียนจริง

ไม้ที่ init_risk ว่างหรือเป็น 0 จะข้าม — เดาค่าที่หายไปย้อนหลังไม่ได้
"""
from __future__ import annotations

import sys

from worker.app.store import backend_name, open_journal

SELECT = ("SELECT id, symbol, side, filled_entry, exit_price, init_risk, "
          "pnl_amount, actual_rr FROM trades "
          "WHERE status IN ('hit_tp','hit_sl','expired') AND exit_price IS NOT NULL")


def recompute(entry, exit_px, init_risk, pnl) -> float:
    return round((abs(exit_px - entry) / init_risk) * (1 if (pnl or 0) >= 0 else -1), 3)


def main(argv: list[str]) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    apply = "--apply" in argv
    j = open_journal()
    is_pg = backend_name() != "sqlite"
    ph = "%s" if is_pg else "?"

    if is_pg:
        with j.conn.cursor() as cur:
            cur.execute(SELECT)
            rows = cur.fetchall()
    else:
        rows = [tuple(r) for r in j.conn.execute(SELECT)]

    changes = []
    for tid, sym, side, entry, exit_px, init_risk, pnl, old_rr in rows:
        if not init_risk or entry is None:
            print(f"  ข้าม id={tid} — ไม่มี init_risk (เดาย้อนหลังไม่ได้)")
            continue
        new_rr = recompute(entry, exit_px, init_risk, pnl)
        if old_rr is not None and abs(new_rr - old_rr) < 0.0005:
            continue
        changes.append((tid, sym, side, old_rr, new_rr))

    if not changes:
        print("\n✅ ทุกไม้ถูกต้องอยู่แล้ว — ไม่มีอะไรต้องแก้\n")
        j.close()
        return

    print(f"\n📐 คำนวณ RR ใหม่ {len(changes)} ไม้  (backend: {backend_name()})\n")
    print(f"  {'id':>4}  {'symbol':<10}{'side':<7}{'RR เดิม':>10}{'RR ใหม่':>10}")
    for tid, sym, side, old_rr, new_rr in changes:
        print(f"  {tid:>4}  {sym:<10}{side:<7}{old_rr if old_rr is not None else '-':>10}{new_rr:>10}")

    if not apply:
        print("\n(ดูอย่างเดียว — ใส่ --apply เพื่อเขียนจริง)\n")
        j.close()
        return

    sql = f"UPDATE trades SET actual_rr={ph} WHERE id={ph}"
    if is_pg:
        with j.conn.cursor() as cur:
            for tid, _, _, _, new_rr in changes:
                cur.execute(sql, (new_rr, tid))
    else:
        for tid, _, _, _, new_rr in changes:
            j.conn.execute(sql, (new_rr, tid))
        j.conn.commit()

    print(f"\n✅ เขียนแล้ว {len(changes)} ไม้")
    print("   หมายเหตุ: pnl_amount ไม่เปลี่ยน — บั๊กอยู่ที่การรายงาน R ไม่ใช่การคิดกำไรขาดทุน\n")
    j.close()


if __name__ == "__main__":
    main(sys.argv)
