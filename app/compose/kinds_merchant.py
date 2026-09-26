"""Merchant-facing planners (send_as = "vera").

Each planner:
  1. pulls only facts that exist in the contexts (never invents names/numbers),
  2. decides the "why now" angle — including contrarian judgement where the data supports it,
  3. writes a deterministic draft in English or Hinglish with exactly one CTA as the last sentence.
Placeholder triggers (payload has no specifics) derive their story from the merchant's real numbers.
"""
from __future__ import annotations

from . import fmt
from .ctx import Ctx, first_sentence, sentences
from .plan import Plan, days_until, human_dates, make_plan

SIGNAL_CAUSES = {
    "no_active_offers": ("there's no active offer on your listing", "listing par koi active offer nahi hai"),
    "unverified_gbp": ("your Google profile is still unverified", "Google profile abhi verified nahi hai"),
    "no_recent_post": ("there's been no recent Google post", "kaafi time se koi Google post nahi gaya"),
    "delivery_not_set_up": ("home delivery isn't set up on the listing", "listing par home delivery set nahi hai"),
}


def _stale_posts(ctx: Ctx) -> tuple[str, str] | None:
    d = ctx.signal_value("stale_posts")
    if d:
        return (f"your last Google post was {d} days ago", f"aapka last Google post {d} din pehle gaya tha")
    return None


def causes(ctx: Ctx, limit: int = 2) -> list[tuple[str, str]]:
    out = []
    sp = _stale_posts(ctx)
    if sp:
        out.append(sp)
    for key, pair in SIGNAL_CAUSES.items():
        if ctx.has_signal(key):
            out.append(pair)
    if not ctx.active_offers() and ("there's no active offer on your listing", "listing par koi active offer nahi hai") not in out:
        out.append(SIGNAL_CAUSES["no_active_offers"])
    if ctx.ident.get("verified") is False and SIGNAL_CAUSES["unverified_gbp"] not in out:
        out.append(SIGNAL_CAUSES["unverified_gbp"])
    return out[:limit]


def trial_clause(ctx: Ctx) -> tuple[str, str, str]:
    """(en, hi, fact) — a secondary 'why now' when the merchant's trial is about to end."""
    sub = ctx.sub()
    days = sub.get("days_remaining")
    if sub.get("status") == "trial" and isinstance(days, int) and 0 < days <= 10:
        return (f" With {days} days left on your trial, this is a quick way to see results before it ends.",
                f" Trial ke {days} din bache hain — khatam hone se pehle results dekhne ka yeh aasaan tareeka hai.",
                f"Trial ends in {days} days")
    return "", "", ""


def matching_offer(ctx: Ctx, text: str) -> str | None:
    """Offer whose words overlap the topic (active offers first, then the category catalog)."""
    import re as _re
    words = {w for w in _re.findall(r"[a-z]{4,}", text.lower()) if w not in {"near", "price", "delhi", "mumbai", "best", "cost"}}
    for pool in (ctx.active_offers(), [o.get("title") for o in ctx.catalog()]):
        for o in pool:
            if o and words & set(_re.findall(r"[a-z]{4,}", o.lower())):
                return o
    return ctx.active_offers()[0] if ctx.active_offers() else None


def _premium(offers: list[str]) -> str | None:
    """Highest-priced service+price offer (best anchor for a package)."""
    import re as _re
    priced = [(int(m.group(1).replace(",", "")), o) for o in offers if (m := _re.search(r"₹\s?([\d,]+)", o))]
    return max(priced)[1] if priced else (offers[0] if offers else None)


def join_and(items: list[str], hi: bool = False) -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return items[0] if items else ""
    return ", ".join(items[:-1]) + (" aur " if hi else " and ") + items[-1]


def cohort(ctx: Ctx, item: dict | None = None) -> tuple[str | None, str | None]:
    """A merchant-specific audience phrase grounded in customer_aggregate."""
    agg = ctx.agg()
    seg = str((item or {}).get("patient_segment") or (item or {}).get("segment_age") or "")
    if "high_risk" in seg and agg.get("high_risk_adult_count"):
        n = fmt.indian_int(agg["high_risk_adult_count"])
        return f"{n} high-risk adult patients", f"Merchant cohort: {n} high-risk adult patients on the roster"
    if agg.get("chronic_rx_count"):
        n = fmt.indian_int(agg["chronic_rx_count"])
        return f"{n} chronic-Rx customers", f"Merchant cohort: {n} chronic-prescription customers"
    if agg.get("total_active_members"):
        n = fmt.indian_int(agg["total_active_members"])
        return f"{n} active members", f"Merchant cohort: {n} active members"
    if "high_risk" in seg:
        return "high-risk adult patients", None
    if agg.get("total_unique_ytd"):
        n = fmt.indian_int(agg["total_unique_ytd"])
        return f"{n} {ctx.nouns['people']} this year", f"Merchant cohort: {n} unique {ctx.nouns['people']} year-to-date"
    return None, None


# ============================================================ knowledge family
def h_research(ctx: Ctx) -> Plan:
    item = ctx.digest_item() or ctx.pick_digest(("research", "trend", "tech", "compete"))
    if not item:
        return h_generic(ctx)
    S = ctx.merchant_salutation()
    title, src = human_dates(item.get("title")), human_dates(item.get("source") or "")
    sents = [human_dates(s) for s in sentences(item.get("summary"))]
    finding = sents[0] if sents else title
    caveat = sents[1] if len(sents) > 1 and len(sents[1]) <= 110 else ""
    n = item.get("trial_n")
    trial = f" (n={fmt.indian_int(n)})" if n else ""
    coh, coh_fact = cohort(ctx, item)
    facts = [f"Digest item ({item.get('kind')}): \"{title}\" — source: {src}",
             f"Trial size: {fmt.indian_int(n)} patients" if n else "",
             f"Summary: {human_dates(item.get('summary'))}",
             f"Suggested action: {item.get('actionable')}" if item.get("actionable") else "", coh_fact or ""]
    tie_en = f" Directly relevant to your {coh}." if coh else ""
    tie_hi = f" Aapke {coh} ke liye yeh seedha relevant hai." if coh else ""
    people = ctx.nouns["people"]
    en = (f"{S}, {src} just published: {title}. {finding.rstrip('.')}{trial}. {caveat}{tie_en} "
          f"Want me to pull the abstract and draft a short WhatsApp for your {people} explaining it?")
    hi = (f"{S}, {src} mein naya data aaya hai: {title}. {finding.rstrip('.')}{trial}. {caveat}{tie_hi} "
          f"Abstract + {people} ke liye ek chhota WhatsApp explainer draft kar doon?")
    return make_plan(ctx, family="knowledge", facts=facts,
                     angle=f"New {item.get('kind')} item from {src} with a concrete finding, tied to the merchant's own cohort.",
                     levers=["specificity (source + numbers)", "curiosity", "reciprocity (I'll pull + draft)", "single yes/no"],
                     cta_type="binary_yes_no", en=en, hi=hi,
                     action_offer=f"pull the abstract and draft a {ctx.nouns['one']}-friendly WhatsApp explainer",
                     must=[S], avoid=["Do not cite any source other than the one given.",
                                      "Do not claim outcomes beyond the summary (no 'cure', no guarantees)."])


def h_compliance(ctx: Ctx) -> Plan:
    item = ctx.digest_item() or ctx.pick_digest(("compliance", "alert"))
    if not item:
        return h_generic(ctx)
    S = ctx.merchant_salutation()
    title, src = human_dates(item.get("title")), human_dates(item.get("source") or "")
    summary = human_dates(item.get("summary"))
    deadline = ctx.p.get("deadline_iso") or item.get("effective_date")
    dl = fmt.date_h(deadline, with_year=True) if deadline else None
    left = days_until(ctx, deadline) if deadline else None
    action = (item.get("actionable") or "").rstrip(".")
    facts = [f"Compliance item: \"{title}\" — source: {src}", f"Summary: {summary}",
             f"Deadline: {dl}" + (f" ({left} days from today)" if left is not None else "") if dl else "",
             f"Suggested action: {human_dates(action)}" if action else ""]
    dl_en = f" Deadline: {dl}" + (f" — {left} days away." if left else ".") if dl else ""
    dl_hi = f" Deadline {dl} hai" + (f" — {left} din bache hain." if left else ".") if dl else ""
    act_en = f" Worth doing now: {action[0].lower() + action[1:]}." if action else ""
    act_hi = f" Abhi ka kaam: {action[0].lower() + action[1:]}." if action else ""
    en = (f"{S}, compliance heads-up from {src}: {title}. {summary}{dl_en}{act_en} "
          f"Want me to send a 1-page checklist + SOP wording you can file today?")
    hi = (f"{S}, ek compliance update ({src}): {title}. {summary}{dl_hi}{act_hi} "
          f"1-page checklist + SOP wording bana ke bhej doon?")
    return make_plan(ctx, family="knowledge", facts=facts,
                     angle="Regulatory change with a hard deadline — loss aversion (penalty/non-compliance) plus a done-for-you checklist.",
                     levers=["urgency/deadline", "loss aversion", "effort externalisation", "single yes/no"],
                     cta_type="binary_yes_no", en=en, hi=hi,
                     action_offer="send a 1-page compliance checklist and SOP wording you can file",
                     must=[S], avoid=["Do not exaggerate penalties; only state what the item says."])


def h_cde(ctx: Ctx) -> Plan:
    item = ctx.digest_item() or ctx.pick_digest(("cde",))
    if not item:
        return h_research(ctx)
    S = ctx.merchant_salutation()
    title, src = item.get("title"), item.get("source") or ""
    when = fmt.weekday_time(item.get("date")) or fmt.date_h(item.get("date"))
    credits = item.get("credits") or ctx.p.get("credits")
    summary = human_dates(item.get("summary"))
    fee = item.get("actionable") or fmt.humanize(ctx.p.get("fee") or "")
    facts = [f"Event: \"{title}\" — {src}", f"When: {when}" if when else "", f"CDE credits: {credits}" if credits else "",
             f"Details: {summary}", f"Fee: {fee}" if fee else ""]
    cr_en = f", {credits} CDE credits" if credits else ""
    en = (f"{S}, {src}: \"{title}\" — {when}{cr_en}. {summary} {fee + '.' if fee else ''} "
          f"Want me to block that evening and send you the registration details?")
    hi = (f"{S}, {src} ka session: \"{title}\" — {when}{cr_en}. {summary} {fee + '.' if fee else ''} "
          f"Calendar mein block karke registration details bhej doon?")
    return make_plan(ctx, family="knowledge", facts=facts,
                     angle="Professional-development event with date and credits; low-effort yes to reserve.",
                     levers=["specificity (date, credits, speaker)", "curiosity", "effort externalisation", "single yes/no"],
                     cta_type="binary_yes_no", en=en, hi=hi,
                     action_offer=f"block the calendar for '{title}' and send the registration details",
                     must=[S])


