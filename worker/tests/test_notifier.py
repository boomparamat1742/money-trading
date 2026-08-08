import asyncio

from worker.app.notifier import DailyQuota, Notifier, suggest_leverage


class _Fake(Notifier):
    def __init__(self):
        self.sent = []

    async def send(self, text: str) -> bool:
        self.sent.append(text)
        return True


def test_daily_quota_caps_messages():
    inner = _Fake()
    q = DailyQuota(inner, max_per_day=3)
    results = [asyncio.run(q.send(f"m{i}")) for i in range(5)]
    assert results == [True, True, True, False, False]
    assert len(inner.sent) == 3          # only 3 actually delivered
    assert "โควตา" in inner.sent[-1]      # last delivered message warns about the cap


def test_daily_quota_resets_next_day(monkeypatch):
    inner = _Fake()
    q = DailyQuota(inner, max_per_day=1)
    assert asyncio.run(q.send("a")) is True
    assert asyncio.run(q.send("b")) is False
    q._day -= 1                           # simulate the day rolling over
    assert asyncio.run(q.send("c")) is True
    assert len(inner.sent) == 2


def test_leverage_capped_and_never_below_one():
    # very tight stop → math allows huge leverage, but the cap must bind
    lev = suggest_leverage(entry=100.0, stop=99.9)
    assert lev["leverage"] <= lev["cap"]
    # very wide stop → suggestion must still be a usable >= 1
    wide = suggest_leverage(entry=100.0, stop=40.0)
    assert wide["leverage"] >= 1


def test_leverage_none_without_prices():
    assert suggest_leverage(None, 97.0) is None
    assert suggest_leverage(100.0, 100.0) is None  # zero stop distance
