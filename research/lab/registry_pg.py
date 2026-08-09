"""Edge Lab registry — Postgres/Supabase backend (interface เดียวกับ registry.Registry)."""
from __future__ import annotations

import json
from typing import Any


class PostgresRegistry:
    def __init__(self, dsn: str):
        import psycopg

        from worker.app.journal_pg import _redact

        self.path = _redact(dsn)
        self.conn = psycopg.connect(dsn, autocommit=True)

    def trials(self) -> int:
        with self.conn.cursor() as cur:
            cur.execute("SELECT COUNT(DISTINCT hypothesis) FROM edge_runs")
            return cur.fetchone()[0]

    def hours_since_last_run(self):
        """ชั่วโมงตั้งแต่รันล่าสุด (None = ยังไม่เคยรัน) — ให้ scheduler ในโปรเซส
        ทนต่อ restart โดยดูเวลาจริงจาก DB"""
        with self.conn.cursor() as cur:
            cur.execute("SELECT EXTRACT(EPOCH FROM (now() - MAX(created_at))) FROM edge_runs")
            v = cur.fetchone()[0]
        return float(v) / 3600 if v is not None else None

    def record(self, ev) -> int:
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO edge_runs (hypothesis, question, neutral, oos_sharpe,
                   oos_cagr, oos_maxdd, oos_n, bench_sharpe, required_sharpe, folds,
                   folds_positive, trials_before, passed, reason, params)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (ev.hypothesis, ev.question, ev.neutral, ev.oos.sharpe, ev.oos.cagr,
                 ev.oos.max_drawdown, ev.oos.n, ev.benchmark.sharpe, ev.required_sharpe,
                 len(ev.folds), ev.folds_positive, ev.trials_before, ev.passed,
                 ev.reason, json.dumps(ev.param_counts, ensure_ascii=False)))
            return cur.fetchone()[0]

    def _rows(self, sql: str, args: tuple) -> list[dict]:
        cols = ["id", "hypothesis", "oos_sharpe", "required_sharpe", "folds",
                "folds_positive", "passed", "created_at"]
        with self.conn.cursor() as cur:
            cur.execute(sql, args)
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def history(self, limit: int = 50) -> list[dict]:
        return self._rows(
            "SELECT id, hypothesis, oos_sharpe, required_sharpe, folds, folds_positive,"
            " passed, created_at FROM edge_runs ORDER BY id DESC LIMIT %s", (limit,))

    def history_for(self, hypothesis: str, limit: int = 20) -> list[dict]:
        return self._rows(
            "SELECT id, hypothesis, oos_sharpe, required_sharpe, folds, folds_positive,"
            " passed, created_at FROM edge_runs WHERE hypothesis=%s"
            " ORDER BY id DESC LIMIT %s", (hypothesis, limit))

    def stability(self, hypothesis: str, window: int = 5) -> dict[str, Any]:
        rows = self.history_for(hypothesis, window)
        with self.conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM edge_runs WHERE hypothesis=%s", (hypothesis,))
            total = cur.fetchone()[0]
        if not rows:
            return {"runs_recent": 0, "passed_recent": 0, "sharpes": [],
                    "trend": 0.0, "total_runs": total}
        sharpes = [r["oos_sharpe"] for r in rows]
        return {
            "runs_recent": len(rows),
            "passed_recent": sum(1 for r in rows if r["passed"]),
            "sharpes": sharpes,
            "trend": round(sharpes[0] - sharpes[-1], 3) if len(sharpes) > 1 else 0.0,
            "total_runs": total,
        }

    def summary(self) -> dict[str, Any]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT COUNT(*), COUNT(*) FILTER (WHERE passed) FROM edge_runs")
            runs, passed = cur.fetchone()
        return {"runs": runs, "hypotheses": self.trials(), "passed": passed}

    def close(self) -> None:
        self.conn.close()
