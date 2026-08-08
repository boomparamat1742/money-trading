"""Paper Trading Engine (design §4.9) — simulates fills with fee + slippage,
tracks TP/SL bar-by-bar, and optionally trails the stop. Used identically in
real-time and backtest so results transfer (design F17).

Trailing (v1.1): once price has moved `trail_r_activate` R in favor, the stop is
ratcheted to `trail_r_dist` R behind the best price seen. R = the initial stop
distance. Exits are checked against the CURRENT stop first, THEN the stop is
trailed for the NEXT bar — so there is no intrabar look-ahead.
"""
from __future__ import annotations

from typing import Optional

from .config import Fees
from .models import Candle, Direction, PaperTrade, RiskDecision, TradeStatus


# สาเหตุการปิด (machine-readable) → คำอธิบายไทยสำหรับแจ้งเตือน
EXIT_REASON_TH = {
    "tp": "ถึงเป้า TP",
    "sl_initial": "โดน SL เดิม (จุดที่ตั้งไว้ตอนเข้า)",
    "sl_trailing": "โดน trailing stop (SL ถูกเลื่อนตามกำไรแล้ว)",
    "expired": "ครบเวลาถือสูงสุด — ปิดที่ราคาตลาด",
}

# รูปแบบของไม้ที่โดน SL — ตัวนี้แหละที่เอาไปวิเคราะห์ต่อ เพราะแต่ละแบบ
# ชี้ไปที่ปัญหาคนละจุด และต้องแก้คนละทาง
EXIT_PATTERN_TH = {
    "never_worked": "ราคาแทบไม่วิ่งไปทางเราเลย → ปัญหาอยู่ที่จังหวะเข้า/ฟิลเตอร์",
    "stalled":      "วิ่งไปได้บ้างแต่ไม่ถึง 1R แล้วย้อนกลับ → สัญญาณอ่อน หรือ SL แคบไป",
    "gave_back":    "เคยกำไรเกิน 1R แล้วคืนหมด → ปัญหาอยู่ที่การบริหารจุดออก",
    "trail_locked": "trailing stop ทำงานตามหน้าที่ ล็อกส่วนที่ได้ไว้",
    "target_hit":   "ไปถึงเป้าตามแผน",
    "timeout":      "ไม่ถึงทั้ง TP และ SL จนหมดเวลา → ตลาดนิ่ง หรือเป้าไกลไป",
}

FAST_STOP_BARS = 2      # โดนภายในกี่แท่งถึงเรียกว่า "เข้าผิดจังหวะทันที"
GAVE_BACK_R = 1.0       # เคยกำไรถึงกี่ R ถึงนับว่า "คืนกำไร"
NEVER_WORKED_R = 0.25   # กำไรสูงสุดต่ำกว่ากี่ R ถึงนับว่า "ไม่เคยไปทางเรา"


def classify_exit(t: PaperTrade) -> tuple[str, str]:
    """แยกสาเหตุการปิดเป็น (reason, pattern).

    เหตุผลที่ต้องแยก `sl_initial` ออกจาก `sl_trailing`: ทั้งคู่ status เป็น
    hit_sl เหมือนกัน แต่ตัวหลังคือระบบล็อกกำไรได้สำเร็จ ส่วนตัวแรกคือสมมติฐาน
    ผิด — ถ้านับรวมกัน สถิติ "แพ้" จะบวมเกินจริงจนวิเคราะห์อะไรไม่ได้
    """
    R = t.init_risk or 0.0
    mfe_r = (t.max_favorable_excursion / R) if R else 0.0

    if t.status == TradeStatus.HIT_TP:
        return "tp", "target_hit"
    if t.status == TradeStatus.EXPIRED:
        return "expired", "timeout"
    if t.status != TradeStatus.HIT_SL:
        return t.status.value, "n/a"

    # SL: stop ถูกเลื่อนไปแล้วหรือยัง (LONG เลื่อนขึ้น / SHORT เลื่อนลง)
    moved = (t.stop_loss > t.initial_stop if t.side == Direction.LONG
             else t.stop_loss < t.initial_stop)
    if moved:
        return "sl_trailing", "trail_locked"
    if mfe_r >= GAVE_BACK_R:
        return "sl_initial", "gave_back"
    if mfe_r < NEVER_WORKED_R:
        return "sl_initial", "never_worked"
    return "sl_initial", "stalled"


def attach_exit_market(t: PaperTrade, snap) -> None:
    """แนบสภาพตลาดตอนปิดเข้าไปใน exit_context (เรียกหลังอินดิเคเตอร์ของแท่งนั้น
    ถูกคำนวณแล้ว). เก็บเฉพาะตัวที่ใช้ตอบคำถามว่า "ตอนโดนตลาดเป็นยังไง" ไม่ต้องทั้งชุด."""
    if not t.exit_context or snap is None:
        return
    keys = ("adx", "rsi", "atr", "atr_percentile", "ema20", "ema50", "ema200",
            "vwap", "vwap_dist_pct", "vol_ratio")
    t.exit_context["market"] = {k: round(snap.values[k], 6)
                                for k in keys if k in snap.values}


