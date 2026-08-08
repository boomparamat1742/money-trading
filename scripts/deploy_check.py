"""ตรวจว่าโค้ดที่ Railway รันอยู่ ใหม่หรือเก่ากว่าเครื่องเรา

    python -m scripts.deploy_check

ตรวจจาก **ร่องรอยที่โค้ดทิ้งไว้ในฐานข้อมูล** ไม่ใช่จากคำบอกเล่าของ Railway:
โค้ดแต่ละรุ่นจะ ALTER TABLE เพิ่มคอลัมน์ของตัวเองตอนเชื่อมต่อ ถ้าคอลัมน์ยังไม่โผล่
แปลว่ารุ่นนั้นไม่เคยรันจริง — เป็นหลักฐานที่ปลอมไม่ได้

หมายเหตุ: ตัว worker เองพิมพ์ commit ที่กำลังรันออก log และใส่ในข้อความ
"เริ่มทำงาน" ที่ส่งเข้า LINE อยู่แล้ว (ดู worker/app/version.py)
"""
from __future__ import annotations

import subprocess
import sys

from worker.app.store import backend_name, database_url
from worker.app.version import build_line

# คอลัมน์ที่โค้ดแต่ละรุ่นเพิ่มเข้ามา — เรียงตามลำดับเวลา
MARKERS = [
    ("initial_stop",  "eb3dbe0", "บันทึกสาเหตุการปิด (exit_reason/exit_context)"),
    ("exit_reason",   "eb3dbe0", "บันทึกสาเหตุการปิด"),
    ("exit_context",  "eb3dbe0", "บันทึกหลักฐานตอนปิด"),
    ("entry_context", "9d86b8c", "บันทึกสูตร/เงื่อนไขที่ทำให้เข้าไม้"),
]


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    print(f"\n📦 เครื่องนี้: {build_line()}")
    try:
        ahead = subprocess.run(["git", "log", "--oneline", "origin/main", "-1"],
                               capture_output=True, text=True, timeout=5).stdout.strip()
        print(f"   origin/main: {ahead}")
    except Exception:
        pass

    dsn = database_url()
    if not dsn:
        print(f"\n⚠️ ไม่ได้ตั้ง DATABASE_URL — ตรวจไม่ได้ (backend: {backend_name()})")
        return

    import psycopg
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute("""SELECT column_name FROM information_schema.columns
                       WHERE table_name='trades'""")
        cols = {r[0] for r in cur.fetchall()}
        cur.execute("SELECT MAX(updated_at), COUNT(*) FROM trades")
        last_trade, n_trades = cur.fetchone()
        cur.execute("SELECT MAX(created_at), COUNT(*) FROM signals")
        last_sig, n_sig = cur.fetchone()

    print("\n🔍 ร่องรอยของโค้ดในฐานข้อมูล:")
    missing = []
    for col, commit, what in MARKERS:
        ok = col in cols
        print(f"   {'✅' if ok else '❌'} trades.{col:<14} {what}")
        if not ok:
            missing.append(commit)

    print(f"\n📊 กิจกรรมล่าสุด:")
    print(f"   signals : {n_sig} แถว  ล่าสุด {last_sig}")
    print(f"   trades  : {n_trades} แถว  ล่าสุด {last_trade}")

    if missing:
        print(f"\n🔴 โค้ดที่รันอยู่ยังเก่า — ยังไม่ถึง commit {sorted(set(missing))[0]}")
        print("   ไปที่ Railway → Deployments → ดูว่า build ล้มหรือ Auto Deploy ปิดอยู่")
        print("   แล้วกด Redeploy")
    else:
        print("\n🟢 คอลัมน์ครบตามโค้ดล่าสุดที่รู้จัก — โค้ดใหม่ได้รันแล้ว")
        print("   (ยืนยัน commit ที่แน่นอนได้จากข้อความ 'เริ่มทำงาน' ใน LINE หรือ Deploy Logs)")
    print()


if __name__ == "__main__":
    main()
