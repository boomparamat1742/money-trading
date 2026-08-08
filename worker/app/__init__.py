"""Quant-first + AI-assistant trading system — worker package.

Architecture (see docs/system-design-quant-first-ai-assistant.md):
    Market Data → Data Quality → Candle Builder → Indicators → Regime
    → Strategy Engine → Signal Scoring → Risk Manager → Paper Trading
    → (slow path) AI Context → Notification → Dashboard

The quant core (indicators, regime, strategies, scoring, risk, paper_trading,
pipeline) is pure-stdlib and shared by BOTH real-time and backtest, so a
signal is reproducible and auditable (design N8, F17).
"""
