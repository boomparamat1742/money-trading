"""Funding Carry Monitor — จับ edge จริง (funding carry) ตอนมันโผล่.

การถือ long spot + short perp เก็บ funding เป็น edge เชิงโครงสร้างที่มีจริง แต่
regime-dependent (รวยตอนตลาดกระทิงคนแห่ leverage long, แห้งตอนอื่น). เครื่องมือนี้
ดึง funding ปัจจุบันของทุกเหรียญในจักรวาล จัดอันดับ carry ต่อปี และ:
  • แจ้งเตือน LINE เมื่อมีเหรียญ funding รวยพอ (annualized > เกณฑ์)
  • เงียบเมื่อ funding แห้ง (แทนที่จะหลอกให้เทรด)

รันครั้งเดียว (เหมาะตั้ง schedule ทุก ~8 ชม. ตรงรอบ funding):
    python -m research.carry_monitor
    python -m research.carry_monitor 15        # เกณฑ์ annualized 15%

⚠️ นี่คือ MONITOR — เก็บ carry จริงต้อง execute 2 ขา (spot + perp) เอง
ไม่ใช่คำแนะนำการลงทุน · funding อาจพลิกลบได้ (ตอนนั้นฝั่ง short ต้องจ่าย)
"""
from __future__ import annotations

import asyncio
import json
import sys
import urllib.request

from research.xsmom import UNIVERSE  # จักรวาลเดียวกับที่ backtest
from worker.app.config import load_settings
from worker.app.notifier import ConsoleNotifier, LineNotifier

PREMIUM_INDEX = "https://fapi.binance.com/fapi/v1/premiumIndex"
PERIODS_PER_YEAR = 3 * 365  # สมมติ funding ทุก 8 ชม. (บางเหรียญ 4 ชม. → ประเมินต่ำไปบ้าง)
# Binance มี baseline funding ~0.01%/8h ≈ 11%/ปี (ส่วน interest rate) อยู่แล้ว
# "รวยจริง" (leverage-long euphoria) ต้องสูงกว่า baseline ชัดๆ
BASELINE_ANNUAL = 0.11
DEFAULT_THRESHOLD_ANNUAL = 0.15  # 15%/ปี


def fetch_current_funding() -> dict[str, float]:
    """ดึง lastFundingRate ปัจจุบันของทุก perp (call เดียว)."""
    req = urllib.request.Request(PREMIUM_INDEX, headers={"User-Agent": "carry-monitor/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read().decode())
    out = {}
    for row in data:
        sym = row.get("symbol", "")
        try:
            out[sym] = float(row.get("lastFundingRate", 0.0))
        except (TypeError, ValueError):
            continue
    return out


def build_notifier(s):
    if s.line_channel_token and s.line_to:
        return LineNotifier(s.line_channel_token, s.line_to)
    return ConsoleNotifier()


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    threshold_annual = (float(argv[1]) / 100) if len(argv) > 1 else DEFAULT_THRESHOLD_ANNUAL
    s = load_settings()

    try:
        funding = fetch_current_funding()
    except Exception as e:
        print(f"ดึง funding ไม่ได้: {e!r}")
        return

    rows = []
    for coin in UNIVERSE:
        sym = f"{coin}USDT"
        if sym in funding:
            rate = funding[sym]                      # ต่อ 8 ชม.
            ann = rate * PERIODS_PER_YEAR            # annualized (ประมาณ)
            rows.append((coin, rate, ann))
    rows.sort(key=lambda x: -x[2])

    # ตารางบน console เสมอ
    avg_ann = sum(r[2] for r in rows) / len(rows) if rows else 0.0
    print(f"\n💰 Funding Carry Monitor — เกณฑ์ annualized > {threshold_annual*100:.0f}%  "
          f"(baseline Binance ~{BASELINE_ANNUAL*100:.0f}%)")
    print(f"funding เฉลี่ยจักรวาล: {avg_ann*100:+.1f}%/ปี  "
          f"({'ตลาด leverage-long' if avg_ann > 0.05 else 'funding แห้ง/เป็นกลาง'})\n")
    print(f"{'coin':<8}{'funding/8h':>12}{'≈ ต่อปี':>10}")
    for coin, rate, ann in rows:
        flag = "  ⭐" if ann > threshold_annual else ""
        print(f"{coin:<8}{rate*100:>11.4f}%{ann*100:>9.1f}%{flag}")

    opps = [(c, rate, ann) for c, rate, ann in rows if ann > threshold_annual]

    if not opps:
        print(f"\n🔴 ไม่มีโอกาส carry ตอนนี้ (funding แห้ง — edge หลับอยู่) ไม่ส่งแจ้งเตือน")
        return

    # มีโอกาส → แจ้งเตือน
    lines = [f"💰 โอกาส Funding Carry ({len(opps)} เหรียญ)",
             "long spot + short perp เก็บ funding:"]
    for i, (coin, rate, ann) in enumerate(opps[:8], 1):
        lines.append(f"{i}. {coin}: {rate*100:+.4f}%/8h ≈ {ann*100:+.0f}%/ปี")
    lines += ["─────────────",
              "⚠️ ต้อง execute 2 ขาเอง (spot+perp) · carry อาจพลิกลบ",
              "ไม่ใช่คำแนะนำการลงทุน · ตรวจสภาพคล่อง/cost เองก่อน"]
    text = "\n".join(lines)
    print("\n🟢 พบโอกาส — ส่งแจ้งเตือน:\n" + text)

    notifier = build_notifier(s)
    ok = asyncio.run(notifier.send(text))
    print("\nส่งแจ้งเตือน:", "สำเร็จ ✅" if ok else "ไม่สำเร็จ ❌")


if __name__ == "__main__":
    main(sys.argv)
