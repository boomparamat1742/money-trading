"""News Service (design §4.10) — SCAFFOLD for Phase 5 (slow path).

Fetches headlines, caches them (Redis, ~5 min), deduplicates by hash, filters by
asset relevance and recency, and returns only relevant headlines to the AI
context service. Respects NEWS_DAILY_CALL_LIMIT (design §14).
"""
from __future__ import annotations

import hashlib
import time
from typing import Optional


def news_hash(title: str, url: str) -> str:
    return hashlib.sha256(f"{title}|{url}".encode()).hexdigest()[:16]


class NewsService:
    def __init__(self, provider: str = "cryptopanic", api_key: Optional[str] = None,
                 cache_ttl_sec: int = 300):
        self.provider = provider
        self.api_key = api_key
        self.cache_ttl = cache_ttl_sec
        self._cache: dict[str, tuple[float, list[str]]] = {}
        self._seen: set[str] = set()

    async def recent_headlines(self, symbol: str) -> list[str]:
        now = time.time()
        cached = self._cache.get(symbol)
        if cached and now - cached[0] < self.cache_ttl:
            return cached[1]
        headlines = await self._fetch(symbol)          # TODO(Phase 5): real provider call
        self._cache[symbol] = (now, headlines)
        return headlines

    async def _fetch(self, symbol: str) -> list[str]:  # pragma: no cover
        # TODO: aiohttp GET provider API, dedupe via news_hash, drop stale by
        # published_at, keep only asset-relevant items. Retry 2-3x (design §6.2).
        return []
