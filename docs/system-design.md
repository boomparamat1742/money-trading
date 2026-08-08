# System Design Document — แพลตฟอร์มวิเคราะห์หุ้น/คริปโตด้วย Claude (Real-time)

> เวอร์ชัน 1.0 · วันที่ 2026-08-06 · สถานะ: Draft สำหรับ MVP

---

## ⚠️ ข้อจำกัดความรับผิดชอบ (อ่านก่อน)

เอกสารนี้อธิบาย **สถาปัตยกรรมทางเทคนิค** เท่านั้น

- เป้าหมาย **"กำไร $1,000/เดือน" ไม่ใช่สิ่งที่ระบบควบคุมได้** — ขึ้นกับเงินทุน สภาพตลาด และการบริหารความเสี่ยง ระบบทำได้แค่ "หาโอกาส + จัดการความเสี่ยง" ไม่การันตีผลตอบแทน
- ระบบที่ใช้ AI ยัง **ขาดทุนได้** ต้องผ่าน backtest และ paper trading ก่อนใช้เงินจริง
- เอกสารนี้ **ไม่ใช่คำแนะนำการลงทุน** ผู้พัฒนาไม่ใช่ที่ปรึกษาการเงินที่มีใบอนุญาต

---

## 1. ภาพรวม (Overview)

### 1.1 วัตถุประสงค์
สร้างแพลตฟอร์มที่เฝ้าดูตลาดแบบ real-time วิเคราะห์จาก **ข่าว + กราฟ + ราคา + ทิศทางตลาด** โดยใช้โมเดล Claude แล้วส่งสัญญาณ **จุดเข้าซื้อ/จุดขาย** พร้อมแจ้งเตือนผู้ใช้

### 1.2 Goals
- เฝ้าราคาแบบ real-time ผ่าน WebSocket
- คำนวณอินดิเคเตอร์ทางเทคนิคแบบ streaming
- ใช้ Claude ตีความบริบท (ข่าว + อินดิเคเตอร์ + ทิศทาง) และสร้างสัญญาณเป็น JSON
- แจ้งเตือนผ่าน Line/Telegram
- เก็บประวัติสัญญาณเพื่อวัดผล (win rate, P&L จำลอง)
- Deploy บน managed host โดย **ไม่ต้องดูแลเซิร์ฟเวอร์เอง**

### 1.3 Non-Goals (ขอบเขตที่ยังไม่ทำในเฟสแรก)
- ❌ ส่งคำสั่งเทรดอัตโนมัติเข้าโบรกฯ (auto-execution) — เริ่มด้วยโหมด "แจ้งเตือนอย่างเดียว"
- ❌ รองรับสินทรัพย์หลายร้อยตัวพร้อมกัน
- ❌ Mobile native app (ใช้ web dashboard + push ก่อน)

### 1.4 สินทรัพย์เป้าหมาย (ตัดสินใจแล้ว)
เริ่มที่ **คริปโต** เพราะฟีด real-time ฟรี (Binance/Bybit), เทรด 24 ชม., ทดสอบระบบได้เร็ว
เฟสหลังค่อยขยายเป็นหุ้น US (Alpaca/Polygon) — สถาปัตยกรรมเดียวกัน เปลี่ยนแค่ data adapter

---

## 2. Requirements

### 2.1 Functional
| # | Requirement |
|---|---|
| F1 | รับ tick ราคาแบบ real-time ผ่าน WebSocket |
| F2 | คำนวณอินดิเคเตอร์ (RSI, MACD, EMA, Bollinger, ATR) แบบ streaming |
| F3 | ตรวจจับ "เหตุการณ์น่าสนใจ" ด้วยกฎเร็ว (ชั้น 1) |
| F4 | เมื่อ trigger ติด → ดึงข่าวล่าสุด + ส่ง context ให้ Claude (ชั้น 2) |
| F5 | Claude คืนสัญญาณเป็น JSON: `{action, entry, stop_loss, take_profit, confidence, reason}` |
| F6 | ตรวจกฎความเสี่ยงก่อนออกสัญญาณ (position sizing, R:R ratio) |
| F7 | แจ้งเตือนผ่าน Line/Telegram + แสดงบน dashboard |
| F8 | เก็บสัญญาณ + ผลลัพธ์ลง DB |
| F9 | โหมด paper trading (ติดตามผลจริงโดยไม่ลงเงิน) |

