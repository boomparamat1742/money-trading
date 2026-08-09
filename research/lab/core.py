"""Edge Lab core — data bundle, return-series statistics, Hypothesis contract."""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

DAY_MS = 86_400_000
ANNUAL = 365  # crypto trades every day


# --------------------------------------------------------------------------
# statistics
# --------------------------------------------------------------------------
@dataclass
class Stats:
    n: int = 0
    total_return: float = 0.0
    cagr: float = 0.0
    sharpe: float = 0.0
    ann_vol: float = 0.0
    max_drawdown: float = 0.0
    exposure: float = 0.0        # fraction of periods with a position
    hit_rate: float = 0.0        # fraction of periods with positive return

    def line(self) -> str:
        return (f"CAGR {self.cagr * 100:>7.1f}%  Sharpe {self.sharpe:>6.2f}  "
                f"vol {self.ann_vol * 100:>6.1f}%  maxDD {self.max_drawdown * 100:>6.1f}%  "
                f"n={self.n}")


def compute_stats(returns: Sequence[float], times: Optional[Sequence[int]] = None,
                  positions: Optional[Sequence[float]] = None) -> Stats:
    """Stats for a series of PERIOD returns (fractional, e.g. 0.01 = +1%)."""
    if not returns:
        return Stats()
    equity, peak, max_dd = 1.0, 1.0, 0.0
    for r in returns:
        equity *= (1 + r)
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / max(len(returns) - 1, 1)
    sd = math.sqrt(var)
    if times and len(times) >= 2:
        years = max((times[-1] - times[0]) / (ANNUAL * DAY_MS), 1e-9)
    else:
        years = max(len(returns) / ANNUAL, 1e-9)
    cagr = (equity ** (1 / years) - 1) if equity > 0 else -1.0
    return Stats(
        n=len(returns),
        total_return=equity - 1,
        cagr=cagr,
        sharpe=(mean / sd * math.sqrt(ANNUAL)) if sd > 0 else 0.0,
        ann_vol=sd * math.sqrt(ANNUAL),
        max_drawdown=max_dd,
        exposure=(sum(1 for p in positions if p) / len(positions)) if positions else 1.0,
        hit_rate=sum(1 for r in returns if r > 0) / len(returns),
    )


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------
@dataclass
class DataBundle:
    """Daily closes (and optionally daily funding) keyed by symbol → {day_ms: value}.

    `bars` holds raw OHLCV candles for hypotheses that run the real strategy
    pipeline (not just close-to-close returns): keyed symbol → list[Candle].
    """
    prices: dict[str, dict[int, float]] = field(default_factory=dict)   # spot closes
    funding: dict[str, dict[int, float]] = field(default_factory=dict)
    bars: dict[str, list] = field(default_factory=dict)
    perp: dict[str, dict[int, float]] = field(default_factory=dict)     # perp closes

    @property
    def dates(self) -> list[int]:
        src = self.prices or self.funding
        if not src:
            return []
        return sorted(set().union(*[set(m) for m in src.values()]))

    def symbols(self) -> list[str]:
        return sorted(self.prices or self.funding)


def _is_stale(path: str, max_age_hours: Optional[float]) -> bool:
    """True when the cached file should be re-fetched. `None` = never refresh.

    Re-running the lab on unchanged data returns an identical verdict, which is
    worthless — continuous research only means something if new bars arrive.
    """
    import time
    if max_age_hours is None or not os.path.exists(path):
        return not os.path.exists(path)
    return (time.time() - os.path.getmtime(path)) > max_age_hours * 3600


