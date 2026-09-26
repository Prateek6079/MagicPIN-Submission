"""Groq (OpenAI-compatible) chat client built for the free tier.

- Model rotation: each Groq model has its own rate-limit bucket, so on 429 / low remaining
  tokens we cool that model down and move to the next one.
- Proactive throttling from x-ratelimit-* headers (don't wait to be 429'd mid-tick).
- Deadline-aware: never starts a call that can't finish inside the caller's budget.
- temperature=0 + fixed seed + a prompt-hash cache (optionally persisted) => deterministic.
- JSON output with tolerant parsing (and salvage of Groq's `failed_generation`).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections import Counter
from pathlib import Path

import httpx

log = logging.getLogger(__name__)


def extract_json(text: str | None) -> dict | None:
    if not text:
        return None
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        val = json.loads(text)
        return val if isinstance(val, dict) else None
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            val = json.loads(m.group(0))
            return val if isinstance(val, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _reset_seconds(val: str | None) -> float | None:
    """Groq reset headers look like '7.66s', '1m2.5s', '350ms'."""
    if not val:
        return None
    total, found = 0.0, False
    for num, unit in re.findall(r"([\d.]+)(ms|s|m|h)", val):
        found = True
        total += float(num) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[unit]
    return total if found else None


class LLMClient:
    def __init__(self, api_key: str, base_url: str, models: list[str], call_timeout: float = 9.0,
                 max_concurrency: int = 3, cache_path: Path | None = None):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.models = list(models)
        self.call_timeout = call_timeout
        self.enabled = bool(api_key) and bool(models)
        self._cool: dict[str, float] = {}
        self._dead: set[str] = set()
        self._no_json: set[str] = set()
        self._no_extras: set[str] = set()
        self._max_conc = max_concurrency
        self._sem: asyncio.Semaphore | None = None
        self._client: httpx.AsyncClient | None = None
        self.cache: dict[str, dict] = {}
        self.cache_path = cache_path
        self.stats: Counter = Counter()
        if cache_path and cache_path.exists():
            try:
                self.cache = json.loads(cache_path.read_text(encoding="utf-8"))
            except Exception:
                self.cache = {}

    # ------------------------------------------------------------------ plumbing
    def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=self.call_timeout,
                                             headers={"Authorization": f"Bearer {self.api_key}"})
        return self._client

    def _semaphore(self) -> asyncio.Semaphore:
        if self._sem is None:
            self._sem = asyncio.Semaphore(self._max_conc)
        return self._sem

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
        self.save_cache()

    def save_cache(self) -> None:
        if not self.cache_path:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(self.cache, ensure_ascii=False), encoding="utf-8")
        except Exception:
            log.exception("could not persist LLM cache")

    def clear_cache(self) -> None:
        self.cache.clear()
        if self.cache_path and self.cache_path.exists():
            self.cache_path.unlink(missing_ok=True)

    def available_models(self, subset: list[str] | None = None) -> list[str]:
        now = time.monotonic()
        return [m for m in (subset or self.models) if m not in self._dead and self._cool.get(m, 0) <= now]

    async def wait_available(self, max_wait: float = 90.0, subset: list[str] | None = None) -> bool:
        """Offline use only: sleep until some model's cooldown ends (never used on the live request path)."""
        end = time.monotonic() + max_wait
        while time.monotonic() < end:
            if self.available_models(subset):
                return True
            live = [self._cool.get(m, 0) for m in (subset or self.models) if m not in self._dead]
            if not live:
                return False
            await asyncio.sleep(max(0.5, min(min(live) - time.monotonic(), end - time.monotonic())))
        return bool(self.available_models(subset))

    def _body(self, model: str, system: str, user: str, max_tokens: int, temperature: float) -> dict:
        body: dict = {"model": model, "temperature": temperature, "seed": 7,
                      "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if model not in self._no_json:
            body["response_format"] = {"type": "json_object"}
        m = model.lower()
        if model not in self._no_extras:
            if "gpt-oss" in m:
                body["reasoning_effort"] = "low"
                body["include_reasoning"] = False
            elif "qwen" in m:
                body["reasoning_format"] = "hidden"
        body["max_tokens"] = max_tokens + (900 if "gpt-oss" in m else 0)
        return body

    def _throttle_from_headers(self, model: str, headers: httpx.Headers) -> None:
        try:
            rem_tok = int(headers.get("x-ratelimit-remaining-tokens", "999999"))
            rem_req = int(headers.get("x-ratelimit-remaining-requests", "999999"))
        except ValueError:
            return
        if rem_tok < 2500:
            wait = _reset_seconds(headers.get("x-ratelimit-reset-tokens")) or 20.0
            self._cool[model] = time.monotonic() + min(wait, 60)
        if rem_req < 3:
            wait = _reset_seconds(headers.get("x-ratelimit-reset-requests")) or 300.0
            self._cool[model] = time.monotonic() + min(wait, 3600)

    # ------------------------------------------------------------------ main call
    async def chat_json(self, system: str, user: str, *, max_tokens: int = 700, temperature: float = 0.0,
                        deadline: float | None = None, models: list[str] | None = None) -> tuple[dict | None, dict]:
        """Returns (parsed_json | None, meta). `deadline` is an absolute time.monotonic() value."""
        if not self.enabled:
            return None, {"model": None, "reason": "llm disabled"}
        key = hashlib.sha256(f"{system}\x00{user}\x00{temperature}".encode()).hexdigest()
        if key in self.cache:
            self.stats["cache_hit"] += 1
            hit = self.cache[key]
            return hit.get("json"), {"model": hit.get("model"), "cached": True}
        order = models or self.models
        for model in order:
            if model in self._dead or self._cool.get(model, 0) > time.monotonic():
                continue
            for _attempt in range(2):  # second attempt only after dropping unsupported params
                remaining = (deadline - time.monotonic()) if deadline else self.call_timeout
                if remaining < 1.2:
                    self.stats["deadline_skip"] += 1
                    return None, {"model": None, "reason": "deadline"}
                timeout = min(remaining, self.call_timeout)
                body = self._body(model, system, user, max_tokens, temperature)
                try:
                    async with self._semaphore():
                        r = await self._http().post("/chat/completions", json=body, timeout=timeout)
                except (httpx.TimeoutException, httpx.TransportError) as e:
                    self.stats["timeout"] += 1
                    self._cool[model] = time.monotonic() + 8
                    log.warning("llm %s timeout/transport: %s", model, e.__class__.__name__)
                    break
                self._throttle_from_headers(model, r.headers)
                if r.status_code == 200:
                    try:
                        data = r.json()
                        content = data["choices"][0]["message"].get("content") or ""
                    except Exception:
                        self.stats["bad_response"] += 1
                        break
                    parsed = extract_json(content)
                    if parsed is None:
                        self.stats["bad_json"] += 1
                        break
                    self.stats[f"ok:{model}"] += 1
                    usage = data.get("usage") or {}
                    self.stats["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
                    self.stats["completion_tokens"] += int(usage.get("completion_tokens") or 0)
                    self.cache[key] = {"json": parsed, "model": model}
                    return parsed, {"model": model, "usage": data.get("usage")}
                text = r.text[:500]
                if r.status_code == 429:
                    retry = r.headers.get("retry-after")
                    try:
                        wait = float(retry) if retry else 20.0
                    except ValueError:
                        wait = 20.0
                    self._cool[model] = time.monotonic() + min(wait, 120)
                    self.stats["429"] += 1
                    log.warning("llm %s rate-limited for %.0fs", model, wait)
                    break
                if r.status_code in (400, 422):
                    try:
                        err = r.json().get("error", {})
                    except Exception:
                        err = {}
                    salvage = extract_json(err.get("failed_generation"))
                    if salvage is not None:
                        self.cache[key] = {"json": salvage, "model": model}
                        return salvage, {"model": model, "salvaged": True}
                    msg = str(err.get("message") or text).lower()
                    if "decommission" in msg or "does not exist" in msg or "not found" in msg:
                        self._dead.add(model)
                        break
                    if model not in self._no_extras and any(k in msg for k in ("reasoning", "include_reasoning", "unsupported", "not supported", "property")):
                        self._no_extras.add(model)
                        continue
                    if model not in self._no_json and ("response_format" in msg or "json" in msg):
                        self._no_json.add(model)
                        continue
                    self.stats["400"] += 1
                    log.warning("llm %s 400: %s", model, msg[:200])
                    break
                if r.status_code in (401, 403):
                    log.error("llm auth failed (%s) — disabling LLM", r.status_code)
                    self.enabled = False
                    return None, {"model": None, "reason": "auth"}
                if r.status_code == 404:
                    self._dead.add(model)
                    break
                self._cool[model] = time.monotonic() + 5
                self.stats[f"http_{r.status_code}"] += 1
                break
        return None, {"model": None, "reason": "all models unavailable"}
