"""AI Context Service (design §4.11) — SLOW PATH ONLY.

Runs after a Signal exists, never on the critical path. AI summarizes news,
flags conflicts, and explains — it never sets entry/SL/TP/size or blocks a
trade. If AI is disabled, times out, or returns bad JSON, the caller proceeds
with no AI context (design §4.11 last paragraph).

Uses the Claude Messages API with structured output. Model default:
claude-sonnet-5 (see docs — fast/cheap; opus-5 for deep analysis).
"""
from __future__ import annotations

import json
from typing import Any, Optional

from .config import Settings
from .models import Signal

PROMPT_VERSION = "ctx-1.0.0"

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "market_context": {"type": "string", "enum": ["bullish", "bearish", "neutral", "uncertain"]},
        "news_risk": {"type": "string", "enum": ["low", "medium", "high"]},
        "conflict_with_signal": {"type": "boolean"},
        "summary": {"type": "string"},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "confidence_note": {"type": "string"},
    },
    "required": ["market_context", "news_risk", "conflict_with_signal", "summary", "warnings", "confidence_note"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "You are a market-context assistant for a quant trading system. You do NOT make "
    "trading decisions, set prices, or size positions — the quant engine already did. "
    "Given a signal and recent news, summarize context, assess news risk, and flag any "
    "conflict between the news and the signal direction. Respond ONLY as JSON matching "
    "the provided schema. Be concise and state uncertainty honestly."
)


def build_user_prompt(sig: Signal, headlines: list[str]) -> str:
    return (
        f"Signal: {sig.symbol} {sig.direction.value} via {sig.strategy_name}, "
        f"score {sig.signal_score}, regime {sig.market_regime.get('regime')}, "
        f"entry {sig.entry_price}, SL {sig.stop_loss}, TP {sig.take_profit}.\n"
        f"Recent headlines:\n" + ("\n".join(f"- {h}" for h in headlines[:5]) or "- (none)")
    )


async def get_context(sig: Signal, headlines: list[str], settings: Settings) -> Optional[dict[str, Any]]:
    """Return structured AI context, or None on any failure (fail-open)."""
    if not settings.ai_enabled or not settings.anthropic_api_key:
        return None
    try:
        from anthropic import AsyncAnthropic  # imported lazily so core has no dep
    except Exception:
        return None

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    try:
        resp = await client.messages.create(
            model=settings.ai_model,
            max_tokens=600,
            system=[{"type": "text", "text": SYSTEM_PROMPT,
                     "cache_control": {"type": "ephemeral"}}],  # cache stable prefix
            output_config={"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
            messages=[{"role": "user", "content": build_user_prompt(sig, headlines)}],
        )
        if resp.stop_reason == "refusal":
            return None
        text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), None)
        return json.loads(text) if text else None
    except Exception:
        return None  # timeout / bad JSON / API down → proceed without AI
