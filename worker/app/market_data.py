"""Market Data Service (design §4.1) — live Binance WebSocket + REST gap recovery.

Streams closed klines, normalizes to the Candle model, auto-reconnects with
exponential backoff, and on every (re)connect backfills any candles missed
during the gap via REST (design §4.1, §6.2).
"""
from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, Optional

import aiohttp
import websockets

from .data_quality import TIMEFRAME_MS
from .models import Candle

WS_BASE = "wss://stream.binance.com:9443/ws"
REST_KLINES = "https://api.binance.com/api/v3/klines"


class MarketDataSource:
    async def stream_closed_candles(self, symbol: str, timeframe: str) -> AsyncIterator[Candle]:  # pragma: no cover
        raise NotImplementedError
        yield  # pragma: no cover


class BinanceSource(MarketDataSource):
    def __init__(self, exchange: str = "binance"):
        self.exchange = exchange
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
            async with session.get(REST_KLINES, params=params, timeout=aiohttp.ClientTimeout(total=20)) as r:
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

    async def stream_closed_candles(self, symbol: str, timeframe: str,
                                    closed_only: bool = True) -> AsyncIterator[Candle]:
        url = f"{WS_BASE}/{symbol.lower()}@kline_{timeframe}"
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
