"""BinanceSource เลือก endpoint ตามตลาด — เราเทรด futures จึง default futures

เหตุที่ต้องล็อก: ถ้าดึงราคา spot มาคำนวณสัญญาณแต่เทรด perp = คำนวณบน instrument
ที่ไม่ได้เทรด (majors basis ~0 แต่เหรียญเล็กเหวี่ยงกว่า)
"""
from worker.app.market_data import (FUTURES_REST, FUTURES_WS, SPOT_REST,
                                    SPOT_WS, BinanceSource, futures_mode)


def test_default_is_futures():
    s = BinanceSource()
    assert s.market == "futures"
    assert s.ws_base == FUTURES_WS
    assert s.rest_klines == FUTURES_REST


def test_explicit_spot_uses_spot_endpoints():
    s = BinanceSource(market="spot")
    assert s.market == "spot"
    assert s.ws_base == SPOT_WS
    assert s.rest_klines == SPOT_REST


def test_env_can_force_spot(monkeypatch):
    monkeypatch.setenv("MARKET", "spot")
    assert futures_mode() is False
    assert BinanceSource().market == "spot"


def test_env_default_and_unknown_value_is_futures(monkeypatch):
    monkeypatch.delenv("MARKET", raising=False)
    assert futures_mode() is True
    monkeypatch.setenv("MARKET", "futures")
    assert BinanceSource().market == "futures"


def test_ws_url_built_from_selected_base():
    s = BinanceSource(market="futures")
    url = f"{s.ws_base}/{'BTCUSDT'.lower()}@kline_15m"
    assert url == "wss://fstream.binance.com/ws/btcusdt@kline_15m"


def test_feed_defaults_rest_for_futures_ws_for_spot(monkeypatch):
    """fstream (futures WS) โดนบล็อกบางภูมิภาค → futures ใช้ REST poll เป็น default"""
    monkeypatch.delenv("FEED", raising=False)
    assert BinanceSource(market="futures").feed == "rest"
    assert BinanceSource(market="spot").feed == "ws"


def test_feed_can_be_forced_by_env(monkeypatch):
    monkeypatch.setenv("FEED", "ws")
    assert BinanceSource(market="futures").feed == "ws"     # บังคับ WS ได้ถ้าภูมิภาคเปิด
    monkeypatch.setenv("FEED", "rest")
    assert BinanceSource(market="spot").feed == "rest"
