"""bucket() ของ calibration — จัดกลุ่มคะแนน + คำนวณ win%/expectancy ถูกต้อง"""
from scripts.calibrate_score import bucket


def test_buckets_by_score_range():
    pairs = [(62, 1.0), (63, -1.0), (72, 2.0), (73, 2.0)]
    b = bucket(pairs, edges=[60, 70, 80])
    assert b[0]["n"] == 2 and b[0]["win"] == 50.0        # 60-70: 1 win 1 loss
    assert b[1]["n"] == 2 and b[1]["win"] == 100.0       # 70-80: 2 wins


def test_expectancy_is_mean_rr():
    b = bucket([(72, 2.0), (73, -1.0)], edges=[70, 80])
    assert b[0]["exp"] == 0.5                            # (2 + -1)/2
    assert b[0]["avg_win"] == 2.0 and b[0]["avg_loss"] == -1.0


def test_score_100_falls_in_top_bucket():
    b = bucket([(100, 1.5)], edges=[90, 100])
    assert b[0]["n"] == 1                                # ขอบบน 100 ต้องนับ ไม่ตกขอบ


def test_empty_bucket_marked_zero():
    b = bucket([(95, 1.0)], edges=[60, 70, 100])
    assert b[0]["n"] == 0                                # 60-70 ว่าง
    assert b[1]["n"] == 1                                # 70-100 มี 1
