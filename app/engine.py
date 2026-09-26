"""Engine: owns the stores, the composer, the responder and the tick policy.

Latency design: compositions are *pre-computed* in the background the moment a trigger
(or a context it depends on) is pushed, keyed by the versions of every input. /v1/tick then
serves from cache; anything missing is composed live within the remaining budget, and any
LLM miss falls back to the deterministic template — so tick never times out.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import datetime, timedelta

from .compose.composer import Composer, Composition
from .config import settings
from .conversation.responder import Responder
from .conversation.state import Conversation, StateStore
from .llm.groq import LLMClient
from .store import ContextStore
from .timeutil import iso, parse_ts, utcnow

log = logging.getLogger(__name__)

# Kinds where acting late is worse than acting at all (get a priority bump).
TIME_CRITICAL = {"supply_alert", "appointment_tomorrow", "ipl_match_today", "chronic_refill_due", "regulation_change",
                 "active_planning_intent", "renewal_due", "perf_dip", "recall_due"}
MIN_GAP_MINUTES = 10  # spacing between proactive merchant-facing sends to the same merchant (unless urgent)


class Engine:
    def __init__(self) -> None:
        self.started = time.time()
        self.store = ContextStore()
        self.state = StateStore()
        self.llm = LLMClient(settings.groq_api_key, settings.groq_base_url, settings.llm_models,
                             settings.llm_call_timeout, settings.llm_max_concurrency,
                             cache_path=(settings.state_dir / "llm_cache.json") if settings.persist_snapshots else None)
        if not settings.llm_enabled:
            self.llm.enabled = False
        self.composer = Composer(self.llm)
        self.responder = Responder(self.store, self.state, self.llm)
        self.cache: dict[str, tuple[tuple, Composition]] = {}
        self.inflight: dict[str, asyncio.Task] = {}
        self.last_now: datetime | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self._precompose_sem: asyncio.Semaphore | None = None
        self._dirty = False
        self.store.on_update(self._on_context)

    # ------------------------------------------------------------------ lifecycle
    async def startup(self) -> None:
        self.loop = asyncio.get_running_loop()
        self._precompose_sem = asyncio.Semaphore(2)
        if settings.use_seed_fallback:
            self.store.load_seed(settings.seed_dir)
        snap = settings.state_dir / "snapshot.json"
        if settings.persist_snapshots and snap.exists() and time.time() - snap.stat().st_mtime < 2 * 3600:
            try:
                data = json.loads(snap.read_text(encoding="utf-8"))
                self.store.restore(data.get("store", {}))
                self.state.restore(data.get("state", {}))
                log.info("restored snapshot: %s", self.store.counts())
            except Exception:
                log.exception("snapshot restore failed")
        if settings.persist_snapshots:
            asyncio.create_task(self._snapshot_loop())

    async def shutdown(self) -> None:
        self._write_snapshot()
        await self.llm.aclose()

    async def _snapshot_loop(self) -> None:
        while True:
            await asyncio.sleep(20)
            if self._dirty:
                self._write_snapshot()

    def _write_snapshot(self) -> None:
        if not settings.persist_snapshots:
            return
        try:
            settings.state_dir.mkdir(parents=True, exist_ok=True)
            data = {"store": self.store.snapshot(), "state": self.state.snapshot()}
            tmp = settings.state_dir / "snapshot.tmp"
            tmp.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
            tmp.replace(settings.state_dir / "snapshot.json")
            self._dirty = False
        except Exception:
            log.exception("snapshot write failed")

    def teardown(self) -> None:
        for t in self.inflight.values():
            t.cancel()
        self.inflight.clear()
        self.cache.clear()
        self.store.clear()
        self.state.clear()
        self.llm.clear_cache()
        for f in ("snapshot.json", "snapshot.tmp"):
            (settings.state_dir / f).unlink(missing_ok=True)

    # ------------------------------------------------------------------ context resolution
    def resolve(self, trg: dict) -> tuple[dict | None, dict | None, dict | None]:
        merchant = self.store.get("merchant", trg.get("merchant_id"))
        category = None
        if merchant:
            category = self.store.get("category", merchant.get("category_slug") or _infer_slug(merchant.get("merchant_id", "")))
        customer = self.store.get("customer", trg.get("customer_id")) if trg.get("customer_id") else None
        return category, merchant, customer

    def version_key(self, tid: str, trg: dict, now: datetime | None) -> tuple:
        merchant = self.store.get("merchant", trg.get("merchant_id")) or {}
        return (self.store.version("trigger", tid), self.store.version("merchant", trg.get("merchant_id")),
                self.store.version("category", merchant.get("category_slug")),
                self.store.version("customer", trg.get("customer_id")), (now or utcnow()).date().isoformat())

    # ------------------------------------------------------------------ precompose
    def _on_context(self, scope: str, cid: str, payload: dict) -> None:
        self._dirty = True
        if not settings.precompose or self.loop is None:
            return
        if scope == "trigger":
            self._schedule(cid)
            return
        for tid, trg in self.store.items("trigger").items():
            if tid in self.state.sent_triggers:
                continue
            if scope == "merchant" and trg.get("merchant_id") in (cid, payload.get("merchant_id")):
                self._schedule(tid)
            elif scope == "customer" and trg.get("customer_id") in (cid, payload.get("customer_id")):
                self._schedule(tid)
            elif scope == "category":
                m = self.store.get("merchant", trg.get("merchant_id")) or {}
                if m.get("category_slug") in (cid, payload.get("slug")):
                    self._schedule(tid)

    def _schedule(self, tid: str) -> None:
        old = self.inflight.get(tid)
        if old and not old.done():
            old.cancel()
        try:
            self.inflight[tid] = self.loop.create_task(self._precompose(tid))
        except RuntimeError:
            pass

    async def _precompose(self, tid: str) -> Composition | None:
        await asyncio.sleep(0.05)  # let bursts of pushes settle
        async with self._precompose_sem:
            trg = self.store.get("trigger", tid)
            if not trg:
                return None
            category, merchant, customer = self.resolve(trg)
            if not (category and merchant):
                return None
            entry = self.store.get_entry("trigger", tid) or {}
            now = self.last_now or parse_ts(entry.get("delivered_at")) or parse_ts(settings.default_now)
            key = self.version_key(tid, trg, now)
            hit = self.cache.get(tid)
            if hit and hit[0] == key:
                return hit[1]
            comp = await self.composer.compose(category, merchant, trg, customer, now, deadline=time.monotonic() + 25)
            self.cache[tid] = (key, comp)
            return comp

    async def composition_for(self, tid: str, trg: dict, now: datetime, deadline: float) -> Composition | None:
        category, merchant, customer = self.resolve(trg)
        if not (category and merchant):
            return None
        key = self.version_key(tid, trg, now)
        hit = self.cache.get(tid)
        if hit and hit[0] == key:
            return hit[1]
        task = self.inflight.get(tid)
        if task and not task.done():
            try:
                comp = await asyncio.wait_for(asyncio.shield(task), timeout=max(0.0, deadline - time.monotonic() - 0.4))
                hit = self.cache.get(tid)
                if comp and hit and hit[0] == key:
                    return comp
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
            except Exception:
                log.exception("precompose task failed")
        try:
            remaining = deadline - time.monotonic()
            if remaining > 2.0:
                comp = await asyncio.wait_for(
                    self.composer.compose(category, merchant, trg, customer, now, deadline=deadline - 0.4),
                    timeout=max(0.1, remaining - 0.2))
            else:
                comp = await self.composer.compose(category, merchant, trg, customer, now, use_llm=False)
        except asyncio.TimeoutError:
            comp = await self.composer.compose(category, merchant, trg, customer, now, use_llm=False)
        self.cache[tid] = (key, comp)
        return comp

    # ------------------------------------------------------------------ tick
    async def tick(self, now_str: str | None, available: list[str]) -> dict:
        t0 = time.monotonic()
        deadline = t0 + settings.tick_budget
        now = parse_ts(now_str) or utcnow()
        self.last_now = now
        candidates = []
        for tid in dict.fromkeys(available or []):
            trg = self.store.get("trigger", tid)
            if not trg or tid in self.state.sent_triggers:
                continue
            sk = trg.get("suppression_key")
            if sk and sk in self.state.sent_suppression:
                continue
            category, merchant, customer = self.resolve(trg)
            if not (merchant and category):
                continue
            mid = merchant.get("merchant_id") or trg.get("merchant_id")
            audience = "customer" if (customer and (trg.get("scope") == "customer" or trg.get("customer_id"))) else "merchant"
            mstate = self.state.merchant(mid)
            if mstate.opted_out:
                continue
            if audience == "customer":
                cstate = self.state.customer(customer.get("customer_id"))
                if cstate.opted_out or cstate.backing_off(now):
                    continue
            elif mstate.backing_off(now):
                continue
            urgency = int(trg.get("urgency") or 1)
            prio = urgency * 10 + (4 if trg.get("kind") in TIME_CRITICAL else 0) + (2 if audience == "customer" else 0)
            exp = parse_ts(trg.get("expires_at"))
            if exp and now and timedelta(0) <= exp - now <= timedelta(hours=36):
                prio += 3
            candidates.append((prio, tid, trg, mid, audience, customer))
        candidates.sort(key=lambda c: (-c[0], c[1]))

        chosen, seen_merchant, seen_customer = [], set(), set()
        for prio, tid, trg, mid, audience, customer in candidates:
            if audience == "merchant":
                if mid in seen_merchant:
                    continue
                last = parse_ts(self.state.merchant(mid).last_sent_at)
                if last and now and now - last < timedelta(minutes=MIN_GAP_MINUTES) and int(trg.get("urgency") or 1) < 4:
                    continue
                seen_merchant.add(mid)
            else:
                cid = customer.get("customer_id")
                if cid in seen_customer:
                    continue
                seen_customer.add(cid)
            chosen.append((tid, trg, mid, audience, customer))
            if len(chosen) >= 20:
                break

        comps = await asyncio.gather(*[self.composition_for(tid, trg, now, deadline) for tid, trg, *_ in chosen],
                                     return_exceptions=True)
        actions = []
        for (tid, trg, mid, audience, customer), comp in zip(chosen, comps):
            if isinstance(comp, Exception) or comp is None:
                if isinstance(comp, Exception):
                    log.error("compose failed for %s: %r", tid, comp)
                continue
            if comp.plan.skip_reason:
                log.info("skipping %s: %s", tid, comp.plan.skip_reason)
                continue
            actions.append(self._record_send(tid, trg, mid, audience, customer, comp, now))
        self._dirty = True
        log.info("tick %s: %d candidates -> %d actions in %.2fs", now_str, len(candidates), len(actions), time.monotonic() - t0)
        return {"actions": actions}

    def _record_send(self, tid, trg, mid, audience, customer, comp: Composition, now: datetime) -> dict:
        msg = comp.to_message()
        cust_id = customer.get("customer_id") if (customer and audience == "customer") else None
        conv_id = self._conversation_id(mid, cust_id, trg)
        slots = [s.get("label") for s in ((trg.get("payload") or {}).get("available_slots") or []) if s.get("label")]
        conv = Conversation(id=conv_id, merchant_id=mid, customer_id=cust_id, trigger_id=tid, kind=trg.get("kind", "unknown"),
                            audience=audience, send_as=msg["send_as"], lang=comp.plan.lang, salutation=comp.plan.salutation,
                            action_offer=comp.plan.action_offer, cta_type=comp.plan.cta_type, facts=comp.plan.facts,
                            slots=slots, created_at=iso(now))
        conv.add("bot", msg["body"], iso(now))
        with self.state.lock:
            self.state.conversations[conv_id] = conv
            self.state.sent_triggers.add(tid)
            if msg.get("suppression_key"):
                self.state.sent_suppression.add(msg["suppression_key"])
            party = self.state.customer(cust_id) if cust_id else self.state.merchant(mid)
            party.last_sent_at = iso(now)  # customer sends don't count against merchant spacing
            party.sends += 1
        return {"conversation_id": conv_id, "merchant_id": mid, "customer_id": cust_id, "send_as": msg["send_as"],
                "trigger_id": tid, "template_name": msg["template_name"], "template_params": msg["template_params"],
                "body": msg["body"], "cta": msg["cta"], "suppression_key": msg["suppression_key"],
                "rationale": msg["rationale"]}

    def _conversation_id(self, mid: str, cust_id: str | None, trg: dict) -> str:
        who = cust_id or mid or "m"
        short = re.sub(r"[^a-z0-9_]", "", who.lower())[:28]
        kind = re.sub(r"[^a-z0-9_]", "", str(trg.get("kind", "msg")).lower())[:24]
        h = hashlib.sha1(f"{trg.get('id')}|{trg.get('suppression_key')}".encode()).hexdigest()[:6]
        cid = f"conv_{short}_{kind}_{h}"
        n = 2
        while cid in self.state.conversations:
            cid = f"conv_{short}_{kind}_{h}_{n}"
            n += 1
        return cid

    # ------------------------------------------------------------------ reply
    async def reply(self, req: dict) -> dict:
        deadline = time.monotonic() + settings.reply_budget
        try:
            out = await asyncio.wait_for(self.responder.handle(req, deadline=deadline), timeout=settings.reply_budget + 1.5)
        except asyncio.TimeoutError:
            log.error("reply timed out for %s", req.get("conversation_id"))
            out = {"action": "wait", "wait_seconds": 1800, "rationale": "Taking a moment to prepare a proper answer; will follow up."}
        self._dirty = True
        return out


def _infer_slug(merchant_id: str) -> str | None:
    for key, slug in (("dentist", "dentists"), ("salon", "salons"), ("restaurant", "restaurants"),
                      ("gym", "gyms"), ("pharmac", "pharmacies")):
        if key in merchant_id:
            return slug
    return None
