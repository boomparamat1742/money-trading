"""Frozen strategy config สำหรับ RTE (สเปก §4 robust_trend_ensemble_v1_frozen).

ทุกค่าที่นี่ "ล็อก" — เปลี่ยนแล้วต้องขึ้น version ใหม่และเริ่มสถิติ forward ใหม่
(สเปก §27) config_hash คำนวณจาก canonical JSON ของค่าเหล่านี้ ใช้ผูกกับทุก
rebalance/snapshot เพื่อพิสูจน์ว่าตอนรัน ใช้กฎชุดเดียวกันจริง (สเปก §19)
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class RTEConfig:
    strategy_version: str = "robust_trend_ensemble_v1_frozen"
    timeframe: str = "4h"
    # 8 majors ตามสเปก — ลำดับนี้ใช้ tie-break (symbol desc) ด้วย
    symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT",
                                "XRPUSDT", "ADAUSDT", "DOGEUSDT", "LINKUSDT")
    ema_horizons: tuple[int, ...] = (21, 63, 126)
    momentum_horizons: tuple[int, ...] = (21, 63, 126)
    min_ensemble_score: int = 3

    btc_ema_bars: int = 100                 # BTC regime: close > EMA100
    crash_lookback_bars: int = 21           # crash filter: BTC 21-bar return
    crash_return_floor: float = -0.10       # <= -10% = crash → cash

    top_n: int = 4
    volatility_lookback_bars: int = 42
    annualization_bars: int = 2190          # sqrt(6×365)
    target_annual_volatility: float = 0.20
    breadth_full_exposure_level: float = 0.50
    max_gross_exposure: float = 1.00

    rebalance_every_bars: int = 6           # ~24h
    rebalance_anchor_utc_hour: int = 0      # 00:00 UTC (สเปก §10)

    fee_plus_slippage_one_way: float = 0.0007
    fee_rate: float = 0.0005
    slippage_rate: float = 0.0002
    include_funding: bool = True

    min_weight_change: float = 0.005        # ต่ำกว่านี้ = HOLD (สเปก §11)
    min_notional_usdt: float = 10.0         # ไม้เล็กกว่านี้ไม่เทรด (สเปก §11)
    max_single_symbol_weight: float = 0.60  # circuit breaker (สเปก §15)
    max_strategy_drawdown: float = 0.25     # DD ถึงนี่ = RISK_HALT
    live_trading_enabled: bool = False      # ห้ามแตะ — paper เท่านั้น

    starting_equity: float = field(default_factory=lambda: _f("RTE_STARTING_EQUITY", 10_000.0))

    @property
    def min_score_bars(self) -> int:
        """แท่งขั้นต่ำต่อเหรียญที่คิด score ได้ (= max horizon + 1 เพื่อให้ momentum126
        มี cl[-1-126]) เหรียญที่ต่ำกว่านี้ = ยังไม่คิด (ข้าม) ไม่ใช่พังทั้งพอร์ต"""
        return max(max(self.ema_horizons), self.btc_ema_bars,
                   self.volatility_lookback_bars, self.crash_lookback_bars) + 1

    @property
    def warmup_bars(self) -> int:
        """แท่งที่ควรโหลดตอน warm-up (สเปก §5.4: >= 250) — เผื่อ buffer จาก min_score_bars"""
        return self.min_score_bars + 5

    def canonical(self) -> dict:
        """dict ที่ไม่รวม starting_equity (ทุน/สภาพแวดล้อม ไม่ใช่ "กฎ")"""
        d = asdict(self)
        d.pop("starting_equity", None)
        return d

    def config_hash(self) -> str:
        blob = json.dumps(self.canonical(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _f(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def coins(cfg: RTEConfig) -> list[str]:
    """symbol → coin (ตัด USDT) สำหรับ loader ที่รับชื่อเหรียญ"""
    return [s[:-4] if s.endswith("USDT") else s for s in cfg.symbols]
