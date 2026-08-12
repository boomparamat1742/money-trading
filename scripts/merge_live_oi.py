"""รวม OI สด (market_snapshots 15m) → oi_history (รายวัน) ให้ series ต่อเนื่องยาวขึ้น

oi_history = ประวัติ Coinalyze (static ถึงวันนำเข้า) · market_snapshots = OI สดที่ worker
เก็บทุก 15 นาที (โตเรื่อยๆ) สคริปต์นี้ย่อ snapshot รายวันเป็น OHLC แล้ว upsert เข้า
oi_history เพื่อให้ Edge Lab ทดสอบ price_oi_confirm ด้วยข้อมูลที่ยาวขึ้นได้

ทั้งคู่เป็น USD notional (Coinalyze convert_to_usd · snapshot = OI × mark) จึงต่อกันได้

    python -m scripts.merge_live_oi          # รันซ้ำได้ (upsert วันปัจจุบันอัปเดตเรื่อยๆ)
"""
from __future__ import annotations

import sys

DAY_MS = 86_400_000


def aggregate_daily(rows) -> dict:
    """rows = [(symbol, ts_ms, oi_value)] → {(symbol, day_ms): (open, high, low, close)}
    open/close = ค่าแรก/สุดท้ายของวัน (เรียงตามเวลา) · high/low = max/min"""
    days: dict = {}
    for sym, ts, val in rows:
        if val is None:
            continue
        day = (int(ts) // DAY_MS) * DAY_MS
        days.setdefault((sym, day), []).append((int(ts), float(val)))
    out = {}
    for key, pts in days.items():
        pts.sort()
        vals = [v for _, v in pts]
        out[key] = (vals[0], max(vals), min(vals), vals[-1])
    return out


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    from worker.app.store import database_url
    dsn = database_url()
    if not dsn:
        print("ต้องมี DATABASE_URL (Supabase)")
        return
    import psycopg
    with psycopg.connect(dsn, autocommit=True) as c, c.cursor() as cur:
        cur.execute("SELECT symbol, ts, open_interest_value FROM market_snapshots "
                    "WHERE open_interest_value IS NOT NULL")
        agg = aggregate_daily(cur.fetchall())
        n = 0
        for (sym, day), (o, h, l, cl) in agg.items():
            cur.execute(
                """INSERT INTO oi_history (symbol, interval, ts, oi_open, oi_high, oi_low, oi_close)
                   VALUES (%s, '1d', %s, %s, %s, %s, %s)
                   ON CONFLICT (symbol, interval, ts) DO UPDATE
                     SET oi_open=EXCLUDED.oi_open, oi_high=EXCLUDED.oi_high,
                         oi_low=EXCLUDED.oi_low, oi_close=EXCLUDED.oi_close""",
                (sym, day, o, h, l, cl))
            n += 1
    print(f"✅ รวม/อัปเดต {n} วัน-เหรียญ จาก live snapshots → oi_history")
    print("   (วันปัจจุบันยังไม่ครบวัน — รันซ้ำเรื่อยๆ จะอัปเดตให้)")


if __name__ == "__main__":
    main()
