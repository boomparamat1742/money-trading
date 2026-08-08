"""Configuration loaded from environment variables (design N9).

Pure stdlib: reads os.environ with typed defaults. A .env file is loaded if
python-dotenv is installed, but is not required for backtests/tests.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

try:  # optional: load .env in dev, no hard dependency
    from dotenv import load_dotenv  # type: ignore

    load_dotenv()
except Exception:  # pragma: no cover
    pass


def _f(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def _i(key: str, default: int) -> int:
    try:
        return int(float(os.environ.get(key, default)))
    except (TypeError, ValueError):
        return default


def _b(key: str, default: bool) -> bool:
    return os.environ.get(key, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _s(key: str, default: str) -> str:
    return os.environ.get(key, default)


@dataclass(frozen=True)
class RiskPolicy:
    account_equity: float = _f("ACCOUNT_EQUITY", 10_000)
    risk_per_trade_pct: float = _f("RISK_PER_TRADE_PCT", 0.5)
    min_reward_risk: float = _f("MIN_REWARD_RISK", 1.5)
    max_open_trades: int = _i("MAX_OPEN_TRADES", 3)
    daily_loss_limit_pct: float = _f("DAILY_LOSS_LIMIT_PCT", 3.0)
    max_consecutive_losses: int = _i("MAX_CONSECUTIVE_LOSSES", 4)
    signal_score_threshold: float = _f("SIGNAL_SCORE_THRESHOLD", 65)
    kill_switch: bool = _b("KILL_SWITCH", False)


@dataclass(frozen=True)
class Fees:
    taker_fee_pct: float = _f("TAKER_FEE_PCT", 0.05)
    slippage_pct: float = _f("SLIPPAGE_PCT", 0.02)


@dataclass(frozen=True)
class Settings:
    exchange: str = _s("EXCHANGE", "binance")
    symbols: list[str] = field(default_factory=lambda: [s.strip() for s in _s("SYMBOLS", "BTCUSDT").split(",") if s.strip()])
    primary_timeframe: str = _s("PRIMARY_TIMEFRAME", "15m")
    # ทุกกรอบเวลาในนี้ต้องเห็นตรงกันสัญญาณถึงผ่าน (คั่นด้วย comma)
    #
    # ตั้งใจไม่รับชื่อเดิม CONFIRM_TIMEFRAME (เอกพจน์): มันถูกประกาศไว้แต่ไม่เคย
    # ถูกใช้ ค่าที่ตั้งไว้คือ "1h" — ถ้ารับมาตอนนี้เท่ากับตัด 4h ออกจากฟิลเตอร์
    # โดยไม่มีใครสั่ง ซึ่งเป็นการผ่อนกลยุทธ์แบบเงียบๆ
    confirm_timeframes: str = _s("CONFIRM_TIMEFRAMES", "1h,4h")

    ai_enabled: bool = _b("AI_ENABLED", True)
    ai_model: str = _s("AI_MODEL", "claude-sonnet-5")
    anthropic_api_key: str = _s("ANTHROPIC_API_KEY", "")

    news_provider: str = _s("NEWS_PROVIDER", "cryptopanic")
    news_api_key: str = _s("NEWS_API_KEY", "")

    # LINE Messaging API (push) — Notify is discontinued; use Messaging API
    line_channel_token: str = _s("LINE_CHANNEL_TOKEN", "")
    line_to: str = _s("LINE_TO", "")  # destination userId or groupId

    ai_daily_call_limit: int = _i("AI_DAILY_CALL_LIMIT", 50)
    ai_daily_cost_limit_usd: float = _f("AI_DAILY_COST_LIMIT_USD", 2)
    news_daily_call_limit: int = _i("NEWS_DAILY_CALL_LIMIT", 500)
    max_symbols: int = _i("MAX_SYMBOLS", 3)

    risk: RiskPolicy = field(default_factory=RiskPolicy)
    fees: Fees = field(default_factory=Fees)


def load_settings() -> Settings:
    return Settings()
