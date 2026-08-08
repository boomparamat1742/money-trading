"""เช็กโควตา LINE ที่เหลือของเดือนนี้ — ถามจาก LINE โดยตรง

    python -m scripts.line_quota

ทำไมต้องเช็ก: LINE นับ **ต่อผู้รับ** ไม่ใช่ต่อข้อความ — push เข้ากลุ่ม 5 คน
= 5 ข้อความ ตัวเลขที่ควรใช้ตั้ง NOTIFY_MAX_PER_DAY จึงไม่ใช่ตัวเลขที่เดาเอง
"""
from __future__ import annotations

import asyncio
import calendar
import sys
from datetime import datetime, timezone

from worker.app.config import load_settings
from worker.app.notifier import LineNotifier


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    s = load_settings()
    if not (s.line_channel_token and s.line_to):
        print("ยังไม่ได้ตั้ง LINE_CHANNEL_TOKEN / LINE_TO (ดู .env.example)")
        return

    q = asyncio.run(LineNotifier(s.line_channel_token, s.line_to).quota())
    if q is None:
        print("แผนนี้ไม่จำกัดจำนวนข้อความ — ไม่ต้องกังวลเรื่องโควตา")
        return

    now = datetime.now(timezone.utc)
    days_left = calendar.monthrange(now.year, now.month)[1] - now.day + 1
    per_day = q["remaining"] / days_left if days_left else 0

    print(f"\n📊 โควตา LINE เดือนนี้")
    print(f"  ทั้งหมด : {q['limit']}")
    print(f"  ใช้ไป   : {q['used']}")
    print(f"  เหลือ   : {q['remaining']}   (อีก {days_left} วันจะขึ้นเดือนใหม่)")
    print(f"\n  ส่งได้เฉลี่ยวันละ ~{per_day:.0f} ข้อความ ถ้าจะให้พอถึงสิ้นเดือน")
    print(f"  ⚠️ ถ้าส่งเข้ากลุ่ม ให้หารด้วยจำนวนคนในกลุ่มอีกที")
    print(f"     (กลุ่ม 3 คน → NOTIFY_MAX_PER_DAY ประมาณ {per_day/3:.0f})")
    print(f"\n  1 ไม้ = 2 ข้อความ (เปิด + ปิด) · redeploy 1 ครั้ง = 1 ข้อความ\n")


if __name__ == "__main__":
    main()
