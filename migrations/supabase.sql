-- Schema สำหรับ Supabase / PostgreSQL
-- ใช้กับทั้ง trade journal และ edge lab registry
--
-- วิธีติดตั้ง:
--   1. เปิด Supabase project → SQL Editor
--   2. วางไฟล์นี้ทั้งหมด → Run
--   3. ใส่ connection string ใน .env เป็น DATABASE_URL
--
-- ปลอดภัยที่จะรันซ้ำ (ใช้ IF NOT EXISTS ทุกที่)

-- ══════════════════════════════════════════════════════════════
-- Trade journal — สัญญาณและไม้จากระบบ live
-- ══════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS signals (
    id                BIGSERIAL PRIMARY KEY,
    exchange          TEXT NOT NULL,
    symbol            TEXT NOT NULL,
    timeframe         TEXT NOT NULL,
    candle_open_time  BIGINT NOT NULL,          -- ms epoch
    strategy_name     TEXT NOT NULL,
    strategy_version  TEXT NOT NULL,
    direction         TEXT NOT NULL,
    signal_score      DOUBLE PRECISION NOT NULL,
    score_breakdown   JSONB,
    market_regime     JSONB,
    entry_price       DOUBLE PRECISION,
    stop_loss         DOUBLE PRECISION,
    take_profit       DOUBLE PRECISION,
    expected_rr       DOUBLE PRECISION,
    position_size     DOUBLE PRECISION,
    risk_amount       DOUBLE PRECISION,
    risk_pct          DOUBLE PRECISION,
    risk_status       TEXT,
    rejection_reason  TEXT,
    indicators        JSONB,
    trigger_reasons   JSONB,
    status            TEXT NOT NULL,
    ai_context        JSONB,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- กันบันทึกซ้ำเมื่อประมวลผลแท่งเดิมอีกครั้ง (idempotency)
    UNIQUE (exchange, symbol, timeframe, strategy_name, strategy_version, candle_open_time)
);

CREATE TABLE IF NOT EXISTS trades (
    id             BIGSERIAL PRIMARY KEY,
    signal_id      BIGINT REFERENCES signals(id),
    symbol         TEXT NOT NULL,
    side           TEXT NOT NULL,
    status         TEXT NOT NULL,
    requested_entry DOUBLE PRECISION,
    filled_entry   DOUBLE PRECISION,
    stop_loss      DOUBLE PRECISION,
    take_profit    DOUBLE PRECISION,
    position_size  DOUBLE PRECISION,
    risk_amount    DOUBLE PRECISION,
    risk_pct       DOUBLE PRECISION,
    entry_fee      DOUBLE PRECISION,
    exit_fee       DOUBLE PRECISION,
    slippage       DOUBLE PRECISION,
    exit_price     DOUBLE PRECISION,
    pnl_amount     DOUBLE PRECISION,
    pnl_pct        DOUBLE PRECISION,
    actual_rr      DOUBLE PRECISION,
    mfe            DOUBLE PRECISION,
    mae            DOUBLE PRECISION,
    bars_held      INTEGER,
    init_risk      DOUBLE PRECISION,
    extreme        DOUBLE PRECISION,
    opened_at      BIGINT,
    closed_at      BIGINT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_signals_symbol_time ON signals(symbol, candle_open_time DESC);

-- ══════════════════════════════════════════════════════════════
-- Edge Lab registry — ผลทดสอบสมมติฐาน (เก็บทุกครั้ง รวมที่ไม่ผ่าน)
-- ══════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS edge_runs (
    id              BIGSERIAL PRIMARY KEY,
    hypothesis      TEXT NOT NULL,
    question        TEXT,
    neutral         BOOLEAN,
    oos_sharpe      DOUBLE PRECISION,
    oos_cagr        DOUBLE PRECISION,
    oos_maxdd       DOUBLE PRECISION,
    oos_n           INTEGER,
    bench_sharpe    DOUBLE PRECISION,
    required_sharpe DOUBLE PRECISION,
    folds           INTEGER,
    folds_positive  INTEGER,
    trials_before   INTEGER,
    passed          BOOLEAN,
    reason          TEXT,
    params          JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_edge_runs_hypothesis ON edge_runs(hypothesis, id DESC);

-- ══════════════════════════════════════════════════════════════
-- ความปลอดภัย: เปิด Row Level Security ไว้ (แนะนำ)
-- ระบบเชื่อมด้วย service/postgres role ซึ่งข้าม RLS อยู่แล้ว
-- แต่ถ้าเปิด anon key ให้ใครก็ตาม RLS จะกันไม่ให้อ่าน/เขียน
-- ══════════════════════════════════════════════════════════════
ALTER TABLE signals   ENABLE ROW LEVEL SECURITY;
ALTER TABLE trades    ENABLE ROW LEVEL SECURITY;
ALTER TABLE edge_runs ENABLE ROW LEVEL SECURITY;
