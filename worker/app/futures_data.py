"""ดึงข้อมูล futures ที่ไม่ได้อยู่ในแท่งเทียน — Open Interest + funding rate.

Binance ให้ประวัติ OI ฟรีแค่ 30 วัน (endpoint openInterestHist) จึงย้อนหลังไม่ได้
ทางเดียวที่ได้ข้อมูลยาวพอทำวิจัย (walk-forward ต้องการ ~750 วัน) คือ **เก็บสดเอง**
ตั้งแต่ตอนนี้แล้วสะสมไป ฟังก์ชันนี้ดึง "ค่าปัจจุบัน" มาให้ main บันทึกทุกแท่ง

ทุกอย่าง best-effort: เน็ตล่ม/endpoint เปลี่ยน → คืน None ไม่โยน exception เข้า
live loop เพราะการเก็บข้อมูลวิจัยต้องไม่ทำให้ตัวเฝ้าตลาดพัง
"""
from __future__ import annotations

from typing import Optional

OPEN_INTEREST_URL = "https://fapi.binance.com/fapi/v1/openInterest"      # ค่าปัจจุบัน
PREMIUM_INDEX_URL = "https://fapi.binance.com/fapi/v1/premiumIndex"      # mark price + funding


async def fetch_oi_funding(symbol: str, timeout_s: float = 10.0) -> Optional[dict]:
    """คืน {open_interest, open_interest_value, funding_rate, mark_price} หรือ None

    open_interest = จำนวน contract คงค้าง (หน่วย base asset)
    open_interest_value = ประมาณมูลค่า notional = OI × mark price (USD)
    funding_rate = funding ล่าสุด (ผู้ถือ long จ่าย short เมื่อเป็นบวก)
    """
    import aiohttp

    try:
        timeout = aiohttp.ClientTimeout(total=timeout_s)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.get(OPEN_INTEREST_URL, params={"symbol": symbol}) as r:
                if r.status != 200:
                    return None
                oi_raw = await r.json()
            async with s.get(PREMIUM_INDEX_URL, params={"symbol": symbol}) as r:
                if r.status != 200:
                    return None
                px_raw = await r.json()
    except Exception:
        return None

    try:
        oi = float(oi_raw["openInterest"])
        mark = float(px_raw["markPrice"])
        funding = float(px_raw.get("lastFundingRate") or 0.0)
    except (KeyError, TypeError, ValueError):
        return None

    return {
        "open_interest": oi,
        "open_interest_value": oi * mark,
        "funding_rate": funding,
        "mark_price": mark,
    }
