"""import_oi_to_supabase — parse ชื่อไฟล์/CSV + load_oi fallback เป็น CSV

ตัว parse ทดสอบได้โดยไม่ต้องต่อ DB (จุดที่พังง่ายคือแยก symbol/interval จากชื่อไฟล์)
"""
import os
import tempfile

import pytest

from research.lab.core import load_oi
from scripts.import_oi_to_supabase import parse_csv


def _write(path, rows):
    with open(path, "w", newline="") as f:
        f.write("open_time,oi_open,oi_high,oi_low,oi_close\n")
        for r in rows:
            f.write(",".join(str(x) for x in r) + "\n")


def test_parse_extracts_symbol_and_interval():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "BTCUSDT_1d_oi.csv")
    _write(p, [(1000, 9, 10, 8, 9.5), (2000, 11, 12, 10, 11.5)])
    sym, interval, rows = parse_csv(p)
    assert sym == "BTCUSDT" and interval == "1d"
    assert rows[0] == (1000, 9.0, 10.0, 8.0, 9.5)
    assert len(rows) == 2


def test_parse_rejects_bad_filename():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "randomfile.csv")
    _write(p, [(1, 2, 3, 4, 5)])
    with pytest.raises(ValueError):
        parse_csv(p)


def test_parse_skips_malformed_rows():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "ETHUSDT_4hour_oi.csv")
    with open(p, "w") as f:
        f.write("open_time,oi_open,oi_high,oi_low,oi_close\n")
        f.write("1000,9,10,8,9.5\n")
        f.write("bad,row,here,x,y\n")            # เสีย → ข้าม
    sym, interval, rows = parse_csv(p)
    assert sym == "ETHUSDT" and interval == "4hour"
    assert len(rows) == 1


def test_load_oi_reads_csv_when_no_database(monkeypatch, tmp_path):
    """DATABASE_URL ไม่ตั้ง → load_oi ต้อง fallback อ่าน CSV ในเครื่อง"""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    _write(str(tmp_path / "data" / "BTCUSDT_1d_oi.csv"),
           [(86_400_000, 9, 10, 8, 9.5), (172_800_000, 11, 12, 10, 11.5)])
    oi = load_oi(["BTC"], quiet=True)
    assert "BTC" in oi
    assert oi["BTC"][86_400_000] == 9.5        # day-aligned key → oi_close