### 2.2 Non-Functional
| # | Requirement | เป้าหมาย |
|---|---|---|
| N1 | Latency (tick → trigger) | < 500 ms |
| N2 | Latency (trigger → แจ้งเตือน) | < 10 s (รวมเรียก Claude) |
| N3 | Uptime | ≥ 99% (worker รันต่อเนื่อง) |
| N4 | Cost | < $50/เดือน (MVP) |
| N5 | Reconnect | auto-reconnect WebSocket เมื่อหลุด |

---

## 3. สถาปัตยกรรมระบบ (Architecture)

### 3.1 หลักการหลัก: ออกแบบ 2 ชั้น (Two-Tier)
ห้ามเรียก Claude ทุก tick (ช้า + เปลืองเงินมหาศาล) — แยกเป็น:

```
┌──────────────────────────────────────────────────────────────┐
│                        WORKER (Railway, always-on)            │
│                                                              │
│  WebSocket ──► [ชั้น 1: Fast Layer]  Python ล้วน             │
│   (ทุก tick)     • อัปเดต rolling window                      │
│                  • คำนวณ RSI/MACD/EMA                          │
│                  • เช็ค trigger rule                          │
│                        │ (เมื่อ trigger ติด + ผ่าน debounce)  │
│                        ▼                                       │
│                  [ชั้น 2: Analysis Layer]                     │
│                  • ดึงข่าวล่าสุด (cache 5 นาที)              │
│                  • เรียก Claude API ──► JSON signal           │
│                  • ตรวจกฎความเสี่ยง                            │
│                        │                                       │
│         ┌──────────────┼──────────────┐                       │
│         ▼              ▼              ▼                        │
│   PostgreSQL      Line/Telegram   (event log)                 │
│    (Supabase)      แจ้งเตือน                                   │
└──────────────────────────────────────────────────────────────┘
              │
              ▼
   Next.js Dashboard (Vercel) ──► อ่านจาก Supabase
```

### 3.2 ทำไมต้อง 2 ชั้น
- **ชั้น 1** รันเร็วมาก (µs–ms) กรอง tick เป็นล้านให้เหลือ "เหตุการณ์" หลักสิบต่อวัน
- **ชั้น 2** ช้ากว่า (Claude ~2–5 วิ) แต่ทำงานเฉพาะตอนจำเป็น → ควบคุมทั้ง latency และต้นทุน

### 3.3 Data Flow ของสัญญาณหนึ่งครั้ง
1. tick เข้า → อัปเดต deque + อินดิเคเตอร์
2. `local_trigger()` = True (เช่น ราคาทะลุ high 50 แท่ง + RSI > 60)
3. เช็ค debounce (เว้นอย่างน้อย N นาที/สินทรัพย์)
4. ประกอบ context: ราคา, อินดิเคเตอร์, ข่าว (จาก cache), ทิศทางตลาด
5. เรียก Claude → รับ JSON signal
6. `risk_check()`: R:R ≥ 1.5? position size ผ่าน? confidence ≥ threshold?
7. บันทึก DB + ยิงแจ้งเตือน + อัปเดต dashboard

---

## 4. รายละเอียด Component

### 4.1 Data Ingestion (`stream.py`)
- ใช้ `websockets` (asyncio) เชื่อม Binance `@trade` / `@kline`
- Auto-reconnect + heartbeat
- แปลงเป็น event มาตรฐาน `{symbol, price, ts, volume}`
- **Adapter pattern**: `BinanceSource`, `AlpacaSource`, `SettradeSource` — สลับได้โดยไม่แตะ logic

### 4.2 Indicator Engine (`indicators.py`)
- `pandas` / `pandas-ta` / `numpy`
- Rolling window ด้วย `collections.deque(maxlen=N)`
- คำนวณ incremental เมื่อเป็นไปได้ (ไม่ recompute ทั้งชุดทุก tick)

### 4.3 Trigger Rules (`triggers.py`)
- กฎเร็วเขียนเป็นฟังก์ชัน Python บริสุทธิ์ (ทดสอบง่าย, backtest ได้)
- ตัวอย่าง: breakout, RSI divergence, MACD cross, volume spike
- คืน `TriggerEvent{type, strength, snapshot}`

### 4.4 News Fetcher (`news.py`)
- ดึงจาก NewsAPI / Finnhub / RSS / CryptoPanic
- **Cache 5 นาที** (Upstash Redis) เพื่อไม่ยิงซ้ำและไม่บวมต้นทุน token
- สรุปหัวข้อ + sentiment เบื้องต้นก่อนส่งให้ Claude

