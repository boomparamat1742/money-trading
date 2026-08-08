"""Send one test message via LINE Messaging API to verify your credentials.

Setup (once):
  1. LINE Developers Console → create a "Messaging API" channel
  2. Copy the Channel access token           → LINE_CHANNEL_TOKEN
  3. Add the bot as a friend / to a group, then get your destination id:
       - your userId (from a webhook event), or a groupId the bot joined
                                             → LINE_TO
  4. Put both in .env (see .env.example)

Run:
  python -m scripts.line_test          # reads LINE_CHANNEL_TOKEN / LINE_TO from env/.env
"""
import asyncio
import sys

from worker.app.config import load_settings
from worker.app.notifier import LineNotifier


async def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    s = load_settings()
    if not s.line_channel_token or not s.line_to:
        print("ยังไม่ได้ตั้งค่า LINE_CHANNEL_TOKEN และ LINE_TO (ดู .env.example)")
        return
    ok = await LineNotifier(s.line_channel_token, s.line_to).send(
        "✅ ทดสอบ LINE Messaging API สำเร็จ — ระบบเฝ้าตลาดพร้อมส่งแจ้งเตือน"
    )
    print("ส่งสำเร็จ ✅" if ok else "ส่งไม่สำเร็จ ❌ (ตรวจ token / destination id)")


if __name__ == "__main__":
    asyncio.run(main())
