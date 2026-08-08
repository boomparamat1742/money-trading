"""เลือก backend ของฐานข้อมูลจาก environment.

    DATABASE_URL ตั้งไว้  → Supabase / PostgreSQL
    ไม่ได้ตั้ง            → SQLite (ไฟล์ในเครื่อง, ไม่ต้องติดตั้งอะไร)

โค้ดที่เรียกใช้ไม่ต้องรู้ว่าเบื้องหลังเป็นอะไร — interface เหมือนกันทั้งคู่
ถ้าต่อ Postgres ไม่สำเร็จ จะ fallback ไป SQLite พร้อมแจ้งเตือน แทนที่จะล้มทั้งระบบ
(ระบบเฝ้าตลาดต้องทำงานต่อได้แม้ฐานข้อมูลนอกมีปัญหา)
"""
from __future__ import annotations

import os
from typing import Optional

try:  # โหลด .env เอง — สคริปต์บางตัว (journal_report, edge lab) ไม่ได้ import config
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass


def database_url() -> Optional[str]:
    """DATABASE_URL จาก .env/environment (None ถ้าไม่ได้ตั้ง). ใช้ตัวนี้เสมอ —
    การอ่าน os.environ ตรงๆ จะพลาดเมื่อ .env ยังไม่ถูกโหลด"""
    dsn = (os.environ.get("DATABASE_URL") or "").strip()
    return dsn or None


_dsn = database_url  # ชื่อเดิม (ใช้ภายใน)


def backend_name() -> str:
    return "supabase/postgres" if _dsn() else "sqlite"


def open_journal(sqlite_path: Optional[str] = None):
    """คืน Journal (SQLite) หรือ PostgresJournal ตาม DATABASE_URL"""
    dsn = _dsn()
    if dsn:
        try:
            from .journal_pg import PostgresJournal
            j = PostgresJournal(dsn)
            print(f"[store] journal: Postgres/Supabase ({j.path})")
            return j
        except Exception as e:
            print(f"[store] ต่อ Postgres ไม่สำเร็จ ({type(e).__name__}: {e}) → ใช้ SQLite แทน")
    from .journal import Journal
    path = sqlite_path or os.environ.get("JOURNAL_DB", "data/journal.db")
    j = Journal(path)
    print(f"[store] journal: SQLite ({j.path})")
    return j


def open_registry(sqlite_path: Optional[str] = None):
    """คืน Registry ของ Edge Lab — Postgres ถ้ามี DATABASE_URL ไม่งั้น SQLite"""
    dsn = _dsn()
    if dsn:
        try:
            from research.lab.registry_pg import PostgresRegistry
            return PostgresRegistry(dsn)
        except Exception as e:
            print(f"[store] ต่อ Postgres ไม่สำเร็จ ({type(e).__name__}: {e}) → ใช้ SQLite แทน")
    from research.lab.registry import Registry
    return Registry(sqlite_path or os.environ.get("EDGE_LAB_DB", "data/edge_lab.db"))
