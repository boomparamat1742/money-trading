# Crypto Market Monitor + Edge Research Toolkit

ระบบเฝ้าตลาดคริปโตแบบ **Quant-first** (Quant Engine สร้างสัญญาณ, Risk Manager อนุมัติ,
Claude ช่วยวิเคราะห์บริบท) + **ชุดเครื่องมือวิจัยหา edge** (backtest, walk-forward, momentum,
cross-sectional, funding carry)

ออกแบบตาม [`docs/system-design-quant-first-ai-assistant.md`](docs/system-design-quant-first-ai-assistant.md)

> ⚠️ **ใช้เป็นเครื่องมือเฝ้าตลาด + แจ้งเตือน + วิจัย เท่านั้น — ไม่ใช่ระบบทำกำไรอัตโนมัติ**
> Paper Trading เท่านั้น · ไม่รับประกันกำไร · ไม่ใช่คำแนะนำการลงทุน

## ผลการวิจัยหา edge (สรุป — ทดสอบด้วย walk-forward OOS จริง)

ทดสอบ 4 สมมติฐานบนข้อมูล Binance จริง — **ไม่มีตัวใดให้ edge บวกชัดเจนหลังต้นทุน
ในช่วงตลาดปัจจุบัน** (ดู `research/` และรันเองได้):

| แนวทาง | ผล OOS | สรุป |
|---|---|---|
| Intraday textbook (15m) | PF 0.28, max drawdown >100% | 🔴 ไม่มี edge (พอร์ตระเบิด) |
| Time-series momentum (BTC 1d) | Sharpe 0.45 vs hold 0.67 | 🟡 ลด drawdown แต่ไม่ชนะการถือ |
| Cross-sectional momentum (16 เหรียญ) | Sharpe 0.42 vs BTC 0.67 | 🟡 ชนะ market ไม่ชนะ BTC |
| Funding carry (market-neutral) | OOS Sharpe ~0 หลัง cost | 🟡 จริงแต่ยุบ + ต้อง execution เทพ |

บทเรียน: **"ถือ BTC เฉยๆ" เป็น benchmark ที่โหด** และ edge ง่ายๆ ที่ retail เข้าถึงได้
มักถูกตลาดกินไปแล้ว — เครื่องมือชุดนี้ทำหน้าที่กันเราจากการเดิมพันเงินจริงกับสิ่งที่ไม่มีจริง

---

## สถานะการพัฒนา

| ส่วน | สถานะ |
|---|---|
| **Quant core** (indicators, regime, strategies, scoring, risk, paper trading) | ✅ ใช้งานได้ (pure stdlib) |
| **Backtest engine** (metrics, in/out-of-sample split) | ✅ ใช้งานได้ |
| **Walk-forward** (จูนพารามิเตอร์บน train → validate OOS หลาย fold) | ✅ ใช้งานได้ |
| **Research track** (`research/`: momentum, xsmom, funding_carry) | ✅ ใช้งานได้ |
| **Data fetcher** (klines + funding จาก Binance public API) | ✅ ใช้งานได้ |
| **Tests** (pytest, 59 ผ่าน) | ✅ |
| Data quality / candle builder | ✅ |
| **Live data (Binance WebSocket + gap recovery)** | ✅ ใช้งานได้ (ทดสอบเชื่อมต่อจริงแล้ว) |
| **LINE Messaging API แจ้งเตือน** | ✅ ใช้งานได้ (ต้องใส่ token เอง) |
| **Live loop** (`worker.app.main`: WS → pipeline → paper trade → LINE) | ✅ ใช้งานได้ |
| **Trade journal (SQLite)** — บันทึกสัญญาณ/ไม้ + กู้คืนเมื่อรีสตาร์ต | ✅ ใช้งานได้ |
| **Supabase/Postgres** — สลับจาก SQLite ด้วย `DATABASE_URL` | ✅ ใช้งานได้ ([คู่มือ](docs/supabase-setup.md)) |
| News, AI context | 🟡 AI ต่อแล้ว (fail-open) · News ยัง scaffold |
| Dashboard | ⬜ ยังไม่เริ่ม |

