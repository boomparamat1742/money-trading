"""feature_buckets — แบ่งไม้ตาม quantile ของ feature + วัด win%/expectancy"""
from scripts.analyze_features import feature_buckets


def _pairs(vals_rrs, feature="adx"):
    return [({feature: v}, rr) for v, rr in vals_rrs]


def test_returns_none_when_too_few():
    assert feature_buckets(_pairs([(1, 1.0)] * 5), "adx") is None    # < 3*8


def test_splits_into_three_quantile_groups():
    # 30 ไม้ ค่า adx 1..30, rr = +1 ถ้า adx>15 ไม่งั้น -1
    data = [(i, 1.0 if i > 15 else -1.0) for i in range(1, 31)]
    b = feature_buckets(_pairs(data), "adx", n=3)
    assert len(b) == 3
    assert b[0]["lo"] == 1 and b[2]["hi"] == 30
    # กลุ่มต่ำ (adx เล็ก) exp ติดลบ · กลุ่มสูง exp บวก
    assert b[0]["exp"] < 0 < b[2]["exp"]


def test_win_rate_computed_per_bucket():
    data = [(i, 1.0) for i in range(24)]          # ชนะทุกไม้
    b = feature_buckets(_pairs(data), "adx", n=3)
    assert all(g["win"] == 100.0 for g in b)


def test_skips_missing_feature_values():
    pairs = [({"adx": 10}, 1.0)] * 12 + [({"other": 5}, -1.0)] * 12   # 12 มี adx
    # 12 < 3*8=24 → None (นับเฉพาะที่มี adx)
    assert feature_buckets(pairs, "adx", n=3) is None
