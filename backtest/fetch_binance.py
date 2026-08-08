"""Download real historical klines from Binance public REST → CSV.

Public endpoint, no API key needed. Paginates to fetch more than the 1000-bar
per-request cap.

Usage:
    python -m backtest.fetch_binance BTCUSDT 15m 5000
    python -m backtest.fetch_binance ETHUSDT 1h 3000 data/eth_1h.csv

Output CSV header: open_time,open,high,low,close,volume  (open_time in ms epoch)
— exactly what backtest.synthetic.load_csv expects.
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

BASE = "https://api.binance.com/api/v3/klines"
INTERVAL_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000,
               "4h": 14_400_000, "1d": 86_400_000}


def fetch(symbol: str, interval: str, total: int) -> list[list]:
    step = INTERVAL_MS.get(interval)
    if step is None:
        raise SystemExit(f"unsupported interval: {interval} (use {', '.join(INTERVAL_MS)})")

    rows: list[list] = []
    # walk backwards from now so we get the most recent `total` bars
    end_time = int(time.time() * 1000)
    while len(rows) < total:
        limit = min(1000, total - len(rows))
        start_time = end_time - limit * step
        params = urllib.parse.urlencode({
            "symbol": symbol, "interval": interval,
            "startTime": start_time, "endTime": end_time, "limit": limit,
        })
        req = urllib.request.Request(f"{BASE}?{params}", headers={"User-Agent": "quant-backtest/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                batch = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            raise SystemExit(f"Binance HTTP {e.code}: {e.read().decode()[:200]}")
        except urllib.error.URLError as e:
            raise SystemExit(f"เชื่อมต่อ Binance ไม่ได้ ({e.reason}) — ตรวจอินเทอร์เน็ต/ไฟร์วอลล์")
        if not batch:
            break
        rows = batch + rows
        end_time = batch[0][0] - 1  # step further back
        print(f"  fetched {len(rows)}/{total} ...", flush=True)
        time.sleep(0.25)  # be polite to the public endpoint
    return rows[-total:]


def write_csv(rows: list[list], path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["open_time", "open", "high", "low", "close", "volume"])
        for k in rows:
            # kline: [open_time, open, high, low, close, volume, close_time, ...]
            w.writerow([int(k[0]), k[1], k[2], k[3], k[4], k[5]])


def main(argv: list[str]) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    if len(argv) < 3:
        print("usage: python -m backtest.fetch_binance <SYMBOL> <INTERVAL> [BARS] [OUT_CSV]")
        print("example: python -m backtest.fetch_binance BTCUSDT 15m 5000")
        return
    symbol = argv[1].upper()
    interval = argv[2]
    total = int(argv[3]) if len(argv) > 3 else 5000
    out = argv[4] if len(argv) > 4 else f"data/{symbol}_{interval}.csv"

    print(f"Downloading {total} × {interval} candles for {symbol} from Binance ...")
    rows = fetch(symbol, interval, total)
    write_csv(rows, out)
    print(f"\nSaved {len(rows)} candles → {out}")
    print(f"Now run:  python -m backtest.run_backtest {out}")


if __name__ == "__main__":
    main(sys.argv)
