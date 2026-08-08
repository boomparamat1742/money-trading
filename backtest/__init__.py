"""Backtest harness — runs the exact same SignalPipeline + PaperBroker as
real-time over historical/synthetic candles, then reports edge metrics
(design §8). This is the go/no-go gate: no positive out-of-sample expectancy,
no edge."""
