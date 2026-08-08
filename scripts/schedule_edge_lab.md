# ตั้งให้ Edge Lab รันเก็บผลอัตโนมัติ

ข้อมูลเป็นรายวัน → **สัปดาห์ละครั้งกำลังดี** (ถี่กว่านั้นข้อมูลใหม่เพิ่มไม่กี่แท่ง
ผลแทบไม่เปลี่ยน แต่เพิ่มโอกาสเจอผลบวกลวงจากการทดสอบซ้ำ)

## วิธีที่ 1 — Windows Task Scheduler (แนะนำ: ปิดคอมแล้วไม่หาย)

รันใน PowerShell **แบบ Run as Administrator**:

```powershell
$action  = New-ScheduledTaskAction -Execute "python" `
           -Argument "-m research.lab.watch --notify" -WorkingDirectory "D:\money-project"
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At 9am
# StartWhenAvailable: ถ้าคอมปิดอยู่ตอนถึงเวลา ให้รันชดเชยเมื่อเปิดเครื่อง
# ไม่ใส่ตัวนี้ = รอบนั้นหายไปเลย ซึ่งเป็นจุดอ่อนเดียวของการรันในเครื่องตัวเอง
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
           -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries
Register-ScheduledTask -TaskName "EdgeLab" -Action $action -Trigger $trigger `
           -Settings $settings -Description "รัน Edge Lab เก็บผลรายสัปดาห์"
```

> ต้องมี `.env` ที่มี `DATABASE_URL` อยู่ใน `D:\money-project` — Task Scheduler
> ไม่ได้สืบทอด environment จากเทอร์มินัลของคุณ ถ้าขาดจะเงียบๆ ไปเขียน SQLite แทน

ตรวจ / ลบ:
```powershell
Get-ScheduledTask -TaskName "EdgeLab"
Start-ScheduledTask -TaskName "EdgeLab"      # สั่งรันทันทีเพื่อทดสอบ
Unregister-ScheduledTask -TaskName "EdgeLab" -Confirm:$false
```

## วิธีที่ 2 — วนในเทอร์มินัล (ต้องเปิดหน้าต่างค้างไว้)

```bash
python -m research.lab.watch --loop 168 --notify
```

## ดูผลสะสม

```bash
python -m research.lab.run history
```

## สิ่งที่ระบบจะแจ้ง (ไม่สแปม)

| สถานการณ์ | แจ้งไหม |
|---|---|
| ไม่ผ่านเหมือนเดิม | ❌ เงียบ |
| ผ่านครั้งแรก | 🟡 แจ้ง + เตือนว่าอาจบังเอิญ |
| ผ่านต่อเนื่อง ≥3 ครั้ง | 🟢 แจ้ง — เริ่มน่าสนใจจริง |
| เคยผ่านแล้วหยุดผ่าน | 🔻 แจ้ง — edge อาจเสื่อม |
