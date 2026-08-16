"""นำเข้า OI ประวัติ (CSV จาก Coinalyze) → Supabase ตาราง oi_history

ทำไม: CSV อยู่ในเครื่องเท่านั้น — Edge Lab บน Railway (`data/` ล้างทุกรอบ) อ่านไม่ได้
พอเข้า Supabase แล้ว `load_oi` จะดึงจาก DB ได้ → in-process Edge Lab บน Railway
ทดสอบ OI hypothesis ได้ (ตอนนี้ข้ามเพราะหา CSV ไม่เจอ)

    python -m scripts.import_oi_to_supabase          # นำเข้า data/*_oi.csv ทั้งหมด

idempotent: รันซ้ำได้ (ON CONFLICT DO NOTHING) — แถวเดิมไม่ซ้ำ
"""
from __future__ import annotations

import csv
import glob
import os
import sys

from worker.app.store import database_url

CREATE = """
CREATE TABLE IF NOT EXISTS oi_history (
    symbol     TEXT NOT NULL,
    interval   TEXT NOT NULL DEFAULT '1d',
    ts         BIGINT NOT NULL,              -- open_time (ms)
    oi_open    DOUBLE PRECISION,
    oi_high    DOUBLE PRECISION,
    oi_low     DOUBLE PRECISION,
    oi_close   DOUBLE PRECISION,             -- USD notional (Binance-only, .A)
    PRIMARY KEY (symbol, interval, ts)
);
"""


def parse_csv(path: str):
    """คืน (symbol, interval, rows[(ts,o,h,l,c)]) จาก data/SYMBOL_INTERVAL_oi[_bv].csv
    (_bv = Binance Vision · ไม่มี suffix = Coinalyze)"""
    base = os.path.basename(path)
    stem = base[:-4] if base.endswith(".csv") else base
    if stem.endswith("_bv"):                  # Binance Vision → ตัด suffix ให้เหลือรูปเดิม
        stem = stem[:-3]
    parts = stem.rsplit("_", 2)               # [SYMBOL, INTERVAL, 'oi']
    if len(parts) != 3 or parts[2] != "oi":
        raise ValueError(f"ชื่อไฟล์ไม่ตรงรูปแบบ SYMBOL_INTERVAL_oi[_bv].csv: {base}")
    symbol, interval = parts[0], parts[1]
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            try:
                rows.append((int(r["open_time"]), float(r["oi_open"]), float(r["oi_high"]),
                             float(r["oi_low"]), float(r["oi_close"])))
            except (KeyError, ValueError):
                continue
    return symbol, interval, rows


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    dsn = database_url()
    if not dsn:
        print("ไม่มี DATABASE_URL — ตั้งใน .env ก่อน (ต้องเป็น Supabase)")
        return

    # Coinalyze (_oi.csv) ก่อน แล้ว Binance Vision (_oi_bv.csv) — BV ยาว/native กว่า
    # จึง import ทีหลังให้ทับ (DO UPDATE) ในช่วงวันที่ซ้ำกัน
    files = sorted(glob.glob("data/*_oi.csv")) + sorted(glob.glob("data/*_oi_bv.csv"))
    if not files:
        print("ไม่พบไฟล์ data/*_oi*.csv — รัน scripts.fetch_binance_vision_oi ก่อน")
        return

    import psycopg

    total = 0
    with psycopg.connect(dsn, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(CREATE)
        for path in files:
            try:
                symbol, interval, rows = parse_csv(path)
            except ValueError as e:
                print(f"  ข้าม {path}: {e}")
                continue
            if not rows:
                continue
            with c.cursor() as cur:
                cur.executemany(
                    """INSERT INTO oi_history
                       (symbol, interval, ts, oi_open, oi_high, oi_low, oi_close)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (symbol, interval, ts) DO UPDATE
                         SET oi_open=EXCLUDED.oi_open, oi_high=EXCLUDED.oi_high,
                             oi_low=EXCLUDED.oi_low, oi_close=EXCLUDED.oi_close""",
                    [(symbol, interval, ts, o, h, l, cl) for ts, o, h, l, cl in rows])
            total += len(rows)
            print(f"  {symbol} {interval}: {len(rows)} แถว")
    print(f"\n✅ นำเข้า {len(files)} ไฟล์ · {total} แถว → oi_history")
    print("   Edge Lab จะดึง OI จาก Supabase ได้แล้ว (รวมบน Railway)")


if __name__ == "__main__":
    main()