def load_prices(coins: Iterable[str], bars: int = 2500, quiet: bool = False,
                max_age_hours: Optional[float] = None) -> dict[str, dict[int, float]]:
    """Daily closes per coin, fetching from Binance on first use (or when stale)."""
    from backtest.fetch_binance import fetch, write_csv
    from backtest.synthetic import load_csv

    out: dict[str, dict[int, float]] = {}
    for coin in coins:
        path = f"data/{coin}USDT_1d.csv"
        if _is_stale(path, max_age_hours):
            try:
                if not quiet:
                    print(f"  fetching {coin}USDT 1d ...", flush=True)
                rows = fetch(f"{coin}USDT", "1d", bars)
                if not rows:
                    continue
                write_csv(rows, path)
            except SystemExit:
                if not quiet:
                    print(f"  skip {coin} (fetch failed)")
                continue
        candles = load_csv(path, symbol=f"{coin}USDT", timeframe="1d")
        out[coin] = {c.open_time: c.close for c in candles}
    return out


def _load_oi_pg(dsn: str, coins: Iterable[str], interval: str) -> dict[str, dict[int, float]]:
    """OI close ต่อวัน จากตาราง oi_history บน Supabase (ให้ Edge Lab บน Railway ใช้ได้
    ที่ `data/` ล้างทุกรอบ) — นำเข้าด้วย scripts/import_oi_to_supabase"""
    import psycopg

    out: dict[str, dict[int, float]] = {}
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        for coin in coins:
            cur.execute(
                "SELECT ts, oi_close FROM oi_history WHERE symbol=%s AND interval=%s",
                (f"{coin}USDT", interval))
            daily = {(int(t) // DAY_MS) * DAY_MS: float(v)
                     for t, v in cur.fetchall() if v is not None}
            if daily:
                out[coin] = daily
    return out


def load_oi(coins: Iterable[str], interval: str = "1d",
            quiet: bool = False) -> dict[str, dict[int, float]]:
    """Open-interest close per coin per day (Binance-only, USD notional), day_ms keys.

    Supabase (oi_history) ก่อนถ้ามี DATABASE_URL — ไม่งั้นอ่าน CSV ในเครื่องที่ดึงด้วย
    scripts/fetch_coinalyze_oi. แบบนี้ทั้ง local และ Railway เห็นข้อมูลชุดเดียวกัน
    """
    import csv

    from worker.app.store import database_url

    dsn = database_url()
    if dsn:
        try:
            pg = _load_oi_pg(dsn, coins, interval)
            if pg:
                return pg
        except Exception as e:      # ตาราง/DB มีปัญหา → fallback CSV
            if not quiet:
                print(f"  อ่าน OI จาก Supabase ไม่ได้ ({type(e).__name__}) → ลอง CSV")

    out: dict[str, dict[int, float]] = {}
    for coin in coins:
        path = f"data/{coin}USDT_{interval}_oi.csv"
        if not os.path.exists(path):
            if not quiet:
                print(f"  ไม่พบ {path} — รัน scripts.fetch_coinalyze_oi / import_oi_to_supabase")
            continue
        daily: dict[int, float] = {}
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                try:
                    t, c = int(row["open_time"]), float(row["oi_close"])
                except (ValueError, KeyError):
                    continue
                daily[(t // DAY_MS) * DAY_MS] = c
        out[coin] = daily
    return out


def load_perp(coins: Iterable[str], timeframe: str = "1d", bars: int = 2500,
              quiet: bool = False, max_age_hours: Optional[float] = None) -> dict[str, dict[int, float]]:
    """Perpetual-futures closes per coin (fapi) — paired with spot closes from
    load_prices to measure basis (perp − spot). Cached to {COIN}USDT_{tf}_perp.csv."""
    from backtest.fetch_binance import PERP_BASE, fetch, write_csv
    from backtest.synthetic import load_csv

    out: dict[str, dict[int, float]] = {}
    for coin in coins:
        path = f"data/{coin}USDT_{timeframe}_perp.csv"
        if _is_stale(path, max_age_hours):
            try:
                if not quiet:
                    print(f"  fetching {coin}USDT {timeframe} perp ...", flush=True)
                rows = fetch(f"{coin}USDT", timeframe, bars, base=PERP_BASE)
                if not rows:
                    continue
                write_csv(rows, path)
            except SystemExit:
                if not quiet:
                    print(f"  skip {coin} perp (fetch failed)")
                continue
        candles = load_csv(path, symbol=f"{coin}USDT", timeframe=timeframe)
        out[coin] = {c.open_time: c.close for c in candles}
    return out


def load_ohlcv(coins: Iterable[str], timeframe: str, bars: int = 6000,
               quiet: bool = False, max_age_hours: Optional[float] = None) -> dict[str, list]:
    """Raw OHLCV candles per coin at `timeframe` — for hypotheses that run the
    actual SignalPipeline (indicators, regime, ATR stops) rather than close-only
    returns. Cached to data/{COIN}USDT_{tf}.csv like the other loaders."""
    from backtest.fetch_binance import fetch, write_csv
    from backtest.synthetic import load_csv

    out: dict[str, list] = {}
    for coin in coins:
        path = f"data/{coin}USDT_{timeframe}.csv"
        if _is_stale(path, max_age_hours):
            try:
                if not quiet:
                    print(f"  fetching {coin}USDT {timeframe} ...", flush=True)
                rows = fetch(f"{coin}USDT", timeframe, bars)
                if not rows:
                    continue
                write_csv(rows, path)
            except SystemExit:
                if not quiet:
                    print(f"  skip {coin} (fetch failed)")
                continue
        out[coin] = load_csv(path, symbol=f"{coin}USDT", timeframe=timeframe)
    return out


def load_funding(coins: Iterable[str], records: int = 3000, quiet: bool = False,
                 max_age_hours: Optional[float] = None) -> dict[str, dict[int, float]]:
    """Daily TOTAL funding per coin (sums the 8h/4h payments inside each day)."""
    import csv

    from backtest.fetch_funding import fetch, write_csv

    out: dict[str, dict[int, float]] = {}
    for coin in coins:
        path = f"data/{coin}USDT_funding.csv"
        if _is_stale(path, max_age_hours):
            try:
                if not quiet:
                    print(f"  fetching {coin}USDT funding ...", flush=True)
                rows = fetch(f"{coin}USDT", records)
                if not rows:
                    continue
                write_csv(rows, path)
            except SystemExit:
                continue
        daily: dict[int, float] = {}
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                try:
                    t, rate = int(row["funding_time"]), float(row["funding_rate"])
                except (ValueError, KeyError):
                    continue
                day = (t // DAY_MS) * DAY_MS
                daily[day] = daily.get(day, 0.0) + rate
        out[coin] = daily
    return out


# --------------------------------------------------------------------------
# hypothesis contract
# --------------------------------------------------------------------------
class Hypothesis:
    """สมมติฐานหนึ่งข้อว่า 'มี edge' — ต้องบอกได้ว่าจะวัดผลยังไงและเทียบกับอะไร.

    ต้องกำหนด:
      name / question   — ถามอะไร (เขียนให้คนอ่านรู้เรื่อง)
      neutral           — True = market-neutral (benchmark คือ cash 0)
      param_grid()      — พารามิเตอร์ที่จะจูน (ประกาศล่วงหน้า ห้ามเพิ่มหลังเห็นผล)
      load()            — เตรียมข้อมูล
      run(data, params) — คืน (times, returns, positions) เป็นผลตอบแทนรายวัน
      benchmark(data)   — คืน (times, returns) ของ benchmark (ถ้า neutral ไม่ต้อง)
    """

    name: str = "unnamed"
    question: str = ""
    neutral: bool = False        # True → benchmark = cash (0)
    cost_note: str = ""          # อธิบายว่าคิดต้นทุนอะไรไปแล้วบ้าง
    # ชั่วโมงก่อนถือว่าข้อมูล cache เก่า (None = ไม่รีเฟรช) — ตั้งโดย watcher
    max_age_hours: Optional[float] = None

    def param_grid(self) -> list[dict]:  # pragma: no cover - interface
        raise NotImplementedError

    def load(self) -> DataBundle:  # pragma: no cover - interface
        raise NotImplementedError

    def run(self, data: DataBundle, params: dict):  # pragma: no cover - interface
        """→ (times, returns, positions)"""
        raise NotImplementedError

    def benchmark(self, data: DataBundle):
        """→ (times, returns). Default: cash for neutral strategies."""
        return [], []
