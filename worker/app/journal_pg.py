"""Trade Journal — Postgres/Supabase backend.

Interface เหมือน journal.Journal ทุกประการ ผู้เรียกจึงไม่ต้องรู้ว่าเบื้องหลังเป็น
SQLite หรือ Supabase (ดู store.open_journal)

ความต่างจาก SQLite ที่ต้องระวัง:
  • placeholder เป็น %s ไม่ใช่ ?
  • idempotent insert ใช้ ON CONFLICT DO NOTHING (ไม่ใช่ INSERT OR IGNORE)
  • JSON เก็บเป็น JSONB — ส่งเป็น string ที่ dump แล้ว
  • เขียนแต่ละครั้งคือ network round-trip (ต่างจากไฟล์ local) — ปริมาณของเรา
    น้อยมาก (ไม่กี่ครั้งต่อ 15 นาที) จึงยอมรับได้ แต่อย่าเรียกถี่กว่านี้มาก
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from .models import Direction, PaperTrade, Signal, TradeStatus


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PostgresJournal:
    def __init__(self, dsn: str):
        import psycopg  # lazy: ไม่บังคับติดตั้งถ้าใช้ SQLite

        self.dsn = dsn
        self.path = _redact(dsn)          # ชื่อเดียวกับ SQLite journal (.path) ใช้โชว์ได้
        self.conn = psycopg.connect(dsn, autocommit=True)
        self._migrate()

    def _migrate(self) -> None:
        """คอลัมน์/ตารางที่เพิ่มทีหลัง — ฐานข้อมูลที่ deploy ไปแล้วยังไม่มี
        (migrations/supabase.sql มีให้ครบสำหรับตารางที่สร้างใหม่)"""
        with self.conn.cursor() as cur:
            for col, decl in (("initial_stop", "DOUBLE PRECISION"),
                              ("entry_context", "JSONB"),
                              ("exit_reason", "TEXT"),
                              ("exit_context", "JSONB")):
                cur.execute(f"ALTER TABLE trades ADD COLUMN IF NOT EXISTS {col} {decl}")
            cur.execute(
                """CREATE TABLE IF NOT EXISTS market_snapshots (
                    id BIGSERIAL PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    ts BIGINT NOT NULL,
                    price DOUBLE PRECISION, mark_price DOUBLE PRECISION,
                    open_interest DOUBLE PRECISION, open_interest_value DOUBLE PRECISION,
                    funding_rate DOUBLE PRECISION,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (symbol, ts)
                )""")

    # ---------- signals ----------
    def record_signal(self, sig: Signal) -> Optional[int]:
        key = (sig.exchange, sig.symbol, sig.timeframe, sig.strategy_name,
               sig.strategy_version, sig.candle_open_time)
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO signals
                   (exchange, symbol, timeframe, candle_open_time, strategy_name,
                    strategy_version, direction, signal_score, score_breakdown,
                    market_regime, entry_price, stop_loss, take_profit, expected_rr,
                    position_size, risk_amount, risk_pct, risk_status,
                    rejection_reason, indicators, trigger_reasons, status, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (exchange, symbol, timeframe, strategy_name,
                                strategy_version, candle_open_time) DO NOTHING
                   RETURNING id""",
                (sig.exchange, sig.symbol, sig.timeframe, sig.candle_open_time,
                 sig.strategy_name, sig.strategy_version,
                 sig.direction.value if isinstance(sig.direction, Direction) else sig.direction,
                 sig.signal_score, json.dumps(sig.score_breakdown),
                 json.dumps(sig.market_regime), sig.entry_price, sig.stop_loss,
                 sig.take_profit, sig.expected_rr, sig.position_size, sig.risk_amount,
                 sig.risk_pct, sig.risk_status, sig.rejection_reason,
                 json.dumps(sig.indicators), json.dumps(sig.trigger_reasons),
                 sig.status, _now()))
            row = cur.fetchone()
            if row:
                return row[0]
            # มีอยู่แล้ว → คืน id เดิม (idempotent)
            cur.execute(
                """SELECT id FROM signals WHERE exchange=%s AND symbol=%s AND timeframe=%s
                   AND strategy_name=%s AND strategy_version=%s AND candle_open_time=%s""",
                key)
            row = cur.fetchone()
            return row[0] if row else None

    def attach_ai_context(self, signal_id: int, ctx: dict[str, Any]) -> None:
        with self.conn.cursor() as cur:
            cur.execute("UPDATE signals SET ai_context=%s WHERE id=%s",
                        (json.dumps(ctx, ensure_ascii=False), signal_id))

    # ---------- market snapshots ----------
    def record_snapshot(self, symbol: str, ts: int, price: Optional[float],
                        snap: dict) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO market_snapshots
                   (symbol, ts, price, mark_price, open_interest,
                    open_interest_value, funding_rate, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (symbol, ts) DO NOTHING""",
                (symbol, ts, price, snap.get("mark_price"), snap.get("open_interest"),
                 snap.get("open_interest_value"), snap.get("funding_rate"), _now()))

    # ---------- trades ----------
    def open_trade(self, t: PaperTrade, signal_id: Optional[int] = None) -> int:
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO trades
                   (signal_id, symbol, side, status, requested_entry, filled_entry,
                    stop_loss, take_profit, position_size, risk_amount, risk_pct,
                    entry_fee, exit_fee, slippage, mfe, mae, bars_held, init_risk,
                    initial_stop, extreme, entry_context, opened_at, created_at, updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   RETURNING id""",
                (signal_id, t.symbol, t.side.value, t.status.value, t.requested_entry,
                 t.filled_entry, t.stop_loss, t.take_profit, t.position_size,
                 t.risk_amount, t.risk_pct, t.entry_fee, t.exit_fee, t.slippage,
                 t.max_favorable_excursion, t.max_adverse_excursion, t.bars_held,
                 t.init_risk, t.initial_stop, t.extreme,
                 json.dumps(t.entry_context, ensure_ascii=False) if t.entry_context else None,
                 t.opened_at, _now(), _now()))
            t.db_id = cur.fetchone()[0]
            return t.db_id

    def update_trade(self, t: PaperTrade) -> None:
        if t.db_id is None:
            return
        with self.conn.cursor() as cur:
            cur.execute(
                """UPDATE trades SET status=%s, stop_loss=%s, exit_price=%s, exit_fee=%s,
                   pnl_amount=%s, pnl_pct=%s, actual_rr=%s, mfe=%s, mae=%s, bars_held=%s,
                   extreme=%s, exit_reason=%s, exit_context=%s, closed_at=%s,
                   updated_at=%s WHERE id=%s""",
                (t.status.value, t.stop_loss, t.exit_price, t.exit_fee, t.pnl_amount,
                 t.pnl_pct, t.actual_rr, t.max_favorable_excursion,
                 t.max_adverse_excursion, t.bars_held, t.extreme, t.exit_reason,
                 json.dumps(t.exit_context, ensure_ascii=False) if t.exit_context else None,
                 t.closed_at, _now(), t.db_id))

    close_trade = update_trade

    def load_open_trades(self, symbol: Optional[str] = None) -> list[PaperTrade]:
        sql = ("SELECT id, signal_id, symbol, side, status, requested_entry, filled_entry,"
               " stop_loss, take_profit, position_size, risk_amount, risk_pct, entry_fee,"
               " exit_fee, slippage, mfe, mae, bars_held, init_risk, extreme, opened_at,"
               " initial_stop, entry_context"
               " FROM trades WHERE status='open'")
        args: tuple = ()
        if symbol:
            sql += " AND symbol=%s"
            args = (symbol,)
        out: list[PaperTrade] = []
        with self.conn.cursor() as cur:
            cur.execute(sql + " ORDER BY id", args)
            for r in cur.fetchall():
                t = PaperTrade(
                    signal_id=r[1], symbol=r[2], side=Direction(r[3]),
                    status=TradeStatus(r[4]), requested_entry=r[5], filled_entry=r[6],
                    stop_loss=r[7], take_profit=r[8], position_size=r[9],
                    risk_amount=r[10], risk_pct=r[11], entry_fee=r[12] or 0.0,
                    exit_fee=r[13] or 0.0, slippage=r[14] or 0.0,
                    max_favorable_excursion=r[15] or 0.0,
                    max_adverse_excursion=r[16] or 0.0, opened_at=r[20],
                )
                t.bars_held = r[17] or 0
                t.init_risk = r[18] or 0.0
                t.extreme = r[19] or 0.0
                # แถวเก่ากว่า migration ไม่มีค่านี้ — เดาว่า stop ยังไม่เคยเลื่อน
                t.initial_stop = r[21] if r[21] is not None else r[7]
                t.entry_context = r[22] or {}      # JSONB → dict อยู่แล้ว
                t.db_id = r[0]
                out.append(t)
        return out

    # ---------- reporting ----------
    def stats(self) -> dict[str, Any]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT pnl_amount FROM trades "
                        "WHERE status IN ('hit_tp','hit_sl','expired')")
            pnls = [r[0] or 0.0 for r in cur.fetchall()]
            cur.execute("SELECT COUNT(*) FROM signals")
            n_signals = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM trades WHERE status='open'")
            n_open = cur.fetchone()[0]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gross_win, gross_loss = sum(wins), abs(sum(losses))
        return {
            "signals": n_signals,
            "trades_closed": len(pnls),
            "trades_open": n_open,
            "wins": len(wins), "losses": len(losses),
            "win_rate": round(len(wins) / len(pnls) * 100, 2) if pnls else 0.0,
            "net_pnl": round(sum(pnls), 4),
            "profit_factor": round(gross_win / gross_loss, 3) if gross_loss else (float("inf") if gross_win else 0.0),
            "expectancy": round(sum(pnls) / len(pnls), 4) if pnls else 0.0,
            "avg_win": round(sum(wins) / len(wins), 4) if wins else 0.0,
            "avg_loss": round(sum(losses) / len(losses), 4) if losses else 0.0,
        }

    def exit_reasons(self) -> list[dict[str, Any]]:
        """จัดกลุ่มไม้ที่ปิดแล้วตาม (สาเหตุ, รูปแบบ) — ให้ Postgres รวมให้เลย
        เพราะ exit_context เป็น JSONB ดึงคีย์ย่อยได้ตรงๆ"""
        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT exit_reason,
                          exit_context->>'pattern'                AS pattern,
                          COUNT(*)                                AS n,
                          ROUND(SUM(pnl_amount)::numeric, 4)      AS net_pnl,
                          ROUND(AVG(actual_rr)::numeric, 3)       AS avg_rr,
                          ROUND(AVG(bars_held)::numeric, 1)       AS avg_bars,
                          ROUND(AVG((exit_context->>'mfe_r')::numeric), 3) AS avg_mfe_r,
                          COUNT(*) FILTER (WHERE exit_context->>'fast_stop' = 'true') AS fast_stops
                   FROM trades
                   WHERE status IN ('hit_tp','hit_sl','expired')
                   GROUP BY 1, 2 ORDER BY n DESC""")
            cols = ["exit_reason", "pattern", "n", "net_pnl", "avg_rr",
                    "avg_bars", "avg_mfe_r", "fast_stops"]
            return [{c: (float(v) if isinstance(v, Decimal) else v)
                     for c, v in zip(cols, r)} for r in cur.fetchall()]

    def sl_by_trigger(self) -> list[dict[str, Any]]:
        """เงื่อนไขที่จุดชนวนแต่ละตัว จบด้วย SL กี่ % — ให้ Postgres กาง array ให้
        (ไม้หนึ่งมีหลายเงื่อนไข จึงนับซ้ำได้ ดูคำอธิบายใน journal.sl_by_trigger)"""
        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT trigger, COUNT(*) AS n,
                          COUNT(*) FILTER (WHERE exit_reason LIKE 'sl%') AS sl,
                          ROUND(SUM(pnl_amount)::numeric, 4) AS net_pnl
                   FROM trades,
                        LATERAL jsonb_array_elements_text(
                            COALESCE(entry_context->'reasons', '["(ไม่มีข้อมูล)"]'::jsonb)
                        ) AS trigger
                   WHERE status IN ('hit_tp','hit_sl','expired')
                   GROUP BY trigger""")
            out = []
            for trigger, n, sl, pnl in cur.fetchall():
                out.append({"trigger": trigger, "n": n, "sl": sl,
                            "net_pnl": float(pnl) if pnl is not None else 0.0,
                            "sl_rate": round(sl / n * 100, 1) if n else 0.0})
        return sorted(out, key=lambda d: (-d["sl_rate"], -d["n"]))

    def recent_trades(self, limit: int = 20) -> list[dict]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT opened_at, symbol, side, status, pnl_amount, actual_rr "
                        "FROM trades ORDER BY id DESC LIMIT %s", (limit,))
            cols = ["opened_at", "symbol", "side", "status", "pnl_amount", "actual_rr"]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def close(self) -> None:
        self.conn.close()


def _redact(dsn: str) -> str:
    """ซ่อนรหัสผ่านเวลาพิมพ์ connection string ออก log"""
    import re
    return re.sub(r"://([^:]+):[^@]+@", r"://\1:***@", dsn)
