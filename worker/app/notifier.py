"""Notification Service (design §4.12).

Console notifier works out of the box; Telegram/LINE are filled in when tokens
exist. All are idempotent via a (signal, channel, type) key held by the caller.
"""
from __future__ import annotations

import asyncio
import os
from typing import Optional

from .models import Signal


def suggest_leverage(entry: Optional[float], stop: Optional[float]) -> Optional[dict]:
    """แนะนำ leverage สูงสุดที่ปลอดภัยสำหรับ Isolated margin: ให้ liquidation
    อยู่ไกลกว่า SL อย่างน้อย LIQ_BUFFER เท่า (กันโดน liquidate ก่อนโดน stop).

    liquidation (isolated) ≈ ราคาเคลื่อน 1/leverage → ต้องการ 1/L ≥ buffer × ระยะ SL
    ดังนั้น max_safe_leverage = 1 / (buffer × stop_pct). แนะนำใช้ ≤ ค่านี้ และไม่เกิน
    เพดาน LEVERAGE_CAP. ความเสี่ยงต่อไม้ไม่เปลี่ยนตาม leverage (คุมด้วย SL/ขนาดไม้)."""
    if not entry or not stop or entry == stop:
        return None
    stop_pct = abs(entry - stop) / entry
    if stop_pct <= 0:
        return None
    buffer = float(os.environ.get("LIQ_BUFFER", 2.0))          # liquidation ไกลกว่า SL กี่เท่า
    cap = float(os.environ.get("LEVERAGE_CAP", 10))            # เพดาน leverage ที่ยอมแนะนำ
    max_safe = 1.0 / (buffer * stop_pct)
    leverage = max(1, min(int(max_safe), int(cap)))
    return {"leverage": leverage, "stop_pct": stop_pct, "max_safe": max_safe,
            "buffer": buffer, "cap": cap}


def format_vwap(ind: dict) -> Optional[str]:
    """บรรทัด VWAP สำหรับข้อความแจ้งเตือน — เป็นข้อมูลประกอบเท่านั้น
    ยังไม่ได้ใช้เป็นเงื่อนไขในกลยุทธ์ (รอทดสอบใน Edge Lab ก่อน)"""
    vwap = ind.get("vwap")
    if not vwap:
        return None
    close = ind.get("close")
    dist = ind.get("vwap_dist_pct", 0.0)
    u1, l1 = ind.get("vwap_upper1"), ind.get("vwap_lower1")
    u2, l2 = ind.get("vwap_upper2"), ind.get("vwap_lower2")

    if close is not None and u2 is not None and close >= u2:
        zone = "เหนือ +2σ (ยืดมาก)"
    elif close is not None and u1 is not None and close >= u1:
        zone = "เหนือ +1σ"
    elif close is not None and l2 is not None and close <= l2:
        zone = "ใต้ −2σ (ยืดมาก)"
    elif close is not None and l1 is not None and close <= l1:
        zone = "ใต้ −1σ"
    else:
        zone = "ในกรอบ ±1σ"

    side = "เหนือ" if dist >= 0 else "ใต้"
    return (f"VWAP วันนี้: {vwap:,.2f} — ราคา{side} VWAP {abs(dist):.2f}% · {zone}\n"
            f"  ⓘ ข้อมูลประกอบ (ยังไม่ใช้ตัดสินใจ) · รีเซ็ต 00:00 UTC")


def format_signal(sig: Signal, ai_note: Optional[str] = None) -> str:
    d = "LONG" if sig.direction.value == "long" else "SHORT"
    lines = [
        f"🔔 เหตุการณ์ทางเทคนิค [{sig.symbol}] {d} — {sig.strategy_name}",
        f"Score: {sig.signal_score}/100 ({', '.join(sig.trigger_reasons) or '-'})",
    ]
    if sig.entry_price is not None:
        lines += [
            f"ราคาอ้างอิง: {sig.entry_price}",
            f"ระดับ SL (อ้างอิง): {sig.stop_loss}",
            f"ระดับ TP (อ้างอิง): {sig.take_profit}",
            f"R:R: {sig.expected_rr}",
        ]
        lev = suggest_leverage(sig.entry_price, sig.stop_loss)
        if lev:
            lines += [
                f"Adjust Leverage (Isolated): {lev['leverage']}x  (อย่าเกิน {int(lev['max_safe'])}x)",
                f"  SL ห่าง {lev['stop_pct']*100:.2f}% · liquidation ต้องไกลกว่า SL ~{lev['buffer']:.0f}×",
            ]
            if sig.position_size:
                base = sig.symbol.replace("USDT", "").replace("USDC", "")
                notional = sig.position_size * sig.entry_price
                margin = notional / lev["leverage"]
                lines += [
                    f"ขนาดไม้: {sig.position_size:.6g} {base} (มูลค่า ~${notional:,.0f})",
                    f"Margin ต้องวาง (Isolated {lev['leverage']}x): ~${margin:,.2f}",
                ]
                if sig.risk_amount:
                    lines.append(
                        f"ขาดทุนสูงสุดถ้าโดน SL: ~${sig.risk_amount:,.2f} ({sig.risk_pct}% ของทุน)")
            lines.append("  ⓘ ความเสี่ยงคุมด้วย SL/ขนาดไม้ ไม่ใช่ leverage — เลือกต่ำไว้ปลอดภัยกว่า")
    vwap_line = format_vwap(sig.indicators)
    if vwap_line:
        lines.append(vwap_line)
    lines += [
        f"Regime: {sig.market_regime.get('regime')}",
        f"AI Context: {ai_note or '-'}",
        "─────────────",
        "⚠️ สัญญาณเทคนิคเฝ้าตลาด · ยังไม่พิสูจน์ว่ามี edge (backtest OOS ไม่ผ่าน)",
        "Paper only · ไม่ใช่คำแนะนำการลงทุน · ตรวจสอบเองก่อนตัดสินใจ",
    ]
    return "\n".join(lines)