def h_supply_alert(ctx: Ctx) -> Plan:
    item = ctx.digest_item() or ctx.pick_digest(("alert", "supply"))
    S = ctx.merchant_salutation()
    mol = ctx.p.get("molecule") or (item or {}).get("molecule") or "the affected product"
    batches = [str(b) for b in (ctx.p.get("affected_batches") or [])]
    mfr = ctx.p.get("manufacturer")
    src = (item or {}).get("source") or "a regulator alert"
    sents = sentences((item or {}).get("summary"))
    safety = next((s for s in sents if "risk" in s.lower()), "")
    coh, coh_fact = cohort(ctx, item)
    asked = next((h for h in reversed(ctx.history()) if h.get("from") == "merchant"
                  and "list" in str(h.get("body", "")).lower()), None)
    b_txt = join_and(batches) if batches else "specific batches"
    facts = [f"Alert: {src} — voluntary recall of {mol} batches {', '.join(batches)}" + (f" by {mfr}" if mfr else ""),
             f"Alert summary: {(item or {}).get('summary', '')}", coh_fact or "",
             f"Merchant earlier asked: \"{asked.get('body')}\"" if asked else ""]
    ack_en = "You'd asked for the customer list — " if asked else ""
    ack_hi = "Aapne customer list maangi thi — " if asked else ""
    who_en = f"your {coh}" if coh else "your repeat-Rx customers"
    en = (f"{S}, urgent: {src} has flagged a voluntary recall on {mol} batches {b_txt}" + (f" ({mfr})" if mfr else "") +
          f" for sub-potency. {safety} {ack_en}I can match {who_en} against these batches and draft the replacement "
          f"WhatsApp for the ones affected. Reply YES and I'll start.")
    hi = (f"{S}, urgent: {src} ne {mol} ke batches {b_txt}" + (f" ({mfr})" if mfr else "") +
          f" par voluntary recall nikala hai — sub-potency ki wajah se. {safety} {ack_hi}Main {who_en} ko in batches se match "
          f"karke affected logon ke liye replacement WhatsApp draft kar deta hoon. YES reply karein, abhi shuru karta hoon.")
    return make_plan(ctx, family="knowledge", facts=facts,
                     angle="Urgent product recall: batch-level specificity, bounded risk, and a done-for-you customer workflow.",
                     levers=["urgency", "specificity (batch numbers)", "effort externalisation", "single yes/no"],
                     cta_type="binary_yes_no", en=en, hi=hi,
                     action_offer=f"match the customer list against the recalled {mol} batches and draft replacement WhatsApps",
                     must=[S] + batches[:1], avoid=["Do not state how many customers are affected — it is not known yet."])


def _parse_trend(tok: str) -> tuple[str, str, float] | None:
    import re
    m = re.match(r"(.+?)_demand_([+-]?\d+(?:\.\d+)?)", str(tok))
    if not m:
        return None
    name = fmt.humanize(m.group(1))
    name = {"cold cough": "cold/cough", "antifungal": "anti-fungal"}.get(name.lower(), name)
    if name.lower() == "ors":
        name = "ORS"
    v = float(m.group(2))
    return name, f"{'+' if v > 0 else ''}{int(v) if v.is_integer() else v}%", v


def h_category_seasonal(ctx: Ctx) -> Plan:
    S = ctx.merchant_salutation()
    parsed = [x for x in (_parse_trend(t) for t in (ctx.p.get("trends") or [])) if x]
    item = ctx.pick_digest(("seasonal",))
    if not parsed:
        return h_trend(ctx)
    ups = [f"{n} {d}" for n, d, v in parsed if v > 0]
    downs = [f"{n} {d}" for n, d, v in parsed if v < 0]
    agg = ctx.agg()
    rep = agg.get("repeat_customer_pct")
    tot = agg.get("total_unique_ytd")
    tie_en = (f" With {fmt.pct(rep)} of your {fmt.indian_int(tot)} customers buying repeat, the counter swap pays off fast."
              if rep and tot else "")
    tie_hi = (f" Aapke {fmt.indian_int(tot)} customers mein {fmt.pct(rep)} repeat karte hain — counter swap jaldi pay off karega."
              if rep and tot else "")
    action = (item or {}).get("actionable", "").rstrip(".")
    content = next((c.get("title") for c in ctx.cat.get("patient_content_library") or [] if "summer" in (c.get("title") or "").lower()), None)
    facts = [f"Seasonal demand shift ({fmt.humanize(ctx.p.get('season', ''))}): " + ", ".join(ups + downs),
             f"Digest: {(item or {}).get('title')} — {(item or {}).get('source')}" if item else "",
             f"Suggested action: {action}" if action else "",
             f"Shareable customer content available: \"{content}\"" if content else ""]
    down_en = f", while {join_and(downs)}" if downs else ""
    down_hi = f", aur {join_and(downs, True)}" if downs else ""
    en = (f"{S}, the summer shift has started: {join_and(ups)}{down_en}. "
          f"{('Quick shelf move: ' + action[0].lower() + action[1:] + '.') if action else ''}{tie_en} "
          f"Want me to draft a short summer first-aid WhatsApp you can send your regulars?")
    hi = (f"{S}, summer demand shift shuru ho gaya: {join_and(ups, True)}{down_hi}. "
          f"{('Shelf par: ' + action[0].lower() + action[1:] + '.') if action else ''}{tie_hi} "
          f"Regular customers ke liye ek summer first-aid WhatsApp draft kar doon?")
    return make_plan(ctx, family="knowledge", facts=facts,
                     angle="Category-wide seasonal demand numbers turned into a concrete shelf move plus a customer touchpoint.",
                     levers=["specificity (category % shifts)", "timeliness", "effort externalisation", "single yes/no"],
                     cta_type="binary_yes_no", en=en, hi=hi,
                     action_offer="draft a summer first-aid WhatsApp for regular customers and a counter-display list",
                     must=[S])


def h_trend(ctx: Ctx) -> Plan:
    S = ctx.merchant_salutation()
    q = ctx.p.get("query")
    delta = ctx.p.get("delta_yoy")
    item = ctx.digest_item() or ctx.pick_digest(("trend",))
    if not q:
        ts = ctx.trend_signals()
        if ts:
            q, delta = ts[0].get("query"), ts[0].get("delta_yoy")
    if not q and item:
        return h_research(ctx)
    if not q:
        return h_generic(ctx)
    offer = matching_offer(ctx, str(q))
    facts = [f"Search trend: '{q}' {fmt.pct(delta, signed=True)} YoY" if delta is not None else f"Search trend: '{q}' rising",
             f"Digest: {item.get('title')} — {item.get('source')}; {item.get('summary')}" if item else "",
             f"Relevant offer: {offer}" if offer else ""]
    d_txt = f" {fmt.pct(delta, signed=True)} YoY" if delta is not None else " rising"
    live = offer in ctx.active_offers() if offer else False
    if offer and not live:
        facts.append(f"Catalog idea (not yet live on this listing): {offer}")
    o_en = (f"Your '{offer}' is the natural answer to that search — " if live else
            f"A '{offer}' offer would answer that search directly — " if offer else "")
    o_hi = (f"Aapka '{offer}' is demand ka seedha jawab hai — " if live else
            f"'{offer}' jaisa offer is search ka seedha jawab hoga — " if offer else "")
    en = (f"{S}, searches for \"{q}\" are{d_txt}. {o_en}"
          "a Google post naming it this week puts you in front of that demand. Want me to draft the post?")
    hi = (f"{S}, \"{q}\" ki searches{d_txt} badh gayi hain. {o_hi}"
          "is hafte ek Google post se aap in logon ke saamne aa jaoge. Post draft kar doon?")
    return make_plan(ctx, family="knowledge", facts=facts, angle="Rising local search demand mapped onto the merchant's own offer.",
                     levers=["specificity (search delta)", "loss aversion (demand going elsewhere)", "single yes/no"],
                     cta_type="binary_yes_no", en=en, hi=hi, action_offer=f"draft a Google post targeting '{q}' searches", must=[S])


# ============================================================ performance family
def _metric_label(metric: str, hi: bool = False) -> str:
    m = fmt.humanize(metric).lower()
    return {"ctr": "CTR", "calls": "calls", "views": "profile views", "directions": "direction requests",
            "review count": "Google reviews", "leads": "leads"}.get(m, m)


def _derive_move(ctx: Ctx, direction: str) -> tuple[str, float] | None:
    deltas = {k.replace("_pct", ""): v for k, v in ctx.delta.items() if isinstance(v, (int, float))}
    if direction == "down":
        c = sorted((v, k) for k, v in deltas.items() if v < 0)
        return (c[0][1], c[0][0]) if c else None
    c = sorted(((-v, k) for k, v in deltas.items() if v > 0))
    return (c[0][1], -c[0][0]) if c else None


def _peer_gap(ctx: Ctx, direction: str) -> tuple[str, float, float, float] | None:
    comps = [c for c in ctx.peer_compare() if c[0] in ("calls", "views", "ctr")]
    if not comps:
        return None
    if direction == "down":
        c = sorted(comps, key=lambda x: x[3])[0]
        return c if c[3] < 1 else None
    c = sorted(comps, key=lambda x: -x[3])[0]
    return c if c[3] > 1 else None


def _fmt_metric_val(metric: str, v: float) -> str:
    return fmt.pct(v) if metric == "ctr" else fmt.indian_int(v)