### 4.5 Claude Analyzer (`claude_analyzer.py`)
ดูรายละเอียดเต็มใน §5

### 4.6 Risk Manager (`risk.py`)
- กฎ hard-coded (ไม่ปล่อยให้ AI กำหนดขนาดไม้เอง):
  - Risk per trade ≤ 1–2% ของพอร์ต
  - Reward:Risk ≥ 1.5
  - จำกัดจำนวนสัญญาณเปิดพร้อมกัน
  - ปฏิเสธสัญญาณ confidence ต่ำกว่า threshold

### 4.7 Notifier (`notifier.py`)
- Line Messaging API / Telegram Bot
- Template ข้อความ: action, ราคาเข้า, SL, TP, เหตุผลย่อ, confidence

### 4.8 Persistence (`db.py`)
- PostgreSQL (Supabase) — ดู schema §6

### 4.9 Dashboard (แยก repo/โฟลเดอร์)
- Next.js + Recharts / TradingView Lightweight Charts บน Vercel
- อ่านสัญญาณ + สถิติจาก Supabase (read-only)

---

## 5. การออกแบบส่วน Claude (หัวใจ)

### 5.1 เลือกโมเดล
| งาน | โมเดล | เหตุผล |
|---|---|---|
| ชั้น real-time (สัญญาณส่วนใหญ่) | **`claude-sonnet-5`** | เร็ว, ถูก, ฉลาดพอ |
| จังหวะสำคัญ / บทวิเคราะห์ลึก | `claude-opus-5` | เหตุผลเชิงลึกกว่า (เลือกใช้เฉพาะกรณี) |
| งานกรอง/จัดหมวดเบาๆ | `claude-haiku-4-5` | ถูกสุด (ถ้าต้องการชั้นกรองพิเศษ) |

> ราคา (ต่อ 1M tokens): Sonnet 5 = **$2/$10** (ราคาโปรถึง 31 ส.ค. 2026, ปกติ $3/$15) · Opus 5 = $5/$25 · Haiku 4.5 = $1/$5

### 5.2 เทคนิคสำคัญ
- **Structured output** (`output_config.format`) บังคับ Claude คืน JSON ตาม schema เป๊ะ — ไม่ต้อง parse เดา
- **Prompt caching**: system prompt + คำสั่ง + กฎ (ส่วนที่ไม่เปลี่ยน) แคชไว้ → cache read ~0.1x ราคา
- **Adaptive thinking**: เปิดไว้สำหรับการวิเคราะห์ที่ต้องใช้เหตุผลหลายขั้น
- Claude **เก่งอ่านข่าว/สรุปทิศทาง** แต่ไม่แม่นเรื่องคำนวณตัวเลขล้วน → ให้ Python คำนวณอินดิเคเตอร์ แล้วส่งผลให้ Claude "ตีความ"

### 5.3 โครง Prompt (แนวคิด)
```
System (แคช): "คุณเป็นนักวิเคราะห์เทคนิค ตอบเป็น JSON ตาม schema เท่านั้น
              ห้ามกำหนด position size (ระบบจัดการเอง) ..."
User (ต่อ request): ราคาปัจจุบัน, อินดิเคเตอร์ล่าสุด, ข่าว 3-5 หัวข้อ,
                    ทิศทางตลาดรวม, trigger ที่จุดชนวน
Output schema: {action: buy|sell|hold, entry, stop_loss, take_profit,
                confidence: 0-1, reason: string}
```

### 5.4 ประมาณการ token ต่อ 1 สัญญาณ
- Input ~2,000 tokens (context + ข่าว), Output ~400 tokens
- Sonnet 5 ราคาโปร: input 2,000 × $2/1M + output 400 × $10/1M ≈ **$0.008/สัญญาณ**
- ถ้าเปิด prompt caching ส่วน system → ถูกลงอีก

---

## 6. Data Model (PostgreSQL / Supabase)