---

## รันได้ทันที (ไม่ต้องติดตั้งอะไร)

Core + backtest ใช้ **Python standard library ล้วน** — ไม่ต้อง `pip install`

**รัน backtest บนข้อมูลสังเคราะห์ (demo):**
```bash
python -m backtest.run_backtest
```
จะพิมพ์ผล in-sample (train 70%) และ out-of-sample (test 30%) พร้อมบอกว่า
"มี edge" หรือไม่ (expectancy > 0)

**รัน backtest บนข้อมูลจริง (CSV):**
```bash
python -m backtest.run_backtest path/to/BTCUSDT_15m.csv
```
CSV header: `open_time,open,high,low,close,volume` (open_time เป็น ms epoch)

**ดึงข้อมูลจริงจาก Binance (ฟรี ไม่ต้องมี API key):**
```bash
python -m backtest.fetch_binance BTCUSDT 15m 30000
```

**Walk-forward (การทดสอบ edge ที่แท้จริง — จูนบน train แล้ว validate OOS หลายช่วง):**
```bash
python -m backtest.walk_forward data/BTCUSDT_15m.csv 2000 500
```
รายงานจะให้ **OOS expectancy รวมทุก fold** (ตัวเลขที่เชื่อได้), เทียบกับ baseline,
ความสม่ำเสมอต่อ fold, และ parameter stability — พร้อมสรุปว่า "🟢 มีสัญญาณว่ามี edge"
หรือ "🔴 ยังไม่พบ edge"

> ผลปัจจุบันบนข้อมูล BTCUSDT 15m จริง ~10 เดือน: **🔴 ยังไม่พบ edge**
> walk-forward: profit factor **0.284** · backtest: 1,023 ไม้ expectancy **−14/ไม้**
> max drawdown **>100% (พอร์ตระเบิด)** · ค่าธรรมเนียมกินเกือบครึ่งของขาดทุน
> — อย่านำไปเทรดจริง ใช้เป็นเครื่องมือเฝ้าตลาด/วิจัยเท่านั้น

**🔬 Edge Lab — กรอบทดสอบสมมติฐาน edge อย่างซื่อสัตย์:**
```bash
python -m research.lab.run list                # ดูสมมติฐานทั้งหมด
python -m research.lab.run test xsmom_smallcap # ทดสอบหนึ่งข้อ
python -m research.lab.run history             # ผลสะสมทุกครั้งที่เคยทดสอบ
python -m research.lab.watch --notify          # รันเก็บผลต่อเนื่อง (ดู scripts/schedule_edge_lab.md)
```
ด่านที่บังคับทุกสมมติฐาน: walk-forward · เกณฑ์สูงขึ้นตามจำนวนครั้งที่ลอง (กัน data
mining) · ต้องชนะ benchmark ≥0.25 Sharpe · เพดาน drawdown 60% · ข้อมูลไม่พอ =
"ตัดสินไม่ได้" ไม่ใช่ "ไม่ผ่าน" — และการรันซ้ำจะรายงาน "ผ่านกี่ครั้งจากกี่ครั้ง"
เสมอ เพื่อกันการหลงผลบวกลวงจากการทดสอบซ้ำ

**เครื่องมือวิจัย edge อื่นๆ (`research/`):**
```bash
python -m backtest.fetch_binance BTCUSDT 1d 2500   # ดึงข้อมูลรายวัน
python -m research.momentum                        # time-series momentum (BTC เดี่ยว)
python -m research.xsmom                            # cross-sectional momentum (16 เหรียญ)
python -m research.funding_carry                    # funding-rate carry (market-neutral)
```

**รันเทสต์:**
```bash
python -m pytest        # ถ้าติดตั้ง pytest แล้ว
```

---

## รันระบบ Live (เฝ้าตลาด + แจ้งเตือน)

