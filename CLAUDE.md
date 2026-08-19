# CLAUDE.md

คู่มือสำหรับ Claude Code ทำงานกับ repo นี้ · ผู้ใช้สื่อสารภาษาไทย — **ข้อความที่ผู้ใช้เห็น (แจ้งเตือน, print, สรุป) เขียนภาษาไทย**

## ระบบนี้คืออะไร (ความจริงที่ต้องยึด)

Crypto **paper-trading + เฝ้าตลาด + วิจัยหา edge** บน Binance USD-M Futures · **ยังไม่มี edge ที่พิสูจน์แล้ว** (walk-forward เป็นลบ, indicator ทำนายไม่ได้, ค่าฟีกลืนกำไร) · **ห้ามอ้างว่ามี edge หรือปั้นตัวเลข win-rate/ความน่าจะเป็น** · โหมด paper เท่านั้น ไม่เคยส่งคำสั่งจริง · ดูสถานะวิจัยล่าสุดใน memory `edge-research-state`

## สถาปัตยกรรม

- **`worker/app/`** — runtime จริง (`python -m worker.app.main`) · stream ราคา futures ผ่าน REST polling (fstream ถูก geo-block ในไทย) → indicators → regime → strategy → score → risk → paper trade → แจ้งเตือน (Discord/LINE)
- **`research/lab/`** — **Edge Lab** (walk-forward OOS + multiple-testing bar) · สมมติฐานอยู่ใน `hypotheses.py` REGISTRY
- **`backtest/`** — `run_backtest.py` (ท่อเดียวกับ realtime บนข้อมูลย้อนหลัง), `fetch_binance.py`, `fetch_funding.py`, `metrics.py`
- **`scripts/`** — เครื่องมือรันมือ (ดึง/นำเข้า OI, calibrate, monitor, ฯลฯ)
- **`worker/tests/`** — pytest (~182 tests)

## โมดูลหลัก (worker/app)

- `indicators.py` — IndicatorEngine (pure Python ไม่ใช้ numpy) · EMA/RSI/MACD/Bollinger/ATR/ADX/Volume/VWAP/Donchian + derived features
- `regime.py` — จำแนก uptrend/downtrend/sideway/high_volatility (จริง ~46% เป็น sideway)
- `strategies/` — `trend_following` (v1.1 ตัวเดียวที่เทรดจริง), `breakout` (Donchian), `mean_reversion` (Bollinger) — 2 ตัวหลังลงทะเบียนแต่**ผ่าน score gate ไม่ได้เชิงโครงสร้าง**
- `scoring.py` — คะแนน 0-100 (เข้าข้างเทรนด์) · เกณฑ์ `SIGNAL_SCORE_THRESHOLD=65`
- `pipeline.py` — indicators→regime→strategy→score→threshold→risk
- `paper_trading.py` — PaperBroker, ATR stops (SL 1.5×, TP 3.0×, trailing), entry_context/exit_reason
- `notifier.py` — Discord (`DISCORD_WEBHOOK_URL` มาก่อน) / LINE (quota-aware) / Telegram / console
- `journal.py` + `journal_pg.py` — บันทึกลง SQLite หรือ Postgres (Supabase)
- `store.py` — `database_url()`, `open_journal()`, `open_registry()`

## ข้อมูล (Supabase — ตั้ง `DATABASE_URL`)

ตาราง: `signals`, `trades` (entry_context/exit_context/features), `market_snapshots` (OI/funding สด 15m), `oi_history` (OI รายวัน 6 ปีจาก Binance Vision, 10 เหรียญ), `edge_runs` (ผล Edge Lab)

## คำสั่งที่ใช้บ่อย

```bash
python -m pytest                       # เทสต์ทั้งหมด
python -m worker.app.main              # รัน worker (paper trading)
python -m research.lab.watch           # รัน Edge Lab (เขียนลง edge_runs)
python -m scripts.build_monitor        # หน้า Monitor (snapshot HTML)
python -m scripts.fetch_binance_vision_oi   # ดึง OI ลึกจาก Binance Vision
python -m scripts.merge_live_oi        # รวม OI สด → oi_history
```

## กฎการทำงาน (สำคัญมาก — ห้ามข้าม)

1. **Commit + push ขึ้น GitHub อย่างเดียว — ห้าม deploy** ผู้ใช้ redeploy Railway เอง (ดู memory `commit-push-only-no-deploy`)
2. **ทดสอบในเครื่องก่อนเสมอ** · ถ้าเวิร์ค → แจ้ง "พร้อมใช้งานจริง" แล้ว **รอผู้ใช้พิมพ์ "อนุมัติ commit"** · ถ้าไม่เวิร์ค → ไม่ commit + revert working tree ให้สะอาด
3. **แก้ `worker/app/*` = แตะ runtime → ต้อง redeploy** · แก้ `scripts/*` = รันมือ ไม่ต้อง deploy
4. **edge "เวิร์ค" = ผ่าน Edge Lab** (OOS Sharpe > bar, ชนะ benchmark ≥0.25, DD < 60%, สม่ำเสมอ, ไม่ติด implausible-Sharpe > 4.0) — ไม่ใช่แค่ backtest สวย
5. **กับดัก DATABASE_URL:** PowerShell ไม่ส่ง env ค่าว่างให้ลูก → `load_dotenv` โหลด DSN จริง → เผลอเขียน Supabase · **รัน Edge Lab ในเครื่องด้วย Bash: `DATABASE_URL= python ...`** หรือเรียก `evaluate()` ตรงๆ (ไม่ persist)
6. **ระเบียบวินัยวิจัย:** เจอตัวผ่าน = สงสัยหนักสุด · stress-test เสมอ (bar ที่นับ trials จริง + drop-one + แยกช่วงเวลา) ก่อนเชื่อ

## Deploy

Railway (worker, region Singapore) + Supabase (Postgres, region Tokyo) · env สำคัญ: `DATABASE_URL`, `DISCORD_WEBHOOK_URL`, `RUN_EDGE_LAB`, `DAILY_SUMMARY`, `MERGE_OI`, `SIGNAL_SCORE_THRESHOLD` · loop ในตัว worker: Edge Lab (สัปดาห์ละครั้ง), สรุปรอบวัน (18:00 ไทย), merge OI (23:00 UTC)

## แนวทางเขียนโค้ด

- Python pure ใน indicators (ไม่มี numpy/pandas) · เข้ากับ style เดิม (comment ผสมไทย-อังกฤษ, docstring อธิบาย "ทำไม")
- เพิ่ม hypothesis = subclass `Hypothesis` ใน `research/lab/hypotheses.py` แล้วใส่ REGISTRY · `param_grid` ต้องเล็ก ประกาศก่อนเห็นผล (ดู `SKILL.md`)
