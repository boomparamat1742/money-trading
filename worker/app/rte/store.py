"""Persistence สำหรับ RTE — Supabase (Postgres) ถ้ามี DATABASE_URL ไม่งั้น SQLite.

Railway ดิสก์ ephemeral (redeploy = หาย) → portfolio state ต้องอยู่ที่ Supabase ไม่งั้น
forward-test รีเซ็ตทุกครั้งที่ deploy · 2 ตาราง:
  rte_state       — portfolio ปัจจุบัน (1 แถวต่อ strategy_version) upsert ทุก rebalance
  rte_rebalances  — audit log ทุกครั้งที่ตัดสินใจ (idempotent ตาม bar_time)
"""
from __future__ import annotations

import json
import os
import time
from typing import Optional

from .config import RTEConfig
from .portfolio import PaperPortfolio


def open_rte_store(cfg: RTEConfig):
    from worker.app.store import database_url
    dsn = database_url()
    if dsn:
        try:
            s = _PgStore(dsn, cfg)
            print("[rte] store: Postgres/Supabase")
            return s
        except Exception as e:
            print(f"[rte] ต่อ Postgres ไม่ได้ ({type(e).__name__}: {e}) → SQLite แทน")
    path = os.environ.get("RTE_DB", "data/rte.db")
    print(f"[rte] store: SQLite ({path})")
    return _SqliteStore(path, cfg)