def h_perf_dip(ctx: Ctx) -> Plan:
    S = ctx.merchant_salutation()
    hi = ctx.lang != "en"
    p = ctx.p
    metric, d, base = p.get("metric"), p.get("delta_pct"), p.get("vs_baseline")
    facts, extra = [], set()
    if not metric:
        mv = _derive_move(ctx, "down")
        if mv:
            metric, d = mv
    cz = causes(ctx)
    cause_en = join_and([c[0] for c in cz])
    cause_hi = join_and([c[1] for c in cz], True)
    offer = ctx.best_service_offer()
    has_offer = bool(ctx.active_offers())
    if metric and d is not None:
        other = _derive_move(ctx, "down")
        other_txt_en = other_txt_hi = ""
        if other and other[0] != metric:
            other_txt_en = f", and {_metric_label(other[0])} are down {fmt.pabs(other[1])} too"
            other_txt_hi = f", {_metric_label(other[0])} bhi {fmt.pabs(other[1])} neeche"
        base_en = f" against your usual {fmt.indian_int(base)}" if isinstance(base, (int, float)) else ""
        base_hi = f" (aapka usual {fmt.indian_int(base)})" if isinstance(base, (int, float)) else ""
        facts.append(f"Trigger: {_metric_label(metric)} {fmt.pct(d, signed=True)} over {p.get('window', '7d')}"
                     + (f" vs baseline {base}" if base is not None else ""))
        head_en = f"{S}, {_metric_label(metric)} are down {fmt.pabs(d)} this week{base_en}{other_txt_en}."
        head_hi = f"{S}, is hafte {_metric_label(metric)} {fmt.pabs(d)} gire hain{base_hi}{other_txt_hi}."
    else:
        gap = _peer_gap(ctx, "down")
        if not gap:
            return h_generic(ctx)
        mk, mine, peer, ratio = gap
        short = round((1 - ratio) * 100)
        extra.add(str(short))
        facts.append(f"{_metric_label(mk)}: {_fmt_metric_val(mk, mine)} vs peer average {_fmt_metric_val(mk, peer)} ({short}% below)")
        strong = _peer_gap(ctx, "up")
        strong_en = strong_hi = ""
        if strong and strong[0] != mk:
            strong_en = f" — even though your {_metric_label(strong[0])} ({_fmt_metric_val(strong[0], strong[1])}) beats the {_fmt_metric_val(strong[0], strong[2])} average"
            strong_hi = f" — halaanki aapka {_metric_label(strong[0])} ({_fmt_metric_val(strong[0], strong[1])}) average {_fmt_metric_val(strong[0], strong[2])} se behtar hai"
        head_en = (f"{S}, a flag on {_metric_label(mk)}: {_fmt_metric_val(mk, mine)} in the last 30 days vs a peer average of "
                   f"{_fmt_metric_val(mk, peer)}{strong_en}.")
        head_hi = (f"{S}, {_metric_label(mk)} par dhyaan dijiye: pichhle 30 din mein {_fmt_metric_val(mk, mine)}, jabki peers ka "
                   f"average {_fmt_metric_val(mk, peer)} hai{strong_hi}.")
    sub = ctx.sub()
    lapsed_en = lapsed_hi = ""
    if sub.get("status") == "expired" and sub.get("days_since_expiry"):
        facts.append(f"Subscription expired {sub['days_since_expiry']} days ago (profile upkeep paused)")
        lapsed_en = f" Profile upkeep has also been paused since your plan lapsed {sub['days_since_expiry']} days ago."
        lapsed_hi = f" Plan lapse hue {sub['days_since_expiry']} din ho gaye, tab se profile upkeep bhi ruka hai."
    tip = ctx.pick_digest(("trend", "tech"), keywords=("calls", "walk-in", "impressions", "visibility"))
    tip_ok = tip and any(k in (tip.get("summary") or "").lower() for k in ("call", "impression", "visib"))
    if tip_ok:
        facts.append(f"Digest: {tip.get('title')} — {tip.get('source')}: {tip.get('summary')}")
    if cz and not has_offer and offer:
        facts.append(f"Suggested offer from category catalog (not yet live): {offer}")
        fix_en = f" Likely reasons: {cause_en}. Fastest fix is a live service+price offer like '{offer}'."
        fix_hi = f" Wajah: {cause_hi}. Sabse tez fix — '{offer}' jaisa service+price offer live karna."
        cta_en, cta_hi = "Reply YES and I'll put it live today.", "YES reply karein, aaj hi live kar deta hoon."
        action = f"set up and publish the '{offer}' offer on the listing today"
    elif tip_ok:
        fix_en = f" One quick lever from this week's data: {tip.get('title')[0].lower() + tip.get('title')[1:]} ({tip.get('source')})."
        fix_hi = f" Is hafte ka ek aasaan lever: {tip.get('title')} ({tip.get('source')})."
        cta_en, cta_hi = "Want me to make that change on your profile?", "Yeh change aapke profile par kar doon?"
        action = f"apply this change to the profile: {tip.get('actionable') or tip.get('title')}"
    else:
        fix_en = f" Likely reasons: {cause_en}." if cause_en else " A fresh post and updated photos usually lift this within a week."
        fix_hi = f" Wajah: {cause_hi}." if cause_hi else " Ek fresh post aur nayi photos se yeh jaldi sudharta hai."
        cta_en, cta_hi = "Want me to draft this week's Google post to pull it back up?", "Is hafte ka Google post draft kar doon?"
        action = "draft and publish this week's Google post"
    en = f"{head_en}{lapsed_en}{fix_en} {cta_en}"
    hi_txt = f"{head_hi}{lapsed_hi}{fix_hi} {cta_hi}"
    return make_plan(ctx, family="performance", facts=facts,
                     angle="Performance dip quantified, diagnosed from profile signals, with one concrete fix.",
                     levers=["loss aversion", "specificity (own numbers vs peers)", "effort externalisation", "single yes/no"],
                     cta_type="binary_yes_no", en=en, hi=hi_txt, action_offer=action, must=[S], extra_numbers=extra,
                     avoid=["Do not claim a dip that the numbers don't show."] if ctx.placeholder else [])


def h_perf_spike(ctx: Ctx) -> Plan:
    S = ctx.merchant_salutation()
    p = ctx.p
    metric, d, base, driver = p.get("metric"), p.get("delta_pct"), p.get("vs_baseline"), p.get("likely_driver")
    facts, extra = [], set()
    if not metric:
        mv = _derive_move(ctx, "up")
        if mv:
            metric, d = mv
    if metric and d is not None:
        facts.append(f"Trigger: {_metric_label(metric)} {fmt.pct(d, signed=True)} over {p.get('window', '7d')}"
                     + (f" vs baseline {base}" if base is not None else "") + (f"; likely driver: {fmt.humanize(driver)}" if driver else ""))
        base_en = f" (your usual: {fmt.indian_int(base)})" if isinstance(base, (int, float)) else ""
        base_hi = f" (aapka usual {fmt.indian_int(base)})" if isinstance(base, (int, float)) else ""
        head_en = f"{S}, {_metric_label(metric)} are up {fmt.pct(d)} this week{base_en}"
        head_hi = f"{S}, is hafte {_metric_label(metric)} {fmt.pct(d)} upar hain{base_hi}"
        if driver:
            head_en += f" — and the {fmt.humanize(driver)} looks like what's driving it."
            head_hi += f" — lagta hai {fmt.humanize(driver)} ki wajah se."
        else:
            head_en += "."
            head_hi += "."
    else:
        gap = _peer_gap(ctx, "up")
        if not gap:
            return h_generic(ctx)
        mk, mine, peer, ratio = gap
        facts.append(f"{_metric_label(mk)} {_fmt_metric_val(mk, mine)} vs peer average {_fmt_metric_val(mk, peer)}")
        head_en = f"{S}, your {_metric_label(mk)} ({_fmt_metric_val(mk, mine)}) is running ahead of the {_fmt_metric_val(mk, peer)} peer average."
        head_hi = f"{S}, aapka {_metric_label(mk)} ({_fmt_metric_val(mk, mine)}) peers ke average {_fmt_metric_val(mk, peer)} se aage chal raha hai."
    gap = _peer_gap(ctx, "up")
    if gap and gap[0] != metric and gap[0] == "ctr":
        facts.append(f"CTR {fmt.pct(gap[1])} vs peer average {fmt.pct(gap[2])}")
        head_en += f" Your CTR ({fmt.pct(gap[1])}) is also above the {fmt.pct(gap[2])} peer average."
        head_hi += f" CTR ({fmt.pct(gap[1])}) bhi peer average {fmt.pct(gap[2])} se upar hai."
    unverified = ctx.ident.get("verified") is False or ctx.has_signal("unverified_gbp")
    planning = ctx.has_signal("active_planning") or any(h.get("engagement") == "intent_planning" for h in ctx.history())
    if unverified:
        body_en = (" The one thing capping it: your Google profile is still unverified. Verifying now turns this uptick into a bigger one."
                   " It's a 5-minute call — want me to start the verification?")
        body_hi = (" Bas ek cheez rok rahi hai: Google profile abhi verified nahi hai. Abhi verify karenge toh yeh uptick aur badhega."
                   " Sirf 5 minute ki call hai — verification shuru kar doon?")
        action = "start Google profile verification"
    elif driver or planning:
        body_en = (" Strike while it's warm: a follow-up post with the full details and a clear 'book a trial' line converts that interest."
                   " Want me to draft it now?")
        body_hi = (" Garam lohe pe chot: poori details aur 'book a trial' line ke saath ek follow-up post is interest ko bookings mein badlega."
                   " Abhi draft kar doon?")
        action = "draft a follow-up post that converts the spike into bookings"
    else:
        body_en = " Good moment to capture it: a fresh post with your best offer keeps the momentum. Want me to draft it?"
        body_hi = " Momentum pakadne ka sahi time: best offer ke saath ek fresh post. Draft kar doon?"
        action = "draft a momentum post with the best offer"
    return make_plan(ctx, family="performance", facts=facts,
                     angle="Positive spike: name the likely driver and convert momentum with one next step.",
                     levers=["specificity", "momentum/curiosity", "effort externalisation", "single yes/no"],
                     cta_type="binary_yes_no", en=head_en + body_en, hi=head_hi + body_hi, action_offer=action, must=[S],
                     extra_numbers=extra)


