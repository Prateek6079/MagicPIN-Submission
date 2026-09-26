"""Optional §7.4 deliverable: multi-turn handling as a plain function.

    respond(state, merchant_message) -> {"action": "send"|"wait"|"end", "body"?, "cta"?, "wait_seconds"?, "rationale"}

`state` is a dict describing the conversation so far:
    {"conversation_id": "...", "merchant_id": "...", "customer_id": None, "from_role": "merchant",
     "category": {...}, "merchant": {...}, "customer": {...} | None, "trigger": {...} | None,
     "history": [{"from": "vera"|"merchant"|"customer", "body": "..."}]}

It drives exactly the same Responder as the live /v1/reply endpoint (auto-reply detection,
opt-out/hostile exits, commit -> action mode, off-topic redirect, language mirroring).
"""
from __future__ import annotations

import asyncio

from app.conversation.responder import Responder
from app.conversation.state import Conversation, StateStore
from app.compose.plan import snapshot_facts
from app.compose.ctx import Ctx
from app.compose.planner import build_plan
from app.config import settings
from app.llm.groq import LLMClient
from app.store import ContextStore

_state = StateStore()
_store = ContextStore()


def _responder() -> Responder:
    llm = LLMClient(settings.groq_api_key, settings.groq_base_url, settings.llm_models, settings.llm_call_timeout)
    llm.enabled = settings.llm_enabled
    return Responder(_store, _state, llm)


def respond(state: dict, merchant_message: str) -> dict:
    cid = state.get("conversation_id") or "conv_local"
    merchant, category, customer, trigger = state.get("merchant"), state.get("category"), state.get("customer"), state.get("trigger")
    if merchant:
        _store.put("merchant", merchant.get("merchant_id", "m"), 10**9, merchant)
    if category:
        _store.put("category", category.get("slug", "c"), 10**9, category)
    if customer:
        _store.put("customer", customer.get("customer_id", "c"), 10**9, customer)
    if cid not in _state.conversations:
        if trigger and merchant and category:
            plan = build_plan(category, merchant, trigger, customer)
            conv = Conversation(id=cid, merchant_id=merchant.get("merchant_id"), customer_id=(customer or {}).get("customer_id"),
                                trigger_id=trigger.get("id"), kind=trigger.get("kind", "unknown"), audience=plan.audience,
                                send_as=plan.send_as, lang=plan.lang, salutation=plan.salutation, action_offer=plan.action_offer,
                                cta_type=plan.cta_type, facts=plan.facts,
                                slots=[s.get("label") for s in (trigger.get("payload") or {}).get("available_slots") or []])
        else:
            ctx = Ctx(category, merchant, {"kind": "conversation", "payload": {}}, customer)
            conv = Conversation(id=cid, merchant_id=(merchant or {}).get("merchant_id"), lang=ctx.lang,
                                salutation=ctx.merchant_salutation(), facts=snapshot_facts(ctx) if merchant else [])
        for h in state.get("history") or []:
            conv.add("bot" if h.get("from") == "vera" else h.get("from", "merchant"), h.get("body", ""))
        _state.conversations[cid] = conv
    req = {"conversation_id": cid, "merchant_id": (merchant or {}).get("merchant_id") or state.get("merchant_id"),
           "customer_id": (customer or {}).get("customer_id"), "from_role": state.get("from_role", "merchant"),
           "message": merchant_message}
    return asyncio.run(_responder().handle(req))
