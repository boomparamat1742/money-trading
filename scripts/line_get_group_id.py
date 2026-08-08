"""ตัวดัก groupId / userId ของ LINE (รันชั่วคราวเพื่อเอา id ครั้งเดียว).

LINE ไม่มีหน้าจอโชว์ groupId — ต้องดักจาก webhook event ตอนบอทอยู่ในกลุ่ม
สคริปต์นี้เปิดเว็บเซิร์ฟเวอร์เล็กๆ รับ webhook แล้ว print id ของทุก event ที่เข้ามา

วิธีใช้:
  1. รัน:  python -m scripts.line_get_group_id            (ฟังพอร์ต 8000)
  2. เปิดพอร์ตนี้ให้ LINE เข้าถึงได้ผ่าน public URL (เลือกวิธีใดวิธีหนึ่ง):
       - cloudflared:  cloudflared tunnel --url http://localhost:8000
       - ngrok:        ngrok http 8000
     จะได้ URL แบบ https://xxxx.trycloudflare.com
  3. LINE Developers Console → ช่อง Messaging API →
       Webhook URL = <public URL>/webhook   แล้วกด Verify + เปิด "Use webhook"
  4. เชิญบอทเข้ากลุ่ม แล้วพิมพ์อะไรก็ได้ในกลุ่ม 1 ข้อความ
  5. ดู console — จะเห็น  source.type=group  groupId=Cxxxxxxxx...
  6. เอา groupId ไปใส่ LINE_TO ใน .env  แล้วปิดสคริปต์นี้ได้เลย

หมายเหตุ: นี่เป็นเครื่องมือชั่วคราวสำหรับดึง id — ไม่ได้ verify ลายเซ็น (X-Line-Signature)
ระบบจริง (worker.app.main) ไม่ได้เปิดพอร์ตรับ webhook ใดๆ ใช้แค่ push ออกอย่างเดียว
"""
from __future__ import annotations

import json
import sys

from aiohttp import web

try:  # Windows consoles default to cp1252; our output has Thai + emoji
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass


async def webhook(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return web.Response(text="OK")
    for ev in body.get("events", []):
        src = ev.get("source", {})
        stype = src.get("type")
        gid = src.get("groupId")
        rid = src.get("roomId")
        uid = src.get("userId")
        print("== LINE event ==", flush=True)
        print(f"  source.type = {stype}", flush=True)
        if gid:
            print(f"  >>> groupId = {gid}   (use this as LINE_TO)", flush=True)
        if rid:
            print(f"  >>> roomId  = {rid}   (multi-person chat; use as LINE_TO)", flush=True)
        if uid:
            print(f"  userId   = {uid}", flush=True)
        print("  raw:", json.dumps(ev, ensure_ascii=False)[:300], flush=True)
    return web.Response(text="OK")  # LINE ต้องได้ 200 กลับ


def main() -> None:
    app = web.Application()
    app.router.add_post("/webhook", webhook)
    app.router.add_get("/", lambda r: web.Response(text="line group-id catcher is running"))
    print("ฟังอยู่ที่ http://localhost:8000  (POST /webhook)")
    print("เปิด public URL ด้วย cloudflared/ngrok แล้วตั้งเป็น Webhook URL ใน LINE console")
    web.run_app(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
