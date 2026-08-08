"""โค้ดที่กำลังรันคือ commit ไหน — ตอบจากในตัวระบบเอง

ไม่งั้นเวลา deploy แล้วสงสัยว่า "ขึ้นหรือยัง" ต้องไปไล่เดาจากรูปแบบข้อความ
หรือจากคอลัมน์ในฐานข้อมูล ซึ่งบอกได้แค่ "เก่ากว่า/ใหม่กว่า" ไม่ได้บอกว่าตัวไหน

Railway ใส่ RAILWAY_GIT_* ให้ทุก deployment ที่มาจาก GitHub อยู่แล้ว
ถ้ารันในเครื่องตัวเองก็ถาม git โดยตรง
"""
from __future__ import annotations

import os
import subprocess
from functools import lru_cache


@lru_cache(maxsize=1)
def build_info() -> dict:
    sha = os.environ.get("RAILWAY_GIT_COMMIT_SHA", "")
    branch = os.environ.get("RAILWAY_GIT_BRANCH", "")
    msg = os.environ.get("RAILWAY_GIT_COMMIT_MESSAGE", "")
    where = "railway" if sha else "local"

    if not sha:  # รันในเครื่อง — ถาม git เอา (ใน image ไม่มี .git จึงคืนค่าว่าง)
        try:
            sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                                 text=True, timeout=5).stdout.strip()
            branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                                    capture_output=True, text=True, timeout=5).stdout.strip()
            msg = subprocess.run(["git", "log", "-1", "--format=%s"], capture_output=True,
                                 text=True, timeout=5).stdout.strip()
        except Exception:
            pass

    return {"sha": sha, "short": sha[:7] if sha else "unknown",
            "branch": branch, "message": msg, "where": where,
            "deployment_id": os.environ.get("RAILWAY_DEPLOYMENT_ID", "")}


def build_line() -> str:
    b = build_info()
    if b["short"] == "unknown":
        return "build: ไม่ทราบ commit (ไม่มี .git และไม่มี RAILWAY_GIT_COMMIT_SHA)"
    out = f"build: {b['short']}"
    if b["branch"]:
        out += f" ({b['branch']})"
    if b["message"]:
        out += f" — {b['message'][:60]}"
    return out
