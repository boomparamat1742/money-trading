"""Market Data Service (design §4.1) — live Binance WebSocket + REST gap recovery.

Streams closed klines, normalizes to the Candle model, auto-reconnects with
exponential backoff, and on every (re)connect backfills any candles missed
during the gap via REST (design §4.1, §6.2).
"""
from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, Optional

import os

import aiohttp
import websockets

from .data_quality import TIMEFRAME_MS
from .models import Candle

# spot vs USD-M futures (perp) — เราเทรด futures จึง default เป็น futures เพื่อให้
# สัญญาณคำนวณบน instrument เดียวกับที่เทรดจริง (majors basis ~0 แต่เหรียญเล็กต่างกว่า)
SPOT_WS = "wss://stream.binance.com:9443/ws"
SPOT_REST = "https://api.binance.com/api/v3/klines"
FUTURES_WS = "wss://fstream.binance.com/ws"
FUTURES_REST = "https://fapi.binance.com/fapi/v1/klines"


def futures_mode() -> bool:
    return os.environ.get("MARKET", "futures").lower() != "spot"


class MarketDataSource:
    async def stream_closed_candles(self, symbol: str, timeframe: str) -> AsyncIterator[Candle]:  # pragma: no cover
        raise NotImplementedError
        yield  # pragma: no cover


class BinanceSource(MarketDataSource):
    def __init__(self, exchange: str = "binance", market: Optional[str] = None):
        self.exchange = exchange
        fut = futures_mode() if market is None else (market.lower() != "spot")
        self.ws_base = FUTURES_WS if fut else SPOT_WS
        self.rest_klines = FUTURES_REST if fut else SPOT_REST
        self.market = "futures" if fut else "spot"
        # transport: fstream (futures WS) โดนบล็อกในบางภูมิภาค (เช่นไทย) — เราแค่
        # ต้องการแท่งที่ปิดทุก 15 นาที การ poll REST จึงทั้ง robust และพอเพียง
        # default: rest สำหรับ futures, ws สำหรับ spot · บังคับได้ด้วย FEED=ws|rest
        feed = os.environ.get("FEED", "auto").lower()
        self.feed = feed if feed in ("ws", "rest") else ("rest" if fut else "ws")
        self._last_open: Optional[int] = None  # last CLOSED candle open_time yielded

    def _to_candle(self, symbol: str, timeframe: str, k: dict) -> Candle:
        return Candle(
            exchange=self.exchange, symbol=symbol, timeframe=timeframe,
            open_time=int(k["t"]), open=float(k["o"]), high=float(k["h"]),
            low=float(k["l"]), close=float(k["c"]), volume=float(k["v"]),
            is_closed=bool(k["x"]),
        )

    async def _backfill(self, session: aiohttp.ClientSession, symbol: str,
                        timeframe: str) -> list[Candle]:
        """Fetch closed candles missed since the last one we yielded."""
        if self._last_open is None:
            return []
        import time as _t
        step = TIMEFRAME_MS[timeframe]
        now_ms = int(_t.time() * 1000)
        params = {"symbol": symbol, "interval": timeframe,
                  "startTime": self._last_open + step, "limit": 1000}
        try:
            async with session.get(self.rest_klines, params=params, timeout=aiohttp.ClientTimeout(total=20)) as r:
                rows = await r.json()
        except Exception:
            return []
        out: list[Candle] = []
        for row in rows:
            open_time = int(row[0])
            # closed only: the candle's period must be fully in the past
            if open_time + step > now_ms:
                continue
            out.append(Candle(
                exchange=self.exchange, symbol=symbol, timeframe=timeframe,
                open_time=open_time, open=float(row[1]), high=float(row[2]),
                low=float(row[3]), close=float(row[4]), volume=float(row[5]),
                is_closed=True,
            ))
        return out

    async def _fetch_recent(self, session: aiohttp.ClientSession, symbol: str,
                            timeframe: str, limit: int = 3) -> list[Candle]:
        """แท่งที่ปิดแล้วล่าสุด N แท่ง (ใช้ seed ตอนเริ่ม poll — backfill ต้องมี anchor)"""
        import time as _t
        step = TIMEFRAME_MS[timeframe]
        now_ms = int(_t.time() * 1000)
        params = {"symbol": symbol, "interval": timeframe, "limit": limit}
        try:
            async with session.get(self.rest_klines, params=params,
                                   timeout=aiohttp.ClientTimeout(total=20)) as r:
                rows = await r.json()
        except Exception:
            return []
        out = []
        for row in rows:
            ot = int(row[0])
            if ot + step > now_ms:          # ยังไม่ปิด
                continue
            out.append(Candle(exchange=self.exchange, symbol=symbol, timeframe=timeframe,
                              open_time=ot, open=float(row[1]), high=float(row[2]),
                              low=float(row[3]), close=float(row[4]), volume=float(row[5]),
                              is_closed=True))
        return sorted(out, key=lambda c: c.open_time)

    async def _poll_rest(self, symbol: str, timeframe: str) -> AsyncIterator[Candle]:
        """ดึงแท่งที่ปิดแล้วจาก REST ทุกครั้งหลังแท่งใหม่ปิด — ไม่พึ่ง WS (fstream
        อาจโดนบล็อก) เหมาะกับกลยุทธ์ที่ทำงานบนแท่งปิดทุก 15 นาทีอยู่แล้ว"""
        import time as _t
        step = TIMEFRAME_MS[timeframe]
        async with aiohttp.ClientSession() as session:
            if self._last_open is None:                 # seed anchor ให้ backfill
                for c in await self._fetch_recent(session, symbol, timeframe, limit=2):
                    self._last_open = c.open_time
                    yield c
            while True:
                now_ms = int(_t.time() * 1000)
                next_close = ((now_ms // step) + 1) * step
                await asyncio.sleep(max(1.0, (next_close - now_ms) / 1000 + 2))  # ตื่นหลังแท่งปิด 2 วิ
                got = await self._backfill(session, symbol, timeframe)
                if not got and self._last_open is None:
                    got = await self._fetch_recent(session, symbol, timeframe, limit=2)
                for c in got:
                    if self._last_open is None or c.open_time > self._last_open:
                        self._last_open = c.open_time
                        yield c

    async def stream_closed_candles(self, symbol: str, timeframe: str,
                                    closed_only: bool = True) -> AsyncIterator[Candle]:
        if self.feed == "rest":
            async for c in self._poll_rest(symbol, timeframe):
                yield c
            return
        url = f"{self.ws_base}/{symbol.lower()}@kline_{timeframe}"
        backoff = 1.0
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    async with websockets.connect(url, ping_interval=20, ping_timeout=20,
                                                  close_timeout=5) as ws:
                        backoff = 1.0  # reset on successful connect
                        # gap recovery for anything missed while disconnected
                        for c in await self._backfill(session, symbol, timeframe):
                            if self._last_open is None or c.open_time > self._last_open:
                                self._last_open = c.open_time
                                yield c
                        async for raw in ws:
                            msg = json.loads(raw)
                            k = msg.get("k")
                            if not k:
                                continue
                            candle = self._to_candle(symbol, timeframe, k)
                            if closed_only and not candle.is_closed:
                                continue
                            if candle.is_closed:
                                if self._last_open is not None and candle.open_time <= self._last_open:
                                    continue  # duplicate
                                self._last_open = candle.open_time
                            yield candle
                except (websockets.ConnectionClosed, OSError, asyncio.TimeoutError) as e:
                    print(f"[market_data] disconnected ({e!r}); reconnecting in {backoff:.0f}s")
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 60)  # exponential backoff, cap 60s
