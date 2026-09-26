"""Submission entry point.

1. HTTP bot:      uvicorn bot:app --host 0.0.0.0 --port 8080
2. compose():     the challenge-brief §7.1 function (dicts in -> dict out), deterministic.

compose() runs the same planner -> LLM writer -> validator -> fallback pipeline the HTTP bot uses.
With GROQ_API_KEY unset it is fully deterministic (template writer); with a key it uses temperature 0,
a fixed seed and a prompt-hash cache, so identical inputs give identical outputs.
"""
from __future__ import annotations

import asyncio
from datetime import datetime

from app.compose.composer import Composer
from app.config import settings
from app.llm.groq import LLMClient
from app.main import app  # noqa: F401  (re-exported for `uvicorn bot:app`)
from app.timeutil import parse_ts

_composer: Composer | None = None


def _get_composer() -> Composer:
    global _composer
    if _composer is None:
        llm = LLMClient(settings.groq_api_key, settings.groq_base_url, settings.llm_models, settings.llm_call_timeout,
                        settings.llm_max_concurrency, cache_path=settings.state_dir / "compose_cache.json")
        llm.enabled = settings.llm_enabled
        _composer = Composer(llm)
    return _composer


async def compose_async(category: dict, merchant: dict, trigger: dict, customer: dict | None = None,
                        now: datetime | str | None = None, use_llm: bool = True, timeout: float = 28.0) -> dict:
    import time
    now_dt = parse_ts(now) if isinstance(now, str) else now
    comp = await _get_composer().compose(category, merchant, trigger, customer, now_dt,
                                         deadline=time.monotonic() + timeout, use_llm=use_llm)
    msg = comp.to_message()
    return {k: msg[k] for k in ("body", "cta", "send_as", "suppression_key", "rationale", "template_name", "template_params")}


def compose(category: dict, merchant: dict, trigger: dict, customer: dict | None = None) -> dict:
    """Challenge contract: returns {body, cta, send_as, suppression_key, rationale} (+ template fields). < 30s."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        result = asyncio.run(compose_async(category, merchant, trigger, customer))
        _get_composer().llm.save_cache()
        return result
    raise RuntimeError("compose() is synchronous; inside an event loop use `await compose_async(...)`")
