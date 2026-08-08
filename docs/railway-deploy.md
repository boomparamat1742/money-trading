# Deploy ขึ้น Railway

ระบบรันเป็น **worker ที่ทำงานตลอดเวลา** (ไม่ใช่เว็บ ไม่มี port ให้เปิด)
ข้อมูลเก็บที่ Supabase จึง **ไม่หายตอน redeploy** และไม่ต้องใช้ disk

## เตรียมพร้อมแล้วในโปรเจกต์

| ไฟล์ | หน้าที่ |
|---|---|
| `Dockerfile` | image (copy worker/, backtest/, research/, scripts/) |
| `.dockerignore` | กัน `.env` และ `data/` เข้า image |
| `railway.json` | ตั้ง builder + start command + restart policy |
| `requirements.txt` | เฉพาะ dependency ที่ใช้จริง 5 ตัว |

---

## ขั้นตอน

### 1. สร้าง service จาก GitHub
1. เข้า https://railway.app → **New Project** → **Deploy from GitHub repo**
2. เลือก `boomparamat1742/money-trading`
3. Railway จะเจอ `Dockerfile` เอง (ไม่ต้องตั้งค่า builder)

### 2. ใส่ Environment Variables
Service → **Variables** → **Raw Editor** วางทีเดียว (แก้ค่าให้เป็นของจริง):

```
DATABASE_URL=postgresql://postgres.dpcmhtsdcpcqxnmjvdza:รหัสจริง@aws-0-ap-northeast-1.pooler.supabase.com:5432/postgres
LINE_CHANNEL_TOKEN=<channel access token>
LINE_TO=<groupId>
EXCHANGE=binance
SYMBOLS=BTCUSDT,ETHUSDT,BNBUSDT
PRIMARY_TIMEFRAME=15m
CONFIRM_TIMEFRAMES=1h,4h
ACCOUNT_EQUITY=10
RISK_PER_TRADE_PCT=0.5
SIGNAL_SCORE_THRESHOLD=65
LEVERAGE_CAP=20
NOTIFY_MAX_PER_DAY=8
AI_ENABLED=false
TZ=Asia/Bangkok
```

### โควตา LINE — ตัวเลขที่ต้องเข้าใจก่อนตั้ง NOTIFY_MAX_PER_DAY

**LINE นับโควตาต่อ "ผู้รับ" ไม่ใช่ต่อข้อความ** — push เข้ากลุ่มที่มี 5 คน
= **5 ข้อความ** ไม่ใช่ 1 ([LINE pricing](https://developers.line.biz/en/docs/messaging-api/pricing/))

เช็กโควตาจริงของบัญชีคุณ:
```bash
python -m scripts.line_quota
```

1 ไม้กินอย่างน้อย 2 ข้อความ (เปิด + ปิด) และทุกครั้งที่ redeploy
มีข้อความ "เริ่มทำงาน" อีก 1 — ตั้ง `NOTIFY_MAX_PER_DAY` จากโควตาที่เหลือหาร
จำนวนวันที่เหลือในเดือน แล้วหารด้วยจำนวนคนในกลุ่มอีกที

ระบบมีตัวกันซ้อนอยู่แล้ว: ถามโควตาจริงจาก LINE เป็นระยะ และหยุดส่งเองก่อนหมด
(ตัวนับในหน่วยความจำเชื่อไม่ได้ เพราะ Railway รีสตาร์ทแล้วนับใหม่จากศูนย์)

> ⚠️ **อย่า upload ไฟล์ `.env`** — Railway ใช้ Variables แทน และ `.env` ถูก gitignore + dockerignore ไว้แล้ว

### 3. Deploy
Railway จะ build และรันเอง — ดู **Deploy Logs** ควรเห็น:
```
[store] journal: Postgres/Supabase (postgresql://...:***@...)
[startup] notifier: LINE Messaging API (cap 20 msgs/day)
[startup] warming up from ~1500 recent 15m candles ...
[startup] streaming BTCUSDT@15m ...
```
และข้อความ "🟢 ระบบเฝ้าตลาดเริ่มทำงาน" เด้งเข้ากลุ่ม LINE

---

## (ทางเลือก) เพิ่ม Edge Lab เป็น service ที่ 2

รัน Edge Lab สัปดาห์ละครั้งจาก image เดียวกัน:

1. ใน project เดิม → **New** → **GitHub Repo** → เลือก repo เดิมอีกครั้ง
2. Settings → **Cron Schedule**: `0 2 * * 1` (ทุกวันจันทร์ ตี 2)
3. Settings → **Custom Start Command**:
   ```
   python -m research.lab.watch --notify
   ```
4. ใส่ Variables ชุดเดียวกัน (อย่างน้อย `DATABASE_URL`, `LINE_CHANNEL_TOKEN`, `LINE_TO`)

---

## ค่าใช้จ่าย

| | ราคา |
|---|---|
| Railway Hobby | $5/เดือน (มี credit $5 ให้ใช้) |
| Supabase Free | $0 |
| **รวม** | **~$5/เดือน** |

worker ตัวนี้ใช้ RAM ~100-200 MB · CPU น้อยมาก (ทำงานทุก 15 นาที)

---

## ปัญหาที่พบบ่อย

| อาการใน log | สาเหตุ / วิธีแก้ |
|---|---|
| `ต่อ Postgres ไม่สำเร็จ ... → ใช้ SQLite แทน` | `DATABASE_URL` ผิด — ข้อมูลจะหายตอน redeploy! แก้ตัวแปรให้ถูก |
| `notifier: console` | ไม่ได้ตั้ง `LINE_CHANNEL_TOKEN`/`LINE_TO` |
| `ModuleNotFoundError: backtest` | Dockerfile เก่า — ต้องใช้ตัวที่ root ของ repo |
| ไม่มีสัญญาณเลยหลายวัน | ปกติ — ฟิลเตอร์เข้ม (ต้อง 1h+4h ตรงกัน) รอตลาดเข้าเงื่อนไข |
| deploy ขึ้นแล้วดับทันที | ดู Deploy Logs — restart policy ตั้งไว้ 10 ครั้งแล้วหยุด |

## ตรวจว่าข้อมูลลง Supabase จริง

Supabase Dashboard → **Table Editor** → ตาราง `signals` / `trades`
หรือรันจากเครื่องตัวเอง (ชี้ DB เดียวกัน):
```bash
python -m scripts.journal_report
```

---

## ⚠️ ย้ำ

ระบบนี้เป็น **paper trading + แจ้งเตือน** เท่านั้น — ไม่ส่งคำสั่งซื้อขายจริง
และสัญญาณ **ยังไม่มี edge พิสูจน์แล้ว** (walk-forward เป็นลบ) ใช้เป็นเครื่องมือเฝ้าตลาด
