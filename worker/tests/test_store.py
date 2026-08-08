"""Store factory: เลือก backend จาก env และต้อง fallback ได้เมื่อ Postgres ล่ม.

การ fallback สำคัญมาก — ระบบเฝ้าตลาดต้องทำงานต่อได้แม้ฐานข้อมูลนอกมีปัญหา
ไม่ใช่ล้มทั้งระบบเพราะ Supabase ตอบช้า
"""
import os
import tempfile

import pytest

from worker.app import store


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("JOURNAL_DB", raising=False)


def test_backend_is_sqlite_without_database_url():
    assert store.backend_name() == "sqlite"


def test_backend_is_postgres_when_database_url_set(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host:5432/db")
    assert store.backend_name() == "supabase/postgres"


def test_blank_database_url_is_treated_as_unset(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "   ")
    assert store.backend_name() == "sqlite"


def test_open_journal_returns_sqlite_by_default(monkeypatch):
    path = os.path.join(tempfile.mkdtemp(), "j.db")
    monkeypatch.setenv("JOURNAL_DB", path)
    j = store.open_journal()
    assert j.path == path
    assert j.stats()["signals"] == 0
    j.close()


def test_open_journal_falls_back_when_postgres_unreachable(monkeypatch):
    """DSN ที่ต่อไม่ได้ต้องไม่ทำให้ระบบล้ม — ต้องกลับไปใช้ SQLite"""
    path = os.path.join(tempfile.mkdtemp(), "fallback.db")
    monkeypatch.setenv("JOURNAL_DB", path)
    monkeypatch.setenv("DATABASE_URL",
                       "postgresql://nobody:nopass@127.0.0.1:1/doesnotexist")
    j = store.open_journal()
    assert j.path == path                 # SQLite path → fallback ทำงาน
    j.close()


def test_dsn_password_is_redacted_in_display():
    from worker.app.journal_pg import _redact
    shown = _redact("postgresql://postgres:SuperSecret123@db.supabase.co:5432/postgres")
    assert "SuperSecret123" not in shown
    assert "postgres:***@" in shown
