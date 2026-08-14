"""ส่งข้อความทดสอบ 1 ครั้งเข้าช่องทางที่ตั้งไว้ (Discord/LINE/Telegram/console)

ใช้ยืนยันว่า notifier ต่อกับปลายทางได้จริง โดยไม่ต้องรอสัญญาณจริง ข้อความเป็น
ตัวอย่าง "เหตุการณ์ทางเทคนิค" รูปแบบเดียวกับที่ระบบส่งจริง แต่ติดป้าย [ทดสอบ]

    python -m scripts.test_notify         # อ่าน DISCORD_WEBHOOK_URL/LINE_* จาก .env
"""
from __future__ import annotations

import asyncio
import sys


def _sample_signal():
    from worker.app.models import Direction, Signal
    return Signal(
        exchange="binance", symbol="BTCUSDT", timeframe="15m",
        candle_open_time=1_700_000_000_000, strategy_name="trend_following",
        strategy_version="1.1.0", direction=Direction.LONG, signal_score=72.5,
        score_breakdown={}, market_regime={"regime": "uptrend"},
        entry_price=64250.0, stop_loss=63100.0, take_profit=66550.0, expected_rr=2.0,
        risk_status="approved", rejection_reason=None, indicators={},
        trigger_reasons=["htf_aligned", "ema_stack"], status="approved",
        position_size=0.0031, risk_amount=0.05, risk_pct=0.5)


async def _main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    from worker.app.config import load_settings
    from worker.app.main import build_notifier
    from worker.app.notifier import format_signal

    notifier = build_notifier(load_settings())
    print(f"notifier: {type(notifier).__name__}")
    msg = "🧪 [ทดสอบ] ระบบแจ้งเตือนต่อกับปลายทางได้แล้ว\n" + \
          "─────────────\n" + format_signal(_sample_signal(), ref=999)
    ok = await notifier.send(msg, priority="high")
    print("✅ ส่งสำเร็จ" if ok else "❌ ส่งไม่สำเร็จ — เช็ค URL/token และ log ข้างบน")


if __name__ == "__main__":
    asyncio.run(_main())
