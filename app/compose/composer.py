"""Composer: plan (deterministic) -> write (LLM) -> validate -> repair once -> fallback to the template draft."""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime

from ..config import settings
from ..llm.groq import LLMClient
from .ctx import Ctx
from .plan import Plan, tidy
from .planner import build_plan
from .prompts import compose_messages, repair_message
from .validator import validate

log = logging.getLogger(__name__)
HEALTH = {"dentists", "pharmacies"}


@dataclass
class Composition:
    plan: Plan
    body: str
    source: str            # "llm" | "template"
    model: str | None
    validation_errors: list[str]

    def template_params(self) -> list[str]:
        sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", self.body.replace("\n", " ")) if s.strip()]
        hook = sents[1] if len(sents) > 2 else (sents[0] if sents else self.body)
        return [self.plan.salutation or "", hook[:200], (sents[-1] if sents else "")[:200]]

    def to_message(self) -> dict:
        p = self.plan
        rationale = p.rationale
        return {
            "body": self.body,
            "cta": p.cta_type,
            "send_as": p.send_as,
            "suppression_key": p.suppression_key,
            "rationale": rationale[:700],
            "template_name": p.template_name,
            "template_params": self.template_params(),
        }


class Composer:
    def __init__(self, llm: LLMClient | None):
        self.llm = llm

    def plan(self, category, merchant, trigger, customer=None, now: datetime | None = None) -> tuple[Plan, Ctx]:
        ctx = Ctx(category, merchant, trigger, customer, now)
        return build_plan(category, merchant, trigger, customer, now), ctx

    async def compose(self, category, merchant, trigger, customer=None, now: datetime | None = None, *,
                      deadline: float | None = None, use_llm: bool = True) -> Composition:
        plan, ctx = self.plan(category, merchant, trigger, customer, now)
        taboos = ctx.taboos()
        health = ctx.slug in HEALTH
        tmpl_errors = validate(plan.draft, plan, taboos, health)
        if tmpl_errors:
            log.info("template draft for %s has soft issues: %s", plan.kind, tmpl_errors)
        if use_llm and self.llm and self.llm.enabled:
            body, model, errs = await self._write(plan, ctx, taboos, health, deadline)
            if body:
                return Composition(plan, body, "llm", model, errs)
        return Composition(plan, plan.draft, "template", None, tmpl_errors)

    async def _write(self, plan: Plan, ctx: Ctx, taboos, health, deadline) -> tuple[str | None, str | None, list[str]]:
        system, user = compose_messages(plan, ctx)
        models = [m for m in settings.compose_models if m in self.llm.models] or None
        out, meta = await self.llm.chat_json(system, user, deadline=deadline, max_tokens=600, models=models)
        body = _clean(out)
        if body is None:
            return None, None, ["llm unavailable"]
        errs = validate(body, plan, taboos, health)
        if not errs:
            return body, meta.get("model"), []
        if deadline is not None and deadline - time.monotonic() < 2.5:
            return None, None, errs
        repair_user = user + f"\n\nPREVIOUS ATTEMPT:\n\"\"\"{body}\"\"\"\n\n" + repair_message(errs)
        out2, meta2 = await self.llm.chat_json(system, repair_user, deadline=deadline, max_tokens=600, models=models)
        body2 = _clean(out2)
        if body2 is None:
            return None, None, errs
        errs2 = validate(body2, plan, taboos, health)
        if not errs2:
            return body2, meta2.get("model"), []
        log.info("llm output rejected twice for %s: %s", plan.kind, errs2)
        return None, None, errs2


def _clean(out: dict | None) -> str | None:
    if not out:
        return None
    body = out.get("body") or out.get("message") or out.get("text")
    if not isinstance(body, str) or not body.strip():
        return None
    body = body.strip().strip('"').strip()
    body = body.replace("\\n", "\n")
    return tidy(body)