> โหมด **Paper Trading เท่านั้น** — ไม่ส่งคำสั่งเทรดจริง และสัญญาณยัง **ไม่มี edge พิสูจน์แล้ว**
> (walk-forward เป็นลบ) ใช้เป็น "ระบบเฝ้าตลาด + แจ้งเตือน" ไม่ใช่ระบบทำกำไรอัตโนมัติ

ติดตั้ง dependency สำหรับ live:
```bash
pip install websockets aiohttp
```

ตั้งค่า `.env` (คัดลอกจาก `.env.example`) — อย่างน้อยใส่:
```
SYMBOLS=BTCUSDT
PRIMARY_TIMEFRAME=15m
LINE_CHANNEL_TOKEN=...      # จาก LINE Developers Console (Messaging API channel)
LINE_TO=...                 # userId หรือ groupId ปลายทาง
AI_ENABLED=false            # หรือ true + ใส่ ANTHROPIC_API_KEY
```

ทดสอบ LINE ก่อน:
```bash
python -m scripts.line_test
```

รันระบบจริง (สตรีมจาก Binance):
```bash
python -m worker.app.main
```
ดูผลที่ระบบทำไปแล้ว (trade journal):
```bash
python -m scripts.journal_report
```
สรุป win rate / profit factor / expectancy จากสัญญาณจริงที่บันทึกไว้ + ไม้ล่าสุด

- ถ้าไม่ใส่ token LINE → แจ้งเตือนออก console แทน
- ทุกสัญญาณ/ไม้ถูกบันทึกลง `data/journal.db` · รีสตาร์ทแล้วไม้ที่เปิดค้างกลับมาเอง
- จำกัดข้อความ LINE ด้วย `NOTIFY_MAX_PER_DAY` (default 20/วัน)
- auto-reconnect + gap recovery เมื่อเน็ตหลุด
- deploy บน Railway ได้ ([คู่มือ](docs/railway-deploy.md)) — ~$5/เดือน

> LINE Notify ถูกปิดแล้ว — ระบบนี้ใช้ **Messaging API (push)** ซึ่งต้องมี channel + destination id

---

## โครงสร้าง

```
worker/app/          # quant core (ใช้ร่วมกันทั้ง real-time และ backtest)
  indicators.py      # RSI/MACD/EMA/ATR/ADX/Bollinger (pure Python)
  regime.py          # จำแนก uptrend/downtrend/sideway/high_vol
  strategies/        # trend_following / breakout / mean_reversion + registry
  scoring.py         # คะแนน 0-100 อธิบายได้ (ไม่ใช่ AI confidence)
  risk.py            # Risk Manager — ผู้อนุมัติสัญญาณเพียงผู้เดียว
  paper_trading.py   # จำลอง fill + fee + slippage
  pipeline.py        # candle → Signal (หัวใจที่ใช้ร่วมกัน)
  ai_context.py      # Claude (slow path, fail-open)
  market_data.py / news.py / db.py / notifier.py / main.py   # live layer (scaffold)
backtest/            # metrics + synthetic data + runner
migrations/schema.sql
worker/tests/
```

---

## หลักการสำคัญ (จากดีไซน์)

1. **AI ไม่อยู่ใน critical path** — ระบบทำงานต่อได้แม้ AI/News ล่ม
2. **Risk Manager เป็นผู้อนุมัติเพียงผู้เดียว** — AI ไม่กำหนด entry/SL/TP/size
3. **สัญญาณทำซ้ำได้** — logic เดียวกันทั้ง backtest และ real-time
4. **วัดด้วย Profit Factor / Expectancy / Drawdown** ไม่ใช่ Win Rate อย่างเดียว
5. **Backtest คือประตู go/no-go** — ไม่มี edge (out-of-sample) = ไม่ลงเงิน

---

## ขั้นถัดไป (ดู docs §15 Roadmap)

- Phase 1: ต่อ Binance WebSocket จริง (`market_data.py`) + Postgres
- Phase 3: ดึงข้อมูลย้อนหลังจริงมา backtest + walk-forward → **พิสูจน์ edge ก่อน**
- Phase 5: ต่อ Claude AI context + Telegram
