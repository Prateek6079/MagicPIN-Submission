"""Prompt builders. Kept compact on purpose: Groq's free tier is ~8K tokens/min per model."""
from __future__ import annotations

from .ctx import Ctx
from .plan import Plan

SYSTEM_COMPOSE = """You are Vera, magicpin's merchant-growth assistant, writing ONE WhatsApp message.
You write like a sharp, respectful colleague who has already done the homework — never like an ad.

HARD RULES (a message that breaks any rule is rejected):
1. Use ONLY facts given in FACTS and DRAFT. Every number, price, date, name, source and offer must appear there. Never invent offers, prices, slots, discounts, freebies, statistics, research, competitor names or social proof ("3 clinics near you…").
2. Exactly ONE call-to-action and it must be the LAST sentence. One ask, not a menu.
3. No URLs, no hashtags, no snake_case field names, no words like "trigger", "payload", "signal".
4. No preamble ("Hope you're doing well", "I'm reaching out"), no self-introduction, no sign-off.
5. Never use: {taboos}.
6. Language: {language}
7. Voice: {voice}
8. WhatsApp style: short sentences, 250-550 characters (a drafted plan/list may be longer, with line breaks). Emojis: {emoji}.

WHAT SCORES HIGHEST: open with the specific why-now fact; address the person by name; anchor on the 2-4 most relevant verifiable numbers/dates/sources (never dump a list of stats — FACTS is a menu, not a checklist); show judgement (what to do AND what not to do) where the ANGLE says so; make the ask tiny and concrete (I'll draft/set it up — you just say yes).
Durations ("3 months ago", "25 din") are claims: only use one if it appears in FACTS/DRAFT; otherwise use the date.
Customer-facing messages keep the draft's opening (greeting + business name) and name the person the service is for.

Return JSON only: {{"body": "<the WhatsApp message>", "rationale": "<one sentence: why this message now, which facts and levers>"}}"""

LANG = {
    "en": "English only.",
    "hinglish": ("Natural Hinglish in Roman script, the way Indian business owners text: English for numbers, names, "
                 "offers and technical terms; Hindi for connectors and the ask (e.g. 'Draft bhej doon?', 'aapke liye'). "
                 "Roughly 30-50% Hindi words. Never Devanagari."),
    "hinglish_light": ("Mostly English with a light Hinglish touch in Roman script (1-2 short Hindi phrases, e.g. in the ask: "
                       "'Draft kar doon?'). Never Devanagari."),
    "hindi": ("Simple, respectful conversational Hindi in Roman script (e.g. 'Namaste! … dawaiyan … reply karein'). "
              "Keep medicine names, prices, dates in English/digits. Never Devanagari."),
}


def language_instruction(plan: Plan, ctx: Ctx) -> str:
    if plan.lang == "hinglish" and plan.audience == "merchant" and (ctx.south or ctx.slug in ("gyms",)):
        return LANG["hinglish_light"]
    if plan.lang == "en" and plan.audience == "customer" and ctx.regional_greeting():
        return f"English. You may open with '{ctx.regional_greeting()}' as the draft does."
    return LANG.get(plan.lang, LANG["en"])


def voice_line(plan: Plan, ctx: Ctx) -> str:
    v = ctx.voice()
    if plan.audience == "customer":
        base = {"dentists": "warm-clinical, reassuring, no medical claims",
                "pharmacies": "trustworthy, precise, respectful (seniors: extra courteous)",
                "salons": "warm, friendly, personal", "gyms": "encouraging coach, zero guilt or shaming",
                "restaurants": "warm, inviting, appetising"}.get(ctx.slug, "warm and personal")
        return f"{base}; written as the business itself ({ctx.sender_intro()}), from its own WhatsApp number."
    ex = "; ".join(v.get("tone_examples", [])[:2])
    vocab = ", ".join(v.get("vocab_allowed", [])[:10])
    return (f"{v.get('tone', 'peer')} / {v.get('register', 'colleague')}. Domain vocabulary welcome: {vocab}. "
            f"Tone examples: {ex}")


def compose_messages(plan: Plan, ctx: Ctx) -> tuple[str, str]:
    taboos = ", ".join(sorted(set(ctx.taboos() + ["guaranteed", "100% safe", "miracle", "cure"])))
    emoji = "none" if plan.audience == "merchant" else "at most one, only if the draft has one"
    system = SYSTEM_COMPOSE.format(taboos=taboos, language=language_instruction(plan, ctx), voice=voice_line(plan, ctx), emoji=emoji)
    facts = "\n".join(f"- {f}" for f in plan.facts if f)
    cta = {
        "binary_yes_no": "single yes/no ask as the last sentence (e.g. 'Want me to …?' or 'Reply YES and I'll …').",
        "binary_confirm_cancel": "single confirm ask as the last sentence (e.g. 'Reply YES to confirm, or send a better time').",
        "multi_choice_slot": "let them pick one of the given slots by replying 1 or 2 (or suggest a time) — last sentence.",
        "open_ended": "end with one open question the merchant can answer in a few words.",
        "none": "no call-to-action needed.",
    }[plan.cta_type]
    who = (f"the merchant's customer, sent on behalf of {ctx.name}" if plan.audience == "customer" else f"the merchant ({ctx.name})")
    user = f"""RECIPIENT: {who}
ADDRESS AS: {plan.salutation or '(no name known — use a warm generic greeting)'}
WHY NOW: {ctx.kind.replace('_', ' ')} (urgency {ctx.t.get('urgency')}/5)
ANGLE: {plan.angle}
LEVERS: {', '.join(plan.levers)}
CTA: {cta}
IF THEY SAY YES, WE WILL: {plan.action_offer}
AVOID: {' '.join(plan.avoid) or '-'}
FACTS:
{facts}
DRAFT (factually safe; make it sharper and more natural — keep its facts and the intent of its ask; you may reorder or trim):
\"\"\"{plan.draft}\"\"\""""
    return system, user


def repair_message(errors: list[str]) -> str:
    return ("Your previous message was rejected for: " + "; ".join(errors) +
            ". Rewrite it fixing every issue. Return JSON only.")
