# ต่อฐานข้อมูลกับ Supabase

ระบบใช้ **SQLite เป็นค่าเริ่มต้น** (ไม่ต้องตั้งค่าอะไร) และสลับไป Supabase
อัตโนมัติเมื่อมี `DATABASE_URL` — โค้ดไม่ต้องแก้เลย

## ขั้นตอน (ทำครั้งเดียว)

### 1. สร้าง Supabase project
เข้า https://supabase.com → New project → ตั้งรหัสผ่านฐานข้อมูล (เก็บไว้ให้ดี)

### 2. สร้างตาราง
Supabase Dashboard → **SQL Editor** → วางเนื้อหาไฟล์
[`migrations/supabase.sql`](../migrations/supabase.sql) ทั้งหมด → **Run**

จะได้ 3 ตาราง: `signals`, `trades`, `edge_runs`

### 3. เอา connection string
**Project Settings → Database → Connection string → URI**

เลือกโหมด:
| โหมด | port | เหมาะกับ |
|---|---|---|
| **Session pooler** | 5432 | worker ที่รันยาว (`worker.app.main`) ← แนะนำ |
| Transaction pooler | 6543 | งานสั้นๆ / serverless |

จะได้หน้าตาแบบ:
```
postgresql://postgres.abcdefgh:[YOUR-PASSWORD]@aws-0-region.pooler.supabase.com:5432/postgres
```
**แทน `[YOUR-PASSWORD]` ด้วยรหัสจริง**

### 4. ใส่ใน `.env`
```
DATABASE_URL=postgresql://postgres.xxxx:รหัสจริง@aws-0-xxx.pooler.supabase.com:5432/postgres
```

### 5. ติดตั้ง driver
```bash
pip install "psycopg[binary]"
```

### 6. ทดสอบ
```bash
python -m scripts.journal_report
```
ถ้าขึ้น `[store] journal: Postgres/Supabase (...)` = ต่อสำเร็จ ✅

## ใช้งานหลังต่อแล้ว

ทุกอย่างเหมือนเดิม แค่ข้อมูลไปอยู่บน Supabase แทน:
```bash
python -m worker.app.main          # สัญญาณ/ไม้ → Supabase
python -m research.lab.watch       # ผล Edge Lab → Supabase
python -m scripts.journal_report   # อ่านจาก Supabase
```

ดูข้อมูลบนเว็บได้ที่ Supabase Dashboard → **Table Editor**

## ข้อดีที่ได้

| | SQLite | Supabase |
|---|---|---|
| ข้อมูลหายตอน redeploy | ❌ หาย | ✅ ไม่หาย |
| ดูจากที่อื่น/มือถือ | ❌ | ✅ ผ่าน Dashboard |
| ทำ dashboard เว็บ | ยาก | ✅ มี REST API ให้ |
| ตั้งค่า | ไม่ต้อง | ต้องทำ 6 ขั้นข้างบน |

## ข้อควรระวัง

- **`DATABASE_URL` มีรหัสผ่าน** — อยู่ใน `.env` ซึ่ง gitignore ไว้แล้ว **อย่า commit**
- ถ้าต่อ Supabase ไม่ได้ ระบบจะ **fallback ไป SQLite อัตโนมัติ** พร้อมแจ้งใน log
  (ระบบเฝ้าตลาดต้องทำงานต่อได้แม้ DB นอกมีปัญหา)
- การเขียนแต่ละครั้งเป็น network call — ปริมาณของเราน้อยมาก (ไม่กี่ครั้งต่อ 15 นาที) จึงไม่มีปัญหา
- Free tier ของ Supabase จะ **pause project เมื่อไม่ใช้งาน 1 สัปดาห์** — ถ้าระบบรันตลอดจะไม่โดน

## ย้ายข้อมูลเก่าจาก SQLite (ถ้ามี)

```bash
python -m scripts.migrate_to_supabase
```
