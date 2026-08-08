-- Database schema (design §7). PostgreSQL / Supabase / Neon.
-- Apply with: psql "$DATABASE_URL" -f migrations/schema.sql

CREATE TABLE IF NOT EXISTS signals (
    id BIGSERIAL PRIMARY KEY,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    candle_open_time TIMESTAMPTZ NOT NULL,
    strategy_name TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    direction TEXT NOT NULL,
    signal_score NUMERIC NOT NULL,
    score_breakdown JSONB NOT NULL,
    market_regime JSONB,
    entry_price NUMERIC,
    stop_loss NUMERIC,
    take_profit NUMERIC,
    expected_rr NUMERIC,
    risk_status TEXT NOT NULL,
    rejection_reason TEXT,
    indicators JSONB NOT NULL,
    trigger_reasons JSONB NOT NULL,
    ai_context JSONB,
    ai_status TEXT DEFAULT 'pending',
    prompt_version TEXT,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (exchange, symbol, timeframe, strategy_name, strategy_version, candle_open_time)
);

CREATE TABLE IF NOT EXISTS paper_trades (
    id BIGSERIAL PRIMARY KEY,
    signal_id BIGINT NOT NULL REFERENCES signals(id),
    status TEXT NOT NULL,
    side TEXT NOT NULL,
    requested_entry NUMERIC,
    filled_entry NUMERIC,
    stop_loss NUMERIC,
    take_profit NUMERIC,
    position_size NUMERIC,
    risk_amount NUMERIC,
    risk_pct NUMERIC,
    entry_fee NUMERIC DEFAULT 0,
    exit_fee NUMERIC DEFAULT 0,
    slippage NUMERIC DEFAULT 0,
    exit_price NUMERIC,
    pnl_amount NUMERIC,
    pnl_pct NUMERIC,
    actual_rr NUMERIC,
    max_favorable_excursion NUMERIC,
    max_adverse_excursion NUMERIC,
    opened_at TIMESTAMPTZ,
    closed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS llm_calls (
    id BIGSERIAL PRIMARY KEY,
    signal_id BIGINT REFERENCES signals(id),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    input_tokens INT,
    output_tokens INT,
    latency_ms INT,
    cost_usd NUMERIC,
    status TEXT NOT NULL,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS strategy_metrics (
    id BIGSERIAL PRIMARY KEY,
    strategy_name TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    period_start TIMESTAMPTZ NOT NULL,
    period_end TIMESTAMPTZ NOT NULL,
    trade_count INT NOT NULL,
    net_profit NUMERIC,
    profit_factor NUMERIC,
    win_rate NUMERIC,
    max_drawdown NUMERIC,
    expectancy NUMERIC,
    avg_win NUMERIC,
    avg_loss NUMERIC,
    created_at TIMESTAMPTZ DEFAULT now()
);
