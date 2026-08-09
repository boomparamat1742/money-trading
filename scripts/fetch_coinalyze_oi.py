"""ดึง Open Interest ย้อนหลังจาก Coinalyze (Binance-only) → CSV สำหรับ Edge Lab

Coinalyze มี **free API** (สมัครเอา key ฟรี) — intraday (≤12h) เก็บแค่ ~2000 จุด
แต่ **interval=daily ไม่ติด cap นั้น** จึงดึงย้อนหลายปีได้ฟรี เหมาะกับ Edge Lab
ที่ทำงานระดับรายวันอยู่แล้ว

ต้องมี key: วางใน .env เป็น  COINALYZE_API_KEY=xxxx  (อย่าใส่ในโค้ด/แชต)

    python -m scripts.fetch_coinalyze_oi                  # BTC/ETH/BNB, daily
    python -m scripts.fetch_coinalyze_oi BTCUSDT 4hour    # เจาะจง (4h = ~11 เดือน)

symbol ของ Coinalyze: <PAIR>_PERP.A  โดย .A = Binance (เราต้องการ Binance-only
ให้ตรงกับที่ worker เก็บสด) · from/to เป็น unix "วินาที" · OI เป็น USD notional
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

BASE = "https://api.coinalyze.net/v1/open-interest-history"
EXCHANGE_CODE = "A"                       # .A = Binance ในระบบ Coinalyze
DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
# label ที่ใช้ตั้งชื่อไฟล์ (ให้ตรงแบบ Binance) → coinalyze interval
INTERVAL = {"1d": "daily", "4h": "4hour", "1h": "1hour", "12h": "12hour"}


def coinalyze_symbol(pair: str) -> str:
    return f"{pair.upper()}_PERP.{EXCHANGE_CODE}"


def _parse_history(payload) -> list[list]:
    """แปลง response ของ Coinalyze เป็นแถว [open_time_ms, open, high, low, close]

    รูปแบบ: [{"symbol": "...", "history": [{"t":วินาที,"o","h","l","c"}, ...]}]
    แยกเป็นฟังก์ชันบริสุทธิ์เพื่อเทสต์โดยไม่ต้องต่อเน็ต (จุดที่พังง่ายสุด)
    """
    if isinstance(payload, dict) and "history" not in payload:
        # เผื่อ error body เช่น {"message": "..."}
        raise SystemExit(f"Coinalyze error: {payload.get('message') or payload}")
    series = payload if isinstance(payload, list) else [payload]
    rows = []
    for item in series:
        for c in (item or {}).get("history", []):
            try:
                rows.append([int(c["t"]) * 1000, float(c["o"]), float(c["h"]),
                             float(c["l"]), float(c["c"])])
            except (KeyError, TypeError, ValueError):
                continue
    return sorted(rows)


def fetch(key: str, pair: str, interval: str, years: float = 6.0) -> list[list]:
    to_s = int(time.time())
    from_s = to_s - int(years * 365 * 86_400)
    params = urllib.parse.urlencode({
        "symbols": coinalyze_symbol(pair), "interval": interval,
        "from": from_s, "to": to_s, "convert_to_usd": "true",
    })
    req = urllib.request.Request(f"{BASE}?{params}",
                                 headers={"api_key": key, "accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Coinalyze HTTP {e.code}: {e.read().decode()[:200]}")
    except urllib.error.URLError as e:
        raise SystemExit(f"ต่อ Coinalyze ไม่ได้ ({e.reason})")
    return _parse_history(payload)


def write_csv(rows: list[list], path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["open_time", "oi_open", "oi_high", "oi_low", "oi_close"])
        w.writerows(rows)


def main(argv: list[str]) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    key = os.environ.get("COINALYZE_API_KEY", "").strip()
    if not key:
        print("ยังไม่ได้ตั้ง COINALYZE_API_KEY ใน .env")
        print("  1. สมัคร coinalyze.net (ฟรี) → API → สร้าง key")
        print("  2. เพิ่มบรรทัดใน .env:  COINALYZE_API_KEY=คีย์ของคุณ")
        print("  (อย่าใส่ key ในโค้ดหรือส่งให้ใคร)")
        return

    args = [a for a in argv[1:] if a]
    symbols = [a.upper() for a in args if a.upper().endswith("USDT")] or DEFAULT_SYMBOLS
    label = next((a for a in args if a in INTERVAL), "1d")
    interval = INTERVAL[label]

    for pair in symbols:
        print(f"▶ ดึง {pair} @ {label} ({interval}) ...", flush=True)
        rows = fetch(key, pair, interval)
        if not rows:
            print(f"  ⚠️ {pair}: ไม่ได้ข้อมูล (เช็ค symbol/แผน)")
            continue
        path = f"data/{pair}_{label}_oi.csv"
        write_csv(rows, path)
        span = (rows[-1][0] - rows[0][0]) / 86_400_000
        print(f"  ✅ {pair}: {len(rows)} แท่ง (~{span:.0f} วัน) → {path}")
        time.sleep(1.6)                  # rate limit 40/นาที
    print("\nเสร็จแล้ว — บอกผมได้เลย ผมจะสร้าง OI hypothesis ใน Edge Lab ทดสอบต่อ")


if __name__ == "__main__":
    main(sys.argv)
