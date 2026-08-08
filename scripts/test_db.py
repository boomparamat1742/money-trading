"""ทดสอบการเชื่อมต่อฐานข้อมูล — บอกชัดว่าติดตรงไหน.

    python -m scripts.test_db

ไม่พิมพ์รหัสผ่านออกมาไม่ว่ากรณีใด
"""
from __future__ import annotations

import os
import sys


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    from worker.app.store import backend_name, database_url

    dsn = database_url() or ""
    print(f"\n🔌 ทดสอบการเชื่อมต่อฐานข้อมูล\n")
    print(f"  backend ที่จะใช้: {backend_name()}")

    if not dsn:
        print("\n  ℹ️ ไม่ได้ตั้ง DATABASE_URL → ระบบใช้ SQLite (ปกติดี ไม่ต้องแก้อะไร)")
        print("     ถ้าต้องการใช้ Supabase ดู docs/supabase-setup.md")
        return

    # แยกส่วนของ DSN มาแสดงโดยไม่โชว์รหัสผ่าน
    from worker.app.journal_pg import _redact
    print(f"  DSN: {_redact(dsn)}")

    try:
        import psycopg
    except ImportError:
        print('\n  ❌ ยังไม่ได้ติดตั้ง driver — รัน:  pip install "psycopg[binary]"')
        return

    try:
        conn = psycopg.connect(dsn, connect_timeout=15)
    except Exception as e:
        msg = str(e)
        print(f"\n  ❌ ต่อไม่สำเร็จ")
        if "password authentication failed" in msg:
            print("     สาเหตุ: รหัสผ่านไม่ถูกต้อง")
            print("     แก้: Supabase → ปุ่ม Connect (หรือ Settings) → reset database password")
            print("          แล้วอัปเดต DATABASE_URL ใน .env")
            print("     ⚠️ ถ้ารหัสมีอักขระพิเศษ (@ : / ? # เว้นวรรค) ต้อง URL-encode")
            print("        เช่น @ → %40,  # → %23,  / → %2F")
        elif "could not translate host name" in msg or "Name or service not known" in msg:
            print("     สาเหตุ: host ไม่ถูก — copy connection string ใหม่จากปุ่ม Connect")
        elif "timeout" in msg.lower() or "Network is unreachable" in msg:
            print("     สาเหตุ: ต่อไม่ถึง — ถ้าใช้ Direct connection ต้องมี IPv6")
            print("     แก้: เปลี่ยนไปใช้ Session pooler (port 5432) แทน")
        elif "does not exist" in msg:
            print("     สาเหตุ: ชื่อฐานข้อมูล/ผู้ใช้ไม่ถูก — ตรวจ connection string อีกครั้ง")
        else:
            print(f"     {msg.splitlines()[0][:200]}")
        print("\n  ℹ️ ระบบยังทำงานได้ปกติด้วย SQLite (fallback อัตโนมัติ)")
        return

    print("\n  ✅ ต่อสำเร็จ")
    with conn.cursor() as cur:
        cur.execute("SELECT current_database(), current_user, version()")
        db, user, ver = cur.fetchone()
        print(f"     database: {db} · user: {user}")
        print(f"     {ver.split(',')[0]}")

        cur.execute("""SELECT table_name FROM information_schema.tables
                       WHERE table_schema='public' ORDER BY table_name""")
        tables = [r[0] for r in cur.fetchall()]
        needed = {"signals", "trades", "edge_runs"}
        print(f"\n     ตารางที่มี: {', '.join(tables) if tables else '(ยังไม่มี)'}")
        missing = needed - set(tables)
        if missing:
            print(f"     ❌ ยังขาด: {', '.join(sorted(missing))}")
            print("     แก้: Supabase → SQL Editor → วาง migrations/supabase.sql → Run")
        else:
            print("     ✅ ตารางครบ")
            for t in ("signals", "trades", "edge_runs"):
                cur.execute(f"SELECT COUNT(*) FROM {t}")
                print(f"        {t}: {cur.fetchone()[0]} แถว")
    conn.close()
    print("\n  พร้อมใช้งาน — ย้ายข้อมูลเก่าด้วย: python -m scripts.migrate_to_supabase")


if __name__ == "__main__":
    main()