class _PgStore:
    def __init__(self, dsn: str, cfg: RTEConfig):
        import psycopg
        self.cfg = cfg
        self.conn = psycopg.connect(dsn, autocommit=True)
        with self.conn.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS rte_state (
                strategy_version TEXT PRIMARY KEY, config_hash TEXT NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL, state JSONB NOT NULL, weights JSONB NOT NULL)""")
            cur.execute("""CREATE TABLE IF NOT EXISTS rte_rebalances (
                id BIGSERIAL PRIMARY KEY, strategy_version TEXT NOT NULL, config_hash TEXT NOT NULL,
                bar_time BIGINT NOT NULL, btc_trend_ok BOOLEAN, crash_ok BOOLEAN,
                breadth DOUBLE PRECISION, gross_exposure DOUBLE PRECISION, to_cash BOOLEAN,
                equity DOUBLE PRECISION, drawdown DOUBLE PRECISION, halted BOOLEAN,
                selected JSONB, target_weights JSONB, reason TEXT, created_at TIMESTAMPTZ NOT NULL,
                UNIQUE(strategy_version, bar_time))""")
            # A3: ประวัติ P&L รายเหรียญต่อ rebalance (attribution ระยะยาว)
            cur.execute("""CREATE TABLE IF NOT EXISTS rte_positions_log (
                id BIGSERIAL PRIMARY KEY, strategy_version TEXT NOT NULL, config_hash TEXT NOT NULL,
                bar_time BIGINT NOT NULL, symbol TEXT NOT NULL, qty DOUBLE PRECISION,
                avg_entry DOUBLE PRECISION, mark_price DOUBLE PRECISION, notional DOUBLE PRECISION,
                unrealized_pnl DOUBLE PRECISION, weight DOUBLE PRECISION, created_at TIMESTAMPTZ NOT NULL,
                UNIQUE(strategy_version, bar_time, symbol))""")

    def load_state(self, config_hash: str) -> Optional[dict]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT config_hash, state, weights FROM rte_state WHERE strategy_version=%s",
                        (self.cfg.strategy_version,))
            row = cur.fetchone()
        if not row:
            return None
        return {"config_hash": row[0], "state": row[1], "weights": row[2]}

    def save_state(self, config_hash, portfolio: PaperPortfolio, weights: dict) -> None:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        with self.conn.cursor() as cur:
            cur.execute("""INSERT INTO rte_state (strategy_version, config_hash, updated_at, state, weights)
                VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT (strategy_version) DO UPDATE SET
                config_hash=EXCLUDED.config_hash, updated_at=EXCLUDED.updated_at,
                state=EXCLUDED.state, weights=EXCLUDED.weights""",
                (self.cfg.strategy_version, config_hash, now,
                 json.dumps(portfolio.to_dict()), json.dumps(weights)))

    def record_rebalance(self, config_hash, dec, equity, drawdown, halted) -> None:
        from datetime import datetime, timezone
        with self.conn.cursor() as cur:
            cur.execute("""INSERT INTO rte_rebalances (strategy_version, config_hash, bar_time,
                btc_trend_ok, crash_ok, breadth, gross_exposure, to_cash, equity, drawdown, halted,
                selected, target_weights, reason, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (strategy_version, bar_time) DO NOTHING""",
                (self.cfg.strategy_version, config_hash, dec.bar_close_time,
                 dec.btc_trend_ok, dec.crash_filter_ok, dec.breadth, dec.gross_exposure,
                 dec.to_cash, equity, drawdown, halted,
                 json.dumps(dec.selected), json.dumps(dec.target_weights), dec.reason,
                 datetime.now(timezone.utc)))

    def record_positions(self, config_hash, bar_time, rows) -> None:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        with self.conn.cursor() as cur:
            for r in rows:
                cur.execute("""INSERT INTO rte_positions_log (strategy_version, config_hash,
                    bar_time, symbol, qty, avg_entry, mark_price, notional, unrealized_pnl,
                    weight, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (strategy_version, bar_time, symbol) DO NOTHING""",
                    (self.cfg.strategy_version, config_hash, bar_time, r["symbol"], r["qty"],
                     r["avg_entry"], r["mark_price"], r["notional"], r["unrealized_pnl"],
                     r["weight"], now))

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass


class _SqliteStore:
    def __init__(self, path: str, cfg: RTEConfig):
        import sqlite3
        self.cfg = cfg
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("""CREATE TABLE IF NOT EXISTS rte_state (
            strategy_version TEXT PRIMARY KEY, config_hash TEXT, updated_at INTEGER,
            state TEXT, weights TEXT)""")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS rte_rebalances (
            id INTEGER PRIMARY KEY AUTOINCREMENT, strategy_version TEXT, config_hash TEXT,
            bar_time INTEGER, btc_trend_ok INTEGER, crash_ok INTEGER, breadth REAL,
            gross_exposure REAL, to_cash INTEGER, equity REAL, drawdown REAL, halted INTEGER,
            selected TEXT, target_weights TEXT, reason TEXT, created_at INTEGER,
            UNIQUE(strategy_version, bar_time))""")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS rte_positions_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, strategy_version TEXT, config_hash TEXT,
            bar_time INTEGER, symbol TEXT, qty REAL, avg_entry REAL, mark_price REAL,
            notional REAL, unrealized_pnl REAL, weight REAL, created_at INTEGER,
            UNIQUE(strategy_version, bar_time, symbol))""")
        self.conn.commit()

    def load_state(self, config_hash: str) -> Optional[dict]:
        r = self.conn.execute("SELECT config_hash, state, weights FROM rte_state WHERE strategy_version=?",
                              (self.cfg.strategy_version,)).fetchone()
        if not r:
            return None
        return {"config_hash": r["config_hash"], "state": json.loads(r["state"]),
                "weights": json.loads(r["weights"])}

    def save_state(self, config_hash, portfolio: PaperPortfolio, weights: dict) -> None:
        self.conn.execute("""INSERT INTO rte_state (strategy_version, config_hash, updated_at, state, weights)
            VALUES (?,?,?,?,?) ON CONFLICT(strategy_version) DO UPDATE SET
            config_hash=excluded.config_hash, updated_at=excluded.updated_at,
            state=excluded.state, weights=excluded.weights""",
            (self.cfg.strategy_version, config_hash, int(time.time()),
             json.dumps(portfolio.to_dict()), json.dumps(weights)))
        self.conn.commit()

    def record_rebalance(self, config_hash, dec, equity, drawdown, halted) -> None:
        self.conn.execute("""INSERT OR IGNORE INTO rte_rebalances (strategy_version, config_hash,
            bar_time, btc_trend_ok, crash_ok, breadth, gross_exposure, to_cash, equity, drawdown,
            halted, selected, target_weights, reason, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (self.cfg.strategy_version, config_hash, dec.bar_close_time,
             int(dec.btc_trend_ok), int(dec.crash_filter_ok), dec.breadth, dec.gross_exposure,
             int(dec.to_cash), equity, drawdown, int(halted),
             json.dumps(dec.selected), json.dumps(dec.target_weights), dec.reason, int(time.time())))
        self.conn.commit()

    def record_positions(self, config_hash, bar_time, rows) -> None:
        for r in rows:
            self.conn.execute("""INSERT OR IGNORE INTO rte_positions_log (strategy_version,
                config_hash, bar_time, symbol, qty, avg_entry, mark_price, notional,
                unrealized_pnl, weight, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (self.cfg.strategy_version, config_hash, bar_time, r["symbol"], r["qty"],
                 r["avg_entry"], r["mark_price"], r["notional"], r["unrealized_pnl"],
                 r["weight"], int(time.time())))
        self.conn.commit()

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass
