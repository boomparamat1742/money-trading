# System Design Document
## แพลตฟอร์มวิเคราะห์หุ้น/คริปโตแบบ Quant-first + AI Assistant (Real-time)

**เวอร์ชัน:** 2.0  
**วันที่:** 2026-08-06  
**สถานะ:** Draft สำหรับ MVP  
**แนวทางหลัก:** ให้ Quant Engine เป็นผู้สร้างสัญญาณหลัก, Risk Manager เป็นผู้อนุมัติ และ AI เป็นผู้ช่วยวิเคราะห์บริบท

---

## ⚠️ ข้อจำกัดความรับผิดชอบ

เอกสารนี้อธิบายสถาปัตยกรรมทางเทคนิคสำหรับระบบวิเคราะห์ตลาดและ Paper Trading เท่านั้น

- ระบบไม่สามารถรับประกันกำไรหรือผลตอบแทน
- กลยุทธ์ที่ผ่าน Backtest ยังอาจขาดทุนเมื่อใช้กับข้อมูลจริง
- AI และโมเดลเชิงสถิติสามารถให้ผลผิดพลาดได้
- ต้องทดสอบย้อนหลัง, Walk-forward และ Paper Trading ก่อนใช้เงินจริง
- เอกสารนี้ไม่ใช่คำแนะนำด้านการลงทุน

---

# 1. ภาพรวม (Overview)

## 1.1 วัตถุประสงค์

สร้างแพลตฟอร์มเฝ้าดูตลาดแบบ Real-time สำหรับหุ้นหรือคริปโต โดย:

1. รับข้อมูลราคาและปริมาณซื้อขายแบบต่อเนื่อง
2. สร้างแท่งราคาและตรวจสอบคุณภาพข้อมูล
3. คำนวณอินดิเคเตอร์เชิงเทคนิค
4. ตรวจจับสภาพตลาดและเรียกใช้กลยุทธ์ที่เหมาะสม
5. ให้คะแนนสัญญาณจากกฎเชิงปริมาณ
6. ตรวจสอบความเสี่ยงด้วยกฎแบบ Hard-coded
7. ติดตามผลด้วย Paper Trading
8. ใช้ AI วิเคราะห์ข่าว สรุปบริบท และอธิบายสัญญาณ
9. แจ้งเตือนผ่าน Telegram/LINE และแสดงผลบน Dashboard

## 1.2 หลักการออกแบบ

ระบบนี้ใช้แนวคิด **Quant-first + AI Assistant**

- **Quant Engine** เป็นผู้ตัดสินใจเชิงระบบจากสูตร กฎ และข้อมูล
- **Risk Manager** เป็นผู้อนุมัติหรือปฏิเสธสัญญาณ
- **AI** ไม่ใช่ผู้ตัดสินใจซื้อขายหลัก
- **AI** ทำหน้าที่สรุปข่าว ตรวจความขัดแย้ง และอธิบายเหตุผล
- ทุกสัญญาณต้องสามารถทำซ้ำ ทดสอบย้อนหลัง และตรวจสอบย้อนกลับได้

## 1.3 Goals

- รับข้อมูลตลาดแบบ Real-time ผ่าน WebSocket
- สร้างแท่งราคา 1m / 5m / 15m อย่างถูกต้อง
- คำนวณ RSI, MACD, EMA, Bollinger Bands, ATR และ Volume Metrics
- จำแนก Market Regime เช่น Trend, Sideway และ High Volatility
- รองรับหลายกลยุทธ์ เช่น Trend Following, Breakout และ Mean Reversion
- สร้าง Signal Score ที่อธิบายได้
- ตรวจสอบ R:R, Stop Loss, Position Size และ Daily Risk
- เก็บผล Paper Trading รวม Fee และ Slippage
- ใช้ AI วิเคราะห์ข่าวและสร้างคำอธิบายประกอบ
- แสดงสถิติ Net Profit, Profit Factor, Win Rate, Max Drawdown และ Expectancy
- Deploy บน Managed Services โดยลดภาระการดูแลเซิร์ฟเวอร์

## 1.4 Non-Goals สำหรับเฟสแรก

- ยังไม่ส่งคำสั่งซื้อขายจริงแบบ Auto-execution
- ยังไม่รองรับสินทรัพย์หลายร้อยตัว
- ยังไม่ใช้ AI กำหนด Entry, Stop Loss, Take Profit หรือ Position Size
- ยังไม่ทำ Mobile Native Application
- ยังไม่ทำ High-Frequency Trading
- ยังไม่รองรับกลยุทธ์ที่ต้องการ Latency ระดับ Microsecond

## 1.5 สินทรัพย์เริ่มต้น

แนะนำเริ่มจาก:

- BTC/USDT
- Exchange: Binance หรือ Bybit
- Timeframe หลัก: 15 นาที
- Timeframe ยืนยันแนวโน้ม: 1 ชั่วโมง
- Tick/Trade Stream ใช้ติดตามราคาปัจจุบัน
- Kline ใช้สร้างอินดิเคเตอร์และประเมินสัญญาณ

---

# 2. Requirements

## 2.1 Functional Requirements

| ID | Requirement |
|---|---|
| F1 | รับข้อมูลราคาและปริมาณผ่าน WebSocket |
| F2 | ตรวจสอบข้อมูลซ้ำ, Timestamp ผิด, Gap และ Connection State |
| F3 | สร้างแท่งราคา 1m / 5m / 15m |
| F4 | คำนวณอินดิเคเตอร์แบบ Incremental |
| F5 | จำแนก Market Regime |
| F6 | เรียกใช้ Strategy Engine ตาม Market Regime |
| F7 | สร้าง Signal Score จาก Trend, Momentum, Volume และ Multi-timeframe |
| F8 | ตรวจ Risk Rule ก่อนออกสัญญาณ |
| F9 | คำนวณ Entry, Stop Loss, Take Profit และ Position Size ด้วยสูตร |
| F10 | จำลอง Paper Trading พร้อม Fee และ Slippage |
| F11 | ดึงข่าวล่าสุดและตรวจข่าวซ้ำ |
| F12 | เรียก AI เฉพาะสัญญาณที่ผ่าน Quant และ Risk |
| F13 | AI คืน Context Summary และ Risk Warning แบบ Structured Output |
| F14 | แจ้งเตือนผ่าน Telegram/LINE |
| F15 | บันทึก Signals, Trades, Metrics, AI Calls และ System Events |
| F16 | แสดง Dashboard และสถิติรายกลยุทธ์ |
| F17 | รองรับ Backtest ด้วย Logic เดียวกับ Real-time |
| F18 | รองรับ Strategy Version และ Prompt Version |
| F19 | รองรับ Pause Strategy และ Global Kill Switch |
| F20 | รองรับ Retry, Timeout, Idempotency และ Dead-letter Handling |

## 2.2 Non-Functional Requirements

| ID | Requirement | เป้าหมาย |
|---|---|---|
| N1 | Market Event Processing | < 500 ms ต่อ Event ใน Fast Path |
| N2 | Quant Signal Generation | < 1 วินาทีหลังแท่งปิด |
| N3 | AI Context Completion | < 10 วินาทีเมื่อ API ปกติ |
| N4 | Uptime | ≥ 99% สำหรับ MVP |
| N5 | Data Integrity | ไม่ประมวลผลแท่งเดียวกันซ้ำ |
| N6 | Reconnect | Auto-reconnect พร้อม Gap Recovery |
| N7 | Observability | มี Logs, Metrics และ Alerts |
| N8 | Reproducibility | ข้อมูลเดียวกัน + Version เดียวกัน ต้องได้ผลเหมือนเดิม |
| N9 | Security | Secret อยู่ใน Environment Variables |
| N10 | Cost Control | จำกัด AI Call, News Call และจำนวนสินทรัพย์ |
| N11 | Auditability | ตรวจย้อนกลับได้ว่าสัญญาณเกิดจากกฎใด |
| N12 | Scalability | เพิ่ม Worker และสินทรัพย์ได้ภายหลัง |

---

# 3. สถาปัตยกรรมระบบ (Architecture)

## 3.1 ภาพรวม

```text
Exchange WebSocket / REST
          |
          v
+-------------------------+
| Market Data Service     |
| - Normalize events      |
| - Reconnect             |
| - Gap recovery          |
+-------------------------+
          |
          v
+-------------------------+
| Data Quality Check      |
| - Duplicate detection   |
| - Timestamp validation  |
| - Missing candle check  |
+-------------------------+
          |
          v
+-------------------------+
| Candle Builder          |
| - 1m / 5m / 15m         |
| - Closed candle only    |
+-------------------------+
          |
          v
+-------------------------+
| Indicator Engine        |
| - EMA / RSI / MACD      |
| - ATR / Bollinger       |
| - Volume metrics        |
+-------------------------+
          |
          v
+-------------------------+
| Market Regime Detector  |
| - Trend                 |
| - Sideway               |
| - High volatility       |
+-------------------------+
          |
          v
+-------------------------+
| Strategy Engine         |
| - Trend Following       |
| - Breakout              |
| - Mean Reversion        |
+-------------------------+
          |
          v
+-------------------------+
| Signal Scoring          |
| - Technical score       |
| - Volume score          |
| - Multi-timeframe       |
+-------------------------+
          |
          v
+-------------------------+
| Risk Manager            |
| - R:R                   |
| - Stop loss             |
| - Position size         |
| - Daily loss limit      |
+-------------------------+
          |
          v
+-------------------------+
| Paper Trading Engine    |
| - Fee / Slippage        |
| - Fill simulation       |
| - TP / SL tracking      |
+-------------------------+
          |
          +----------------------+
          |                      |
          v                      v
+---------------------+  +----------------------+
| AI Context Service  |  | Persistence          |
| - News summary      |  | PostgreSQL           |
| - Conflict check    |  | Signals / Trades     |
| - Explanation       |  | Metrics / Events     |
+---------------------+  +----------------------+
          |                      |
          +----------+-----------+
                     |
                     v
          +----------------------+
          | Notification Service |
          | Telegram / LINE      |
          +----------------------+
                     |
                     v
          +----------------------+
          | Dashboard            |
          | Next.js / Charts     |
          +----------------------+
```

## 3.2 Fast Path และ Slow Path

### Fast Path

ทำงานทันทีเมื่อแท่งปิด:

1. Validate Candle
2. Update Indicators
3. Detect Market Regime
4. Run Strategy Rules
5. Calculate Signal Score
6. Run Risk Check
7. Create Paper Trade หรือ Signal
8. แจ้งเตือนเบื้องต้น

### Slow Path

ทำงานภายหลังโดยไม่ขวางการออกสัญญาณ:

1. ดึงข่าวจาก Cache หรือ News Provider
2. ส่ง Context ให้ AI
3. ตรวจ Structured Output
4. เพิ่มคำอธิบายและข่าวที่เกี่ยวข้อง
5. อัปเดต Notification และ Dashboard

## 3.3 เหตุผลที่ AI ไม่ควรอยู่ใน Critical Path

- ลด Latency
- ลดผลกระทบเมื่อ AI API ล่ม
- ลดค่าใช้จ่าย
- ทำให้ Backtest ทำซ้ำได้
- ป้องกัน AI เปลี่ยนคำตอบเมื่อข้อมูลเดิม
- ทำให้ Logic ซื้อขายโปร่งใส
- สามารถปิด AI ชั่วคราวได้โดยระบบหลักยังทำงาน

---

# 4. รายละเอียด Component

## 4.1 Market Data Service

**ไฟล์แนะนำ:** `market_data.py`

หน้าที่:

- เชื่อมต่อ Binance/Bybit WebSocket
- รองรับ Trade Stream และ Kline Stream
- Normalize Event ให้อยู่ในรูปแบบเดียวกัน
- ตรวจ Heartbeat
- Auto-reconnect
- ดึงข้อมูลย้อนหลังชดเชย Gap หลัง Reconnect
- บันทึก Connection State

รูปแบบข้อมูลมาตรฐาน:

```json
{
  "exchange": "binance",
  "symbol": "BTCUSDT",
  "event_type": "kline",
  "timeframe": "15m",
  "event_time": "2026-08-06T10:15:00Z",
  "open": 0,
  "high": 0,
  "low": 0,
  "close": 0,
  "volume": 0,
  "is_closed": true
}
```

## 4.2 Data Quality Service

**ไฟล์แนะนำ:** `data_quality.py`

ตรวจสอบ:

- Event ซ้ำ
- Candle ซ้ำ
- Timestamp ย้อนกลับ
- Candle หาย
- ค่า OHLC ผิดตรรกะ
- Volume ติดลบ
- Symbol หรือ Timeframe ไม่ตรง
- ข้อมูลเก่าเกินเกณฑ์

เมื่อพบปัญหา:

- Reject
- Repair จาก REST API
- Log Event
- ส่ง Alert เมื่อเกิดซ้ำเกิน Threshold

## 4.3 Candle Builder

**ไฟล์แนะนำ:** `candle_builder.py`

หลักการ:

- กลยุทธ์ประเมินเฉพาะแท่งที่ปิดแล้ว
- ใช้ Tick/Trade สำหรับติดตามราคาและจำลอง Fill
- รองรับการ Aggregate 1m เป็น 5m และ 15m
- ใช้ Exchange Timestamp เป็นหลัก
- ใช้ Idempotency Key:

```text
exchange:symbol:timeframe:open_time
```

## 4.4 Indicator Engine

**ไฟล์แนะนำ:** `indicators.py`

Indicators:

- EMA 20 / 50 / 200
- RSI 14
- MACD
- Bollinger Bands
- ATR 14
- ADX
- Volume Moving Average
- Rate of Change
- Rolling High / Low

หลักการ:

- คำนวณ Incremental เมื่อทำได้
- ไม่ Recompute ทั้ง Dataset ทุก Event
- เก็บ Warm-up Status
- ไม่สร้างสัญญาณจนกว่าข้อมูลครบ Minimum Lookback

## 4.5 Market Regime Detector

**ไฟล์แนะนำ:** `regime.py`

จำแนกตลาด:

| Regime | เงื่อนไขตัวอย่าง |
|---|---|
| Uptrend | EMA20 > EMA50, ADX สูง, Close เหนือ EMA20 |
| Downtrend | EMA20 < EMA50, ADX สูง, Close ใต้ EMA20 |
| Sideway | ADX ต่ำ, Range แคบ |
| High Volatility | ATR Percentile สูง |
| Low Liquidity | Volume ต่ำกว่าค่าเฉลี่ยมาก |
| Unsafe | Spread, Gap หรือข่าวรุนแรงเกิน Threshold |

ผลลัพธ์:

```json
{
  "regime": "uptrend",
  "strength": 0.82,
  "volatility_state": "normal",
  "liquidity_state": "normal"
}
```

## 4.6 Strategy Engine

**ไฟล์แนะนำ:** `strategies/`

โครงสร้าง:

```text
strategies/
├─ base.py
├─ trend_following.py
├─ breakout.py
├─ mean_reversion.py
└─ registry.py
```

### Strategy A: Trend Following

- EMA20 > EMA50
- ADX สูงกว่าเกณฑ์
- Close เหนือ EMA20
- Momentum เป็นบวก
- Timeframe ใหญ่ยืนยันแนวโน้ม

### Strategy B: Breakout

- Close เหนือ High 20 หรือ High 50
- Volume สูงกว่าค่าเฉลี่ย
- ATR ไม่ต่ำเกินไป
- ไม่มี Gap ผิดปกติ
- Higher Timeframe ไม่สวนทาง

### Strategy C: Mean Reversion

- Market Regime เป็น Sideway
- ราคาแตะ Bollinger Band
- RSI อยู่ในเขตสุดโต่ง
- ADX ต่ำ
- ไม่มีข่าวความเสี่ยงสูง

ผลลัพธ์จากทุก Strategy:

```json
{
  "strategy_name": "breakout",
  "strategy_version": "1.0.0",
  "direction": "long",
  "raw_score": 72,
  "reasons": [
    "close_above_20_bar_high",
    "volume_above_average",
    "higher_timeframe_confirmed"
  ],
  "invalidations": []
}
```

## 4.7 Signal Scoring

**ไฟล์แนะนำ:** `scoring.py`

| หมวด | คะแนนสูงสุด |
|---|---:|
| Trend | 25 |
| Momentum | 20 |
| Volume | 15 |
| Multi-timeframe | 20 |
| Volatility Quality | 10 |
| Liquidity Quality | 10 |
| รวม | 100 |

| คะแนน | ระดับ |
|---|---|
| 80–100 | Strong |
| 65–79 | Normal |
| 50–64 | Watchlist |
| < 50 | No Trade |

หลักการ:

- คะแนนต้องอธิบายได้
- เก็บคะแนนย่อยทุกหมวด
- Threshold ต้องกำหนดตาม Backtest
- ห้ามใช้ AI Confidence แทน Probability จริง

## 4.8 Risk Manager

**ไฟล์แนะนำ:** `risk.py`

กฎขั้นต่ำ:

- Risk per trade ≤ 0.5–1% สำหรับ MVP
- Reward-to-Risk ≥ 1.5
- จำกัดจำนวนสถานะเปิด
- จำกัด Risk รวมทุกสถานะ
- จำกัด Daily Loss
- จำกัด Consecutive Loss
- Cooldown หลัง Stop Loss
- งดเทรดเมื่อ Data Quality ไม่ผ่าน
- งดเทรดเมื่อ Volatility สูงเกินไป
- Global Kill Switch

### Position Size

```text
position_size = risk_amount / abs(entry_price - stop_loss)
```

AI ไม่มีสิทธิ์กำหนด Position Size

## 4.9 Paper Trading Engine

**ไฟล์แนะนำ:** `paper_trading.py`

รองรับ:

- Market Fill Simulation
- Limit Fill Simulation
- Fee
- Slippage
- Partial Fill แบบง่าย
- Stop Loss
- Take Profit
- Expired Trade
- Maximum Holding Period
- Entry Timeout

สถานะ:

```text
pending
open
hit_tp
hit_sl
expired
cancelled
rejected
```

## 4.10 News Service

**ไฟล์แนะนำ:** `news.py`

หน้าที่:

- ดึงข่าวจากหลายแหล่ง
- Cache ข่าว
- Deduplicate ด้วย Hash
- เก็บ `published_at` และ `fetched_at`
- ประเมิน Asset Relevance
- ตัดข่าวเก่าเกิน Threshold
- ส่งเฉพาะหัวข้อที่เกี่ยวข้องให้ AI

## 4.11 AI Context Service

**ไฟล์แนะนำ:** `ai_context.py`

AI มีหน้าที่:

- สรุปข่าว
- ประเมิน Sentiment
- ตรวจความขัดแย้งระหว่างข่าวกับสัญญาณ
- ระบุความไม่แน่นอน
- อธิบายเหตุผลเป็นภาษาคน
- สร้าง Risk Warning

AI ไม่มีหน้าที่:

- สร้าง Signal หลัก
- กำหนด Position Size
- เปลี่ยน Stop Loss
- Override Daily Risk
- ส่งคำสั่งซื้อขายจริง

### Structured Output

```json
{
  "market_context": "bullish | bearish | neutral | uncertain",
  "news_risk": "low | medium | high",
  "conflict_with_signal": false,
  "summary": "ข้อความสรุปสั้น",
  "warnings": ["ข้อความเตือน"],
  "confidence_note": "อธิบายความไม่แน่นอน"
}
```

ถ้า AI Timeout, คืน JSON ไม่ถูกต้อง หรือ API ใช้งานไม่ได้ ระบบต้องไม่หยุด Signal Pipeline และต้องส่งแจ้งเตือนแบบไม่มี AI Context ได้

## 4.12 Notification Service

**ไฟล์แนะนำ:** `notifier.py`

ช่องทาง:

- Telegram Bot
- LINE Messaging API
- Dashboard Push

ตัวอย่างข้อความ:

```text
[BTCUSDT] LONG - Breakout
Score: 78/100
Entry: ...
Stop Loss: ...
Take Profit: ...
R:R: ...
Risk: ...%
Regime: Uptrend
เหตุผล: ...
AI Context: ...
สถานะ: Paper Trading
```

## 4.13 Dashboard

เทคโนโลยี:

- Next.js
- TradingView Lightweight Charts หรือ Recharts
- Supabase Read-only API

หน้าหลัก:

1. Live Signals
2. Open Paper Trades
3. Trade History
4. Equity Curve
5. Strategy Comparison
6. Market Regime
7. AI Context
8. System Health
9. Cost Tracking
10. Risk Status

---

# 5. Data Flow ของสัญญาณหนึ่งครั้ง

1. รับ Kline Event
2. ตรวจว่าแท่งปิดแล้ว
3. ตรวจ Duplicate และ Timestamp
4. อัปเดต Indicators
5. ตรวจ Market Regime
6. เรียก Strategy ที่เหมาะสม
7. สร้างคะแนน Signal
8. ตรวจ Threshold
9. คำนวณ Entry, Stop Loss และ Take Profit
10. ตรวจ Risk Manager
11. สร้าง Paper Trade
12. บันทึก Signal และ Trade
13. ส่ง Notification เบื้องต้น
14. ส่ง Job เข้า AI Context Queue
15. AI วิเคราะห์ข่าวและบริบท
16. Validate Structured Output
17. อัปเดต Signal Record
18. ส่ง Notification ฉบับเสริม
19. ติดตาม TP, SL, Fee และ Slippage
20. ปิด Trade และคำนวณ Metrics

---

# 6. Queue และ Reliability

## 6.1 Queue ที่แนะนำ

```text
market-events
signal-candidates
approved-signals
ai-context-jobs
notifications
trade-updates
dead-letter
```

## 6.2 Retry Policy

| งาน | Retry |
|---|---|
| WebSocket | Exponential Backoff |
| REST Gap Recovery | 3–5 ครั้ง |
| News API | 2–3 ครั้ง |
| AI API | 1–2 ครั้ง |
| Notification | 3 ครั้ง |
| Database | Transaction Retry |

## 6.3 Idempotency

```text
signal:{strategy}:{symbol}:{timeframe}:{candle_open_time}
trade:{signal_id}
notification:{signal_id}:{channel}:{type}
```

## 6.4 Dead-letter Queue

งานที่ล้มเหลวเกิน Retry Limit ต้องเก็บ Payload, Error, Retry Count พร้อมแจ้งผู้ดูแลและรองรับ Manual Replay

---

# 7. Data Model

## 7.1 signals

```sql
CREATE TABLE signals (
    id BIGSERIAL PRIMARY KEY,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    candle_open_time TIMESTAMPTZ NOT NULL,
    strategy_name TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    direction TEXT NOT NULL,
    signal_score NUMERIC NOT NULL,
    score_breakdown JSONB NOT NULL,
    market_regime JSONB,
    entry_price NUMERIC,
    stop_loss NUMERIC,
    take_profit NUMERIC,
    expected_rr NUMERIC,
    risk_status TEXT NOT NULL,
    rejection_reason TEXT,
    indicators JSONB NOT NULL,
    trigger_reasons JSONB NOT NULL,
    ai_context JSONB,
    ai_status TEXT DEFAULT 'pending',
    prompt_version TEXT,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (exchange, symbol, timeframe, strategy_name, strategy_version, candle_open_time)
);
```

## 7.2 paper_trades

```sql
CREATE TABLE paper_trades (
    id BIGSERIAL PRIMARY KEY,
    signal_id BIGINT NOT NULL REFERENCES signals(id),
    status TEXT NOT NULL,
    side TEXT NOT NULL,
    requested_entry NUMERIC,
    filled_entry NUMERIC,
    stop_loss NUMERIC,
    take_profit NUMERIC,
    position_size NUMERIC,
    risk_amount NUMERIC,
    risk_pct NUMERIC,
    entry_fee NUMERIC DEFAULT 0,
    exit_fee NUMERIC DEFAULT 0,
    slippage NUMERIC DEFAULT 0,
    exit_price NUMERIC,
    pnl_amount NUMERIC,
    pnl_pct NUMERIC,
    actual_rr NUMERIC,
    max_favorable_excursion NUMERIC,
    max_adverse_excursion NUMERIC,
    opened_at TIMESTAMPTZ,
    closed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now()
);
```

## 7.3 llm_calls

```sql
CREATE TABLE llm_calls (
    id BIGSERIAL PRIMARY KEY,
    signal_id BIGINT REFERENCES signals(id),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    input_tokens INT,
    output_tokens INT,
    latency_ms INT,
    cost_usd NUMERIC,
    status TEXT NOT NULL,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);
```

## 7.4 strategy_metrics

```sql
CREATE TABLE strategy_metrics (
    id BIGSERIAL PRIMARY KEY,
    strategy_name TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    period_start TIMESTAMPTZ NOT NULL,
    period_end TIMESTAMPTZ NOT NULL,
    trade_count INT NOT NULL,
    net_profit NUMERIC,
    profit_factor NUMERIC,
    win_rate NUMERIC,
    max_drawdown NUMERIC,
    expectancy NUMERIC,
    avg_win NUMERIC,
    avg_loss NUMERIC,
    created_at TIMESTAMPTZ DEFAULT now()
);
```

---

# 8. Backtest Design

## 8.1 หลักการสำคัญ

- ใช้ Strategy Logic เดียวกับ Real-time
- ใช้ Closed Candle เท่านั้น
- ป้องกัน Look-ahead Bias
- รวม Fee และ Slippage
- ใช้ข้อมูลหลายช่วงตลาด
- แบ่ง Train / Validation / Test
- ทำ Walk-forward
- เปรียบเทียบกับ Baseline
- เก็บ Strategy Version

## 8.2 Metrics

- Net Profit
- Profit Factor
- Win Rate
- Maximum Drawdown
- Expectancy
- Average Win
- Average Loss
- Sharpe Ratio
- Sortino Ratio
- Trade Count
- Exposure Time
- Fee
- Slippage
- Max Consecutive Loss

## 8.3 A/B Comparison

1. Quant Only
2. Quant + AI Explanation
3. Quant + AI Risk Filter
4. แต่ละ Strategy
5. แต่ละ Market Regime
6. แต่ละ Timeframe

---

# 9. Security

- เก็บ Secrets ใน Environment Variables
- Dashboard ต้องมี Authentication
- Database ใช้ Role แยก Read / Write
- ห้ามเผย API Key ใน Log
- เริ่มใน Paper Trading
- Auto-execution ปิดโดยค่าเริ่มต้น
- ต้องมี Daily Loss Limit และ Kill Switch

---

# 10. Observability

## 10.1 Logs

- Connection logs
- Candle creation logs
- Signal decision logs
- Risk rejection logs
- AI call logs
- Notification logs
- Trade lifecycle logs
- Error logs

## 10.2 Metrics

- Events per second
- Candle latency
- Signal latency
- AI latency
- AI cost
- Reconnect count
- Missing candle count
- Notification failure count
- Active trades
- Daily P&L
- Drawdown
- Queue depth

## 10.3 Alerts

- WebSocket หลุดนานเกิน Threshold
- Candle หาย
- Database เขียนไม่ได้
- Queue ค้าง
- AI Error Rate สูง
- Daily Loss Limit ถูกแตะ
- Drawdown เกิน Threshold
- Duplicate Signal เกิดขึ้น
- Cost สูงเกิน Budget

---

# 11. Tech Stack

| ชั้น | เทคโนโลยี |
|---|---|
| ภาษา | Python 3.11+ |
| Async | asyncio |
| WebSocket | websockets หรือ aiohttp |
| Data Processing | pandas, numpy |
| Indicators | pandas-ta หรือ custom incremental functions |
| Queue/Cache | Redis / Upstash |
| Database | PostgreSQL / Supabase / Neon |
| Worker Host | Railway / Render / Fly.io |
| AI | Provider Adapter + Structured Output |
| News | NewsAPI / Finnhub / CryptoPanic / RSS |
| Dashboard | Next.js |
| Charts | TradingView Lightweight Charts / Recharts |
| Notifications | Telegram Bot / LINE Messaging API |
| Container | Docker |
| Monitoring | Sentry / Grafana / Railway Logs |
| Backtest | vectorbt / backtrader / custom engine |
| Testing | pytest |
| Migration | Alembic |

---

# 12. โครงสร้างไฟล์

```text
money-project/
├─ worker/
│  ├─ app/
│  │  ├─ main.py
│  │  ├─ config.py
│  │  ├─ market_data.py
│  │  ├─ data_quality.py
│  │  ├─ candle_builder.py
│  │  ├─ indicators.py
│  │  ├─ regime.py
│  │  ├─ scoring.py
│  │  ├─ risk.py
│  │  ├─ paper_trading.py
│  │  ├─ news.py
│  │  ├─ ai_context.py
│  │  ├─ notifier.py
│  │  ├─ db.py
│  │  ├─ queue.py
│  │  ├─ monitoring.py
│  │  └─ strategies/
│  │     ├─ base.py
│  │     ├─ registry.py
│  │     ├─ trend_following.py
│  │     ├─ breakout.py
│  │     └─ mean_reversion.py
│  ├─ tests/
│  ├─ migrations/
│  ├─ requirements.txt
│  └─ Dockerfile
├─ backtest/
│  ├─ run_backtest.py
│  ├─ walk_forward.py
│  ├─ metrics.py
│  └─ reports/
├─ dashboard/
├─ docs/
│  ├─ system-design.md
│  ├─ strategy-spec.md
│  ├─ risk-policy.md
│  └─ operations-runbook.md
├─ docker-compose.yml
├─ .env.example
└─ README.md
```

---

# 13. Deployment

- Worker: Docker บน Railway, Render หรือ Fly.io
- Dashboard: Vercel
- Database: Managed PostgreSQL
- Queue/Cache: Managed Redis
- แยก Environment เป็น development, staging และ production
- Production ระยะแรกหมายถึง Paper Trading Production ไม่ใช่เงินจริง

---

# 14. Cost Control

- เรียก AI เฉพาะ Approved Signal
- Cache Prompt ส่วนคงที่
- Cache ข่าว
- จำกัดข่าวต่อ Signal
- จำกัดจำนวนสินทรัพย์
- จำกัดจำนวน AI Calls ต่อวัน
- ใช้ Model Routing
- เก็บ Token และ Cost ทุก Call
- ปิด AI ได้โดยไม่หยุด Quant Pipeline

ตัวอย่าง Budget Guard:

```text
AI_DAILY_CALL_LIMIT=50
AI_DAILY_COST_LIMIT_USD=2
NEWS_DAILY_CALL_LIMIT=500
MAX_SYMBOLS=3
```

---

# 15. Roadmap

## Phase 1: Data Pipeline

- เชื่อม Binance/Bybit
- รับ Kline
- Data Quality Check
- Candle Storage
- Telegram Health Alert

## Phase 2: Quant MVP

- Indicators
- Market Regime
- Strategy แรก
- Signal Scoring
- Risk Manager
- Telegram Signal

## Phase 3: Backtest

- Historical Data
- Fee และ Slippage
- Metrics
- Walk-forward
- Strategy Versioning

## Phase 4: Paper Trading

- Fill Simulation
- TP / SL Tracking
- Equity Curve
- Daily Risk
- Trade Report

## Phase 5: AI Context

- News Fetcher
- Structured Output
- Explanation
- Conflict Detection
- AI Cost Tracking

## Phase 6: Dashboard

- Live Signals
- Trades
- Metrics
- Strategy Comparison
- System Health

## Phase 7: Expansion

- เพิ่มสินทรัพย์
- เพิ่ม Strategy
- เพิ่มหุ้น
- ปรับ Scale
- พิจารณา Manual Execution

---

# 16. Testing Strategy

## Unit Tests

- Indicators
- Candle Aggregation
- Market Regime
- Strategy Rules
- Signal Scoring
- Risk Rules
- Fee / Slippage
- Position Sizing

## Integration Tests

- WebSocket → Candle
- Candle → Signal
- Signal → Risk
- Risk → Paper Trade
- Signal → AI Queue
- Trade → Database
- Notification Delivery

## Failure Tests

- WebSocket Disconnect
- Duplicate Candle
- Missing Candle
- Database Timeout
- Redis Down
- AI Timeout
- Invalid AI JSON
- Telegram Failure
- Restart ระหว่างมี Trade เปิด

---

# 17. ความเสี่ยงและแนวทางรับมือ

| ความเสี่ยง | แนวทางรับมือ |
|---|---|
| Data Gap | REST Recovery + Missing Candle Alert |
| Duplicate Signal | Unique Constraint + Idempotency Key |
| Over-fitting | Walk-forward + Out-of-sample |
| AI Hallucination | Structured Output + AI ไม่ควบคุม Trade |
| AI API ล่ม | ทำงานแบบ Quant-only |
| News ปลอมหรือช้า | หลายแหล่ง + Relevance + Timestamp |
| Cost บานปลาย | Daily Budget + Cache + Call Limit |
| Slippage สูง | Simulate ตาม Liquidity |
| Market Regime เปลี่ยน | Regime Detector + Strategy Gating |
| Drawdown สูง | Daily Loss Limit + Kill Switch |
| Strategy เสื่อม | Rolling Metrics + Disable Strategy |
| Security Leak | Secret Management + Log Masking |
| WebSocket หลุด | Auto-reconnect + Gap Recovery |
| Database ล่ม | Retry + Queue + Backup |
| Notification ซ้ำ | Notification Idempotency |

---

# 18. ขั้นตอนถัดไป

- [ ] ยืนยันสินทรัพย์เริ่มต้น
- [ ] ยืนยัน Exchange
- [ ] เลือก Timeframe หลัก
- [ ] กำหนด Risk Policy
- [ ] สร้าง Project Structure
- [ ] สร้าง Database Schema
- [ ] ทำ Market Data Service
- [ ] ทำ Data Quality Check
- [ ] ทำ Indicator Engine
- [ ] ทำ Strategy แรก
- [ ] ทำ Signal Scoring
- [ ] ทำ Risk Manager
- [ ] ทำ Telegram Notification
- [ ] ทำ Backtest
- [ ] ทำ Paper Trading
- [ ] เก็บ Metrics หลายสัปดาห์
- [ ] เพิ่ม AI Context Service
- [ ] ทำ Dashboard
- [ ] สรุปผลก่อนพิจารณาขยายระบบ

---

# 19. ข้อสรุปการออกแบบ

ระบบที่ปรับปรุงแล้วมีหลักสำคัญดังนี้:

1. **Quant Engine เป็นผู้สร้างสัญญาณหลัก**
2. **Risk Manager เป็นผู้อนุมัติ**
3. **AI เป็นผู้ช่วยวิเคราะห์บริบท ไม่ใช่ผู้ควบคุมการซื้อขาย**
4. **ทุกสัญญาณต้องทดสอบย้อนหลังและทำซ้ำได้**
5. **ระบบหลักต้องทำงานต่อได้แม้ AI หรือ News API ล่ม**
6. **เริ่มจาก Paper Trading และขยายทีละขั้น**
7. **วัดผลด้วย Profit Factor, Drawdown และ Expectancy ไม่ใช่ Win Rate อย่างเดียว**
8. **ควบคุมต้นทุน ความเสี่ยง และข้อมูลเป็นลำดับแรก**

แนวทางนี้ทำให้ระบบมีความเร็ว โปร่งใส ควบคุมความเสี่ยงง่าย และเหมาะกับการพัฒนาจาก MVP ไปสู่ระบบที่ใช้งานจริงมากกว่าสถาปัตยกรรมที่ให้ AI เป็นผู้ตัดสินใจหลัก
