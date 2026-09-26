"""The Plan: everything the writer (LLM or template) needs, decided deterministically."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..timeutil import parse_ts
from . import fmt
from .ctx import Ctx

_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})(?:T[\d:.+\-Z]+)?\b")


def human_dates(text: str | None) -> str:
    """Rewrite ISO dates inside free text ('2026-12-15' -> '15 Dec 2026')."""
    if not text:
        return ""
    return _ISO_DATE.sub(lambda m: fmt.date_h(m.group(0)[:10], with_year=True) or m.group(0), str(text))


def tidy(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" +([.,!?;:])", r"\1", text)
    text = re.sub(r"\.\.+", ".", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"(^|\n) +", r"\1", text)
    return text.strip()


@dataclass
class Plan:
    kind: str
    family: str
    audience: str                 # merchant | customer
    send_as: str                  # vera | merchant_on_behalf
    lang: str                     # en | hinglish | hindi
    salutation: str | None
    facts: list[str]
    angle: str                    # the judgement / why-now framing
    levers: list[str]
    cta_type: str                 # binary_yes_no | binary_confirm_cancel | open_ended | multi_choice_slot | none
    draft: str                    # deterministic body (fallback + seed for the LLM)
    action_offer: str             # what we do if they say yes (English; drives multi-turn)
    template_name: str
    template_params: list[str]
    suppression_key: str
    rationale: str
    trigger_id: str | None = None
    merchant_id: str | None = None
    customer_id: str | None = None
    must_mention: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)
    extra_numbers: set[str] = field(default_factory=set)
    skip_reason: str | None = None
    placeholder: bool = False


def make_plan(ctx: Ctx, *, family: str, facts: list[str], angle: str, levers: list[str], cta_type: str,
              en: str, hi: str | None = None, action_offer: str, must: list[str] | None = None,
              avoid: list[str] | None = None, extra_numbers: set[str] | None = None) -> Plan:
    draft = tidy(ctx.L(en, hi or en))
    customer = ctx.audience == "customer"
    must = list(must or [])
    if customer:
        addressee, subject = ctx.customer_names()
        sal = addressee
        # customer-facing: must say who's writing, and whose appointment/medicine it is
        must += [x for x in (addressee, subject.replace(" ji", "") if subject else None, ctx.name.split()[0]) if x]
    else:
        sal = ctx.merchant_salutation()
    facts = [human_dates(f) for f in facts if f]
    facts.extend(snapshot_facts(ctx))
    sents = re.split(r"(?<=[.!?])\s+", draft)
    hook = sents[1] if len(sents) > 2 else (sents[0] if sents else draft)
    params = [sal or ctx.name, hook.strip()[:200], sents[-1].strip()[:200] if sents else ""]
    trig = ctx.t
    urg = trig.get("urgency")
    rationale = (f"{ctx.kind} ({trig.get('source', 'internal')}, urgency {urg}) → {angle} "
                 f"Levers: {', '.join(levers)}. CTA: {cta_type}. Language: {ctx.lang}.")
    return Plan(
        kind=ctx.kind, family=family, audience=ctx.audience,
        send_as="merchant_on_behalf" if customer else "vera", lang=ctx.lang, salutation=sal,
        facts=facts, angle=angle, levers=levers, cta_type=cta_type, draft=draft,
        action_offer=action_offer,
        template_name=f"{'merchant' if customer else 'vera'}_{re.sub(r'[^a-z0-9]+', '_', ctx.kind.lower())}_v1",
        template_params=params,
        suppression_key=trig.get("suppression_key") or f"{ctx.kind}:{ctx.m.get('merchant_id')}",
        rationale=rationale[:600],
        trigger_id=trig.get("id"), merchant_id=ctx.m.get("merchant_id"),
        customer_id=(ctx.c or {}).get("customer_id") if customer else None,
        must_mention=list(dict.fromkeys(x for x in must if x)), avoid=list(avoid or []),
        extra_numbers=set(extra_numbers or set()), placeholder=ctx.placeholder,
    )


def snapshot_facts(ctx: Ctx) -> list[str]:
    """Compact, always-true merchant (and customer) snapshot the writer may draw on."""
    out = []
    if ctx.now:
        out.append(f"Today: {fmt.date_h(ctx.now, with_year=True)} (anything in a later month is upcoming, not live)")
    loc = ", ".join(x for x in [ctx.locality, ctx.city] if x)
    out.append(f"Merchant: {ctx.name} ({ctx.slug}) in {loc}; owner/salutation: {ctx.merchant_salutation()}; "
               f"languages: {', '.join(ctx.langs) or 'en'}")
    p = ctx.perf
    if p:
        bits = [f"{k} {fmt.indian_int(p[k])}" for k in ("views", "calls", "directions", "leads") if isinstance(p.get(k), (int, float))]
        if isinstance(p.get("ctr"), (int, float)):
            bits.append(f"CTR {fmt.pct(p['ctr'])}")
        out.append(f"Last {p.get('window_days', 30)} days: " + ", ".join(bits))
        d = ctx.delta
        if d:
            out.append("7-day change: " + ", ".join(f"{k.replace('_pct', '')} {fmt.pct(v, signed=True)}"
                                                    for k, v in d.items() if isinstance(v, (int, float))))
    peers = ctx.cat.get("peer_stats") or {}
    if peers:
        pv = []
        for k, label in (("avg_views_30d", "views"), ("avg_calls_30d", "calls"), ("avg_ctr", "CTR"), ("avg_rating", "rating"), ("avg_review_count", "reviews")):
            v = peers.get(k)
            if isinstance(v, (int, float)):
                pv.append(f"{label} {fmt.pct(v) if k == 'avg_ctr' else (v if k == 'avg_rating' else fmt.indian_int(v))}")
        out.append(f"Peer averages ({fmt.humanize(peers.get('scope', 'peers'))}): " + ", ".join(pv))
    act = ctx.active_offers()
    out.append("Active offers: " + ("; ".join(act) if act else "none"))
    sub = ctx.sub()
    if sub:
        if sub.get("status") == "active":
            out.append(f"Subscription: {sub.get('plan')} plan, active, {sub.get('days_remaining')} days remaining")
        elif sub.get("status") == "expired":
            out.append(f"Subscription: expired {sub.get('days_since_expiry')} days ago")
        else:
            out.append(f"Subscription: {sub.get('status')} ({sub.get('days_remaining')} days remaining)")
    agg = ctx.agg()
    if agg:
        out.append("Customer base: " + ", ".join(
            f"{fmt.humanize(k)} {fmt.pct(v) if 'pct' in k else fmt.indian_int(v)}" for k, v in agg.items() if isinstance(v, (int, float))))
    rv = ctx.review_themes()
    if rv:
        out.append("Review themes (30d): " + "; ".join(
            f"{fmt.humanize(r.get('theme'))} ({r.get('sentiment')}, {r.get('occurrences_30d')}x)"
            + (f" e.g. \"{r['common_quote']}\"" if r.get("common_quote") else "") for r in rv))
    for h in ctx.history()[-2:]:
        d = fmt.date_h(h.get("ts"))
        out.append(f"History {d}: {h.get('from')} said \"{str(h.get('body'))[:160]}\"")
    if ctx.audience == "customer" and ctx.c:
        rel, cid = ctx.rel, ctx.cid
        lv = fmt.date_h(rel.get("last_visit"), with_year=True)
        svc = ctx.services()
        out.append(f"Customer: {cid.get('name')}, language {cid.get('language_pref')}, state {fmt.humanize(ctx.c.get('state', ''))}, "
                   f"visits {rel.get('visits_total')}, last visit {lv}" + (f", services: {', '.join(svc[-4:])}" if svc else "")
                   + (f", prefers {ctx.preferred_slot()}" if ctx.preferred_slot() else ""))
    return out


def days_until(ctx: Ctx, value) -> int | None:
    dt = parse_ts(value) if isinstance(value, str) else None
    if not dt or not ctx.now:
        return None
    d = (dt - ctx.now).days
    return d if d >= 0 else None
