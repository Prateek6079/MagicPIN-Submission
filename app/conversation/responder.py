"""Reply policy for /v1/reply.

Decision order (deterministic first, LLM only where it adds value):
  auto-reply  -> 1st: one owner-flag nudge; 2nd: wait 24h; 3rd+: end          (per merchant, across conversations)
  opt-out     -> end + suppress merchant
  hostile     -> 1st: short apology + STOP path; 2nd: end
  off-topic   -> polite decline + redirect to the open thread
  later       -> wait (duration parsed from the message)
  decline     -> end (graceful, no pitch)
  commit      -> ACTION MODE: deliver/confirm the concrete next step, never re-qualify
  question    -> answer from facts, then one ask
  slot choice -> confirm booking (customer flows)
Also: per-turn language mirroring, anti-repetition, and a turn cap.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta

from ..compose.ctx import Ctx
from ..compose.plan import Plan, snapshot_facts, tidy
from ..compose.validator import validate
from ..llm.groq import LLMClient
from ..store import ContextStore
from ..timeutil import iso, parse_ts, utcnow
from .intents import OFF_TOPIC_REPLY, Classified, classify, norm_text
from .state import Conversation, StateStore, norm_body

log = logging.getLogger(__name__)
MAX_BOT_TURNS = 6
QUALIFYING = re.compile(r"\b(would you|do you|can you tell|what if|how about|could you (share|tell)|may i know|which one)\b", re.I)


def L(lang: str, en: str, hi: str) -> str:
    return hi if lang in ("hinglish", "hindi") else en


SYSTEM_REPLY = """You are Vera, magicpin's merchant-growth assistant{who}, continuing a WhatsApp conversation.

HARD RULES (breaking one = rejected):
1. Use ONLY facts in FACTS / the conversation. Never invent numbers, prices, offers, slots, names, research or results.
2. One clear next step, as the LAST sentence. No URLs. No preamble, no re-introducing yourself, no sign-off.
3. Language for this reply: {language}
4. Never repeat an earlier message. Under 600 characters unless you are delivering a draft (then short lines are fine).
5. Never use: guaranteed, 100% safe, miracle, cure.

CONVERSATION POLICY — the latest message was classified as: {intent}.
REQUIRED MOVE: {move}

