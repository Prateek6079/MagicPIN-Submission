"""Dispatch a (category, merchant, trigger, customer) tuple to the right planner."""
from __future__ import annotations

from datetime import datetime

from . import kinds_customer as kc
from . import kinds_merchant as km
from . import fmt
from .ctx import Ctx
from .plan import Plan, make_plan

MERCHANT_KINDS = {
    "research_digest": km.h_research, "research_digest_release": km.h_research,
    "category_research_digest_release": km.h_research,
    "regulation_change": km.h_compliance, "compliance_alert": km.h_compliance, "compliance_change": km.h_compliance,
    "cde_opportunity": km.h_cde, "webinar": km.h_cde,
    "supply_alert": km.h_supply_alert, "drug_recall": km.h_supply_alert,
    "category_trend_movement": km.h_trend, "trend_movement": km.h_trend,
    "category_seasonal": km.h_category_seasonal,
    "perf_dip": km.h_perf_dip, "perf_spike": km.h_perf_spike, "seasonal_perf_dip": km.h_seasonal_dip,
    "milestone_reached": km.h_milestone,
    "renewal_due": km.h_renewal, "winback_eligible": km.h_winback, "gbp_unverified": km.h_unverified,
    "dormant_with_vera": km.h_dormant,
    "festival_upcoming": km.h_festival, "ipl_match_today": km.h_ipl, "competitor_opened": km.h_competitor,
    "weather_heatwave": km.h_weather, "weather_alert": km.h_weather, "local_news_event": km.h_local_news,
    "review_theme_emerged": km.h_review_theme,
    "curious_ask_due": km.h_curious, "scheduled_recurring": km.h_curious,
    "active_planning_intent": km.h_planning,
}

CUSTOMER_KINDS = {
    "recall_due": kc.h_recall, "customer_lapsed_soft": kc.h_lapsed, "customer_lapsed_hard": kc.h_lapsed,
    "appointment_tomorrow": kc.h_appointment, "chronic_refill_due": kc.h_refill, "trial_followup": kc.h_trial_followup,
    "wedding_package_followup": kc.h_bridal, "bridal_followup": kc.h_bridal, "unplanned_slot_open": kc.h_slot_open,
}


MERCHANT_KINDS["new_review_posted"] = km.h_single_review
MERCHANT_KINDS["review_received"] = km.h_single_review

# Unknown kinds (the judge injects new triggers after submission) are routed by keywords in the kind name.
MERCHANT_KEYWORDS = [
    (("review",), km.h_single_review), (("weather", "heat", "rain", "monsoon", "flood", "cold_wave"), km.h_weather),
    (("festival", "holiday", "diwali", "holi", "eid", "christmas"), km.h_festival), (("ipl", "match", "cricket"), km.h_ipl),
    (("competitor", "rival"), km.h_competitor), (("regulat", "compliance", "circular", "mandate", "law"), km.h_compliance),
    (("recall", "shortage", "supply", "alert"), km.h_supply_alert), (("trend", "search", "demand"), km.h_trend),
    (("digest", "research", "study", "journal", "paper"), km.h_research), (("webinar", "cde", "training", "conference"), km.h_cde),
    (("dip", "drop", "decline", "fall"), km.h_perf_dip), (("spike", "surge", "jump", "growth"), km.h_perf_spike),
    (("milestone", "crossed", "anniversary"), km.h_milestone), (("renewal", "expir", "subscription"), km.h_renewal),
    (("dormant", "inactive", "silent"), km.h_dormant), (("unverified", "verification"), km.h_unverified),
    (("planning", "intent", "idea"), km.h_planning), (("ask", "question", "poll"), km.h_curious),
    (("news", "event", "closure", "traffic", "strike", "election"), km.h_local_news),
]
CUSTOMER_KEYWORDS = [
    (("appointment", "booking", "reminder"), kc.h_appointment), (("refill", "medicine", "prescription"), kc.h_refill),
    (("lapsed", "winback", "win_back", "inactive"), kc.h_lapsed), (("recall", "due", "checkup", "check_up"), kc.h_recall),
    (("trial",), kc.h_trial_followup), (("bridal", "wedding"), kc.h_bridal), (("slot", "cancellation"), kc.h_slot_open),
]


def _route(kind: str, table: dict, keywords: list, default):
    if kind in table:
        return table[kind]
    k = kind.lower()
    for words, handler in keywords:
        if any(w in k for w in words):
            return handler
    return default


def _customer_trigger_without_customer(ctx: Ctx) -> Plan:
    """Customer-scoped trigger but no CustomerContext: brief the merchant instead of guessing about the customer."""
    S = ctx.merchant_salutation()
    topic = fmt.humanize(ctx.kind)
    facts = [f"Customer-scoped trigger '{topic}' for customer id {ctx.t.get('customer_id')} (no customer profile available)"]
    en = f"{S}, one of your {ctx.nouns['people']} is flagged for {topic}. Want me to send them a reminder from your number?"
    hi = f"{S}, aapke ek {ctx.nouns['one']} ke liye {topic} flag hua hai. Aapke number se unhe reminder bhej doon?"
    return make_plan(ctx, family="customer_via_merchant", facts=facts,
                     angle="Customer event without a customer profile — ask the merchant before contacting anyone.",
                     levers=["effort externalisation", "single yes/no"], cta_type="binary_yes_no", en=en, hi=hi,
                     action_offer=f"send the {topic} reminder to the customer", must=[S])


def build_plan(category: dict | None, merchant: dict | None, trigger: dict | None,
               customer: dict | None = None, now: datetime | None = None) -> Plan:
    ctx = Ctx(category, merchant, trigger, customer, now)
    kind = ctx.kind
    customer_scope = (trigger or {}).get("scope") == "customer" or bool((trigger or {}).get("customer_id"))
    if ctx.audience == "customer":
        handler = _route(kind, CUSTOMER_KINDS, CUSTOMER_KEYWORDS, kc.h_generic_customer)
        plan = handler(ctx)
        if not ctx.customer_consented():
            plan.skip_reason = "customer has no recorded consent / opt-in"
        return plan
    if customer_scope:
        return _customer_trigger_without_customer(ctx)
    handler = _route(kind, MERCHANT_KINDS, MERCHANT_KEYWORDS, km.h_generic)
    return handler(ctx)
