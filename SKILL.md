---
name: test-edge-hypothesis
description: ทดสอบสมมติฐาน edge อย่างซื่อสัตย์ใน Edge Lab — ตั้งแต่ร่างจนถึง stress-test กันหลอกตัวเอง ใช้เมื่อจะหา/ทดสอบ edge ของกลยุทธ์เทรด
---

# Skill: ทดสอบสมมติฐาน edge อย่างซื่อสัตย์

หัวใจของโปรเจกต์นี้ไม่ใช่ "หา backtest สวย" แต่คือ **"ไม่หลอกตัวเอง"** · สมมติฐานส่วนใหญ่จะล้ม — นั่นคือระบบทำงานถูกต้อง

## กฎเหล็ก

1. **มี "เหตุผล" ก่อนเทสต์** — ตอบให้ได้ว่า *"ทำไม inefficiency นี้มีอยู่ ใครอยู่อีกฝั่งของออเดอร์เรา ทำไมเขายอมเสีย"* · ตอบไม่ได้ = data mining จะพังตอนใช้จริง
2. **`param_grid` เล็ก + ประกาศก่อนเห็นผล** — grid ใหญ่ = จูนจนหลอกตัวเอง
3. **นับ trials จริง** — ยิ่งลองหลายสมมติฐาน bar ยิ่งสูง (multiple-testing) · คุณภาพ > จำนวน
4. **ระวังกับดัก:** ทุกกลยุทธ์ที่ลองมาก้นบึ้งคือ trend-following → "เวิร์ค" เฉพาะช่วงตลาดเทรนด์ · edge จริงต้องอิสระจาก regime (เช่น carry/market-neutral)

## ขั้นตอน

### 1. ร่าง hypothesis
เพิ่ม subclass ใน `research/lab/hypotheses.py`:
```python
class MyHypo(Hypothesis):        # หรือ subclass TrendFollowHTF ถ้าใช้ backtest จริง
    name = "my_hypo"
    question = "..."             # คำถามที่ทดสอบ (ไทย)
    neutral = False              # True = market-neutral → benchmark เป็น cash
    def param_grid(self): return [{"lookback": L} for L in (7, 14, 30)]
    def load(self): ...          # คืน DataBundle
    def run(self, data, params): return dates, returns, positions   # รายวัน
    def benchmark(self, data): return _equal_weight_market(data)
```

### 2. ทดสอบในเครื่อง (ห้ามเขียน edge_runs)
```bash
DATABASE_URL= PYTHONPATH=/d/money-project python scratch_experiment.py
```
เรียก `evaluate(MyHypo(), trials_before=N)` ตรงๆ — **ไม่ persist** (ต่างจาก `watch.run_once` ที่เขียน edge_runs) · ถ้าต้องใช้ข้อมูลจาก Supabase (เช่น OI 6 ปี) รันโดยมี DATABASE_URL ได้ เพราะ `evaluate()` อ่านอย่างเดียว

### 3. อ่านผล
ผ่าน = **ทุกข้อพร้อมกัน**: OOS Sharpe > required_sharpe (ปรับตาม trials) · ชนะ benchmark ≥ 0.25 · DD < 60% · บวก ≥ ครึ่งของ fold · Sharpe ≤ 4.0 (เกินนี้ = ลืมนับความเสี่ยง เช่น basis risk)

### 4. ⚠️ เจอตัวผ่าน → stress-test 3 ด่าน (สำคัญสุด)
**อย่าเชื่อ / อย่า commit จนกว่าจะผ่านทั้ง 3:**
- **bar ที่ซื่อสัตย์** — รันซ้ำด้วย `trials_before` = จำนวนจริงที่ลองใน session (ไม่ใช่แค่ 7) · ยังผ่านไหม?
- **drop-one** — เอา component/เหรียญออกทีละตัว · edge กระจายหรือมาจากตัวเดียว (ฟลุค)?
- **แยกช่วงเวลา** — Sharpe ทุกช่วง (ต้น/กลาง/ปลาย) หรือกระจุกช่วงเดียว? **ช่วงล่าสุดสำคัญสุด** (เสื่อม = อย่าเทรด)

ตัวอย่างจริง: `price_oi_confirm` 10 เหรียญ ผ่าน 0.99 ที่ trials=7 → ล้มทั้ง 3 ด่าน (bar จริง 1.12, กระจุกที่ SEI, เสื่อมปี 2024-26) = mirage

### 5. หลังผ่าน (ถ้าผ่านจริง)
รายงาน "พร้อม" → รอ "อนุมัติ commit" → เพิ่มเข้า REGISTRY (จะรันบน Railway อัตโนมัติทุกสัปดาห์) · **ยังห้ามเทรดเงินจริงจนกว่าจะ paper เงินเล็กพิสูจน์ execution**

## อ้างอิง
`research/lab/evaluate.py` (เกณฑ์ + guard) · `research/lab/hypotheses.py` (ตัวอย่าง) · memory `edge-research-state` (อะไรลองแล้วล้มบ้าง — อย่าทำซ้ำ)
