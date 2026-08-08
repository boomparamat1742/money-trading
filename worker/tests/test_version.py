"""ตัวระบบต้องบอกได้เองว่ากำลังรัน commit ไหน — ไม่งั้นเวลา deploy แล้วสงสัย
ว่า "ขึ้นหรือยัง" ต้องไปเดาจากรูปแบบข้อความหรือคอลัมน์ในฐานข้อมูล"""
from worker.app import version


def _fresh():
    version.build_info.cache_clear()


def test_reads_railway_provided_commit(monkeypatch):
    _fresh()
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "9d86b8c1234567890abcdef")
    monkeypatch.setenv("RAILWAY_GIT_BRANCH", "main")
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_MESSAGE", "Say which position closed")
    b = version.build_info()
    assert b["short"] == "9d86b8c"
    assert b["where"] == "railway"
    line = version.build_line()
    assert "9d86b8c" in line and "main" in line
    _fresh()


def test_falls_back_to_local_git(monkeypatch):
    """รันในเครื่องต้องยังบอกได้ (ใน image ไม่มี .git จึงใช้ตัวแปรจาก Railway แทน)"""
    _fresh()
    monkeypatch.delenv("RAILWAY_GIT_COMMIT_SHA", raising=False)
    b = version.build_info()
    assert b["where"] == "local"
    assert len(b["short"]) in (7, len("unknown"))   # มี git ก็ได้ sha ไม่มีก็ unknown
    _fresh()


def test_never_raises_when_nothing_is_available(monkeypatch):
    """ไม่มี git ไม่มี env — ต้องคืนข้อความ ไม่ใช่ระเบิดตอน startup"""
    _fresh()
    monkeypatch.delenv("RAILWAY_GIT_COMMIT_SHA", raising=False)
    monkeypatch.setattr(version.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    assert "ไม่ทราบ commit" in version.build_line()
    _fresh()
