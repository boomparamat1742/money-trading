"""Backtest runner — same pipeline as real-time, over historical/synthetic bars.

Usage:
    python -m backtest.run_backtest                # synthetic demo, 70/30 split
    python -m backtest.run_backtest data.csv       # your CSV (see synthetic.load_csv)

Guards against look-ahead: existing trades are settled on each incoming bar
BEFORE a new signal is evaluated on that same (closed) bar, and higher-timeframe
trend uses only already-closed HTF candles (design §8.1).
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

from worker.app.config import Fees, RiskPolicy
from worker.app.htf import MultiTimeframeTrend
from worker.app.models import Candle, PaperTrade, TradeStatus
from worker.app.paper_trading import PaperBroker, entry_from_signal
from worker.app.pipeline import SignalPipeline
from worker.app.risk import PortfolioState

from .metrics import Metrics, compute_metrics, format_report
from .synthetic import generate, load_csv

DAY_MS = 86_400_000


@dataclass
class BacktestOutput:
    trades: list[PaperTrade]
    metrics: Metrics


def run(candles: list[Candle], policy: RiskPolicy, fees: Fees,
        confirm_tfs: tuple[str, ...] = ("1h", "4h"), sl_mult: float = 1.5, tp_mult: float = 3.0,
        trail_r_activate=1.0, trail_r_dist: float = 1.0, entry_filter=None) -> BacktestOutput:
    """entry_filter(sig) -> bool : ถ้าให้มา จะเปิดไม้เฉพาะเมื่อคืน True — ใช้ทดสอบ
    ตัวกรองเข้าไม้ (เช่น 'เข้าเร็ว' = รับเฉพาะ score ต่ำ) โดยไม่แตะ pipeline"""
    pipeline = SignalPipeline(policy, sl_mult=sl_mult, tp_mult=tp_mult)
    broker = PaperBroker(fees, trail_r_activate=trail_r_activate, trail_r_dist=trail_r_dist)
    htf = MultiTimeframeTrend(candles[0].timeframe, list(confirm_tfs)) if candles else None

    portfolio = PortfolioState()
    open_trades: list[PaperTrade] = []
    all_trades: list[PaperTrade] = []
    day_pnl = 0.0

    for c in candles:
        if portfolio.roll_day(c.open_time):  # resets daily loss + loss-streak cooldown
            day_pnl = 0.0

        # 1) settle existing trades on this bar
        still_open = []
        for t in open_trades:
            broker.update(t, c)
            if t.status == TradeStatus.OPEN:
                still_open.append(t)
            else:
                portfolio.open_trades -= 1
                portfolio.open_risk_pct -= t.risk_pct
                pnl = t.pnl_amount or 0.0
                if pnl <= 0:
                    portfolio.consecutive_losses += 1
                    day_pnl += pnl
                else:
                    portfolio.consecutive_losses = 0
                if day_pnl < 0:
                    portfolio.daily_loss_pct = abs(day_pnl) / policy.account_equity * 100
        open_trades = still_open

        # 2) update multi-timeframe (1h+4h) trend from CLOSED htf candles only
        htf_trend = htf.update(c) if htf is not None else 0

        # 3) evaluate a new signal on this closed bar
        sig = pipeline.process(c, portfolio, htf_trend=htf_trend)
        if (sig and sig.status == "approved" and hasattr(sig, "_decision")
                and (entry_filter is None or entry_filter(sig))):
            decision = sig._decision  # type: ignore[attr-defined]
            t = broker.open(decision, sig.direction, c.symbol, None, c,
                            entry=entry_from_signal(sig))
            open_trades.append(t)
            all_trades.append(t)
            portfolio.open_trades += 1
            portfolio.open_risk_pct += decision.risk_pct or 0.0

    # force-close any still-open at the last candle
    if candles:
        for t in open_trades:
            broker._close(t, candles[-1].close, TradeStatus.EXPIRED, candles[-1])

    return BacktestOutput(trades=all_trades, metrics=compute_metrics(all_trades, policy.account_equity))


def main(argv: list[str]) -> None:
    try:  # Windows consoles default to cp1252; our report uses ✅/─/∞/Thai
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    policy = RiskPolicy()
    fees = Fees()

    if len(argv) > 1:
        import os
        if not os.path.exists(argv[1]):
            print(f"ไม่พบไฟล์: {argv[1]}")
            print("(ค่านั้นเป็นแค่ตัวอย่างใน README ไม่ใช่ไฟล์จริง)\n")
            print("วิธีได้ข้อมูลจริง — ดาวน์โหลดจาก Binance ก่อน:")
            print("  python -m backtest.fetch_binance BTCUSDT 15m 5000")
            print("แล้วค่อยรัน:")
            print(f"  python -m backtest.run_backtest data/BTCUSDT_15m.csv\n")
            print("หรือรันแบบไม่ใส่ path เพื่อใช้ข้อมูลสังเคราะห์ (demo):")
            print("  python -m backtest.run_backtest")
            return
        # infer SYMBOL_INTERVAL from a filename like data/BTCUSDT_15m.csv
        stem = os.path.splitext(os.path.basename(argv[1]))[0]
        sym, tf = "BTCUSDT", "15m"
        parts = stem.split("_")
        if len(parts) >= 2 and parts[-1] in ("1m", "5m", "15m", "1h", "4h", "1d"):
            sym, tf = parts[0].upper(), parts[-1]
        candles = load_csv(argv[1], symbol=sym, timeframe=tf)
        is_synthetic = False
        print(f"Loaded {len(candles)} candles from {argv[1]} ({sym} {tf})")
    else:
        candles = generate(bars=4000)
        is_synthetic = True
        print(f"Generated {len(candles)} synthetic 15m candles (seeded, demo only)")

    # simple in-sample / out-of-sample split (design §8.1)
    split = int(len(candles) * 0.7)
    train, test = candles[:split], candles[split:]

    print()
    print(format_report(run(train, policy, fees).metrics, "In-sample (train 70%)"))
    print()
    print(format_report(run(test, policy, fees).metrics, "Out-of-sample (test 30%)"))
    print()
    if is_synthetic:
        print("หมายเหตุ: ข้อมูลสังเคราะห์เพื่อทดสอบว่าท่อทำงาน — ตัวเลขไม่ใช่ผลจริง")
        print("ดึงข้อมูลจริง:  python -m backtest.fetch_binance BTCUSDT 15m 5000")
    else:
        print("หมายเหตุ: ข้อมูลจริง แต่ยังเป็นการทดสอบเบื้องต้น —")
        print("edge จริงต้องยืนยันด้วยข้อมูลยาวขึ้น + walk-forward หลายช่วงตลาด (design §8)")


if __name__ == "__main__":
    main(sys.argv)