```sql
-- สัญญาณที่ระบบสร้าง
CREATE TABLE signals (
  id            BIGSERIAL PRIMARY KEY,
  symbol        TEXT NOT NULL,
  action        TEXT NOT NULL,          -- buy | sell | hold
  entry_price   NUMERIC,
  stop_loss     NUMERIC,
  take_profit   NUMERIC,
  confidence    NUMERIC,                -- 0..1
  reason        TEXT,
  trigger_type  TEXT,                   -- breakout | rsi | macd ...
  indicators    JSONB,                  -- snapshot ตอนสร้างสัญญาณ
  model         TEXT,                   -- claude-sonnet-5 ...
  created_at    TIMESTAMPTZ DEFAULT now()
);

-- ผลลัพธ์ (paper trading / ผลจริง)
CREATE TABLE outcomes (
  id            BIGSERIAL PRIMARY KEY,
  signal_id     BIGINT REFERENCES signals(id),
  status        TEXT,                   -- open | hit_tp | hit_sl | expired
  exit_price    NUMERIC,
  pnl_pct       NUMERIC,
  closed_at     TIMESTAMPTZ
);

-- log การเรียก Claude (สำหรับดูต้นทุน/ดีบัก)
CREATE TABLE llm_calls (
  id            BIGSERIAL PRIMARY KEY,
  signal_id     BIGINT,
  input_tokens  INT,
  output_tokens INT,
  cost_usd      NUMERIC,
  created_at    TIMESTAMPTZ DEFAULT now()
);
```

---

## 7. Tech Stack สรุป

| ชั้น | เทคโนโลยี |
|---|---|
| ภาษา | Python 3.11+ (asyncio) |
| Worker host | **Railway** (always-on, ~$5/เดือน) · ทางเลือก: Render, Fly.io |
| WebSocket | `websockets` |
| Indicators | `pandas`, `pandas-ta`, `numpy` |
| LLM | Claude API (`anthropic` SDK) — Sonnet 5 หลัก |
| Cache/queue | Upstash Redis (free tier) |
| Database | Supabase / Neon (Postgres, free tier) |
| News | NewsAPI / Finnhub / CryptoPanic |
| แจ้งเตือน | Line Messaging API / Telegram Bot |
| Dashboard | Next.js + Recharts บน Vercel (free tier) |
| Container | Dockerfile (สำหรับ Railway) |
| Monitoring | Railway logs + (ทางเลือก) Grafana/Sentry |

---

## 8. Deployment

- **Worker**: Docker → Railway (รันต่อเนื่อง, cron ไม่จำเป็นเพราะเป็น long-running process)
- **Secrets**: เก็บใน Railway env vars (`ANTHROPIC_API_KEY`, `BINANCE_*`, `LINE_TOKEN`, `SUPABASE_URL`...)
- **Dashboard**: push ขึ้น Vercel (auto-deploy จาก Git)
- **DB/Redis**: managed (Supabase/Upstash) — ไม่ต้องดูแล
- โครงไฟล์:
```
money-project/
├─ worker/
│  ├─ stream.py
│  ├─ indicators.py
│  ├─ triggers.py
│  ├─ news.py
│  ├─ claude_analyzer.py
│  ├─ risk.py
│  ├─ notifier.py
│  ├─ db.py
│  ├─ main.py
│  ├─ requirements.txt
│  └─ Dockerfile
├─ dashboard/            # Next.js (Vercel)
└─ docs/system-design.md
```

---

## 9. ค่าใช้จ่ายต่อเดือนแบบละเอียด

### 9.1 สมมติฐาน
- เฝ้า **1–3 สินทรัพย์** (คริปโต)
- Debounce 5 นาที/สินทรัพย์ → เพดานทฤษฎี 288 สัญญาณ/วัน/สินทรัพย์ แต่ trigger จริงน้อยกว่ามาก
- ประมาณ **50–150 สัญญาณ/วัน** (รวมทุกสินทรัพย์)
- Claude ต่อสัญญาณ ≈ $0.008 (Sonnet 5 ราคาโปร, ยังไม่หัก caching)

### 9.2 ต้นทุน Claude API (ตัวแปรหลัก)

| ปริมาณ | สัญญาณ/เดือน | ต้นทุน/สัญญาณ | **รวม Claude/เดือน** |
|---|---|---|---|
| เบา (MVP) | ~1,500 | $0.008 | **~$12** |
| กลาง | ~4,500 | $0.008 | **~$36** |
| หนัก (หลายสินทรัพย์) | ~9,000 | $0.008 | **~$72** |

> ถ้าเปิด **prompt caching** ส่วน system/กฎ (คงที่ทุก request) ต้นทุน input ลดได้ ~50–80% ในส่วนที่แคช → ตัวเลขจริงมักต่ำกว่าตารางนี้

