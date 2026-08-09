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
    initial_stop   DOUBLE PRECISION,        -- stop ตอนเข้า (trailing เขียนทับ stop_loss)
    extreme        DOUBLE PRECISION,
    entry_context  JSONB,                   -- กลยุทธ์ คะแนน และเงื่อนไขที่จุดชนวนการเข้า
    exit_reason    TEXT,                    -- tp | sl_initial | sl_trailing | expired
    exit_context   JSONB,                   -- pattern, mfe_r, mae_r, สภาพตลาดตอนปิด
    opened_at      BIGINT,
    closed_at      BIGINT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ตารางที่สร้างไว้ก่อนหน้ายังไม่มี 3 คอลัมน์นี้ (โค้ดก็ ALTER เองตอนเชื่อมต่อ)
ALTER TABLE trades ADD COLUMN IF NOT EXISTS initial_stop  DOUBLE PRECISION;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS entry_context JSONB;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS exit_reason  TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS exit_context JSONB;

CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_exit_reason ON trades(exit_reason);

-- ══════════════════════════════════════════════════════════════
-- Market snapshots — เก็บ OI/funding สดทุกแท่ง (Binance ให้ประวัติ OI ฟรีแค่ 30 วัน
-- จึงต้องสะสมเอง) ไว้ทำวิจัย edge จาก derivatives positioning ในอนาคต
-- ══════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS market_snapshots (
    id                   BIGSERIAL PRIMARY KEY,
    symbol               TEXT NOT NULL,
    ts                   BIGINT NOT NULL,          -- candle open_time (ms)
    price                DOUBLE PRECISION,
    mark_price           DOUBLE PRECISION,
    open_interest        DOUBLE PRECISION,         -- contracts (base asset)
    open_interest_value  DOUBLE PRECISION,         -- notional USD = OI × mark
    funding_rate         DOUBLE PRECISION,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (symbol, ts)                            -- idempotent ต่อแท่ง
);
CREATE INDEX IF NOT EXISTS idx_snapshots_symbol_ts ON market_snapshots(symbol, ts DESC);
ALTER TABLE market_snapshots ENABLE ROW LEVEL SECURITY;

-- ══════════════════════════════════════════════════════════════
-- OI history — Open Interest ประวัติรายวัน (นำเข้าจาก Coinalyze CSV)
-- ให้ Edge Lab บน Railway ใช้ได้ (data/ ที่นั่นล้างทุกรอบ)
-- นำเข้าด้วย: python -m scripts.import_oi_to_supabase
-- ══════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS oi_history (
    symbol     TEXT NOT NULL,
    interval   TEXT NOT NULL DEFAULT '1d',
    ts         BIGINT NOT NULL,              -- open_time (ms)
    oi_open    DOUBLE PRECISION,
    oi_high    DOUBLE PRECISION,
    oi_low     DOUBLE PRECISION,
    oi_close   DOUBLE PRECISION,             -- USD notional (Binance-only)
    PRIMARY KEY (symbol, interval, ts)
);
ALTER TABLE oi_history ENABLE ROW LEVEL SECURITY;
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