class PaperBroker:
    def __init__(self, fees: Fees, max_holding_bars: int = 96,
                 trail_r_activate: Optional[float] = None, trail_r_dist: float = 1.0):
        self.fees = fees
        self.max_holding_bars = max_holding_bars
        self.trail_r_activate = trail_r_activate   # None → trailing disabled
        self.trail_r_dist = trail_r_dist

    def open(self, decision: RiskDecision, side: Direction, symbol: str,
             signal_id: Optional[int], at: Candle) -> PaperTrade:
        slip = self.fees.slippage_pct / 100
        filled = decision.entry_price * (1 + slip) if side == Direction.LONG else decision.entry_price * (1 - slip)
        notional = filled * decision.position_size
        entry_fee = notional * self.fees.taker_fee_pct / 100
        t = PaperTrade(
            signal_id=signal_id, symbol=symbol, side=side, status=TradeStatus.OPEN,
            requested_entry=decision.entry_price, filled_entry=round(filled, 8),
            stop_loss=decision.stop_loss, take_profit=decision.take_profit,
            position_size=decision.position_size, risk_amount=decision.risk_amount or 0.0,
            risk_pct=decision.risk_pct or 0.0, entry_fee=round(entry_fee, 6),
            slippage=round(abs(filled - decision.entry_price) * decision.position_size, 6),
            opened_at=at.open_time,
        )
        t.bars_held = 0
        t.init_risk = abs(filled - decision.stop_loss)
        t.initial_stop = decision.stop_loss   # trailing เขียนทับ stop_loss — เก็บของเดิมไว้เทียบ
        t.extreme = filled
        return t

    def update(self, t: PaperTrade, candle: Candle) -> PaperTrade:
        if t.status != TradeStatus.OPEN:
            return t
        t.bars_held += 1

        # excursions (reporting)
        if t.side == Direction.LONG:
            t.max_favorable_excursion = max(t.max_favorable_excursion, candle.high - t.filled_entry)
            t.max_adverse_excursion = min(t.max_adverse_excursion, candle.low - t.filled_entry)
            hit_sl = candle.low <= t.stop_loss
            hit_tp = candle.high >= t.take_profit
        else:
            t.max_favorable_excursion = max(t.max_favorable_excursion, t.filled_entry - candle.low)
            t.max_adverse_excursion = min(t.max_adverse_excursion, t.filled_entry - candle.high)
            hit_sl = candle.high >= t.stop_loss
            hit_tp = candle.low <= t.take_profit

        # 1) exits vs CURRENT stop/tp (SL assumed first if both in one bar — conservative)
        if hit_sl:
            self._close(t, t.stop_loss, TradeStatus.HIT_SL, candle)
            return t
        if hit_tp:
            self._close(t, t.take_profit, TradeStatus.HIT_TP, candle)
            return t
        if t.bars_held >= self.max_holding_bars:
            self._close(t, candle.close, TradeStatus.EXPIRED, candle)
            return t

        # 2) still open → trail the stop for the NEXT bar
        if self.trail_r_activate is not None:
            R = t.init_risk
            if R > 0:
                if t.side == Direction.LONG:
                    t.extreme = max(t.extreme, candle.high)
                    if t.extreme - t.filled_entry >= self.trail_r_activate * R:
                        t.stop_loss = max(t.stop_loss, t.extreme - self.trail_r_dist * R)
                else:
                    t.extreme = min(t.extreme, candle.low)
                    if t.filled_entry - t.extreme >= self.trail_r_activate * R:
                        t.stop_loss = min(t.stop_loss, t.extreme + self.trail_r_dist * R)
        return t

    def _close(self, t: PaperTrade, price: float, status: TradeStatus, candle: Candle) -> None:
        slip = self.fees.slippage_pct / 100
        exit_px = price * (1 - slip) if t.side == Direction.LONG else price * (1 + slip)
        notional = exit_px * t.position_size
        t.exit_fee = round(notional * self.fees.taker_fee_pct / 100, 6)
        if t.side == Direction.LONG:
            gross = (exit_px - t.filled_entry) * t.position_size
        else:
            gross = (t.filled_entry - exit_px) * t.position_size
        t.exit_price = round(exit_px, 8)
        t.pnl_amount = round(gross - t.entry_fee - t.exit_fee, 6)
        t.pnl_pct = round((t.pnl_amount / t.risk_amount) * t.risk_pct, 4) if t.risk_amount else 0.0
        risk_per_unit = abs(t.filled_entry - t.stop_loss)
        t.actual_rr = round((abs(exit_px - t.filled_entry) / risk_per_unit) * (1 if t.pnl_amount >= 0 else -1), 3) if risk_per_unit else 0.0
        t.status = status
        t.closed_at = candle.open_time

        # บันทึกสาเหตุ + หลักฐานรอบตัวไว้ให้วิเคราะห์ย้อนหลัง (ตอนปิดเท่านั้นที่รู้ครบ)
        reason, pattern = classify_exit(t)
        R = t.init_risk or 0.0
        t.exit_reason = reason
        t.exit_context = {
            "pattern": pattern,
            "mfe_r": round(t.max_favorable_excursion / R, 3) if R else None,
            "mae_r": round(t.max_adverse_excursion / R, 3) if R else None,
            "bars_held": t.bars_held,
            # "เข้าผิดจังหวะ" = ตายเร็ว *และ* ไม่เคยไปทางเราเลย ไม้ที่วิ่งไป 1.5R
            # ใน 2 แท่งแล้วค่อยโดน ไม่ใช่ปัญหาจังหวะเข้า
            "fast_stop": pattern == "never_worked" and t.bars_held <= FAST_STOP_BARS,
            "stop_moved": round(t.stop_loss - t.initial_stop, 8) != 0,
            "initial_stop": t.initial_stop,
            "final_stop": t.stop_loss,
            "exit_candle": {"o": candle.open, "h": candle.high,
                            "l": candle.low, "c": candle.close, "v": candle.volume},
        }
