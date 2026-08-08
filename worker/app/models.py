"""Domain models — the vocabulary shared across the whole pipeline.

All dataclasses, no external deps. These mirror the DB schema in design §7 so
persisting a Signal/Trade is a straight field map.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"


class RiskStatus(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"


class TradeStatus(str, Enum):
    PENDING = "pending"
    OPEN = "open"
    HIT_TP = "hit_tp"
    HIT_SL = "hit_sl"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass
class Candle:
    """A single OHLCV bar. Only closed candles drive strategy logic (design §3.2)."""
    exchange: str
    symbol: str
    timeframe: str
    open_time: int          # ms epoch (exchange timestamp — authoritative)
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool = True

    @property
    def idempotency_key(self) -> str:
        # exchange:symbol:timeframe:open_time  (design §4.3)
        return f"{self.exchange}:{self.symbol}:{self.timeframe}:{self.open_time}"


@dataclass
class IndicatorSnapshot:
    """Indicator values at a candle close. `ready` gates signal generation."""
    ready: bool = False
    values: dict[str, float] = field(default_factory=dict)

    def get(self, name: str, default: float = float("nan")) -> float:
        return self.values.get(name, default)


@dataclass
class RegimeResult:
    regime: str                      # uptrend|downtrend|sideway|high_volatility|low_liquidity|unsafe
    strength: float = 0.0            # 0..1
    volatility_state: str = "normal" # low|normal|high
    liquidity_state: str = "normal"  # low|normal


@dataclass
class StrategyResult:
    strategy_name: str
    strategy_version: str
    direction: Direction
    raw_score: float                 # 0..100 pre-scoring hint from the strategy
    reasons: list[str] = field(default_factory=list)
    invalidations: list[str] = field(default_factory=list)


@dataclass
class ScoreResult:
    total: float
    breakdown: dict[str, float] = field(default_factory=dict)

    @property
    def level(self) -> str:
        if self.total >= 80:
            return "strong"
        if self.total >= 65:
            return "normal"
        if self.total >= 50:
            return "watchlist"
        return "no_trade"


@dataclass
class RiskDecision:
    status: RiskStatus
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    position_size: Optional[float] = None
    risk_amount: Optional[float] = None
    risk_pct: Optional[float] = None
    expected_rr: Optional[float] = None
    rejection_reason: Optional[str] = None


@dataclass
class Signal:
    """The full, auditable record — every field traces back to a rule (design N11)."""
    exchange: str
    symbol: str
    timeframe: str
    candle_open_time: int
    strategy_name: str
    strategy_version: str
    direction: Direction
    signal_score: float
    score_breakdown: dict[str, float]
    market_regime: dict[str, Any]
    entry_price: Optional[float]
    stop_loss: Optional[float]
    take_profit: Optional[float]
    expected_rr: Optional[float]
    risk_status: str
    rejection_reason: Optional[str]
    indicators: dict[str, float]
    trigger_reasons: list[str]
    status: str
    ai_context: Optional[dict[str, Any]] = None
    ai_status: str = "pending"
    prompt_version: Optional[str] = None
    position_size: Optional[float] = None
    risk_amount: Optional[float] = None
    risk_pct: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["direction"] = self.direction.value if isinstance(self.direction, Direction) else self.direction
        return d


@dataclass
class PaperTrade:
    signal_id: Optional[int]
    symbol: str
    side: Direction
    status: TradeStatus
    requested_entry: float
    stop_loss: float
    take_profit: float
    position_size: float
    risk_amount: float
    risk_pct: float
    filled_entry: Optional[float] = None
    entry_fee: float = 0.0
    exit_fee: float = 0.0
    slippage: float = 0.0
    exit_price: Optional[float] = None
    pnl_amount: Optional[float] = None
    pnl_pct: Optional[float] = None
    actual_rr: Optional[float] = None
    max_favorable_excursion: float = 0.0
    max_adverse_excursion: float = 0.0
    opened_at: Optional[int] = None
    closed_at: Optional[int] = None
    # runtime state owned by PaperBroker (kept on the trade, NOT in an id()-keyed
    # dict — CPython reuses id() after GC, and such dicts never get cleaned up)
    bars_held: int = 0
    init_risk: float = 0.0        # R in price terms, fixed at entry
    initial_stop: float = 0.0     # stop at entry — trailing overwrites stop_loss
    extreme: float = 0.0          # best price seen since entry (for trailing)
    # why the trade ended. `status` says WHAT happened (hit_sl); these say WHY —
    # a trailing-stop exit and a thesis-was-wrong exit are both "hit_sl" but are
    # completely different events, and only one of them is a problem to fix.
    exit_reason: Optional[str] = None      # tp | sl_initial | sl_trailing | expired
    exit_context: dict[str, Any] = field(default_factory=dict)
    db_id: Optional[int] = None   # journal row id (set once persisted)
