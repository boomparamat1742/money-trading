"""Registry — บันทึกทุกสมมติฐานที่เคยทดสอบ รวมถึงอันที่ไม่ผ่าน.

ทำไมต้องเก็บอันที่ไม่ผ่านด้วย: จำนวนครั้งที่ลองคือตัวกำหนดว่า "ผลที่ดูดี" น่าเชื่อ
แค่ไหน ถ้าจำได้แค่อันที่ผ่าน เราจะประเมินความบังเอิญต่ำเกินจริงเสมอ
(survivorship bias ในกระบวนการวิจัยของตัวเอง)
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hypothesis TEXT NOT NULL,
    question TEXT,
    neutral INTEGER,
    oos_sharpe REAL, oos_cagr REAL, oos_maxdd REAL, oos_n INTEGER,
    bench_sharpe REAL,
    required_sharpe REAL,
    folds INTEGER, folds_positive INTEGER,
    trials_before INTEGER,
    passed INTEGER,
    reason TEXT,
    params TEXT,
    created_at TEXT NOT NULL
);
"""


class Registry:
    def __init__(self, path: str = "data/edge_lab.db"):
        self.path = path
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def trials(self) -> int:
        """จำนวนสมมติฐาน (ไม่ซ้ำชื่อ) ที่เคยทดสอบ — ใช้ปรับเกณฑ์"""
        return self.conn.execute(
            "SELECT COUNT(DISTINCT hypothesis) c FROM runs").fetchone()["c"]

    def record(self, ev) -> int:
        cur = self.conn.execute(
            """INSERT INTO runs (hypothesis, question, neutral, oos_sharpe, oos_cagr,
               oos_maxdd, oos_n, bench_sharpe, required_sharpe, folds, folds_positive,
               trials_before, passed, reason, params, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ev.hypothesis, ev.question, int(ev.neutral), ev.oos.sharpe, ev.oos.cagr,
             ev.oos.max_drawdown, ev.oos.n, ev.benchmark.sharpe, ev.required_sharpe,
             len(ev.folds), ev.folds_positive, ev.trials_before, int(ev.passed),
             ev.reason, json.dumps(ev.param_counts, ensure_ascii=False),
             datetime.now(timezone.utc).isoformat(timespec="seconds")))
        self.conn.commit()
        return cur.lastrowid

    def history(self, limit: int = 50) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)))

    def history_for(self, hypothesis: str, limit: int = 20) -> list[sqlite3.Row]:
        """ผลย้อนหลังของสมมติฐานเดียว (ใหม่→เก่า)"""
        return list(self.conn.execute(
            "SELECT * FROM runs WHERE hypothesis=? ORDER BY id DESC LIMIT ?",
            (hypothesis, limit)))

    def stability(self, hypothesis: str, window: int = 5) -> dict[str, Any]:
        """เสถียรภาพของผลเมื่อรันซ้ำหลายครั้ง.

        สำคัญ: การรันสมมติฐานเดิมซ้ำๆ คือ multiple testing ในมิติเวลา — ถ้ารันทุกวัน
        สักวันก็ต้องมีวันที่ 'ผ่าน' ด้วยความบังเอิญ ตัวเลข passed_recent/runs_recent
        มีไว้ให้เห็นว่า 'ผ่านครั้งเดียวจาก 20 ครั้ง' ต่างจาก 'ผ่าน 18 จาก 20 ครั้ง'
        """
        rows = self.history_for(hypothesis, window)
        if not rows:
            return {"runs_recent": 0, "passed_recent": 0, "sharpes": [], "trend": 0.0}
        sharpes = [r["oos_sharpe"] for r in rows]
        return {
            "runs_recent": len(rows),
            "passed_recent": sum(1 for r in rows if r["passed"]),
            "sharpes": sharpes,
            "trend": round(sharpes[0] - sharpes[-1], 3) if len(sharpes) > 1 else 0.0,
            "total_runs": self.conn.execute(
                "SELECT COUNT(*) c FROM runs WHERE hypothesis=?",
                (hypothesis,)).fetchone()["c"],
        }

    def summary(self) -> dict[str, Any]:
        rows = list(self.conn.execute("SELECT * FROM runs"))
        return {
            "runs": len(rows),
            "hypotheses": self.trials(),
            "passed": sum(1 for r in rows if r["passed"]),
        }

    def close(self) -> None:
        self.conn.close()