### 9.3 สรุปต้นทุนรวมต่อเดือน — 3 สถานการณ์

| รายการ | MVP (เบา) | เติบโต (กลาง) | Production (หนัก) |
|---|---|---|---|
| Worker (Railway) | $5 | $5–10 | $10–20 |
| Claude API | $12 | $36 | $72 |
| Database (Supabase) | $0 (free) | $0–25 | $25 (Pro) |
| Redis (Upstash) | $0 (free) | $0 | $0–10 |
| News API | $0 (free tier) | $0–50 | $50 |
| Dashboard (Vercel) | $0 (free) | $0 | $0–20 (Pro) |
| แจ้งเตือน (Line/Telegram) | $0 | $0 | $0 |
| **รวมโดยประมาณ** | **~$17/เดือน** | **~$45–75/เดือน** | **~$150–200/เดือน** |

> 💡 **MVP อยู่ได้ ~$17/เดือน** ส่วนใหญ่เป็นค่า Railway + Claude ที่เหลือใช้ free tier ได้หมด
> ค่าที่โตตามการใช้งานคือ **Claude API** และ **News API** — คุมได้ด้วย debounce, caching, และลดจำนวนสินทรัพย์

### 9.4 ตัวคุมต้นทุน (Cost Levers)
1. **Debounce ให้ยาวขึ้น** → สัญญาณน้อยลง → Claude ถูกลงตรงๆ
2. **Prompt caching** ส่วนคงที่
3. **ใช้ Haiku 4.5 เป็นชั้นกรอง** ก่อนส่งต่อ Sonnet/Opus (two-tier ภายใน LLM)
4. **จำกัดจำนวนสินทรัพย์** ที่เฝ้าพร้อมกัน
5. เฝ้า **timeframe ใหญ่ขึ้น** (เช่น 15m แทน tick) ถ้ากลยุทธ์เป็น swing

---

## 10. Roadmap (แนะนำทำเป็นเฟส)

| เฟส | ขอบเขต | ผลลัพธ์ |
|---|---|---|
| **1. MVP** | stream + indicators + trigger + Claude + Telegram แจ้งเตือน (1 สินทรัพย์) | ได้สัญญาณจริงเข้ามือถือ |
| **2. Backtest** | นำ trigger + logic ไปทดสอบย้อนหลัง (`backtrader`/`vectorbt`) | รู้ว่ากลยุทธ์เวิร์กไหมก่อนใช้จริง |
| **3. Paper trading** | ติดตามผลสัญญาณจริงหลายสัปดาห์ เก็บ win rate/P&L | ยืนยันความแม่นก่อนลงเงิน |
| **4. Dashboard + risk** | หน้าเว็บ + risk manager เต็มรูปแบบ | ใช้งานจริง มองภาพรวมได้ |
| **5. ขยาย** | เพิ่มสินทรัพย์/หุ้น, ปรับ prompt, เพิ่ม Opus สำหรับจังหวะสำคัญ | สเกลระบบ |

---

## 11. ความเสี่ยงและข้อควรระวัง

| ความเสี่ยง | การรับมือ |
|---|---|
| AI ให้สัญญาณผิด/ขาดทุน | Paper trade ก่อนจริง, ตั้ง SL เสมอ, จำกัด risk/trade |
| WebSocket หลุด | Auto-reconnect + alert |
| ต้นทุน Claude บานปลาย | Debounce + caching + จำกัดสินทรัพย์ + log ต้นทุน |
| ข่าวปลอม/ล่าช้า | ใช้หลายแหล่ง, cache, ให้ Claude ระบุความไม่แน่นอน |
| Over-fitting กลยุทธ์ | Backtest บนหลายช่วงเวลา + walk-forward |
| ฟีดหุ้นไทย real-time แพง/จำกัด | เริ่มคริปโต, ค่อยขยายหุ้นเมื่อระบบนิ่ง |

---

## 12. ขั้นถัดไป
- [ ] ยืนยันสินทรัพย์เริ่มต้น (แนะนำ BTC/USDT บน Binance)
- [ ] สร้างโครง `worker/` ตาม §8 + Dockerfile
- [ ] เชื่อม Supabase + สร้าง schema §6
- [ ] เขียน MVP: stream → trigger → Claude → Telegram
- [ ] ตั้งค่า Railway + deploy
- [ ] เปิดโหมด paper trading เก็บสถิติ
