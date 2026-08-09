"""in-process Edge Lab scheduler — ต้องทน restart โดยดูเวลารันล่าสุดจาก DB

Railway trial จำกัด 1 service ต่อ project → ตั้ง cron service แยกไม่ได้ จึงรัน
Edge Lab ในตัว worker เดิม ตัวตัดสิน "ถึงเวลาหรือยัง" ต้องมาจาก DB ไม่ใช่ตัวนับ
ในหน่วยความจำ (ที่รีเซ็ตทุก restart → restart ถี่แล้วจะไม่รันเลย)
"""
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone

from research.lab.registry import Registry


def _reg():
    return Registry(os.path.join(tempfile.mkdtemp(), "lab.db"))


def _insert_run(reg, when: datetime):
    reg.conn.execute(
        "INSERT INTO runs (hypothesis, passed, created_at) VALUES (?,?,?)",
        ("tsmom_btc", 0, when.isoformat(timespec="seconds")))
    reg.conn.commit()


def test_none_when_never_run():
    reg = _reg()
    assert reg.hours_since_last_run() is None
    reg.close()


def test_reports_recent_run_as_near_zero_hours():
    reg = _reg()
    _insert_run(reg, datetime.now(timezone.utc))
    h = reg.hours_since_last_run()
    assert h is not None and 0 <= h < 1
    reg.close()


def test_reports_old_run_in_hours():
    reg = _reg()
    _insert_run(reg, datetime.now(timezone.utc) - timedelta(hours=200))
    h = reg.hours_since_last_run()
    assert 199 < h < 201
    reg.close()


def test_uses_the_most_recent_run():
    reg = _reg()
    _insert_run(reg, datetime.now(timezone.utc) - timedelta(hours=300))
    _insert_run(reg, datetime.now(timezone.utc) - timedelta(hours=10))
    assert reg.hours_since_last_run() < 11        # ดูตัวล่าสุด ไม่ใช่ตัวเก่า
    reg.close()


def test_handles_naive_timestamp_without_crashing():
    """created_at เก่าที่ไม่มี timezone ต้องไม่ทำให้พัง"""
    reg = _reg()
    reg.conn.execute(
        "INSERT INTO runs (hypothesis, passed, created_at) VALUES (?,?,?)",
        ("x", 0, "2020-01-01T00:00:00"))       # naive
    reg.conn.commit()
    assert reg.hours_since_last_run() > 1000
    reg.close()


def test_due_logic_matches_interval():
    """เลียนแบบการตัดสินใน _edge_lab_tick: ครบรอบเมื่อ None หรือ ≥ interval"""
    reg = _reg()
    interval = 168.0
    assert (reg.hours_since_last_run() is None)                 # ยังไม่เคย → ครบรอบ
    _insert_run(reg, datetime.now(timezone.utc) - timedelta(hours=10))
    h = reg.hours_since_last_run()
    assert not (h is None or h >= interval)                    # ล่าสุด 10 ชม. → ยังไม่ครบ
    reg.close()

    # registry ที่รันล่าสุดเมื่อ 200 ชม.ก่อน → ครบรอบ
    reg2 = _reg()
    _insert_run(reg2, datetime.now(timezone.utc) - timedelta(hours=200))
    h = reg2.hours_since_last_run()
    assert h is None or h >= interval
    reg2.close()
