"""วิเคราะห์ expectancy รายกลุ่ม feature จากไม้ที่ปิดแล้ว (journal)

ตอบคำถามจากไฟล์วิเคราะห์: "feature ไหนแยก winner/loser ได้?" — แบ่งไม้เป็นกลุ่มตาม
ค่า feature (trend_extension, adx_slope, vwap_zscore ฯลฯ) แล้ววัด win%/expectancy
ต่อกลุ่ม ถ้ากลุ่มค่าสูง/ต่ำ expectancy ต่างกันเป็นระบบ = feature นั้นมีข้อมูล

    python -m scripts.analyze_features

⚠️ ต้องมีไม้ที่เก็บ feature "หลายร้อย" ถึงจะเชื่อได้ — feature เพิ่งเริ่มเก็บ ตอนนี้
ยังน้อย ผลจะเป็น noise · เครื่องมือนี้สร้างไว้ให้พร้อมเมื่อข้อมูลโตพอ
และถ้ากลุ่มไหนดูดี **ต้องทดสอบ OOS ใน Edge Lab ก่อนใช้กรอง** ไม่ใช่ hard-code
"""
from __future__ import annotations

import sys

FEATURES = ["trend_extension", "ema20_dist_atr", "adx", "adx_slope", "macd_hist",
            "macd_hist_slope", "vwap_dist_pct", "vwap_zscore", "atr_percentile",
            "rsi", "vol_zscore", "vol_ratio"]
MIN_PER_BUCKET = 8            # ต่ำกว่านี้ไม่แสดง (noise เกิน)


def feature_buckets(pairs, feature, n=3):
    """pairs = [(features_dict, rr)] → n กลุ่มแบ่งตาม quantile ของ feature
    คืน None ถ้าข้อมูลไม่พอ (ต้อง ≥ n*MIN_PER_BUCKET)"""
    data = sorted((f[feature], rr) for f, rr in pairs
                  if f.get(feature) is not None and rr is not None)
    if len(data) < n * MIN_PER_BUCKET:
        return None
    size = len(data) / n
    out = []
    for i in range(n):
        chunk = data[int(i * size): (int((i + 1) * size) if i < n - 1 else len(data))]
        rrs = [rr for _, rr in chunk]
        vals = [v for v, _ in chunk]
        wins = sum(1 for r in rrs if r > 0)
        out.append({"lo": round(vals[0], 3), "hi": round(vals[-1], 3), "n": len(rrs),
                    "win": round(wins / len(rrs) * 100, 1),
                    "exp": round(sum(rrs) / len(rrs), 3)})
    return out


def _load_pairs(dsn):
    import psycopg
    out = []
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute("""SELECT entry_context, actual_rr FROM trades
                       WHERE status IN ('hit_tp','hit_sl','expired')
                       AND entry_context IS NOT NULL AND actual_rr IS NOT NULL""")
        for ec, rr in cur.fetchall():
            feats = (ec or {}).get("features") or {}
            if feats:
                out.append((feats, float(rr)))
    return out


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    from worker.app.store import database_url
    dsn = database_url()
    if not dsn:
        print("ต้องมี DATABASE_URL (ไม้จริงอยู่ใน Supabase)")
        return
    pairs = _load_pairs(dsn)
    print(f"\nไม้ที่ปิดแล้วและมี feature: {len(pairs)} ไม้")
    if len(pairs) < 3 * MIN_PER_BUCKET:
        print(f"⚠️ ยังน้อยเกินไป (ต้อง ≥ {3*MIN_PER_BUCKET}) — ผลจะเป็น noise")
        print("   ปล่อยระบบสะสมต่อ แล้วรันใหม่เมื่อครบหลายร้อยไม้")
        return
    overall = sum(rr for _, rr in pairs) / len(pairs)
    print(f"expectancy รวม: {overall:+.3f}R\n")
    for feat in FEATURES:
        b = feature_buckets(pairs, feat, n=3)
        if not b:
            continue
        print(f"── {feat}")
        for i, g in enumerate(["ต่ำ", "กลาง", "สูง"]):
            x = b[i]
            print(f"   {g:<5} [{x['lo']:+.2f}..{x['hi']:+.2f}] n={x['n']:>3} "
                  f"win {x['win']:>5.1f}% exp {x['exp']:+.3f}R")
        spread = b[-1]["exp"] - b[0]["exp"]
        print(f"   → ต่างสูง-ต่ำ {spread:+.3f}R {'(น่าสนใจ ทดสอบ OOS)' if abs(spread)>0.3 else '(ไม่ต่างชัด)'}\n")
    print("⚠️ in-sample — กลุ่มที่ดูดีต้องผ่าน Edge Lab OOS ก่อนใช้กรอง")


if __name__ == "__main__":
    main()
