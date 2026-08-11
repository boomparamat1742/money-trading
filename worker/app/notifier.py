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


def fmt_price(p: Optional[float]) -> str:
    """ราคาสำหรับ *แสดงผล* เท่านั้น — คำนวณยังใช้ค่าเต็มความละเอียดเหมือนเดิม

    2 ตำแหน่งพอสำหรับเหรียญหลัก (BTC/ETH/BNB) แต่เหรียญที่ราคาต่ำกว่า $1
    เช่น DOGE 0.08432 ถ้าปัดเหลือ 2 ตำแหน่งจะกลายเป็น 0.08 ซึ่งเอาไปตั้ง SL
    ไม่ได้จริง — ช่วงนั้นจึงใช้เลขนัยสำคัญแทน
    """
    if p is None:
        return "-"
    return f"{p:,.2f}" if abs(p) >= 1 else f"{p:,.6g}"


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
    return (f"VWAP วันนี้: {fmt_price(vwap)} — ราคา{side} VWAP {abs(dist):.2f}% · {zone}\n"
            f"  ⓘ ข้อมูลประกอบ (ยังไม่ใช้ตัดสินใจ) · รีเซ็ต 00:00 UTC")


def format_signal(sig: Signal, ai_note: Optional[str] = None,
                  ref: Optional[int] = None) -> str:
    d = "LONG" if sig.direction.value == "long" else "SHORT"
    tag = f"ไม้ #{ref} · " if ref is not None else ""   # เลขไม้เดียวกับตอนปิด → จับคู่ได้
    lines = [
        f"🔔 {tag}เหตุการณ์ทางเทคนิค [{sig.symbol}] {d} — {sig.strategy_name}",
        f"Score: {sig.signal_score}/100 ({', '.join(sig.trigger_reasons) or '-'})",
    ]
    if sig.entry_price is not None:
        lines += [
            f"ราคาอ้างอิง: {fmt_price(sig.entry_price)}",
            f"ระดับ TP (อ้างอิง): {fmt_price(sig.take_profit)}",
            f"ระดับ SL (อ้างอิง): {fmt_price(sig.stop_loss)}",
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

    # เลขไม้อยู่หัวข้อความ — ตรงกับ "ไม้ #N" ตอนเปิด เพื่อจับคู่ได้ทันที
    tag = f"ไม้ #{t.db_id} · " if t.db_id else ""
    lines = [f"{icon} {tag}[{t.symbol}] ปิดสถานะ {side} — {EXIT_REASON_TH.get(reason, reason)}"]

    detail = []
    if t.opened_at:
        import time
        detail.append("เปิด " + time.strftime("%d/%m %H:%M", time.localtime(t.opened_at / 1000)))
    if t.filled_entry:
        detail.append(f"ที่ {fmt_price(t.filled_entry)}")
    if detail:
        lines.append("  (" + " · ".join(detail) + ")")

    # เข้าไม้นี้เพราะอะไร — สูตร/เงื่อนไขที่จุดชนวน
    ent = t.entry_context or {}
    if ent.get("strategy"):
        head = f"เข้าด้วย: {ent['strategy']}"
        if ent.get("version"):
            head += f" v{ent['version']}"
        if ent.get("score") is not None:
            head += f" · score {ent['score']}"
        if ent.get("regime"):
            head += f" · regime {ent['regime']}"
        lines.append(head)
    if ent.get("reasons"):
        lines.append("  เงื่อนไขที่จุดชนวน: " + ", ".join(ent["reasons"]))

    pattern = ctx.get("pattern")
    if pattern in EXIT_PATTERN_TH:
        lines.append(f"สาเหตุที่ปิด: {EXIT_PATTERN_TH[pattern]}")

    lines.append(f"PnL: {t.pnl_amount}  (RR {t.actual_rr})")

    mfe, mae = ctx.get("mfe_r"), ctx.get("mae_r")
    if mfe is not None and mae is not None:
        lines.append(f"ระหว่างถือ: กำไรสูงสุด +{mfe:.2f}R · ขาดทุนสูงสุด {mae:.2f}R")

    bars = ctx.get("bars_held")
    if bars is not None:
        note = " ⚠️ โดนเร็วมาก" if ctx.get("fast_stop") else ""
        lines.append(f"ถือไว้ {bars} แท่ง{note}")

    if ctx.get("stop_moved"):
        lines.append(f"SL ถูกเลื่อนจาก {fmt_price(ctx.get('initial_stop'))} "
                     f"→ {fmt_price(ctx.get('final_stop'))}")

    lines.append("บันทึกลง journal แล้ว (ใช้วิเคราะห์ย้อนหลังได้)")
    return "\n".join(lines)


class Notifier:
    async def send(self, text: str) -> bool:  # pragma: no cover - interface
        raise NotImplementedError


class DailyQuota(Notifier):
    """คุมปริมาณข้อความ 2 ชั้น: เพดานรายวัน (กันสแปม) + โควตารายเดือนจริงของ LINE

    ทำไมต้องมีชั้นที่ 2: **LINE นับโควตาต่อ "ผู้รับ" ไม่ใช่ต่อข้อความ** — push
    เข้ากลุ่มที่มี 5 คน = 5 ข้อความ แผนฟรีให้ 500/เดือน ดังนั้น 20 ข้อความ/วัน
    ในกลุ่ม 3 คน = 1,800/เดือน คือเกินโควตาตั้งแต่สัปดาห์ที่สอง แล้วระบบจะ
    เงียบสนิทโดยไม่มีใครรู้ — ซึ่งแย่กว่าไม่มีระบบเฝ้าตลาดเลย

    ตัวนับในหน่วยความจำใช้ไม่ได้ เพราะ Railway รีสตาร์ทแล้วนับใหม่จากศูนย์
    จึงถาม LINE เอายอดใช้จริงเป็นระยะแทน (inner.quota())
    """

    def __init__(self, inner: Notifier, max_per_day: int,
                 monthly_reserve: int = 20, refresh_every: int = 10):
        self.inner = inner
        self.max_per_day = max_per_day
        self.monthly_reserve = monthly_reserve   # กันไว้ให้ข้อความเตือนตัวเอง
        self.refresh_every = refresh_every       # ถามยอดจริงทุกกี่ข้อความ
        self._day: Optional[int] = None
        self._sent = 0
        self._since_refresh = 10**9              # ครั้งแรกถามทันที
        self._remaining: Optional[int] = None    # None = ไม่รู้ (ไม่บล็อก)
        self._warned_month = False

    async def _refresh(self) -> None:
        getter = getattr(self.inner, "quota", None)
        if getter is None:
            return
        try:
            q = await getter()
        except Exception as e:
            print(f"[notify] อ่านโควตา LINE ไม่ได้: {e!r}")
            return
        self._since_refresh = 0
        if q is None:                            # แผนไม่จำกัด
            self._remaining = None
            return
        self._remaining = q["remaining"]
        print(f"[notify] โควตา LINE เดือนนี้: ใช้ไป {q['used']}/{q['limit']} "
              f"เหลือ {q['remaining']}")

    async def send(self, text: str) -> bool:
        import time
        day = int(time.time() // 86_400)
        if self._day != day:
            self._day, self._sent = day, 0

        if self._since_refresh >= self.refresh_every:
            await self._refresh()

        if self._remaining is not None and self._remaining <= self.monthly_reserve:
            if not self._warned_month:
                self._warned_month = True
                print(f"[notify] โควตา LINE รายเดือนใกล้หมด (เหลือ {self._remaining}) "
                      f"— หยุดส่งจนกว่าจะขึ้นเดือนใหม่")
            print(f"[notify] ไม่ได้ส่ง:\n{text}\n")
            return False

        if self._sent >= self.max_per_day:
            print(f"[notify] ครบเพดาน {self.max_per_day} ข้อความ/วัน — ไม่ได้ส่ง:\n{text}\n")
            return False

        self._sent += 1
        self._since_refresh += 1
        if self._remaining is not None:
            self._remaining -= 1                 # ประมาณการระหว่างรอ refresh
        if self._sent == self.max_per_day:
            text += f"\n\n(ถึงเพดาน {self.max_per_day} ข้อความ/วันแล้ว — ข้อความถัดไปจะขึ้นเฉพาะใน log)"
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
    QUOTA_URL = "https://api.line.me/v2/bot/message/quota"
    CONSUMPTION_URL = "https://api.line.me/v2/bot/message/quota/consumption"

    def __init__(self, channel_token: str, to: str):
        self.channel_token = channel_token
        self.to = to

    async def quota(self) -> Optional[dict]:
        """โควตาที่เหลือจริงของเดือนนี้ ถามจาก LINE โดยตรง.

        คืน None เมื่อแผนไม่จำกัด (type != "limited") — ตัวนับในเครื่องเชื่อไม่ได้
        เพราะรีสตาร์ททีก็เริ่มนับใหม่ ส่วนของ LINE คือความจริงเพียงหนึ่งเดียว
        """
        import aiohttp

        headers = {"Authorization": f"Bearer {self.channel_token}"}
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as s:
            async with s.get(self.QUOTA_URL) as r:
                if r.status != 200:
                    raise RuntimeError(f"quota HTTP {r.status}: {(await r.text())[:120]}")
                q = await r.json()
            if q.get("type") != "limited":
                return None
            limit = int(q.get("value", 0))
            async with s.get(self.CONSUMPTION_URL) as r:
                if r.status != 200:
                    raise RuntimeError(f"consumption HTTP {r.status}: {(await r.text())[:120]}")
                used = int((await r.json()).get("totalUsage", 0))
        return {"limit": limit, "used": used, "remaining": max(0, limit - used)}

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