Return JSON only: {{"body": "<reply>", "cta": "binary_yes_no|binary_confirm_cancel|open_ended|none", "rationale": "<one sentence>"}}"""

MOVES = {
    "commit": ("ACTION MODE. They said yes — do NOT ask any qualifying or clarifying question (no 'would you', 'do you', "
               "'which', 'how about'). Deliver the promised work right now: one short line saying it's done, then the actual "
               "draft inline after 'Draft:' (3-6 short lines, only FACTS). If the draft is meant for their customers/patients "
               "(WhatsApp, post), write it TO those customers in simple, warm language — not to the merchant, no jargon, no "
               "medical claims. Checklists/plans are written to the merchant. End with a single 'Reply CONFIRM to …'."),
    "question": "Answer their question directly and briefly from FACTS (if FACTS don't cover it, say so honestly and offer what you can do). Then one simple ask.",
    "other": "Acknowledge what they said in one line, fold it into the plan, and move one concrete step forward with a single ask.",
    "customer_question": "Answer as the business, briefly and warmly, from FACTS only. Then one simple ask (e.g. confirm a slot).",
}


class Responder:
    def __init__(self, store: ContextStore, state: StateStore, llm: LLMClient | None):
        self.store = store
        self.state = state
        self.llm = llm

    # ------------------------------------------------------------------ entry
    async def handle(self, req: dict, deadline: float | None = None) -> dict:
        conv = self._get_or_adopt(req)
        now = parse_ts(req.get("received_at")) or utcnow()
        msg = str(req.get("message") or "")
        role = req.get("from_role") or ("customer" if conv.audience == "customer" else "merchant")
        party_id = conv.customer_id if conv.audience == "customer" else conv.merchant_id
        party = self.state.customer(party_id) if conv.audience == "customer" else self.state.merchant(party_id)

        key = norm_text(msg)
        substantial = len(key) >= 30 and len(key.split()) >= 5
        repeated = party.inbound.get(key, 0) if substantial else 0
        party.inbound[key] += 1
        conv.add(role, msg, req.get("received_at"))
        c = classify(msg, audience=conv.audience, repeated=repeated, slot_labels=conv.slots)
        lang = self._reply_lang(conv, c, msg)
        if c.intent != "auto_reply":
            party.auto_streak = 0
            conv.nudges_unanswered = 0

        if conv.status == "ended" and c.intent in ("auto_reply", "opt_out", "hostile", "decline", "thanks"):
            return self._end(conv, f"Conversation already closed; '{c.intent}' needs no further reply.")
        if conv.status == "ended":
            conv.status = "open"  # merchant re-engaged after we exited — respond, but gently

        handler = getattr(self, f"_on_{c.intent}", self._on_other)
        out = await handler(conv, c, msg, lang, party, now, deadline)
        return self._finalise(conv, out, lang, now, party)

    # ------------------------------------------------------------------ adoption of unknown conversations
    def _get_or_adopt(self, req: dict) -> Conversation:
        cid = req.get("conversation_id") or f"conv_adhoc_{int(time.time())}"
        with self.state.lock:
            conv = self.state.conversations.get(cid)
            if conv:
                return conv
            merchant = self.store.get("merchant", req.get("merchant_id"))
            customer = self.store.get("customer", req.get("customer_id")) if req.get("customer_id") else None
            category = self.store.get("category", (merchant or {}).get("category_slug")) if merchant else None
            audience = "customer" if (req.get("from_role") == "customer" or customer) else "merchant"
            ctx = Ctx(category, merchant, {"kind": "conversation", "scope": audience, "payload": {}}, customer)
            conv = Conversation(id=cid, merchant_id=req.get("merchant_id"), customer_id=req.get("customer_id"),
                                kind="conversation", audience=audience,
                                send_as="merchant_on_behalf" if audience == "customer" else "vera", lang=ctx.lang,
                                salutation=(ctx.customer_names()[0] if audience == "customer" else ctx.merchant_salutation()),
                                action_offer=infer_pending_action(ctx), facts=snapshot_facts(ctx) if merchant else [],
                                created_at=req.get("received_at"))
            self.state.conversations[cid] = conv
            return conv

    def _ctx(self, conv: Conversation) -> Ctx:
        merchant = self.store.get("merchant", conv.merchant_id)
        category = self.store.get("category", (merchant or {}).get("category_slug")) if merchant else None
        customer = self.store.get("customer", conv.customer_id) if conv.customer_id else None
        return Ctx(category, merchant, {"kind": conv.kind, "scope": conv.audience, "payload": {}}, customer)

    def _reply_lang(self, conv: Conversation, c: Classified, msg: str) -> str:
        if len(msg.split()) >= 3 and c.lang in ("hinglish", "hindi"):
            return "hindi" if (conv.lang == "hindi" or c.lang == "hindi") else "hinglish"
        if len(msg.split()) >= 4 and c.lang == "en":
            return "en"
        return conv.lang

    # ------------------------------------------------------------------ deterministic intents
    async def _on_auto_reply(self, conv, c, msg, lang, party, now, deadline) -> dict:
        party.auto_streak += 1
        conv.auto_count += 1
        n = max(party.auto_streak, conv.auto_count)
        if n == 1:
            offer = conv.action_offer or "take care of the next step for you"
            body = L(lang,
                     f"Looks like an automated reply 🙂 Whenever the owner sees this: I can {offer} — just reply YES and I'll take it from there.",
                     f"Lagta hai yeh auto-reply hai 🙂 Owner jab dekhein: I can {offer} — bas YES reply kar dijiye, baaki main sambhal lunga.")
            return {"action": "send", "body": body, "cta": "binary_yes_no",
                    "rationale": f"Detected WhatsApp auto-reply ({c.reason}); one short owner-flag with a one-word CTA, no further pitching."}
        if n == 2:
            party.backoff_until = iso(now + timedelta(hours=24))
            conv.status = "waiting"
            return {"action": "wait", "wait_seconds": 86400,
                    "rationale": "Same auto-reply again — the owner isn't at the phone. Backing off 24h instead of burning turns."}
        return self._end(conv, f"Auto-reply {n}x in a row with zero human signal — closing the conversation to avoid spam.",
                         party=party, backoff_hours=72, now=now)

    async def _on_opt_out(self, conv, c, msg, lang, party, now, deadline) -> dict:
        party.opted_out = True
        return self._end(conv, "Explicit opt-out ('stop'/'not interested') — ending immediately and suppressing all future proactive sends to this party.")

    async def _on_hostile(self, conv, c, msg, lang, party, now, deadline) -> dict:
        conv.hostile_count += 1
        if conv.hostile_count >= 2:
            party.backoff_until = iso(now + timedelta(days=30))
            return self._end(conv, "Repeated frustration — closing politely and pausing proactive messages for 30 days.")
        name = self._ctx(conv).name if conv.audience == "merchant" else "you"
        if c.off_topic_kind:
            en_extra, hi_extra = OFF_TOPIC_REPLY[c.off_topic_kind]
            body = L(lang, f"Sorry for the bother. {en_extra} I'll only message when something directly affects {name} — reply STOP anytime and I'll go quiet.",
                     f"Maaf kijiye, pareshan karna maksad nahi tha. {hi_extra} Sirf wahi bhejunga jo {name} ko seedha affect kare — STOP reply karein toh bilkul band.")
        else:
            body = L(lang, f"Sorry — didn't mean to bother you. I'll keep messages to things that directly move {name}'s numbers. If you'd rather not hear from me at all, just reply STOP.",
                     f"Maaf kijiye — pareshan karna maksad nahi tha. Sirf wahi bhejunga jo {name} ke numbers ko seedha affect kare. Bilkul nahi chahiye toh bas STOP reply kar dijiye.")
        party.backoff_until = iso(now + timedelta(days=7))
        return {"action": "send", "body": body, "cta": "none",
                "rationale": "Frustration without an explicit stop: one-line apology, no pitch, clear opt-out path; proactive sends paused 7 days."}

    async def _on_off_topic(self, conv, c, msg, lang, party, now, deadline) -> dict:
        en, hi = OFF_TOPIC_REPLY.get(c.off_topic_kind or "personal", OFF_TOPIC_REPLY["personal"])
        offer = conv.action_offer
        if conv.hostile_count > 0:  # they were upset a turn ago — help, don't pitch
            body = L(lang, f"{en} If anything on your Google listing or offers needs a hand, I'm a message away.",
                     f"{hi} Google listing ya offers mein kabhi madad chahiye ho, bas ek message kijiye.")
            return {"action": "send", "body": body, "cta": "none",
                    "rationale": f"Off-topic ask ({c.off_topic_kind}) right after frustration: polite decline, stay available, no re-pitch."}
        if conv.stage == "action":
            body = L(lang, f"{en} Meanwhile, the draft above is ready — reply CONFIRM and I'll send it out.",
                     f"{hi} Tab tak upar wala draft ready hai — CONFIRM reply kijiye, main bhej dunga.")
            return {"action": "send", "body": body, "cta": "binary_confirm_cancel",
                    "rationale": f"Out-of-scope ask ({c.off_topic_kind}) declined; pointed back to the draft awaiting confirmation."}
        if conv.stage == "confirmed" or not offer:
            body = L(lang, f"{en} On the growth side, I'm here whenever you need me — just message.",
                     f"{hi} Business growth ke liye main hamesha yahin hoon — bas message kijiye.")
            cta = "none"
        else:
            body = L(lang, f"{en} Coming back to where we were — I can still {offer}. Shall I go ahead?",
                     f"{hi} Jahan ruke the wahin se — I can still {offer}. Aage badhoon?")
            cta = "binary_yes_no"
        return {"action": "send", "body": body, "cta": cta,
                "rationale": f"Out-of-scope request ({c.off_topic_kind}) politely declined; redirected to the open thread without losing it."}

    async def _on_later(self, conv, c, msg, lang, party, now, deadline) -> dict:
        low = msg.lower()
        secs = 4 * 3600
        if re.search(r"next week|agle hafte", low):
            secs = 7 * 86400
        elif re.search(r"tomorrow|\bkal\b", low):
            secs = 86400
        elif re.search(r"weekend", low):
            secs = 2 * 86400
        elif re.search(r"evening|shaam|tonight|raat", low):
            secs = 6 * 3600
        elif re.search(r"(\d+)\s*(min|mins|minutes)", low):
            secs = max(600, int(re.search(r"(\d+)\s*(min|mins|minutes)", low).group(1)) * 60)
        elif re.search(r"(\d+)\s*(hour|hours|hr|hrs|ghante)", low):
            secs = int(re.search(r"(\d+)\s*(hour|hours|hr|hrs|ghante)", low).group(1)) * 3600
        conv.status = "waiting"
        party.backoff_until = iso(now + timedelta(seconds=secs))
        return {"action": "wait", "wait_seconds": secs,
                "rationale": f"Merchant asked for time ('{msg[:40]}'); backing off {secs // 3600 or secs // 60}{'h' if secs >= 3600 else 'm'} without another nudge."}

    async def _on_decline(self, conv, c, msg, lang, party, now, deadline) -> dict:
        return self._end(conv, "Clear 'no' to this proposal — exiting gracefully without re-pitching; merchant stays reachable for future triggers.",
                         party=party, backoff_hours=24, now=now)

    async def _on_thanks(self, conv, c, msg, lang, party, now, deadline) -> dict:
        if conv.stage in ("action", "confirmed") or any(t.get("thanks_reply") for t in conv.turns if t["role"] == "bot"):
            return self._end(conv, "Merchant signed off with thanks after the work was handed over — closing warmly, nothing pending.")
        offer = conv.action_offer or "set it up"
        body = L(lang, f"Anytime 🙂 Whenever you're ready, just reply YES and I'll {offer}.",
                 f"Koi baat nahi 🙂 Jab ready hon, bas YES reply kijiye — I'll {offer}.")
        return {"action": "send", "body": body, "cta": "binary_yes_no", "thanks_reply": True,
                "rationale": "Polite thanks without a decision: one light, no-pressure reminder of the single next step."}

    async def _on_slot_choice(self, conv, c, msg, lang, party, now, deadline) -> dict:
        ctx = self._ctx(conv)
        slot = conv.slots[c.slot_index] if c.slot_index is not None and c.slot_index < len(conv.slots) else None
        who = conv.salutation or ""
        if not slot:
            return await self._on_other(conv, c, msg, lang, party, now, deadline)
        conv.stage = "confirmed"
        body = L(lang, f"Booked ✅ {who + ', ' if who else ''}you're confirmed for {slot} at {ctx.name}. We'll send a reminder the day before — reply here if anything changes.",
                 f"Ho gaya ✅ {who + ' ji, ' if who else ''}aapka slot {slot} ke liye {ctx.name} mein confirm hai. Ek din pehle reminder bhejenge — kuch badle toh yahin reply karein.")
        return {"action": "send", "body": body, "cta": "none", "rationale": f"Customer picked '{slot}' — confirmed the booking in one message, no extra questions."}

    # ------------------------------------------------------------------ LLM-assisted intents
    async def _on_commit(self, conv, c, msg, lang, party, now, deadline) -> dict:
        if conv.audience == "customer":
            return await self._customer_commit(conv, c, msg, lang, deadline)
        if conv.stage == "action":
            conv.stage = "confirmed"
            body = L(lang, "Done ✅ It's live now. I'll share how it performs in a week — nothing more needed from you.",
                     "Ho gaya ✅ Ab live hai. Ek hafte mein performance share karunga — aapko kuch aur nahi karna.")
            return {"action": "send", "body": body, "cta": "none",
                    "rationale": "Merchant confirmed the delivered draft — executed and closed the loop with a follow-up promise."}
        if conv.stage == "confirmed":
            return await self._llm_or(conv, "other", msg, lang, deadline, self._fallback_other(conv, lang))
        conv.stage = "action"
        promised = conv.action_offer
        conv.action_offer = "send/publish the delivered draft once they reply CONFIRM"
        fb_body = L(lang,
                    f"Done — starting now: I'll {promised}. The draft will be right here within 10 minutes; reply CONFIRM once you've seen it and I'll take it live.",
                    f"Badhiya — kaam shuru: I'll {promised}. Draft 10 minute mein yahin milega; dekh ke CONFIRM reply kijiye, main live kar dunga.")
        fallback = {"action": "send", "body": fb_body, "cta": "binary_confirm_cancel",
                    "rationale": "Explicit go-ahead detected — switched from pitch to action mode; concrete next step + single CONFIRM, no qualifying questions."}
        return await self._llm_or(conv, "commit", msg, lang, deadline, fallback)

    async def _customer_commit(self, conv, c, msg, lang, deadline) -> dict:
        ctx = self._ctx(conv)
        k = conv.kind
        conv.stage = "confirmed"
        if conv.slots:
            opts = " or ".join(f"{i + 1} for {s}" for i, s in enumerate(conv.slots[:2]))
            opts_hi = " ya ".join(f"{s} ke liye {i + 1}" for i, s in enumerate(conv.slots[:2]))
            conv.stage = "pitch"
            body = L(lang, f"Great! Just pick one — reply {opts}.", f"Badhiya! Bas ek chun lijiye — {opts_hi} reply karein.")
            cta = "multi_choice_slot"
        elif k == "chronic_refill_due":
            body = L(lang, f"Done ✅ Packing it now at {ctx.name} — we'll message you once it's out for delivery.",
                     f"Ho gaya ✅ {ctx.name} mein pack ho raha hai — dispatch hote hi message karenge.")
            cta = "none"
        elif k == "appointment_tomorrow":
            body = L(lang, "Confirmed ✅ See you tomorrow! Reply here if anything changes.",
                     "Confirm ho gaya ✅ Kal milte hain! Kuch badle toh yahin reply karein.")
            cta = "none"
        else:
            body = L(lang, f"Wonderful! {ctx.name} will send you this week's open slots shortly — reply with a day/time that suits you to speed it up.",
                     f"Bahut badhiya! {ctx.name} jaldi hi is hafte ke slots bhejega — apna pasandida din/time bata dein toh aur jaldi ho jayega.")
            cta = "open_ended"
        return {"action": "send", "body": body, "cta": cta, "rationale": "Customer said yes — confirmed the next step immediately, no re-qualifying."}

    async def _on_question(self, conv, c, msg, lang, party, now, deadline) -> dict:
        move = "customer_question" if conv.audience == "customer" else "question"
        return await self._llm_or(conv, move, msg, lang, deadline, self._fallback_question(conv, msg, lang))

    async def _on_other(self, conv, c, msg, lang, party, now, deadline) -> dict:
        move = "customer_question" if conv.audience == "customer" else "other"
        return await self._llm_or(conv, move, msg, lang, deadline, self._fallback_other(conv, lang))

    def _fallback_question(self, conv: Conversation, msg: str, lang: str) -> dict:
        words = {w for w in re.findall(r"[a-z]{4,}", msg.lower())}
        best, score = None, 0
        for f in conv.facts:
            if f.startswith(("Merchant:", "History", "Customer:")):
                continue
            s = len(words & set(re.findall(r"[a-z]{4,}", f.lower())))
            if s > score:
                best, score = f, s
        offer = conv.action_offer or "set it up"
        if best and score >= 2:
            fact = re.sub(r"^[A-Z][\w ()/-]{0,40}:\s*", "", best)
            body = L(lang, f"Short answer: {fact}. Want me to go ahead and {offer}?",
                     f"Seedha jawab: {fact}. Aage badhoon — {offer}? Bas YES bol dijiye.")
        else:
            body = L(lang, f"Fair question — I don't have that detail on my side, so I won't guess. What I can do right away is {offer}. Shall I?",
                     f"Sahi sawaal — yeh detail mere paas nahi hai, isliye guess nahi karunga. Abhi turant kar sakta hoon: {offer}. Karoon?")
        return {"action": "send", "body": tidy(body), "cta": "binary_yes_no",
                "rationale": "Answered from available facts only (no guessing), then restated the single next step."}

    def _fallback_other(self, conv: Conversation, lang: str) -> dict:
        offer = conv.action_offer or "take the next step"
        body = L(lang, f"Got it — noted, I'll factor that in. Next step from my side: {offer}. Reply YES and I'll start.",
                 f"Samajh gaya — dhyaan mein rakhunga. Mera agla step: {offer}. YES reply karein, shuru karta hoon.")
        return {"action": "send", "body": body, "cta": "binary_yes_no",
                "rationale": "Acknowledged the merchant's input and moved one concrete step forward with a single ask."}

    async def _llm_or(self, conv: Conversation, move: str, msg: str, lang: str, deadline: float | None, fallback: dict) -> dict:
        if not (self.llm and self.llm.enabled):
            return fallback
        ctx = self._ctx(conv)
        from ..compose.prompts import LANG
        who = "" if conv.audience == "merchant" else f", writing as {ctx.name} to its customer"
        system = SYSTEM_REPLY.format(who=who, language=LANG.get(lang, LANG["en"]), intent=move, move=MOVES[move])
        facts = "\n".join(f"- {f}" for f in fresh_facts(conv, ctx)[:22])
        stage = {"pitch": "pitch — nothing agreed yet",
                 "action": "action — the draft/work was already delivered above; awaiting their CONFIRM",
                 "confirmed": "confirmed — the work is done and live"}.get(conv.stage, conv.stage)
        user = (f"ADDRESS AS: {conv.salutation or '-'}\nSTAGE: {stage}\nOPEN THREAD / NEXT STEP: {conv.action_offer or '-'}\n"
                f"FACTS:\n{facts}\n\nCONVERSATION SO FAR:\n{conv.transcript()}\n\n"
                f"A safe fallback reply (improve on it, don't copy it): \"{fallback.get('body', '')}\"")
        out, meta = await self.llm.chat_json(system, user, deadline=deadline, max_tokens=500)
        body = (out or {}).get("body")
        if not isinstance(body, str) or not body.strip():
            return fallback
        body = tidy(body.strip().strip('"'))
        plan = _grounding_plan(conv, fallback.get("body", ""), lang, ctx)
        errs = validate(body, plan, ctx.taboos(), ctx.slug in ("dentists", "pharmacies"))
        errs = [e for e in errs if not e.startswith("must address")]
        if move == "commit" and QUALIFYING.search(body):
            errs.append("asks a qualifying question after a commitment")
        if norm_body(body) in conv.bot_bodies():
            errs.append("repeats an earlier message")
        if errs:
            log.info("reply LLM output rejected (%s): %s", move, errs)
            return fallback
        cta = out.get("cta") if out.get("cta") in ("binary_yes_no", "binary_confirm_cancel", "open_ended", "none", "multi_choice_slot") else fallback.get("cta", "open_ended")
        rationale = str(out.get("rationale") or fallback.get("rationale"))[:400]
        return {"action": "send", "body": body, "cta": cta, "rationale": rationale}

    # ------------------------------------------------------------------ finalisation
    def _end(self, conv: Conversation, rationale: str, party=None, backoff_hours: int | None = None, now: datetime | None = None) -> dict:
        conv.status = "ended"
        conv.ended_reason = rationale
        if party is not None and backoff_hours and now:
            party.backoff_until = iso(now + timedelta(hours=backoff_hours))
        return {"action": "end", "rationale": rationale}

    def _finalise(self, conv: Conversation, out: dict, lang: str, now: datetime, party) -> dict:
        if out.get("action") != "send":
            out.pop("thanks_reply", None)
            return out
        if conv.bot_turns() >= MAX_BOT_TURNS:
            return self._end(conv, f"Reached {MAX_BOT_TURNS} bot turns in this conversation — closing rather than over-messaging.")
        body = out["body"].strip()
        if norm_body(body) in conv.bot_bodies():
            return {"action": "wait", "wait_seconds": 3600, "rationale": "Would have repeated an earlier message verbatim — waiting instead."}
        conv.add("bot", body, iso(now))
        if out.pop("thanks_reply", False):
            conv.turns[-1]["thanks_reply"] = True
        party.last_sent_at = iso(now)
        return {"action": "send", "body": body, "cta": out.get("cta", "open_ended"), "rationale": out.get("rationale", "")}


_SNAPSHOT_PREFIXES = ("Today:", "Merchant:", "Last ", "7-day change", "Peer averages", "Active offers", "Subscription:",
                      "Customer base:", "Review themes", "History", "Customer:")


def fresh_facts(conv: Conversation, ctx: Ctx) -> list[str]:
    """Trigger-specific facts from send time + a *fresh* merchant snapshot (contexts may have been re-pushed since)."""
    specific = [f for f in conv.facts if not f.startswith(_SNAPSHOT_PREFIXES)]
    snap = snapshot_facts(ctx) if ctx.m else [f for f in conv.facts if f.startswith(_SNAPSHOT_PREFIXES)]
    return specific + snap


def _grounding_plan(conv: Conversation, fallback_body: str, lang: str, ctx: Ctx | None = None) -> Plan:
    facts = (fresh_facts(conv, ctx) if ctx is not None else list(conv.facts)) + [t["text"] for t in conv.turns]
    return Plan(kind=conv.kind, family="reply", audience=conv.audience, send_as=conv.send_as, lang=lang,
                salutation=None, facts=facts, angle="", levers=[],
                cta_type="open_ended", draft=fallback_body, action_offer=conv.action_offer, template_name="",
                template_params=[], suppression_key="", rationale="")


def infer_pending_action(ctx: Ctx) -> str:
    """For conversations we didn't start: pick up the open thread from history, else the top fix."""
    last_vera = ctx.last_vera_message()
    offer = None
    if last_vera:
        m = re.search(r"(?:want me to|shall i|should i|can i)\s+([^?]{8,120})\?", str(last_vera.get("body")), re.I)
        if m:
            offer = m.group(1).strip()
    last_m = ctx.last_merchant_message()
    if offer and last_m and re.search(r"\bfocus\b|\bon\b", last_m, re.I):
        focus = re.search(r"focus on (.+)$", last_m, re.I)
        if focus:
            offer += f" (focus: {focus.group(1).strip().rstrip('.')})"
    if offer:
        return offer
    if not ctx.active_offers() and ctx.best_service_offer():
        return f"put a '{ctx.best_service_offer()}' offer live on your listing"
    if ctx.ident.get("verified") is False:
        return "start your Google profile verification"
    return "draft this week's Google post for your review"
