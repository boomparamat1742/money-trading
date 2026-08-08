"""Signal Pipeline — the shared heart used by BOTH real-time and backtest
(design F17, §5 Data Flow). Feed it a closed candle; it returns a fully-formed,
auditable Signal (or None when no strategy fires / data not warm).

Split into two steps so the expensive, parameter-INDEPENDENT part (indicators →
regime → strategy → score) can be computed once and reused across many risk
parameter combos during walk-forward optimization:

    candidate(candle, htf_trend) -> Candidate | None      # param-independent
    process(candle, portfolio, htf_trend) -> Signal | None # adds threshold + risk

The AI Context Service is deliberately NOT here — it runs on the slow path,
after a Signal exists, and never blocks or alters the quant decision (§3.3).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .config import RiskPolicy
from .indicators import IndicatorEngine
from .models import (Candle, IndicatorSnapshot, RegimeResult, RiskStatus,
                     ScoreResult, Signal, StrategyResult)
from .regime import detect_regime
from .risk import ATR_SL_MULT, ATR_TP_MULT, PortfolioState, evaluate_risk
from .scoring import score_signal
from .strategies.base import StrategyContext
from .strategies.registry import best_signal


@dataclass
class Candidate:
    """A scored, pre-risk trade idea. Parameter-independent."""
    snap: IndicatorSnapshot
    regime: RegimeResult
    strat: StrategyResult
    score: ScoreResult


class SignalPipeline:
    def __init__(self, policy: RiskPolicy,
                 sl_mult: float = ATR_SL_MULT, tp_mult: float = ATR_TP_MULT):
        self.policy = policy
        self.sl_mult = sl_mult
        self.tp_mult = tp_mult
        self.ind = IndicatorEngine()
        # last computed snapshot, kept even when no signal fires — callers use it
        # to record the market conditions a trade exited in (design §7 journal)
        self.last_snapshot: Optional[IndicatorSnapshot] = None

    def candidate(self, candle: Candle, htf_trend: int = 0) -> Optional[Candidate]:
        """Indicators → regime → strategy → score. No risk, no threshold.
        Advances the indicator engine (call once per candle, in order)."""
        if not candle.is_closed:
            return None
        snap = self.ind.update(candle)
        self.last_snapshot = snap
        if not snap.ready:
            return None
        regime = detect_regime(snap)
        if regime.regime == "unsafe":
            return None
        ctx = StrategyContext(snapshot=snap, regime=regime, htf_trend=htf_trend)
        strat = best_signal(ctx)
        if strat is None:
            return None
        score = score_signal(snap, regime, strat, htf_trend)
        return Candidate(snap=snap, regime=regime, strat=strat, score=score)

    def process(self, candle: Candle, portfolio: PortfolioState,
                htf_trend: int = 0) -> Optional[Signal]:
        cand = self.candidate(candle, htf_trend)
        if cand is None:
            return None

        # threshold gate (design §4.7) — below it we still record, but don't trade
        if cand.score.total < self.policy.signal_score_threshold:
            return self._build(candle, cand,
                               risk_status=RiskStatus.REJECTED.value,
                               rejection_reason="below_score_threshold",
                               status="watchlist")

        decision = evaluate_risk(cand.strat.direction, cand.snap, self.policy,
                                 portfolio, self.sl_mult, self.tp_mult)
        if decision.status == RiskStatus.REJECTED:
            return self._build(candle, cand, risk_status=decision.status.value,
                               rejection_reason=decision.rejection_reason,
                               status="rejected")

        sig = self._build(candle, cand, risk_status=decision.status.value,
                          rejection_reason=None, status="approved")
        sig.entry_price = decision.entry_price
        sig.stop_loss = decision.stop_loss
        sig.take_profit = decision.take_profit
        sig.expected_rr = decision.expected_rr
        sig.position_size = decision.position_size
        sig.risk_amount = decision.risk_amount
        sig.risk_pct = decision.risk_pct
        sig._decision = decision  # type: ignore[attr-defined]
        return sig

    def _build(self, candle: Candle, cand: Candidate, *,
               risk_status, rejection_reason, status) -> Signal:
        return Signal(
            exchange=candle.exchange, symbol=candle.symbol, timeframe=candle.timeframe,
            candle_open_time=candle.open_time,
            strategy_name=cand.strat.strategy_name, strategy_version=cand.strat.strategy_version,
            direction=cand.strat.direction, signal_score=cand.score.total,
            score_breakdown=cand.score.breakdown,
            market_regime={"regime": cand.regime.regime, "strength": cand.regime.strength,
                           "volatility_state": cand.regime.volatility_state,
                           "liquidity_state": cand.regime.liquidity_state},
            entry_price=None, stop_loss=None, take_profit=None, expected_rr=None,
            risk_status=risk_status, rejection_reason=rejection_reason,
            indicators={k: round(v, 6) for k, v in cand.snap.values.items()},
            trigger_reasons=cand.strat.reasons, status=status,
        )
