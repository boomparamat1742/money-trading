"""ดึง OI (metrics) จาก Binance Vision → ย่อรายวัน OHLC → CSV

Binance เผยแพร่ historical futures "metrics" (รวม sum_open_interest_value) ฟรี ย้อน
ได้ไกลกว่า REST API (30 วัน) มาก — BTC/ETH/BNB ถึง 2020-09 · เป็น Binance แท้ ตรงกับ
ที่ระบบเทรด แต่ละไฟล์ = 1 วัน (OI ทุก ~5 นาที) → ย่อเป็น open/high/low/close รายวัน

    python -m scripts.fetch_binance_vision_oi            # ทุก SYMBOLS ใน config
    python -m scripts.fetch_binance_vision_oi BTCUSDT    # เจาะเหรียญ

เขียนลง data/{SYM}_1d_oi_bv.csv (bv = binance vision) — ไม่ทับ CSV เดิม · รันซ้ำได้
(ข้ามวันที่มีแล้ว)
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import os
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed

S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
DL = "https://data.binance.vision"
NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
UTC = dt.timezone.utc


def _get(url: str, tries: int = 4) -> bytes:
    last = None
    for i in range(tries):
        try:
            return urllib.request.urlopen(url, timeout=40).read()
        except Exception as e:      # transient network / 5xx → ถอยแล้วลองใหม่
            last = e
            import time
            time.sleep(1.5 * (i + 1))
    raise last  # type: ignore[misc]


def list_keys(symbol: str) -> list[str]:
    """ทุก key ไฟล์ .zip daily metrics ของ symbol (paginate ผ่าน continuation-token)"""
    prefix = f"data/futures/um/daily/metrics/{symbol}/"
    keys: list[str] = []
    token = None
    while True:
        url = f"{S3}?list-type=2&prefix={urllib.parse.quote(prefix)}&max-keys=1000"
        if token:
            url += f"&continuation-token={urllib.parse.quote(token)}"
        root = ET.fromstring(_get(url))
        for c in root.findall(f"{NS}Contents"):
            k = c.find(f"{NS}Key").text or ""
            if k.endswith(".zip"):
                keys.append(k)
        if (root.findtext(f"{NS}IsTruncated") or "false") == "true":
            token = root.findtext(f"{NS}NextContinuationToken")
        else:
            break
    return keys


def _day_ms(key: str) -> int:
    date = key.rsplit("-metrics-", 1)[1].replace(".zip", "")   # YYYY-MM-DD
    y, m, d = map(int, date.split("-"))
    return int(dt.datetime(y, m, d, tzinfo=UTC).timestamp() * 1000)


def fetch_day(key: str):
    """ดาวน์โหลด 1 zip → (day_ms, open, high, low, close) ของ sum_open_interest_value"""
    z = zipfile.ZipFile(io.BytesIO(_get(f"{DL}/{key}")))
    with z.open(z.namelist()[0]) as f:
        vals = [float(r["sum_open_interest_value"])
                for r in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8"))
                if r.get("sum_open_interest_value")]
    if not vals:
        return None
    return (_day_ms(key), vals[0], max(vals), min(vals), vals[-1])


def _existing_days(path: str) -> set[int]:
    if not os.path.exists(path):
        return set()
    with open(path, newline="") as f:
        return {int(r["open_time"]) for r in csv.DictReader(f) if r.get("open_time")}


def fetch_symbol(symbol: str) -> None:
    path = f"data/{symbol}_1d_oi_bv.csv"
    have = _existing_days(path)
    keys = [k for k in list_keys(symbol) if _day_ms(k) not in have]
    print(f"{symbol}: มีอยู่แล้ว {len(have)} วัน · ต้องดึงเพิ่ม {len(keys)} วัน", flush=True)
    rows = []
    if keys:
        with ThreadPoolExecutor(max_workers=16) as ex:
            futs = {ex.submit(fetch_day, k): k for k in keys}
            for i, fut in enumerate(as_completed(futs), 1):
                try:
                    r = fut.result()
                    if r:
                        rows.append(r)
                except Exception as e:
                    print(f"  ! {futs[fut]}: {e!r}", flush=True)
                if i % 300 == 0:
                    print(f"  {symbol}: {i}/{len(keys)}", flush=True)
    # รวมของเก่า + ใหม่ เรียงตามวัน เขียนกลับทั้งไฟล์
    merged = {}
    if os.path.exists(path):
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                merged[int(r["open_time"])] = (float(r["oi_open"]), float(r["oi_high"]),
                                               float(r["oi_low"]), float(r["oi_close"]))
    for ts, o, h, l, c in rows:
        merged[ts] = (o, h, l, c)
    os.makedirs("data", exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["open_time", "oi_open", "oi_high", "oi_low", "oi_close"])
        for ts in sorted(merged):
            o, h, l, c = merged[ts]
            w.writerow([ts, o, h, l, c])
    if merged:
        lo = dt.datetime.fromtimestamp(min(merged) / 1000, UTC).date()
        hi = dt.datetime.fromtimestamp(max(merged) / 1000, UTC).date()
        print(f"✅ {symbol}: รวม {len(merged)} วัน ({lo} → {hi}) → {path}", flush=True)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    if len(sys.argv) > 1:
        symbols = [s.upper() for s in sys.argv[1:]]
    else:
        from worker.app.config import load_settings
        symbols = load_settings().symbols
    print(f"ดึง OI (Binance Vision) {len(symbols)} เหรียญ: {', '.join(symbols)}\n")
    for s in symbols:
        try:
            fetch_symbol(s)
        except Exception as e:
            print(f"❌ {s}: {e!r}", flush=True)


if __name__ == "__main__":
    main()
