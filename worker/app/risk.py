"""Risk Manager (design §4.8) — the sole approver of signals, all rules
hard-coded. AI never sets entry/SL/TP/size. Entry/SL/TP derive from ATR;
position size from the fixed-risk formula."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .config import RiskPolicy
from .models import Direction, IndicatorSnapshot, RiskDecision, RiskStatus

ATR_SL_MULT = 1.5   # stop distance = 1.5 * ATR
ATR_TP_MULT = 3.0   # target distance = 3.0 * ATR  → base R:R 2.0


DAY_MS = 86_400_000


@dataclass
class PortfolioState:
    """Live risk counters the manager checks against (design §4.8)."""
    open_trades: int = 0
    open_risk_pct: float = 0.0
    daily_loss_pct: float = 0.0
    consecutive_losses: int = 0
    _day: Optional[int] = None

    def roll_day(self, ts_ms: int) -> bool:
        """Reset per-day counters when the UTC day changes.

        The loss-streak cooldown MUST expire on its own. Resetting it only on a
        winning trade deadlocks the system: once the streak hits the limit no new
        trade can open, so no win can ever occur, and trading stops forever.
        """
        day = ts_ms // DAY_MS
        if self._day is None:
            self._day = day
            return False
        if day != self._day:
            self._day = day
            self.daily_loss_pct = 0.0
            self.consecutive_losses = 0
            return True
        return False


def evaluate_risk(direction: Direction, snap: IndicatorSnapshot,
                  policy: RiskPolicy, portfolio: PortfolioState,
                  sl_mult: float = ATR_SL_MULT, tp_mult: float = ATR_TP_MULT) -> RiskDecision:
    # --- global guards ---
    if policy.kill_switch:
        return RiskDecision(RiskStatus.REJECTED, rejection_reason="kill_switch")
    if portfolio.daily_loss_pct >= policy.daily_loss_limit_pct:
        return RiskDecision(RiskStatus.REJECTED, rejection_reason="daily_loss_limit")
    if portfolio.consecutive_losses >= policy.max_consecutive_losses:
        return RiskDecision(RiskStatus.REJECTED, rejection_reason="consecutive_loss_cooldown")
    if portfolio.open_trades >= policy.max_open_trades:
        return RiskDecision(RiskStatus.REJECTED, rejection_reason="max_open_trades")

    v = snap.values
    entry = v.get("close")
    atr = v.get("atr")
    if entry is None or atr is None or atr <= 0:
        return RiskDecision(RiskStatus.REJECTED, rejection_reason="insufficient_data")

    if direction == Direction.LONG:
        stop = entry - sl_mult * atr
        target = entry + tp_mult * atr
    else:
        stop = entry + sl_mult * atr
        target = entry - tp_mult * atr

    risk_per_unit = abs(entry - stop)
    if risk_per_unit <= 0:
        return RiskDecision(RiskStatus.REJECTED, rejection_reason="zero_stop_distance")

    rr = abs(target - entry) / risk_per_unit
    if rr < policy.min_reward_risk:
        return RiskDecision(RiskStatus.REJECTED, rejection_reason=f"rr_below_min({rr:.2f})")

    # portfolio-level risk budget
    if portfolio.open_risk_pct + policy.risk_per_trade_pct > policy.risk_per_trade_pct * policy.max_open_trades:
        return RiskDecision(RiskStatus.REJECTED, rejection_reason="portfolio_risk_budget")

    risk_amount = policy.account_equity * (policy.risk_per_trade_pct / 100)
    position_size = risk_amount / risk_per_unit

    return RiskDecision(
        status=RiskStatus.APPROVED,
        entry_price=round(entry, 8),
        stop_loss=round(stop, 8),
        take_profit=round(target, 8),
        position_size=round(position_size, 8),
        risk_amount=round(risk_amount, 2),
        risk_pct=policy.risk_per_trade_pct,
        expected_rr=round(rr, 2),
    )