def h_seasonal_dip(ctx: Ctx) -> Plan:
    S = ctx.merchant_salutation()
    p = ctx.p
    metric, d = p.get("metric") or "views", p.get("delta_pct")
    beat = next((b for b in ctx.cat.get("seasonal_beats") or [] if "acquisition" in (b.get("note") or "").lower()), None) or (ctx.seasonal_now() or [None])[0]
    agg = ctx.agg()
    members, churn, peer_churn = agg.get("total_active_members"), agg.get("monthly_churn_pct"), ctx.peer("monthly_churn_pct")
    facts = [f"Trigger: {_metric_label(metric)} {fmt.pct(d, signed=True) if d is not None else ''} over {p.get('window', '7d')}; expected seasonal: {p.get('is_expected_seasonal')}",
             f"Seasonal beat: {beat.get('month_range')} — {beat.get('note')}" if beat else ""]
    extra = set()
    risk_en = risk_hi = ""
    if members and churn:
        at_risk = round(members * churn)
        extra.add(str(at_risk))
        facts.append(f"Members {members}, monthly churn {fmt.pct(churn)}" + (f" (peer {fmt.pct(peer_churn)})" if peer_churn else "")
                     + f" → about {at_risk} members at risk per month")
        peer_txt = f" (peer avg {fmt.pct(peer_churn)})" if peer_churn else ""
        risk_en = f" With {members} active members and {fmt.pct(churn)} monthly churn{peer_txt}, that's ~{at_risk} members a month to protect."
        risk_hi = f" {members} active members aur {fmt.pct(churn)} monthly churn{peer_txt} — matlab har mahine ~{at_risk} members ko rokna hai."
    beat_en = f" {beat.get('month_range')} is the {beat.get('note').split('—')[0].strip()} for {ctx.slug} every year" if beat else " this is the usual seasonal lull"
    en = (f"{S}, {_metric_label(metric)} are down {fmt.pabs(d) if d is not None else ''} this week — before that worries you,{beat_en}, "
          f"so it's expected, not a problem with your listing. The better play now is retention, not ad spend.{risk_en} "
          f"Want me to draft a summer attendance challenge to keep them coming?")
    hi = (f"{S}, is hafte {_metric_label(metric)} {fmt.pabs(d) if d is not None else ''} neeche hain — par ghabraiye mat,{beat_en}, "
          f"yeh har saal hota hai, listing ki problem nahi. Abhi ad spend nahi, retention pe focus kijiye.{risk_hi} "
          f"Members ko roke rakhne ke liye ek summer attendance challenge draft kar doon?")
    return make_plan(ctx, family="performance", facts=facts,
                     angle="Contrarian reframe: the dip is seasonal and expected; redirect effort from acquisition to retention.",
                     levers=["anxiety pre-emption", "specificity (members, churn)", "reframe", "single yes/no"],
                     cta_type="binary_yes_no", en=en, hi=hi, action_offer="draft a summer attendance challenge for existing members",
                     must=[S], extra_numbers=extra)


def h_milestone(ctx: Ctx) -> Plan:
    S = ctx.merchant_salutation()
    p = ctx.p
    metric, now_v, goal = p.get("metric"), p.get("value_now"), p.get("milestone_value")
    extra = set()
    fan = ctx.top_review("pos")
    if metric and isinstance(now_v, (int, float)) and isinstance(goal, (int, float)):
        gap = int(goal - now_v)
        extra.add(str(abs(gap)))
        label = _metric_label(metric)
        facts = [f"Milestone: {label} at {fmt.indian_int(now_v)}, milestone {fmt.indian_int(goal)}" + (f" ({gap} to go)" if gap > 0 else " (crossed)")]
        fan_en = fan_hi = ""
        if fan:
            facts.append(f"Top positive review theme: {fmt.humanize(fan.get('theme'))} ({fan.get('occurrences_30d')} mentions in 30 days)")
            fan_en = f" The fans are already there — {fan.get('occurrences_30d')} reviews this month praise your {fmt.humanize(fan.get('theme')).replace(' quality', '')}."
            fan_hi = f" Fans already hain — is mahine {fan.get('occurrences_30d')} reviews mein aapke {fmt.humanize(fan.get('theme')).replace(' quality', '')} ki taareef hai."
        if gap > 0:
            en = (f"{S}, {ctx.name} is at {fmt.indian_int(now_v)} {label} — just {gap} short of {fmt.indian_int(goal)}.{fan_en} "
                  f"A small 'enjoyed it? leave us a review' card at billing can close that gap this week. Want me to design the card + a WhatsApp line for regulars?")
            hi = (f"{S}, {ctx.name} {fmt.indian_int(now_v)} {label} par hai — {fmt.indian_int(goal)} se sirf {gap} door.{fan_hi} "
                  f"Billing counter par ek chhota 'review dijiye' card yeh gap is hafte bhar sakta hai. Card + regulars ke liye WhatsApp line design kar doon?")
            action = f"design a review-request card and WhatsApp line to reach {fmt.indian_int(goal)} {label}"
        else:
            en = (f"{S}, {ctx.name} just crossed {fmt.indian_int(goal)} {label} — a real trust signal for new customers.{fan_en} "
                  f"Want me to turn it into a thank-you Google post this week?")
            hi = (f"{S}, {ctx.name} ne {fmt.indian_int(goal)} {label} cross kar liye — naye customers ke liye strong trust signal.{fan_hi} "
                  f"Is par ek thank-you Google post bana doon?")
            action = "publish a milestone thank-you Google post"
        return make_plan(ctx, family="performance", facts=facts,
                         angle="Milestone within reach: make the remaining gap concrete and hand them the tool to close it.",
                         levers=["goal-gradient", "social proof", "effort externalisation", "single yes/no"],
                         cta_type="binary_yes_no", en=en, hi=hi, action_offer=action, must=[S], extra_numbers=extra)
    # placeholder: find a genuine milestone in the merchant's own numbers
    ctr, peer_ctr = ctx.perf.get("ctr"), ctx.peer("avg_ctr")
    views, peer_views = ctx.perf.get("views"), ctx.peer("avg_views_30d")
    photos = ctx.peer("avg_photos")
    if isinstance(ctr, (int, float)) and isinstance(peer_ctr, (int, float)) and peer_ctr and ctr / peer_ctr >= 1.4:
        mult = ctr / peer_ctr
        mult_txt = f"{mult:.1f}".rstrip("0").rstrip(".")
        extra.add(mult_txt)
        facts = [f"CTR {fmt.pct(ctr)} vs peer average {fmt.pct(peer_ctr)} ({mult_txt}x)"]
        see_en = see_hi = ""
        if isinstance(views, (int, float)) and isinstance(peer_views, (int, float)) and views < peer_views:
            facts.append(f"Views {fmt.indian_int(views)} vs peer average {fmt.indian_int(peer_views)}")
            see_en = (f" The catch: only {fmt.indian_int(views)} people saw the listing in 30 days (peers average {fmt.indian_int(peer_views)}), "
                      f"so every extra view is worth more to you than to most.")
            see_hi = (f" Bas dikkat yeh hai ki 30 din mein sirf {fmt.indian_int(views)} logon ne listing dekhi (peers ka average {fmt.indian_int(peer_views)}) — "
                      f"aapke liye har extra view zyada keemti hai.")
        ph_en = f" Peers carry ~{photos} photos and post weekly — the quickest way to get seen more." if photos else ""
        en = (f"{S}, a quiet milestone for {ctx.name}: your listing converts at {fmt.pct(ctr)} CTR — {mult_txt}x the {fmt.pct(peer_ctr)} "
              f"average for your category.{see_en}{ph_en} Want me to draft this week's post to get more eyes on it?")
        hi = (f"{S}, {ctx.name} ke liye ek chhota milestone: aapki listing {fmt.pct(ctr)} CTR par convert karti hai — category average "
              f"{fmt.pct(peer_ctr)} ka {mult_txt}x.{see_hi} Zyada logon tak pahunchne ke liye is hafte ka post draft kar doon?")
        return make_plan(ctx, family="performance", facts=facts,
                         angle="Milestone derived from real numbers (CTR multiple vs peers); turn strength into reach.",
                         levers=["pride/recognition", "specificity (vs peers)", "curiosity", "single yes/no"],
                         cta_type="binary_yes_no", en=en, hi=hi, action_offer="draft this week's Google post to increase reach",
                         must=[S], extra_numbers=extra)
    for thr in (10000, 5000, 2500, 2000, 1000, 500):
        if isinstance(views, (int, float)) and views >= thr:
            facts = [f"Profile views in 30 days: {fmt.indian_int(views)} (crossed {fmt.indian_int(thr)})"]
            en = (f"{S}, {ctx.name} crossed {fmt.indian_int(thr)} profile views this month ({fmt.indian_int(views)} in 30 days). "
                  f"Good moment to convert that attention — want me to draft a thank-you post with your best offer?")
            hi = (f"{S}, {ctx.name} ne is mahine {fmt.indian_int(thr)} profile views cross kar liye ({fmt.indian_int(views)} in 30 din). "
                  f"Is attention ko convert karne ke liye best offer ke saath ek thank-you post draft kar doon?")
            return make_plan(ctx, family="performance", facts=facts, angle="Milestone derived from real view count.",
                             levers=["pride", "specificity", "single yes/no"], cta_type="binary_yes_no", en=en, hi=hi,
                             action_offer="draft a milestone thank-you post", must=[S])
    return h_generic(ctx)


# ============================================================ account / lifecycle family
def h_renewal(ctx: Ctx) -> Plan:
    S = ctx.merchant_salutation()
    p, sub = ctx.p, ctx.sub()
    days = p.get("days_remaining") if p.get("days_remaining") is not None else sub.get("days_remaining")
    plan_name = p.get("plan") or sub.get("plan") or "Pro"
    amt = p.get("renewal_amount")
    views, calls = ctx.perf.get("views"), ctx.perf.get("calls")
    facts = [f"Renewal: {plan_name} plan, {days} days remaining" + (f", renewal amount {fmt.money(amt)}" if amt else "")]
    dip = _derive_move(ctx, "down")
    cz = causes(ctx)
    if dip and dip[1] <= -0.2 and cz:
        facts.append(f"{_metric_label(dip[0])} {fmt.pct(dip[1], signed=True)} this week")
        en = (f"{S}, your {plan_name} plan renews in {days} days" + (f" ({fmt.money(amt)})" if amt else "") +
              f". Honest view: {_metric_label(dip[0])} are down {fmt.pabs(dip[1])} this week, and {join_and([c[0] for c in cz])}. "
              f"Let's fix those before the renewal so it's clearly worth it. Want me to start on both today?")
        hi = (f"{S}, aapka {plan_name} plan {days} din mein renew hona hai" + (f" ({fmt.money(amt)})" if amt else "") +
              f". Seedhi baat: is hafte {_metric_label(dip[0])} {fmt.pabs(dip[1])} gire hain, aur {join_and([c[1] for c in cz], True)}. "
              f"Renewal se pehle yeh dono theek kar dete hain. Aaj hi shuru karoon?")
        action = "fix the listing issues (" + ", ".join(c[0] for c in cz) + ") ahead of renewal"
    else:
        val_en = f" In the last 30 days the listing brought {fmt.indian_int(views)} views and {fmt.indian_int(calls)} calls." if views and calls else ""
        val_hi = f" Pichhle 30 din mein listing se {fmt.indian_int(views)} views aur {fmt.indian_int(calls)} calls aaye." if views and calls else ""
        if str(plan_name).lower() == "trial" or ctx.sub().get("status") == "trial":
            en = (f"{S}, your trial ends in {days} days.{val_en} To keep the profile work and posts running without a break, "
                  f"the next step is moving to a paid plan. Want me to send the plan options?")
            hi = (f"{S}, aapka trial {days} din mein khatam ho raha hai.{val_hi} Profile ka kaam aur posts bina break ke chalte rahein, "
                  f"iske liye paid plan par shift karna hoga. Plan options bhej doon?")
            action = "send the paid plan options"
        else:
            en = (f"{S}, your {plan_name} plan renews in {days} days" + (f" ({fmt.money(amt)})" if amt else "") + f".{val_en} "
                  f"If it lapses, profile upkeep and posts pause. Want me to send the renewal link so there's no gap?")
            hi = (f"{S}, aapka {plan_name} plan {days} din mein renew hona hai" + (f" ({fmt.money(amt)})" if amt else "") + f".{val_hi} "
                  f"Lapse hua toh profile upkeep aur posts ruk jayenge. Renewal link bhej doon taaki gap na aaye?")
            action = "send the renewal link"
    return make_plan(ctx, family="account", facts=facts,
                     angle="Renewal due: show value (or fix what's broken first) rather than a bare payment reminder.",
                     levers=["loss aversion", "honesty/reciprocity", "single yes/no"], cta_type="binary_yes_no",
                     en=en, hi=hi, action_offer=action, must=[S])


def h_winback(ctx: Ctx) -> Plan:
    S = ctx.merchant_salutation()
    p = ctx.p
    days = p.get("days_since_expiry") or ctx.sub().get("days_since_expiry")
    dip = p.get("perf_dip_pct")
    lapsed = p.get("lapsed_customers_added_since_expiry")
    facts = [f"Plan expired {days} days ago", f"Performance since expiry: {fmt.pct(dip, signed=True)}" if dip is not None else "",
             f"Customers who went quiet since expiry: {lapsed}" if lapsed else ""]
    dip_en = f" calls/views are down {fmt.pabs(dip)}" if dip is not None else ""
    lap_en = f" and {lapsed} more customers have gone quiet" if lapsed else ""
    en = (f"{S}, it's been {days} days since {ctx.name}'s plan lapsed — since then{dip_en}{lap_en}. "
          f"No hard sell: I can show you exactly what slipped and a 2-step recovery plan. Want to see it?")
    hi = (f"{S}, {ctx.name} ka plan lapse hue {days} din ho gaye — tab se{dip_en.replace('are down', 'down hain')}"
          f"{(' aur ' + str(lapsed) + ' aur customers chup ho gaye') if lapsed else ''}. "
          f"Koi hard sell nahi: main dikha deta hoon kya slip hua aur 2-step recovery plan. Dekhna chahenge?")
    return make_plan(ctx, family="account", facts=facts,
                     angle="Win-back: quantify what was lost since expiry; offer diagnosis before any pitch.",
                     levers=["loss aversion", "curiosity", "reciprocity", "single yes/no"], cta_type="binary_yes_no",
                     en=en, hi=hi, action_offer="share the what-slipped report and 2-step recovery plan", must=[S])


def h_unverified(ctx: Ctx) -> Plan:
    S = ctx.merchant_salutation()
    p = ctx.p
    raw_path = fmt.humanize(p.get("verification_path") or "phone call or postcard")
    path = raw_path.replace(" or ", " or a ")
    path_hi = raw_path.replace(" or ", " ya ")
    uplift = p.get("estimated_uplift_pct")
    views, calls = ctx.perf.get("views"), ctx.perf.get("calls")
    facts = [f"Google profile unverified; verification path: {fmt.humanize(p.get('verification_path') or '')}",
             f"Estimated visibility uplift after verification: {fmt.pct(uplift)}" if uplift else "",
             f"Current 30-day views {views}, calls {calls}" if views else ""]
    up_en = f" — verifying is estimated to lift your visibility by about {fmt.pct(uplift)}" if uplift else ""
    up_hi = f" — verify karne se visibility lagbhag {fmt.pct(uplift)} badhne ka estimate hai" if uplift else ""
    cur_en = f" Right now the listing gets {fmt.indian_int(views)} views and {fmt.indian_int(calls)} calls a month." if views and calls else ""
    cur_hi = f" Abhi listing ko mahine mein {fmt.indian_int(views)} views aur {fmt.indian_int(calls)} calls milte hain." if views and calls else ""
    en = (f"{S}, {ctx.name} is still unverified on Google{up_en}.{cur_en} Verification is just a {path}, and I can walk you "
          f"through it in 5 minutes. Reply YES and I'll start it now.")
    hi = (f"{S}, {ctx.name} Google par abhi bhi unverified hai{up_hi}.{cur_hi} Verification bas ek {path_hi} hai — main 5 minute "
          f"mein karwa deta hoon. YES reply karein, abhi shuru karte hain.")
    return make_plan(ctx, family="account", facts=facts,
                     angle="Unverified listing: quantify the upside and make the fix feel like 5 minutes.",
                     levers=["loss aversion", "specificity (uplift estimate)", "effort externalisation", "single yes/no"],
                     cta_type="binary_yes_no", en=en, hi=hi, action_offer="start Google profile verification", must=[S])


def h_dormant(ctx: Ctx) -> Plan:
    S = ctx.merchant_salutation()
    p = ctx.p
    days = p.get("days_since_last_merchant_message") or ctx.signal_value("dormant_with_vera")
    kw = {"salons": ("walk-in", "calls"), "restaurants": ("impressions", "verified", "searches"),
          "gyms": ("personal training", "class", "slot"), "pharmacies": ("retention", "reminder", "refill"),
          "dentists": ("searches", "aligner", "consultation")}.get(ctx.slug, ("calls", "search"))
    tip = ctx.pick_digest(("trend", "tech"), keywords=kw)
    dip = _derive_move(ctx, "down")
    facts = [f"No message from merchant in {days} days" if days else "Merchant has been quiet with Vera",
             f"Last topic: {fmt.humanize(p.get('last_topic'))}" if p.get("last_topic") else "",
             f"Digest: {tip.get('title')} — {tip.get('source')}: {tip.get('summary')}" if tip else "",
             f"{_metric_label(dip[0])} {fmt.pct(dip[1], signed=True)} this week" if dip else ""]
    if not tip:
        return h_generic(ctx)
    ttl = human_dates(tip.get("title"))
    dip_en = f" {ctx.name}'s {_metric_label(dip[0])} dipped {fmt.pabs(dip[1])} this week, so it's worth a try." if dip else ""
    dip_hi = f" Is hafte {ctx.name} ke {_metric_label(dip[0])} {fmt.pabs(dip[1])} gire hain, toh try karna banta hai." if dip else ""
    act = (tip.get("actionable") or "").rstrip(".")
    move_en = f" The move: {act[0].lower() + act[1:]}." if act else ""
    move_hi = f" Move: {act[0].lower() + act[1:]}." if act else ""
    en = (f"{S}, no pitch today — just one thing from this week's data you can use for free: {ttl[0].lower() + ttl[1:]} "
          f"({tip.get('source')}).{dip_en}{move_en} Want me to set it up for {ctx.name}?")
    hi = (f"{S}, aaj koi pitch nahi — bas is hafte ka ek kaam ka data point, bilkul free: {ttl} ({tip.get('source')}).{dip_hi}"
          f"{move_hi} {ctx.name} ke liye main set kar doon?")
    return make_plan(ctx, family="account",
                     facts=facts + ([f"Suggested action: {act}"] if act else []),
                     angle="Dormant merchant: re-open with free, specific value (no ask for money), then a tiny yes.",
                     levers=["reciprocity", "curiosity", "specificity", "single yes/no"], cta_type="binary_yes_no",
                     en=en, hi=hi, action_offer=f"set this up for you: {(act or ttl)[0].lower() + (act or ttl)[1:]}", must=[S],
                     avoid=["Do not push subscription renewal in this message."])


# ============================================================ events family
def h_festival(ctx: Ctx) -> Plan:
    S = ctx.merchant_salutation()
    p = ctx.p
    fest = p.get("festival")
    date = fmt.date_h(p.get("date"))
    days = p.get("days_until")
    offer = ctx.active_offers()
    beat = ctx.seasonal_matching("festival", "diwali", "wedding")
    facts = [f"Festival: {fest} on {fmt.date_h(p.get('date'), True)} ({days} days away)" if fest else "Trigger: festival season approaching",
             f"Seasonal beat: {beat.get('month_range')} — {beat.get('note')}" if beat else "",
             f"Active offers: {'; '.join(offer)}" if offer else ""]
    beat_en = f" {beat.get('month_range')} is peak season — {beat.get('note').split('—')[-1].strip()}." if beat else ""
    beat_hi = f" {beat.get('month_range')} peak season hai — {beat.get('note').split('—')[-1].strip()}." if beat else ""
    if fest:
        far = isinstance(days, (int, float)) and days > 45
        when_en = f"{fest} is on {date} — {days} days out." if days is not None else f"{fest} is coming up on {date}."
        when_hi = f"{fest} {date} ko hai — {days} din baaki." if days is not None else f"{fest} {date} ko aa raha hai."
        if far:
            core_en = " Early, but the businesses that open pre-bookings now lock their best slots before the rush."
            core_hi = " Abhi jaldi lagta hai, par jo abhi pre-booking khol dete hain unke best slots rush se pehle bhar jaate hain."
        else:
            core_en = " This is the window to get a festive offer live and posted."
            core_hi = " Festive offer live karke post karne ka yahi time hai."
        anchor = _premium(offer) if offer else ctx.best_service_offer()
        anc_en = f" Your '{anchor}' can anchor a festive package." if anchor else ""
        anc_hi = f" Aapka '{anchor}' festive package ka base ban sakta hai." if anchor else ""
        en = f"{S}, {when_en}{beat_en}{core_en}{anc_en} Want me to draft the {fest} pre-booking post?"
        hi = f"{S}, {when_hi}{beat_hi}{core_hi}{anc_hi} {fest} pre-booking post draft kar doon?"
        action = f"draft the {fest} pre-booking post and package"
    else:
        cust = ctx.agg().get("total_unique_ytd")
        idea = ctx.catalog_title("family", "couple") or ctx.catalog_title("refer") or ctx.best_service_offer()
        cust_en = f" {ctx.name} has seen {fmt.indian_int(cust)} {ctx.nouns['people']} this year — a comeback offer to them is the cheapest win." if cust else ""
        cust_hi = f" Is saal {ctx.name} ke paas {fmt.indian_int(cust)} {ctx.nouns['people']} aaye — unhe comeback offer bhejna sabse sasta win hai." if cust else ""
        idea_en = f" Something like '{idea}' fits the season." if idea else ""
        idea_hi = f" '{idea}' jaisa offer season ke hisaab se fit hai." if idea else ""
        en = f"{S}, festival season is coming up.{beat_en}{cust_en}{idea_en} Want me to draft the festive WhatsApp + Google post?"
        hi = f"{S}, festival season aa raha hai.{beat_hi}{cust_hi}{idea_hi} Festive WhatsApp + Google post draft kar doon?"
        action = "draft a festive comeback WhatsApp and Google post"
        if idea:
            facts.append(f"Idea from category catalog (not yet live): {idea}")
    return make_plan(ctx, family="event", facts=facts,
                     angle="Upcoming festival: tie timing to the category's seasonal peak and an existing offer; act ahead of the rush.",
                     levers=["timeliness", "loss aversion (slots fill)", "effort externalisation", "single yes/no"],
                     cta_type="binary_yes_no", en=en, hi=hi, action_offer=action, must=[S],
                     avoid=["Do not name a festival or date that is not in the facts."] if not fest else [])


def h_ipl(ctx: Ctx) -> Plan:
    S = ctx.merchant_salutation()
    p = ctx.p
    match, venue = p.get("match"), p.get("venue")
    t = fmt.weekday_time(p.get("match_time_iso"))
    time_only = t.split(", ")[-1] if t else None
    day = t.split(" ")[0] if t else None
    weeknight = p.get("is_weeknight")
    item = ctx.pick_digest(("seasonal",), keywords=("ipl",))
    agg = ctx.agg()
    deliv, dine = agg.get("delivery_orders_30d"), agg.get("dine_in_orders_30d")
    offers = ctx.active_offers()
    combo = ctx.catalog_title("match-night", "match night")
    facts = [f"Match: {match} at {venue}, {t}; weeknight: {weeknight}",
             f"Digest: {item.get('title')} — {item.get('source')}: {item.get('summary')}" if item else "",
             f"Orders last 30 days: {deliv} delivery, {dine} dine-in" if deliv and dine else "",
             f"Active offers: {'; '.join(offers)}" if offers else "", f"Catalog idea: {combo}" if combo else ""]
    extra = set()
    if deliv and dine:
        extra.add(str(deliv + dine))
    head_en = f"{S}, {match} tonight at {venue}, {time_only}."
    head_hi = f"{S}, aaj raat {match} hai, {venue} mein, {time_only}."
    if weeknight is False:
        data_en = (" Worth knowing: it's a Saturday, and Saturday IPL games have been pulling restaurant covers down 12% "
                   "(weeknight games lift them 18%) because people watch at home.") if item else " It's a weekend game, so expect people to watch at home."
        data_hi = (" Ek baat dhyaan dein: aaj Saturday hai, aur Saturday IPL matches mein restaurant covers 12% gire hain "
                   "(weeknight matches mein 18% badhe) — log ghar pe dekhte hain.") if item else " Weekend match hai, log ghar pe dekhenge."
        if day and day not in ("Sat", "Sun"):
            data_en = data_en.replace("it's a Saturday, and ", "")
            data_hi = data_hi.replace("aaj Saturday hai, aur ", "")
        mix_en = f" Delivery is already {deliv} of your last {deliv + dine} orders, so tonight is a delivery night." if deliv and dine else " Tonight is a delivery night."
        mix_hi = f" Pichhle {deliv + dine} orders mein {deliv} delivery the — aaj delivery ki raat hai." if deliv and dine else " Aaj delivery ki raat hai."
        bogo = next((o for o in offers if "tue" in o.lower() or "thu" in o.lower()), None)
        bogo_en = f" Your '{bogo}' doesn't cover tonight — save it for the weeknight games." if bogo else ""
        bogo_hi = f" Aapka '{bogo}' aaj apply nahi hota — use weeknight matches ke liye rakhiye." if bogo else ""
        cta_en = (f" Want me to put up a delivery-only '{combo}' just for this evening?" if combo
                  else " Want me to set up a delivery-only match-night special for this evening?")
        cta_hi = (f" Sirf aaj shaam ke liye delivery-only '{combo}' laga doon?" if combo
                  else " Aaj shaam ke liye delivery-only match-night special laga doon?")
        angle = "Contrarian call: weekend IPL shifts demand to home viewing — push delivery, don't run a dine-in promo."
    else:
        data_en = " Weeknight matches have been lifting restaurant covers ~18% — a good night for a match special." if item else " Match nights pull crowds."
        data_hi = " Weeknight matches mein covers ~18% badhe hain — match special ke liye badhiya raat." if item else " Match nights mein bheed aati hai."
        mix_en = mix_hi = bogo_en = bogo_hi = ""
        anchor = offers[0] if offers else combo
        cta_en = f" Want me to push '{anchor}' as tonight's match-night offer on Google + WhatsApp?" if anchor else " Want me to set up a match-night offer for tonight?"
        cta_hi = f" '{anchor}' ko aaj ke match-night offer ki tarah Google + WhatsApp par push kar doon?" if anchor else " Aaj ke liye match-night offer set kar doon?"
        angle = "Weeknight IPL lifts covers — activate a match-night offer."
    late = next((r for r in ctx.review_themes() if "late" in str(r.get("theme"))), None)
    if late and weeknight is False:
        facts.append(f"Review theme: late delivery ({late.get('occurrences_30d')} mentions in 30 days)")
    tr_en, tr_hi, tr_fact = trial_clause(ctx)
    if tr_fact:
        facts.append(tr_fact)
    en = head_en + data_en + mix_en + bogo_en + tr_en + cta_en
    hi = head_hi + data_hi + mix_hi + bogo_hi + tr_hi + cta_hi
    return make_plan(ctx, family="event", facts=facts, angle=angle,
                     levers=["counter-intuitive data", "loss aversion", "existing-offer leverage", "single yes/no"],
                     cta_type="binary_yes_no", en=en, hi=hi,
                     action_offer=f"set up tonight's delivery-only match-night offer and banner for {match}",
                     must=[S], extra_numbers=extra)


def h_competitor(ctx: Ctx) -> Plan:
    S = ctx.merchant_salutation()
    p = ctx.p
    comp, dist, their, opened = p.get("competitor_name"), p.get("distance_km"), p.get("their_offer"), p.get("opened_date")
    fan = ctx.top_review("pos")
    offers = ctx.active_offers()
    facts = [f"Competitor: {comp}, {dist} km away, opened {fmt.date_h(opened, True)}, offer: {their}" if comp else
             "Trigger: a new competitor listing appeared nearby (no name/distance given)",
             f"Top positive review theme: {fmt.humanize(fan.get('theme'))} ({fan.get('occurrences_30d')}x)"
             + (f" — \"{fan.get('common_quote')}\"" if fan.get("common_quote") else "") if fan else "",
             f"Merchant's active offers: {'; '.join(offers)}" if offers else ""]
    extra = set()
    price_en = price_hi = ""
    if their and offers:
        import re as _re
        tp = _re.search(r"₹\s?([\d,]+)", their)
        mine = next((o for o in offers if _re.search(r"₹\s?([\d,]+)", o)), None)
        mp = _re.search(r"₹\s?([\d,]+)", mine) if mine else None
        if tp and mp:
            diff = int(mp.group(1).replace(",", "")) - int(tp.group(1).replace(",", ""))
            if diff > 0:
                extra.add(str(diff))
                price_en = f" They're pitching '{their}' — ₹{diff} under your '{mine}'. Don't chase the price;"
                price_hi = f" Woh '{their}' de rahe hain — aapke '{mine}' se ₹{diff} kam. Price war mat kijiye;"
    if comp:
        head_en = f"{S}, heads-up: {comp} opened {dist} km from you on {fmt.date_h(opened)}."
        head_hi = f"{S}, heads-up: {comp} aapse {dist} km door {fmt.date_h(opened)} ko khula hai."
    else:
        head_en = f"{S}, heads-up: a new competitor listing has come up in your area this week."
        head_hi = f"{S}, heads-up: is hafte aapke area mein ek naya competitor listing aaya hai."
    if fan:
        what = fmt.humanize(fan.get("theme"))
        quote = fan.get("common_quote")
        str_en = (f" compete on what they can't copy — {fan.get('occurrences_30d')} reviews this month praise your {what}"
                  + (f" (\"{quote}\")" if quote else "") + ".")
        str_hi = (f" jo woh copy nahi kar sakte uspe compete kijiye — is mahine {fan.get('occurrences_30d')} reviews aapke {what} ki taareef karte hain"
                  + (f" (\"{quote}\")" if quote else "") + ".")
        if not price_en:
            str_en = " Best response is to" + str_en
            str_hi = " Sabse accha jawab:" + str_hi
        cta_en = "Want me to draft a Google post that leads with those reviews?"
        cta_hi = "Un reviews ko aage rakh ke ek Google post draft kar doon?"
        action = "draft a Google post leading with the merchant's best reviews"
    else:
        views = ctx.perf.get("views")
        str_en = f" Your {fmt.indian_int(views)} monthly views are the lead to protect — keep your listing fresh this week." if views else " Keep your listing fresh this week."
        str_hi = f" Aapke {fmt.indian_int(views)} monthly views bachane hain — is hafte listing fresh rakhiye." if views else " Is hafte listing fresh rakhiye."
        if not price_en:
            str_en, str_hi = str_en.lstrip(), str_hi.lstrip()
            str_en, str_hi = " " + str_en, " " + str_hi
        cta_en = "Want me to draft a post featuring your best offer to hold your ground?"
        cta_hi = "Best offer ke saath ek post draft kar doon?"
        action = "draft a post featuring the best offer"
    tr_en, tr_hi, tr_fact = trial_clause(ctx)
    if tr_fact:
        facts.append(tr_fact)
    en = f"{head_en}{price_en}{str_en}{tr_en} {cta_en}"
    hi = f"{head_hi}{price_hi}{str_hi}{tr_hi} {cta_hi}"
    return make_plan(ctx, family="event", facts=facts,
                     angle="Competitor nearby: don't price-war; differentiate on proof the merchant already owns (reviews).",
                     levers=["loss aversion", "social proof (own reviews)", "judgement (no price war)", "single yes/no"],
                     cta_type="binary_yes_no", en=en, hi=hi, action_offer=action, must=[S], extra_numbers=extra,
                     avoid=["Do not name any competitor unless it is in the facts."] if not comp else [])


def h_weather(ctx: Ctx) -> Plan:
    S = ctx.merchant_salutation()
    p = ctx.p
    temp = p.get("temp_c") or p.get("temperature_c") or p.get("temp")
    city = p.get("city") or ctx.city
    blob = f"{ctx.kind} {' '.join(str(v) for v in p.values())}".lower()
    rain = any(w in blob for w in ("rain", "monsoon", "flood", "storm", "cyclone"))
    heat_ideas = {"restaurants": ("push cold drinks and delivery — people avoid stepping out in the heat",
                                  "cold drinks aur delivery push kijiye — garmi mein log bahar kam nikalte hain"),
                  "pharmacies": ("keep ORS and sunscreen at the counter and offer home delivery",
                                 "ORS aur sunscreen counter par rakhiye, home delivery offer kijiye"),
                  "gyms": ("nudge members to the early-morning and evening slots", "members ko early-morning aur evening slots ki taraf le jaiye"),
                  "salons": ("promote cooling treatments like a hair spa", "hair spa jaise cooling treatments promote kijiye"),
                  "dentists": ("move walk-ins to cooler morning and evening slots", "walk-ins ko subah aur shaam ke slots mein shift kijiye")}
    rain_ideas = {"restaurants": ("run a rain-day delivery offer — monsoon evenings shift orders to delivery",
                                  "rain-day delivery offer chalaiye — baarish mein orders delivery par shift hote hain"),
                  "pharmacies": ("stock anti-fungal and anti-bacterial basics at the counter and push home delivery",
                                 "anti-fungal aur anti-bacterial basics counter par rakhiye, home delivery push kijiye"),
                  "gyms": ("remind members the indoor classes are on, rain or not", "members ko yaad dilaiye ki indoor classes baarish mein bhi chal rahi hain"),
                  "salons": ("promote anti-frizz and scalp treatments", "anti-frizz aur scalp treatments promote kijiye"),
                  "dentists": ("offer easy rescheduling for today's appointments", "aaj ke appointments ke liye aasaan rescheduling offer kijiye")}
    ideas = rain_ideas if rain else heat_ideas
    idea = ideas.get(ctx.slug, ("adjust today's offer to the weather", "mausam ke hisaab se aaj ka offer adjust kijiye"))
    beat = ctx.seasonal_matching("monsoon") if rain else None
    what_en = f"{temp}°C in {city} today" if temp else (f"heavy rain alert in {city} today" if rain else f"heat alert in {city} today")
    what_hi = f"aaj {city} mein {temp}°C hai" if temp else (f"aaj {city} mein heavy rain alert hai" if rain else f"aaj {city} mein heat alert hai")
    facts = [f"Weather: {what_en}", f"Seasonal beat: {beat.get('month_range')} — {beat.get('note')}" if beat else ""]
    en = f"{S}, {what_en}. Quick move: {idea[0]}. Want me to put up a same-day post for it?"
    hi = f"{S}, {what_hi}. Aaj ka move: {idea[1]}. Same-day post laga doon?"
    return make_plan(ctx, family="event", facts=facts, angle="Weather event → a same-day, category-specific operational move.",
                     levers=["timeliness", "specificity", "single yes/no"], cta_type="binary_yes_no", en=en, hi=hi,
                     action_offer="publish a same-day weather post", must=[S])


def h_local_news(ctx: Ctx) -> Plan:
    S = ctx.merchant_salutation()
    p = ctx.p
    head = p.get("headline") or p.get("event") or p.get("title") or fmt.humanize(ctx.kind)
    impact = p.get("impact") or p.get("summary")
    facts = [f"Local news: {head}" + (f" — {impact}" if impact else "")]
    en = f"{S}, local update: {head}." + (f" {impact}." if impact else "") + " Want me to adjust today's post and offer for it?"
    hi = f"{S}, local update: {head}." + (f" {impact}." if impact else "") + " Aaj ka post aur offer iske hisaab se adjust kar doon?"
    return make_plan(ctx, family="event", facts=facts, angle="Local event with a direct operational implication.",
                     levers=["timeliness", "relevance", "single yes/no"], cta_type="binary_yes_no", en=en, hi=hi,
                     action_offer="adjust today's post and offer for the local event", must=[S])


# ============================================================ reviews
def h_single_review(ctx: Ctx) -> Plan:
    """A specific new review in the payload (rating/text) — reply fast, don't argue."""
    S = ctx.merchant_salutation()
    p = ctx.p
    rating = p.get("rating") or p.get("stars")
    text = p.get("review_text") or p.get("text") or p.get("quote") or p.get("common_quote")
    if not text and not rating:
        return h_review_theme(ctx)
    when = fmt.date_h(p.get("posted_at") or p.get("date"))
    facts = [f"New review: {rating}★" + (f" on {when}" if when else "") + (f" — \"{text}\"" if text else "")]
    neg = isinstance(rating, (int, float)) and rating <= 3
    r_en = f"a {rating}★ review" if rating else "a new review"
    q_en = f": \"{text}\"" if text else ""
    if neg:
        en = (f"{S}, {r_en} just landed on {ctx.name}{q_en}. A calm, specific reply within a day tells every future reader "
              f"you fix things. Want me to draft one for your approval?")
        hi = (f"{S}, {ctx.name} par {r_en.replace('a ', '', 1)} aaya hai{q_en}. Ek din ke andar shaant, specific reply se naye customers ko dikhta hai "
              f"ki aap cheezein theek karte ho. Approval ke liye reply draft kar doon?")
        action = "draft a calm public reply to the new review"
    else:
        en = (f"{S}, {r_en} just came in for {ctx.name}{q_en}. Worth a thank-you reply and a quick Google post quoting it. "
              f"Want me to draft both?")
        hi = (f"{S}, {ctx.name} ke liye {r_en.replace('a ', '', 1)} aaya hai{q_en}. Thank-you reply aur ise quote karta ek Google post banta hai. Dono draft kar doon?")
        action = "draft a thank-you reply and a Google post quoting the review"
    return make_plan(ctx, family="reviews", facts=facts,
                     angle="A specific new review: respond quickly and publicly; negative → fix-it tone, positive → amplify.",
                     levers=["loss aversion" if neg else "social proof", "specificity (their words)", "effort externalisation", "single yes/no"],
                     cta_type="binary_yes_no", en=en, hi=hi, action_offer=action, must=[S])


def h_review_theme(ctx: Ctx) -> Plan:
    S = ctx.merchant_salutation()
    p = ctx.p
    theme, n, trend, quote = p.get("theme"), p.get("occurrences_30d"), p.get("trend"), p.get("common_quote")
    if not theme:
        neg = ctx.top_review("neg")
        if neg:
            theme, n, quote = neg.get("theme"), neg.get("occurrences_30d"), neg.get("common_quote")
    if not theme:
        rating, reviews = ctx.peer("avg_rating"), ctx.peer("avg_review_count")
        facts = ["Trigger: a theme is emerging in recent Google reviews (details not provided)"]
        peer_en = peer_hi = ""
        if rating and reviews:
            facts.append(f"Peer benchmark: {rating}★ average over {reviews} reviews")
            peer_en = f" Peers in your category average {rating}★ across {reviews} reviews — replies are part of how that trust gets built."
            peer_hi = f" Aapki category mein peers ka average {rating}★ hai, {reviews} reviews par — replies se hi yeh trust banta hai."
        en = (f"{S}, a pattern is starting to show in {ctx.name}'s recent Google reviews, and new customers read these before they call."
              f"{peer_en} Want me to pull the recent ones and draft a reply to each for your approval?")
        hi = (f"{S}, {ctx.name} ke recent Google reviews mein ek pattern dikhne laga hai — naye customers call karne se pehle yahi padhte hain."
              f"{peer_hi} Recent reviews nikaal ke har ek ka reply draft kar doon, aapke approval ke liye?")
        return make_plan(ctx, family="reviews", facts=facts,
                         angle="Emerging review pattern with no detail given: don't guess the theme; offer to pull and reply.",
                         levers=["loss aversion", "social proof (peer benchmark)", "effort externalisation", "single yes/no"],
                         cta_type="binary_yes_no", en=en, hi=hi, action_offer="pull the recent reviews and draft replies for approval",
                         must=[S], avoid=["Do not state what the reviews say — the theme is not in the data."])
    what = fmt.humanize(theme)
    facts = [f"Review theme: {what}, {n} mentions in 30 days" + (f", trend {trend}" if trend else "") + (f"; quote: \"{quote}\"" if quote else "")]
    q_en = f" — one says \"{quote}\"" if quote else ""
    tr_en = " and it's rising" if trend == "rising" else ""
    tr_hi = " aur yeh badh raha hai" if trend == "rising" else ""
    en = (f"{S}, {n} reviews in the last 30 days mention {what}{q_en}{tr_en}. New customers read these before they call. "
          f"Two quick moves: a calm public reply to each, and fixing the root cause. Want me to draft the {n} replies for you to approve?")
    hi = (f"{S}, pichhle 30 din mein {n} reviews mein {what} ki shikayat hai{(' — ek mein likha hai \"' + quote + '\"') if quote else ''}{tr_hi}. "
          f"Naye customers call karne se pehle yeh padhte hain. Har review ka ek shaant public reply aur root cause fix — "
          f"{n} replies draft karke aapko approve ke liye bhej doon?")
    return make_plan(ctx, family="reviews", facts=facts,
                     angle="Emerging negative review pattern: protect conversion with replies + an operational fix.",
                     levers=["loss aversion (new customers read reviews)", "specificity (count + quote)", "effort externalisation", "single yes/no"],
                     cta_type="binary_yes_no", en=en, hi=hi, action_offer=f"draft public replies to the {n} '{what}' reviews", must=[S])


# ============================================================ conversational family
def h_curious(ctx: Ctx) -> Plan:
    S = ctx.merchant_salutation()
    fan = ctx.top_review("pos")
    orders = None
    import re as _re
    for h in ctx.history():
        m = _re.search(r"(\d+)\s*orders/day", str(h.get("body")))
        if m:
            orders = m.group(1)
    offers = ctx.active_offers()
    facts = []
    if orders and offers:
        facts.append(f"From history: '{offers[0]}' averaging {orders} orders/day")
        en = (f"{S}, your '{offers[0]}' is averaging {orders} orders a day. What's the #2 thing lunch customers keep asking for? "
              f"Tell me and I'll build a combo post around it this week.")
        hi = (f"{S}, aapka '{offers[0]}' roz {orders} orders kar raha hai. Lunch pe log doosri kaunsi cheez sabse zyada maangte hain? "
              f"Bata dijiye, is hafte uske saath ek combo post bana deta hoon.")
    elif fan and fan.get("common_quote"):
        what = fmt.humanize(fan.get("theme"))
        facts.append(f"Top positive review theme: {what} ({fan.get('occurrences_30d')}x) — \"{fan.get('common_quote')}\"")
        en = (f"{S}, {fan.get('occurrences_30d')} reviews this month rave about your {what} (\"{fan.get('common_quote')}\"). "
              f"Is that also this week's most-asked service, or is something else moving faster? Tell me which, and I'll turn it into a "
              f"Google post + a ready reply for price enquiries.")
        hi = (f"{S}, is mahine {fan.get('occurrences_30d')} reviews aapke {what} ki taareef kar rahe hain (\"{fan.get('common_quote')}\"). "
              f"Is hafte sabse zyada demand bhi usi ki hai, ya kuch aur? Bata dijiye — main uska Google post + price enquiry ka ready reply bana dunga.")
    else:
        offer = offers[0] if offers else None
        if offer:
            facts.append(f"Active offer: {offer}")
        en = (f"{S}, quick one — which service or item has been most asked-for at {ctx.name} this week? "
              f"It takes 5 minutes: tell me, and I'll turn it into a Google post + a ready WhatsApp reply for enquiries.")
        hi = (f"{S}, ek chhota sawaal — is hafte {ctx.name} mein sabse zyada kya maanga gaya? Bata dijiye, main uska Google post + "
              f"enquiries ke liye ready WhatsApp reply bana deta hoon — bas 5 minute ka kaam, bata dijiye.")
    return make_plan(ctx, family="conversational", facts=facts,
                     angle="Curiosity ask: invite the merchant's own knowledge, with a concrete deliverable promised in return.",
                     levers=["asking the merchant", "reciprocity", "low effort", "open question"],
                     cta_type="open_ended", en=en, hi=hi,
                     action_offer="turn the merchant's answer into a Google post and a ready price-enquiry reply", must=[S])


def h_planning(ctx: Ctx) -> Plan:
    S = ctx.merchant_salutation()
    p = ctx.p
    topic = fmt.humanize(p.get("intent_topic") or "the idea")
    last = p.get("merchant_last_message") or ctx.last_merchant_message()
    offers = ctx.active_offers()
    import re as _re
    facts = [f"Merchant intent: {topic}; merchant said: \"{last}\"" if last else f"Merchant intent: {topic}"]
    extra = set()
    if "thali" in topic or "bulk" in topic or "corporate" in topic:
        base_offer = next((o for o in offers if "₹" in o), None)
        base = int(_re.search(r"₹\s?([\d,]+)", base_offer).group(1).replace(",", "")) if base_offer else None
        orders = None
        for h in ctx.history():
            m = _re.search(r"(\d+)\s*orders/day", str(h.get("body")))
            if m:
                orders = m.group(1)
        deliv = ctx.agg().get("delivery_share_pct")
        if base:
            t1, t2, t3 = base - 10, base - 20, base - 30
            extra |= {str(t1), str(t2), str(t3), "10", "24", "25", "49", "50"}
            facts.append(f"Base offer: {base_offer}" + (f"; averaging {orders} orders/day" if orders else ""))
            if deliv:
                facts.append(f"Delivery share of orders: {fmt.pct(deliv)}")
            loc = ctx.locality or "nearby"
            card = (f"\n\n{ctx.name.split()[0]} Corporate Thali — for {loc} offices\n"
                    f"• 10–24 thalis: ₹{t1} each\n• 25–49 thalis: ₹{t2} each\n• 50+ thalis: ₹{t3} each + free delivery\n"
                    f"• Order by 5pm the day before; delivered 12:30–1pm\n\n")
            why_en = (f"It builds on your {base_offer.split('@')[0].strip()} at ₹{base}" + (f" ({orders}/day already)" if orders else "") +
                      (f", and with {fmt.pct(deliv)} of orders already on delivery, the kitchen flow exists." if deliv else "."))
            why_hi = (f"Yeh aapki ₹{base} wali thali par based hai" + (f" (roz {orders} already)" if orders else "") +
                      (f", aur {fmt.pct(deliv)} orders already delivery hain, toh kitchen flow ready hai." if deliv else "."))
            en = (f"{S}, here's a first cut you can edit:{card}{why_en} "
                  f"Want me to turn this into a Google post + a WhatsApp flyer for office admins?")
            hi = (f"{S}, yeh raha pehla draft — aap edit kar sakte hain:{card}{why_hi} "
                  f"Ise Google post + office admins ke liye WhatsApp flyer bana doon?")
            return make_plan(ctx, family="conversational", facts=facts,
                             angle="Merchant already said yes to the idea — deliver a concrete, editable artifact (tiered pricing anchored on their live offer), not more questions.",
                             levers=["effort externalisation (finished draft)", "specificity (tiers, times)", "momentum", "single yes/no"],
                             cta_type="binary_yes_no", en=en, hi=hi,
                             action_offer="turn the corporate thali plan into a Google post and a WhatsApp flyer for office admins",
                             must=[S], extra_numbers=extra)
    # program-style planning (e.g. kids yoga) — reuse numbers Vera already proposed in history
    prop = ctx.last_vera_message()
    prop_body = str((prop or {}).get("body") or "")
    weeks = _re.search(r"(\d+)-week", prop_body)
    per_week = _re.search(r"(\d+)\s*classes/week", prop_body)
    ages = _re.search(r"age\s*(\d+)\s*[-–]\s*(\d+)", prop_body)
    price = _re.search(r"₹\s?([\d,]+)", prop_body)
    small = next((r for r in ctx.review_themes() if "small" in str(r.get("theme"))), None)
    lines = []
    if weeks or per_week:
        lines.append(f"• {weeks.group(1) + ' weeks' if weeks else ''}{', ' if weeks and per_week else ''}{per_week.group(1) + ' classes a week' if per_week else ''}")
    if ages:
        lines.append(f"• Ages {ages.group(1)}–{ages.group(2)}, grouped by age")
    if price:
        lines.append(f"• ₹{price.group(1)} for the full program")
    lines.append("• Saturday & Sunday morning batches (easiest for parents)")
    if small:
        lines.append(f"• Small batches — your reviews already praise small classes ({small.get('occurrences_30d')} mentions)")
    if prop_body:
        facts.append(f"Vera's earlier proposal: \"{prop_body}\"")
    if small:
        facts.append(f"Review theme: small classes (positive, {small.get('occurrences_30d')}x)")
    card = "\n\n" + "\n".join(lines) + "\n\n"
    en = (f"{S}, here's the {topic} laid out so you can react to something concrete:{card}"
          f"Want me to publish it as a Google post + Insta carousel? Reply YES.")
    hi = (f"{S}, {topic} ka poora structure yeh raha — dekh ke bataiye:{card}"
          f"Ise Google post + Insta carousel bana ke publish kar doon? YES reply karein.")
    return make_plan(ctx, family="conversational", facts=facts,
                     angle="Merchant asked what it should look like — hand over a finished structure built from numbers already agreed.",
                     levers=["effort externalisation (finished draft)", "specificity", "momentum", "single yes/no"],
                     cta_type="binary_yes_no", en=en, hi=hi, action_offer=f"publish the {topic} as a Google post and Insta carousel",
                     must=[S], extra_numbers=extra)


def h_generic(ctx: Ctx) -> Plan:
    """Unknown kind or thin payload: ground in payload scalars + the merchant's strongest real signal."""
    S = ctx.merchant_salutation()
    p = {k: v for k, v in ctx.p.items() if k not in ("placeholder", "metric_or_topic") and isinstance(v, (str, int, float))}
    topic = fmt.humanize(ctx.kind)
    facts = [f"Trigger kind: {topic}"] + [f"{fmt.humanize(k)}: {human_dates(str(v))}" for k, v in list(p.items())[:6]]
    cz = causes(ctx)
    gap = _peer_gap(ctx, "down")
    offer = ctx.best_service_offer()
    detail_en = "; ".join(f"{fmt.humanize(k)} {human_dates(str(v))}" for k, v in list(p.items())[:3])
    if gap:
        mk, mine, peer, _ = gap
        stat_en = f" Your {_metric_label(mk)} is {_fmt_metric_val(mk, mine)} vs a {_fmt_metric_val(mk, peer)} peer average."
        stat_hi = f" Aapka {_metric_label(mk)} {_fmt_metric_val(mk, mine)} hai, peers ka average {_fmt_metric_val(mk, peer)}."
    else:
        v = ctx.perf.get("views")
        stat_en = f" The listing had {fmt.indian_int(v)} views in the last 30 days." if v else ""
        stat_hi = f" Pichhle 30 din mein listing par {fmt.indian_int(v)} views aaye." if v else ""
    fix_en = f" Top fix: {cz[0][0]}." if cz else ""
    fix_hi = f" Sabse pehla fix: {cz[0][1]}." if cz else ""
    en = (f"{S}, quick update on {topic}" + (f" ({detail_en})" if detail_en else "") + f".{stat_en}{fix_en} "
          + (f"Want me to set up '{offer}' and a post around it?" if offer and not ctx.active_offers() else "Want me to draft this week's post around it?"))
    hi = (f"{S}, {topic} par ek update" + (f" ({detail_en})" if detail_en else "") + f".{stat_hi}{fix_hi} "
          + (f"'{offer}' set karke uska post bana doon?" if offer and not ctx.active_offers() else "Iske around is hafte ka post draft kar doon?"))
    return make_plan(ctx, family="generic", facts=facts, angle=f"{topic}: grounded in the merchant's own numbers and one concrete fix.",
                     levers=["specificity", "effort externalisation", "single yes/no"], cta_type="binary_yes_no",
                     en=en, hi=hi, action_offer="draft this week's post and fix the top listing gap", must=[S])
