# Image สำหรับ Railway / Render / Fly.io — long-running worker
#
# ครอบคลุมทั้ง 2 งานจาก image เดียว (เปลี่ยนที่ start command บน Railway):
#   python -m worker.app.main       ← เฝ้าตลาด + แจ้งเตือน (ค่าเริ่มต้น)
#   python -m research.lab.watch    ← Edge Lab (ตั้งเป็น cron service)
FROM python:3.12-slim

# ไม่เขียน .pyc, ไม่ buffer stdout (log ขึ้น Railway ทันที)
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# ติดตั้ง dependency ก่อน copy โค้ด เพื่อให้ layer cache ทำงาน
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# copy ทุก package ที่โค้ดใช้จริง
#   worker/   — quant core + live loop
#   backtest/ — main.py ใช้ fetch_binance ตอน warm-up (ขาดไม่ได้)
#   research/ — Edge Lab (store.open_registry อ้างถึง)
#   scripts/  — เครื่องมือ (journal_report, test_db)
COPY worker ./worker
COPY backtest ./backtest
COPY research ./research
COPY scripts ./scripts

# รันด้วย user ธรรมดา ไม่ใช่ root
RUN useradd --create-home --shell /bin/bash app && chown -R app:app /app
USER app

CMD ["python", "-m", "worker.app.main"]
