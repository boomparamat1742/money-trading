"""ย้ายข้อมูลเดิมจาก SQLite → Supabase/Postgres.

ใช้เมื่อรันด้วย SQLite มาก่อนแล้วเพิ่งต่อ Supabase — ข้อมูลเก่าจะได้ไม่หาย

    python -m scripts.migrate_to_supabase            # ดูว่าจะย้ายอะไรบ้าง (dry run)
    python -m scripts.migrate_to_supabase --apply    # ย้ายจริง

ปลอดภัยที่จะรันซ้ำ: signals ใช้ ON CONFLICT DO NOTHING ส่วน trades/edge_runs
จะข้ามถ้าปลายทางมีข้อมูลอยู่แล้ว (กันย้ำซ้ำโดยไม่ตั้งใจ)
"""
from __future__ import annotations

import os
import sqlite3
import sys


def _connect_pg():
    from worker.app.store import database_url
    dsn = database_url()
    if not dsn:
        print("ยังไม่ได้ตั้ง DATABASE_URL — ดู docs/supabase-setup.md")
        return None
    import psycopg
    return psycopg.connect(dsn, autocommit=True)


def _sqlite(path: str):
    if not os.path.exists(path):
        return None
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    return c


def _count(pg, table: str) -> int:
    with pg.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        return cur.fetchone()[0]


def _unshift_legacy(r: dict) -> dict:
    """ซ่อมแถวที่เขียนผิดคอลัมน์จากบั๊กเดิม.

    บั๊ก: ลำดับใน UNIQUE key (…, strategy_name, strategy_version, candle_open_time)
    ต่างจากลำดับคอลัมน์ใน INSERT (…, candle_open_time, strategy_name, strategy_version)
    ทำให้ 3 ค่าถูกหมุนไปหนึ่งตำแหน่ง SQLite ไม่เช็ค type จึงรับไว้เงียบๆ

    ตรวจจับได้ชัด: candle_open_time ที่ถูกต้องต้องเป็นตัวเลข ถ้าเป็นข้อความ = แถวเสีย
    """
    d = dict(r)
    cot = d.get("candle_open_time")
    if isinstance(cot, str) and not str(cot).isdigit():
        d["candle_open_time"], d["strategy_name"], d["strategy_version"] = (
            int(d["strategy_version"]), d["candle_open_time"], d["strategy_name"])
        d["_repaired"] = True
    return d


def migrate_journal(pg, path: str, apply: bool) -> None:
    src = _sqlite(path)
    if src is None:
        print(f"  ข้าม journal — ไม่มีไฟล์ {path}")
        return
    sigs = [_unshift_legacy(r) for r in src.execute("SELECT * FROM signals ORDER BY id")]
    trades = src.execute("SELECT * FROM trades ORDER BY id").fetchall()
    repaired = sum(1 for r in sigs if r.get("_repaired"))
    print(f"  journal ({path}): signals {len(sigs)} · trades {len(trades)}")
    if repaired:
        print(f"     ซ่อมแถวที่เขียนผิดคอลัมน์จากบั๊กเดิม: {repaired} แถว")
    if not apply:
        return
    if _count(pg, "trades") or _count(pg, "signals"):
        print("  ⚠️ ปลายทางมีข้อมูลอยู่แล้ว — ข้าม (กันย้ำซ้ำ)")
        return

    id_map: dict[int, int] = {}
    with pg.cursor() as cur:
        for r in sigs:
            cur.execute(
                """INSERT INTO signals (exchange, symbol, timeframe, candle_open_time,
                   strategy_name, strategy_version, direction, signal_score,
                   score_breakdown, market_regime, entry_price, stop_loss, take_profit,
                   expected_rr, position_size, risk_amount, risk_pct, risk_status,
                   rejection_reason, indicators, trigger_reasons, status, ai_context)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT DO NOTHING RETURNING id""",
                (r["exchange"], r["symbol"], r["timeframe"], r["candle_open_time"],
                 r["strategy_name"], r["strategy_version"], r["direction"],
                 r["signal_score"], r["score_breakdown"], r["market_regime"],
                 r["entry_price"], r["stop_loss"], r["take_profit"], r["expected_rr"],
                 r["position_size"], r["risk_amount"], r["risk_pct"], r["risk_status"],
                 r["rejection_reason"], r["indicators"], r["trigger_reasons"],
                 r["status"], r["ai_context"]))
            got = cur.fetchone()
            if got:
                id_map[r["id"]] = got[0]

        for r in trades:
            cur.execute(
                """INSERT INTO trades (signal_id, symbol, side, status, requested_entry,
                   filled_entry, stop_loss, take_profit, position_size, risk_amount,
                   risk_pct, entry_fee, exit_fee, slippage, exit_price, pnl_amount,
                   pnl_pct, actual_rr, mfe, mae, bars_held, init_risk, extreme,
                   opened_at, closed_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (id_map.get(r["signal_id"]), r["symbol"], r["side"], r["status"],
                 r["requested_entry"], r["filled_entry"], r["stop_loss"], r["take_profit"],
                 r["position_size"], r["risk_amount"], r["risk_pct"], r["entry_fee"],
                 r["exit_fee"], r["slippage"], r["exit_price"], r["pnl_amount"],
                 r["pnl_pct"], r["actual_rr"], r["mfe"], r["mae"], r["bars_held"],
                 r["init_risk"], r["extreme"], r["opened_at"], r["closed_at"]))
    print(f"  ✅ ย้าย signals {len(id_map)} · trades {len(trades)}")


def migrate_edge_lab(pg, path: str, apply: bool) -> None:
    src = _sqlite(path)
    if src is None:
        print(f"  ข้าม edge lab — ไม่มีไฟล์ {path}")
        return
    runs = src.execute("SELECT * FROM runs ORDER BY id").fetchall()
    print(f"  edge lab ({path}): runs {len(runs)}")
    if not apply:
        return
    if _count(pg, "edge_runs"):
        print("  ⚠️ ปลายทางมีข้อมูลอยู่แล้ว — ข้าม (กันย้ำซ้ำ)")
        return
    with pg.cursor() as cur:
        for r in runs:
            cur.execute(
                """INSERT INTO edge_runs (hypothesis, question, neutral, oos_sharpe,
                   oos_cagr, oos_maxdd, oos_n, bench_sharpe, required_sharpe, folds,
                   folds_positive, trials_before, passed, reason, params)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (r["hypothesis"], r["question"], bool(r["neutral"]), r["oos_sharpe"],
                 r["oos_cagr"], r["oos_maxdd"], r["oos_n"], r["bench_sharpe"],
                 r["required_sharpe"], r["folds"], r["folds_positive"],
                 r["trials_before"], bool(r["passed"]), r["reason"], r["params"]))
    print(f"  ✅ ย้าย edge runs {len(runs)}")


def main(argv: list[str]) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    apply = "--apply" in argv
    pg = _connect_pg()
    if pg is None:
        return
    print(f"\n{'🚚 ย้ายข้อมูลจริง' if apply else '👀 ดูก่อน (dry run)'} → Supabase\n")
    migrate_journal(pg, os.environ.get("JOURNAL_DB", "data/journal.db"), apply)
    migrate_edge_lab(pg, os.environ.get("EDGE_LAB_DB", "data/edge_lab.db"), apply)
    pg.close()
    if not apply:
        print("\nถ้าถูกต้องแล้ว รันจริงด้วย:  python -m scripts.migrate_to_supabase --apply")


if __name__ == "__main__":
    main(sys.argv)
