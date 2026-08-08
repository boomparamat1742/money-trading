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