def format_close(t) -> str:
    """ข้อความปิดสถานะ — ต้องบอก *สาเหตุ* ไม่ใช่แค่สถานะ.

    "hit_sl" อย่างเดียวตอบไม่ได้ว่าควรแก้อะไร: โดน trailing stop หลังกำไร 2R
    กับโดน SL เดิมโดยราคาไม่เคยขยับไปทางเราเลย เป็นคนละเรื่องกันสิ้นเชิง
    """
    from .paper_trading import EXIT_PATTERN_TH, EXIT_REASON_TH

    ctx = t.exit_context or {}
    reason = t.exit_reason or (t.status.value if hasattr(t.status, "value") else str(t.status))
    icon = {"tp": "🟢", "sl_trailing": "🟡", "sl_initial": "🔴"}.get(reason, "⚪")
    side = t.side.value.upper() if hasattr(t.side, "value") else str(t.side).upper()

    lines = [f"{icon} [{t.symbol}] ปิดสถานะ {side} — {EXIT_REASON_TH.get(reason, reason)}"]

    pattern = ctx.get("pattern")
    if pattern in EXIT_PATTERN_TH:
        lines.append(f"สาเหตุ: {EXIT_PATTERN_TH[pattern]}")

    lines.append(f"PnL: {t.pnl_amount}  (RR {t.actual_rr})")

    mfe, mae = ctx.get("mfe_r"), ctx.get("mae_r")
    if mfe is not None and mae is not None:
        lines.append(f"ระหว่างถือ: กำไรสูงสุด +{mfe:.2f}R · ขาดทุนสูงสุด {mae:.2f}R")

    bars = ctx.get("bars_held")
    if bars is not None:
        note = " ⚠️ โดนเร็วมาก" if ctx.get("fast_stop") else ""
        lines.append(f"ถือไว้ {bars} แท่ง{note}")

    if ctx.get("stop_moved"):
        lines.append(f"SL ถูกเลื่อนจาก {ctx.get('initial_stop'):,.8g} "
                     f"→ {ctx.get('final_stop'):,.8g}")

    lines.append("บันทึกลง journal แล้ว (ใช้วิเคราะห์ย้อนหลังได้)")
    return "\n".join(lines)


class Notifier:
    async def send(self, text: str) -> bool:  # pragma: no cover - interface
        raise NotImplementedError


class DailyQuota(Notifier):
    """Wrap a notifier with a daily message cap. LINE's free tier allows only a
    few hundred pushes per month — an unguarded signal stream can burn through it
    in weeks. Once the cap is hit the remaining messages are logged, not sent,
    and a single 'quota reached' notice goes out."""

    def __init__(self, inner: Notifier, max_per_day: int):
        self.inner = inner
        self.max_per_day = max_per_day
        self._day: Optional[int] = None
        self._sent = 0

    async def send(self, text: str) -> bool:
        import time
        day = int(time.time() // 86_400)
        if self._day != day:
            self._day, self._sent = day, 0
        if self._sent >= self.max_per_day:
            print(f"[notify] quota {self.max_per_day}/day reached — not sent:\n{text}\n")
            return False
        self._sent += 1
        if self._sent == self.max_per_day:
            text += f"\n\n(ถึงโควตา {self.max_per_day} ข้อความ/วันแล้ว — ข้อความถัดไปจะขึ้นเฉพาะใน log)"
        return await self.inner.send(text)


class ConsoleNotifier(Notifier):
    async def send(self, text: str) -> bool:
        print("── NOTIFY ──\n" + text + "\n")
        return True


class LineNotifier(Notifier):
    """LINE Messaging API push (LINE Notify is discontinued).

    Needs a Channel access token and a destination id (userId/groupId). Retries
    up to 3× on transient errors (design §6.2).
    """

    PUSH_URL = "https://api.line.me/v2/bot/message/push"

    def __init__(self, channel_token: str, to: str):
        self.channel_token = channel_token
        self.to = to

    async def send(self, text: str) -> bool:
        import aiohttp

        headers = {"Authorization": f"Bearer {self.channel_token}",
                   "Content-Type": "application/json"}
        # LINE hard-limits a text message to 5000 chars
        payload = {"to": self.to, "messages": [{"type": "text", "text": text[:4900]}]}
        for attempt in range(3):
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.post(self.PUSH_URL, headers=headers, json=payload,
                                      timeout=aiohttp.ClientTimeout(total=15)) as r:
                        if r.status == 200:
                            return True
                        body = await r.text()
                        print(f"[line] HTTP {r.status}: {body[:200]}")
                        if r.status in (400, 401, 403):
                            return False  # bad token/id/payload — retrying won't help
            except Exception as e:
                print(f"[line] send failed (attempt {attempt + 1}): {e!r}")
            await asyncio.sleep(1.5 * (attempt + 1))
        return False


class TelegramNotifier(Notifier):
    """Optional Telegram fallback."""

    def __init__(self, token: str, chat_id: str):
        self.token, self.chat_id = token, chat_id

    async def send(self, text: str) -> bool:
        import aiohttp

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        for attempt in range(3):
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.post(url, json={"chat_id": self.chat_id, "text": text[:4000]},
                                      timeout=aiohttp.ClientTimeout(total=15)) as r:
                        if r.status == 200:
                            return True
            except Exception as e:
                print(f"[telegram] send failed (attempt {attempt + 1}): {e!r}")
            await asyncio.sleep(1.5 * (attempt + 1))
        return False
