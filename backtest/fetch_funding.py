"""Download historical funding rates from Binance USDⓈ-M Futures (public API).

Funding is exchanged between perp longs and shorts (every 8h historically; some
symbols 4h since 2025). A delta-neutral carry (long spot + short perp) RECEIVES
funding when the rate is positive. This fetches the raw rate history to backtest
that carry.

Usage:
    python -m backtest.fetch_funding BTCUSDT 3000
Output CSV: funding_time,funding_rate  (funding_time ms epoch)
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

BASE = "https://fapi.binance.com/fapi/v1/fundingRate"


def fetch(symbol: str, total: int) -> list[dict]:
    rows: list[dict] = []
    end_time = int(time.time() * 1000)
    while len(rows) < total:
        limit = min(1000, total - len(rows))
        params = urllib.parse.urlencode({"symbol": symbol, "endTime": end_time, "limit": limit})
        req = urllib.request.Request(f"{BASE}?{params}", headers={"User-Agent": "carry-backtest/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                batch = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            raise SystemExit(f"Binance futures HTTP {e.code}: {e.read().decode()[:200]}")
        except urllib.error.URLError as e:
            raise SystemExit(f"เชื่อมต่อ fapi.binance.com ไม่ได้ ({e.reason})")
        if not batch:
            break
        rows = batch + rows
        end_time = int(batch[0]["fundingTime"]) - 1
        print(f"  {symbol}: {len(rows)}/{total} funding records ...", flush=True)
        time.sleep(0.25)
    return rows[-total:]


def write_csv(rows: list[dict], path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["funding_time", "funding_rate"])
        for r in rows:
            w.writerow([int(r["fundingTime"]), r["fundingRate"]])


def main(argv: list[str]) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    if len(argv) < 2:
        print("usage: python -m backtest.fetch_funding <SYMBOL> [RECORDS]")
        print("example: python -m backtest.fetch_funding BTCUSDT 3000")
        return
    symbol = argv[1].upper()
    total = int(argv[2]) if len(argv) > 2 else 3000
    rows = fetch(symbol, total)
    out = f"data/{symbol}_funding.csv"
    write_csv(rows, out)
    print(f"Saved {len(rows)} funding records → {out}")


if __name__ == "__main__":
    main(sys.argv)
