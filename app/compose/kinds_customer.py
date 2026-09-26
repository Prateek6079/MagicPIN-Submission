"""Customer-facing planners (send_as = "merchant_on_behalf").

Voice: warm, from the merchant's own number, no medical/results claims, customer's language,
real slots/prices only. When the trigger gives no slot/time/price we never invent one — we ask
the customer to pick instead.
"""
from __future__ import annotations

from . import fmt
from .ctx import Ctx
from .plan import Plan, make_plan

EMOJI = {"dentists": "🦷", "salons": "✨", "gyms": "💪", "pharmacies": "", "restaurants": "🍽️"}


def greet(ctx: Ctx) -> tuple[str, str]:
    """Opening line (en, hi) — greeting + who is writing."""
    addressee, _subject = ctx.customer_names()
    intro = ctx.sender_intro()
    em = EMOJI.get(ctx.slug, "")
    em = f" {em}" if em else ""
    reg = ctx.regional_greeting()
    if ctx.lang == "hindi":
        g = f"Namaste {addressee}!" if addressee else "Namaste!"
        return f"{g}{em} {intro} se.", f"{g}{em} {intro} se."
    hello = reg or "Hi"
    g = f"{hello} {addressee}!" if addressee else f"{'Namaste' if ctx.lang != 'en' else hello}!"
    return f"{g}{em} {intro} here.", f"{g}{em} {intro} se."


def subject_phrase(ctx: Ctx, en: bool = True) -> str:
    """'your' or '<child>'s' / 'Sharma ji ki' when the customer is someone else."""
    _addr, subject = ctx.customer_names()
    if subject:
        return f"{subject}'s" if en else f"{subject} ki"
    return "your" if en else "aapki"


def _offer_line(ctx: Ctx, *keywords: str) -> tuple[str | None, str | None]:
    offers = ctx.active_offers()
    for o in offers:
        if not keywords or any(k in o.lower() for k in keywords):
            return o, o
    return None, None


def h_recall(ctx: Ctx) -> Plan:
    g_en, g_hi = greet(ctx)
    p = ctx.p
    svc = fmt.humanize(p.get("service_due") or "") or None
    svc = svc.replace("6 month", "6-month") if svc else None
    slots = [s.get("label") for s in (p.get("available_slots") or []) if s.get("label")]
    last = fmt.date_h(p.get("last_service_date") or ctx.rel.get("last_visit"))
    offer, _ = _offer_line(ctx, "clean", "check", "consult", "analysis", "session", "trial")
    pref = ctx.preferred_slot()
    visits = ctx.rel.get("visits_total")
    facts = [f"Recall: {svc or 'routine visit'} due" + (f" on {fmt.date_h(p.get('due_date'), True)}" if p.get("due_date") else ""),
             f"Last service: {last}" if last else "", f"Open slots: {', '.join(slots)}" if slots else "",
             f"Merchant offer: {offer}" if offer else "", f"Customer prefers: {pref}" if pref else ""]
    svc_en = svc or f"routine {ctx.nouns['visit']}"
    if slots:
        s_en = " or ".join(slots[:2])
        s_hi = " ya ".join(slots[:2])
        pref_en = f" Keeping your {pref} preference in mind, we've held" if pref else " We've held"
        pref_hi = f" Aapke {pref} preference ke hisaab se" if pref else ""
        price_en = f" {offer}." if offer else ""
        choose_en = "Reply 1 for the first, 2 for the second — or tell us a time that suits you." if len(slots) > 1 else "Reply YES to confirm, or tell us a time that suits you."
        choose_hi = "Pehla slot ke liye 1, doosre ke liye 2 reply karein — ya apna time bata dijiye." if len(slots) > 1 else "Confirm ke liye YES reply karein, ya apna time bata dijiye."
        en = (f"{g_en} {subject_phrase(ctx).capitalize()} {svc_en} is due" + (f" (last one was on {last})" if last else "") +
              f".{pref_en} {len(slots[:2])} slot{'s' if len(slots) > 1 else ''}: {s_en}.{price_en} {choose_en}")
        hi = (f"{g_hi} {subject_phrase(ctx, False).capitalize()} {svc_en} ab due hai" + (f" (pichhli baar {last} ko hua tha)" if last else "") +
              f".{pref_hi} {len(slots[:2])} slot rakhe hain: {s_hi}." + (f" {offer}." if offer else "") + f" {choose_hi}")
        cta = "multi_choice_slot" if len(slots) > 1 else "binary_yes_no"
    else:
        default_svc = {"dentists": "routine check-up", "gyms": "check-in", "salons": "next appointment",
                       "pharmacies": "refill", "restaurants": "next visit"}.get(ctx.slug, "next visit")
        svc_en = svc or default_svc
        since_en = f"We haven't seen you since {last}" if last else "It's been a while since your last visit"
        since_hi = f"{last} ke baad aap nahi aaye" if last else "Kaafi time ho gaya"
        unit = "sessions" if ctx.slug == "gyms" else "visits"
        v_en = f" — after {visits} {unit} with us, a {svc_en} is due." if visits and visits > 1 else f" — your {svc_en} is due."
        v_hi = f" — {visits} {unit} ke baad ab {svc_en} ka time hai." if visits and visits > 1 else f" — {svc_en} ka time hai."
        off_en = f" Come in for our {offer} to see where you stand." if offer else ""
        off_hi = f" {offer} ke liye aaiye, dekhte hain aap kahan hain." if offer else ""
        en = f"{g_en} {since_en}{v_en}{off_en} Reply YES and we'll share this week's slots."
        hi = f"{g_hi} {since_hi}{v_hi}{off_hi} YES reply karein, hum is hafte ke slots bhej denge."
        cta = "binary_yes_no"
    return make_plan(ctx, family="customer", facts=facts,
                     angle="Recall window is open: name the service, use real slots/offer, honour the customer's preference and language.",
                     levers=["personalisation", "specificity (dates/slots/price)", "low-friction choice"],
                     cta_type=cta, en=en, hi=hi, action_offer="book the chosen slot and send a confirmation",
                     avoid=["Do not add free extras or discounts that are not in the merchant's offers.", "No medical claims."])


def h_lapsed(ctx: Ctx) -> Plan:
    g_en, g_hi = greet(ctx)
    p = ctx.p
    days = p.get("days_since_last_visit")
    if not days:  # only quote a computed gap when it is clearly a lapse; otherwise use the date itself
        d = ctx.days_since_last_visit()
        days = d if d and d >= 45 else None
    focus = fmt.humanize(p.get("previous_focus") or ctx.prefs.get("training_focus") or ctx.prefs.get("health_focus") or "") or None
    months = p.get("previous_membership_months")
    visits = ctx.rel.get("visits_total")
    last = fmt.date_h(ctx.rel.get("last_visit"))
    pref = ctx.preferred_slot()
    offer, _ = _offer_line(ctx)
    facts = [f"Days since last visit: {days}" if days else (f"Last visit: {last}" if last else ""),
             f"Previous focus: {focus}" if focus else "", f"Was a member for {months} months" if months else "",
             f"Total visits: {visits}" if visits else "", f"Prefers: {pref}" if pref else "",
             f"Merchant offer: {offer}" if offer else ""]
    gap_en = f"It's been {days} days since your last {ctx.nouns['visit']}" if days else (f"We haven't seen you since {last}" if last else "It's been a while")
    gap_hi = f"Aapki last {ctx.nouns['visit']} ko {days} din ho gaye" if days else (f"{last} ke baad aap nahi aaye" if last else "Kaafi time ho gaya")
    if ctx.slug == "gyms":
        base_en = (f" — happens to most of us, no judgment. You'd put in {months} months towards your {focus} goal; that base is still there."
                   if months and focus else " — happens to most of us, no judgment.")
        base_hi = (f" — sabke saath hota hai, koi baat nahi. Aapne {months} mahine {focus} goal par lagaye the; woh base abhi bhi hai."
                   if months and focus else " — sabke saath hota hai, koi baat nahi.")
        if offer:
            base_en += f" We're running {offer} right now — happy to use them to ease you back in."
            base_hi += f" Abhi {offer} chal rahe hain — wapsi aasaan banane ke liye use kar sakte hain."
        ask_en = (f" Want us to set up a simple restart plan around your {pref} slot? Reply YES — no pressure."
                  if pref else " Want us to set up a simple restart plan for you? Reply YES — no pressure.")
        ask_hi = (f" Aapke {pref} slot ke hisaab se ek simple restart plan bana dein? YES reply karein — koi pressure nahi."
                  if pref else " Ek simple restart plan bana dein? YES reply karein — koi pressure nahi.")
    elif ctx.slug == "pharmacies":
        base_en = f" — hope all is well. After {visits} visits, we know your regulars." if visits and visits > 2 else " — hope all is well."
        base_hi = f" — ummeed hai sab theek hai. {visits} visits ke baad aapki regular cheezein humein pata hain." if visits and visits > 2 else " — ummeed hai sab theek hai."
        ask_en = " Send us your list and we'll keep it packed and ready. Just reply here."
        ask_hi = " Apni list bhej dijiye, hum pack karke ready rakh denge. Bas yahin reply karein."
    else:
        base_en = f" — after {visits} visits with us, it's a good time for a routine {ctx.nouns['visit']}." if visits and visits > 1 else f" — a good time for a routine {ctx.nouns['visit']}."
        base_hi = f" — {visits} visits ke baad ab ek routine {ctx.nouns['visit']} ka sahi time hai." if visits and visits > 1 else f" — ek routine {ctx.nouns['visit']} ka sahi time hai."
        off_en = f" {offer} is on right now." if offer else ""
        off_hi = f" Abhi {offer} chal raha hai." if offer else ""
        base_en += off_en
        base_hi += off_hi
        ask_en = f" Reply YES and we'll send a couple of {pref + ' ' if pref else ''}slots for this week."
        ask_hi = f" YES reply karein, hum is hafte ke {pref + ' ke ' if pref else ''}slots bhej denge."
    en = f"{g_en} {gap_en}{base_en}{ask_en}"
    hi = f"{g_hi} {gap_hi}{base_hi}{ask_hi}"
    return make_plan(ctx, family="customer", facts=facts,
                     angle="Lapsed customer: warm, no-guilt re-entry anchored on their own history, one easy yes.",
                     levers=["personalisation (their history)", "no-shame framing", "low-friction yes"],
                     cta_type="binary_yes_no", en=en, hi=hi, action_offer="share slots / set up the restart and confirm",
                     avoid=["Do not invent classes, discounts or free sessions not in the merchant's offers."])


def h_appointment(ctx: Ctx) -> Plan:
    g_en, g_hi = greet(ctx)
    p = ctx.p
    when = fmt.weekday_time(p.get("appointment_iso") or p.get("slot_iso") or p.get("time_iso"))
    svc = fmt.humanize(p.get("service") or "") or None
    facts = [f"Appointment: tomorrow" + (f", {when}" if when else "") + (f", service: {svc}" if svc else "")]
    what_en = f"your {svc} appointment" if svc else "your appointment"
    what_hi = f"aapka {svc} appointment" if svc else "aapka appointment"
    at_en = f" ({when})" if when else ""
    last = fmt.date_h(ctx.rel.get("last_visit"))
    offer = (ctx.active_offers() or [None])[0]
    if last:
        facts.append(f"Last visit: {last}")
    if offer:
        facts.append(f"Merchant offer: {offer}")
    back_en = f" Looking forward to seeing you again — your last visit was on {last}." if last else ""
    back_hi = f" Aapse phir milne ka intezaar hai — pichhli baar aap {last} ko aaye the." if last else ""
    off_en = f" {offer} is on if you'd like to add it." if offer else ""
    off_hi = f" Chahein toh {offer} bhi add kar sakte hain." if offer else ""
    loc_en = f" We're at {ctx.locality}." if ctx.locality and ctx.locality not in g_en else ""
    en = (f"{g_en} Quick reminder: {what_en} with us is tomorrow{at_en}.{back_en}{off_en}{loc_en} "
          f"Reply YES to confirm, or send us a time that works better and we'll move it.")
    hi = (f"{g_hi} Yaad dila dein — kal {what_hi} hai{at_en}.{back_hi}{off_hi} "
          f"Confirm karne ke liye YES reply karein, ya koi aur time chahiye toh bata dijiye.")
    return make_plan(ctx, family="customer", facts=facts,
                     angle="Appointment tomorrow: reduce no-shows with a one-tap confirm and an easy reschedule path.",
                     levers=["timeliness", "low-friction confirm"], cta_type="binary_confirm_cancel", en=en, hi=hi,
                     action_offer="confirm or reschedule the appointment",
                     avoid=["Do not state an appointment time or service that is not in the facts."])


def h_refill(ctx: Ctx) -> Plan:
    g_en, g_hi = greet(ctx)
    p = ctx.p
    mols = [str(m) for m in (p.get("molecule_list") or [])]
    runs_out = fmt.date_h(p.get("stock_runs_out_iso"))
    saved = p.get("delivery_address_saved")
    senior = next((o for o in ctx.active_offers() if "senior" in o.lower()), None) if ctx.cid.get("senior_citizen") else None
    delivery = next((o for o in ctx.active_offers() if "delivery" in o.lower()), None)
    recall = next((d for d in ctx.digest() if d.get("kind") == "alert" and any(m.lower() in (d.get("title", "") + d.get("summary", "")).lower() for m in mols)), None)
    _addr, subject = ctx.customer_names()
    facts = [f"Refill due: {', '.join(mols)}" + (f"; stock runs out {fmt.date_h(p.get('stock_runs_out_iso'), True)}" if runs_out else "") if mols else "Refill due (no molecule list given)",
             "Delivery address saved" if saved else "", f"Offer: {senior}" if senior else "", f"Offer: {delivery}" if delivery else "",
             f"Category alert: {recall.get('title')} — {recall.get('source')}" if recall else ""]
    if mols:
        n = len(mols)
        who_en = f"{subject}'s" if subject else "your"
        who_hi = f"{subject} ki" if subject else "aapki"
        mol_en = ", ".join(mols[:-1]) + f" and {mols[-1]}" if n > 1 else mols[0]
        mol_hi = ", ".join(mols[:-1]) + f" aur {mols[-1]}" if n > 1 else mols[0]
        en = f"{g_en} {who_en.capitalize()} {n} monthly medicines — {mol_en} — run out on {runs_out}. Same pack ready to go?"
        hi = f"{g_hi} {who_hi.capitalize()} {n} monthly dawaiyan — {mol_hi} — {runs_out} ko khatam ho rahi hain. Same pack ready kar dein?"
        if senior:
            en += f" {senior} applies."
            hi += f" {senior} lagega."
        if delivery and saved:
            en += f" {delivery} to the saved address."
            hi += f" {delivery} — saved address par."
        if recall:
            m = next((m for m in mols if m.lower() in (recall.get("title", "") + recall.get("summary", "")).lower()), mols[0])
            en += f" We'll also check the {m} batch against this month's recall list before packing."
            hi += f" {m.capitalize()} ka batch is mahine ki recall list se check karke hi pack karenge."
        en += " Reply YES to dispatch, or tell us if the dose has changed."
        hi += " Dispatch ke liye YES reply karein, ya dose badla ho toh bata dijiye."
    elif ctx.slug in ("dentists", "salons", "gyms"):
        # A "refill" with no product list means nothing to a clinic/salon/gym customer — frame it as the routine follow-up.
        return h_recall(ctx)
    else:
        item_en, item_hi = {
            "restaurants": ("your usual order", "aapka usual order"),
            "salons": ("your regular at-home care products", "aapke regular at-home care products"),
            "gyms": ("your regular pack", "aapka regular pack"),
        }.get(ctx.slug, ("your regular refill", "aapki regular refill"))
        visits = ctx.rel.get("visits_total")
        last = fmt.date_h(ctx.rel.get("last_visit"))
        hist_en = (f" You've been with us for {visits} visits" + (f", the last on {last}" if last else "") + ".") if visits and visits > 1 else ""
        hist_hi = (f" Aap humare saath {visits} visits kar chuke hain" + (f", pichhli {last} ko" if last else "") + ".") if visits and visits > 1 else ""
        doc_en = " If anything has changed, the doctor can take a quick look when you come in." if ctx.slug == "dentists" else ""
        doc_hi = " Kuch badla ho toh aate waqt doctor ek baar dekh lenge." if ctx.slug == "dentists" else ""
        en = (f"{g_en} Going by your past visits, it's about time for {item_en}.{hist_en}{doc_en} Reply YES and we'll have it ready"
              + (" for pickup." if not saved else " for delivery."))
        hi = (f"{g_hi} Pichhli visits ke hisaab se {item_hi} ka time ho gaya hai.{hist_hi}{doc_hi} YES reply karein, hum ready rakh denge.")
    return make_plan(ctx, family="customer", facts=facts,
                     angle="Chronic refill before stock-out: exact molecules and date, real offers, zero effort to confirm; safety check where a recall applies.",
                     levers=["specificity (molecules, date)", "convenience", "trust/safety", "single yes/no"],
                     cta_type="binary_yes_no", en=en, hi=hi, action_offer="pack and dispatch the refill",
                     avoid=["Do not quote a total price — no prices are in the data.", "No dosage advice."])


def h_trial_followup(ctx: Ctx) -> Plan:
    g_en, g_hi = greet(ctx)
    p = ctx.p
    trial = fmt.date_h(p.get("trial_date"))
    opts = [o.get("label") for o in (p.get("next_session_options") or []) if o.get("label")]
    _addr, subject = ctx.customer_names()
    who = subject or "you"
    offer, _ = _offer_line(ctx, "month", "trial", "membership")
    facts = [f"Trial on {trial}" if trial else "Trial completed", f"Next session options: {', '.join(opts)}" if opts else "",
             f"Merchant offer: {offer}" if offer else ""]
    thanks_en = f"Thanks for coming in for the trial on {trial}" if not subject else f"Thanks for bringing {subject} for the trial on {trial}"
    thanks_hi = f"{trial} ko trial ke liye aane ka shukriya" if not subject else f"{subject} ko {trial} ko trial ke liye laane ka shukriya"
    if not trial:
        thanks_en, thanks_hi = thanks_en.split(" on ")[0], thanks_hi.replace(" ko trial", " trial")
    if opts:
        en = f"{g_en} {thanks_en}. The next session is {opts[0]} — want us to hold {'a spot for ' + who if subject else 'your spot'}? Reply YES to confirm."
        hi = f"{g_hi} {thanks_hi}. Agla session {opts[0]} ko hai — {'unki' if subject else 'aapki'} seat hold kar dein? Confirm ke liye YES reply karein."
    else:
        off_en = f" If you'd like to continue, {offer} is on right now." if offer else ""
        en = f"{g_en} {thanks_en}.{off_en} Reply YES and we'll share this week's session times."
        hi = f"{g_hi} {thanks_hi}." + (f" Continue karna ho toh abhi {offer} chal raha hai." if offer else "") + " YES reply karein, is hafte ke session times bhej denge."
    return make_plan(ctx, family="customer", facts=facts,
                     angle="Post-trial: convert while intent is fresh, with a concrete next session.",
                     levers=["momentum", "specificity (next session)", "single yes/no"], cta_type="binary_yes_no",
                     en=en, hi=hi, action_offer="hold the next session spot and confirm")


def h_bridal(ctx: Ctx) -> Plan:
    g_en, g_hi = greet(ctx)
    p = ctx.p
    wd = fmt.date_h(p.get("wedding_date") or ctx.prefs.get("wedding_date"))
    days = p.get("days_to_wedding")
    trial = fmt.date_h(p.get("trial_completed"))
    step = fmt.humanize(p.get("next_step_window_open") or "").replace("30day", "30-day") or "next prep step"
    pref = ctx.preferred_slot()
    beat = ctx.seasonal_matching("bridal")
    facts = [f"Wedding: {wd} ({days} days away)" if wd else "", f"Bridal trial done: {trial}" if trial else "",
             f"Next step window open: {step}", f"Prefers: {pref}" if pref else "",
             f"Seasonal: {beat.get('month_range')} — {beat.get('note')}" if beat else ""]
    rush_en = f" {beat.get('month_range')} weekends fill up fast in wedding season." if beat else ""
    rush_hi = f" Wedding season mein {beat.get('month_range')} ke weekends jaldi bhar jaate hain." if beat else ""
    en = (f"{g_en} {days} days to go for {wd}! 💍 Since your bridal trial on {trial}, the next step is the {step} — "
          f"starting early keeps it relaxed.{rush_en} Want us to book a {pref or 'weekend'} consult to plan it? Reply YES.")
    hi = (f"{g_hi} {wd} ke liye {days} din baaki! 💍 {trial} ke bridal trial ke baad ab {step} ka time hai — "
          f"jaldi shuru karne se sab relaxed rehta hai.{rush_hi} {pref or 'weekend'} consult book kar dein? YES reply karein.")
    return make_plan(ctx, family="customer", facts=facts,
                     angle="Bridal journey: countdown specificity + the next step in their own journey, booked on their preferred day.",
                     levers=["countdown specificity", "continuity", "scarcity (season)", "single yes/no"],
                     cta_type="binary_yes_no", en=en, hi=hi, action_offer="book the skin-prep planning consult",
                     avoid=["Do not quote a price for the program — none is in the data."])


def h_slot_open(ctx: Ctx) -> Plan:
    g_en, g_hi = greet(ctx)
    p = ctx.p
    label = p.get("slot_label") or fmt.weekday_time(p.get("slot_iso"))
    offer, _ = _offer_line(ctx)
    facts = [f"Slot opened: {label}" if label else "A slot opened up", f"Offer: {offer}" if offer else ""]
    en = f"{g_en} A slot just opened up{(' — ' + label) if label else ''}." + (f" {offer}." if offer else "") + " Want it? Reply YES and it's yours."
    hi = f"{g_hi} Ek slot abhi khula hai{(' — ' + label) if label else ''}." + (f" {offer}." if offer else "") + " Chahiye? YES reply karein, aapke naam kar denge."
    return make_plan(ctx, family="customer", facts=facts, angle="Unplanned capacity offered to a likely-to-book customer.",
                     levers=["scarcity", "convenience", "single yes/no"], cta_type="binary_yes_no", en=en, hi=hi,
                     action_offer="book the open slot")


def h_generic_customer(ctx: Ctx) -> Plan:
    g_en, g_hi = greet(ctx)
    topic = fmt.humanize(ctx.kind)
    offer, _ = _offer_line(ctx)
    last = fmt.date_h(ctx.rel.get("last_visit"))
    facts = [f"Trigger: {topic}", f"Last visit: {last}" if last else "", f"Offer: {offer}" if offer else ""]
    en = f"{g_en} A quick note from us" + (f" since your last visit on {last}" if last else "") + "." + (f" {offer} is on right now." if offer else "") + " Reply YES and we'll share the details."
    hi = f"{g_hi} Aapke liye ek chhota update" + (f" — last visit {last} ko thi" if last else "") + "." + (f" Abhi {offer} chal raha hai." if offer else "") + " YES reply karein, details bhej denge."
    return make_plan(ctx, family="customer", facts=facts, angle=f"{topic}: warm, grounded note with one easy yes.",
                     levers=["personalisation", "single yes/no"], cta_type="binary_yes_no", en=en, hi=hi,
                     action_offer="share details and book")
