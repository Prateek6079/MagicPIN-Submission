"""Post-generation guardrails. A body must pass these before it can be sent.

The most important check is *grounding*: every number (and every ₹ price / % figure, strictly)
in the body must be derivable from the facts the planner handed the writer. This is what
keeps the LLM from fabricating stats, prices, counts or social proof.
"""
from __future__ import annotations

import re

from .plan import Plan

NUM_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{2,3})+|\d+(?:\.\d+)?)")
TIME_RE = re.compile(r"\b\d{1,2}(?::\d{2})?\s?(?:am|pm|AM|PM)\b|\b\d{1,2}:\d{2}\b|\b\d{1,2}(?::\d{2})?\s?[–-]\s?\d{1,2}(?::\d{2})?\s?(?:am|pm)\b")
URL_RE = re.compile(r"https?://|www\.|\b[\w-]+\.(?:com|in|org|net|io|co|ly|me)\b(?!\w)", re.I)
JARGON_RE = re.compile(r"\b[a-z]+_[a-z0-9_]+\b")
INTERNAL_WORDS = re.compile(r"\b(trigger|payload|suppression|context_id|merchant_id|customer_id|signal[s]?\b:|placeholder)\b", re.I)
PREAMBLE_RE = re.compile(r"\b(hope (you are|you're|this finds)|i am reaching out|i'm reaching out|this is vera|vera here|i am vera|i'm vera|greetings from)\b", re.I)
SOCIAL_PROOF_RE = re.compile(r"(\d[\d,]*)\s+(?:other\s+|more\s+|nearby\s+)?(dentists|clinics|salons|restaurants|gyms|studios|pharmacies|chemists|merchants|businesses|competitors|doctors|owners|stores|shops)\b", re.I)
GLOBAL_TABOOS = ["guaranteed", "guarantee", "100% safe", "miracle", "best in city", "risk-free", "risk free"]
HEALTH_TABOOS = [r"\bcure[sd]?\b"]
HINDI_MARKERS = {
    "hai", "hain", "kya", "aap", "aapka", "aapke", "aapki", "aapko", "karo", "karein", "kar", "doon", "chahiye", "abhi",
    "mein", "se", "ko", "bhi", "toh", "haan", "nahi", "theek", "chalega", "bataiye", "bata", "bhej", "ji", "sakte",
    "sakti", "hoga", "wala", "wali", "liye", "yahan", "aur", "ka", "ki", "ke", "hum", "humein", "raha", "rahi", "rahe",
    "gaye", "gayi", "din", "hafte", "mahine", "kal", "aaj", "sirf", "bas", "yeh", "woh", "dijiye", "hoon", "ho",
}
CTA_HINT_RE = re.compile(r"\?|\breply\b|\byes\b|\bconfirm\b|\bbata|\bbhej|\btell (me|us)\b|\blet me know\b|\bkarein\b|\bdijiye\b", re.I)
SHOUT_OK = {"YES", "NO", "STOP", "CONFIRM", "GBP", "CTR", "RCT", "IOPA", "OPG", "DCI", "IDA", "JIDA", "CDE", "ORS", "OTC", "IPL",
            "DC", "MI", "BOGO", "GST", "FSSAI", "CDSCO", "DGCI", "FDA", "ICMR", "HIIT", "PT", "SOP", "SOPS", "RVG", "CAD", "CAM",
            "PFM", "AOV", "RPC", "GRO", "UPI", "ID", "OFF", "HAAN", "OK", "YoY", "YOY", "SPF", "LDL", "EMOM", "AMRAP", "BMR", "PR",
            "1RM", "VO2MAX", "BP", "MRP", "PCR", "H1", "WA", "HSR", "SR", "CVD", "ROI", "NEW", "FAQ", "QR"}


def norm_num(tok: str) -> str:
    s = tok.replace(",", "")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def numbers_in(text: str) -> list[str]:
    return [norm_num(m.group(1)) for m in NUM_RE.finditer(text or "")]


def allowed_numbers(plan: Plan) -> set[str]:
    allowed: set[str] = set(plan.extra_numbers)
    for src in list(plan.facts) + [plan.draft, plan.salutation or ""]:
        for n in numbers_in(src):
            allowed.add(n)
            try:  # tolerate harmless re-roundings of the same figure (e.g. 2.1 -> 2, 4,792 -> 4792)
                f = float(n)
                allowed.add(norm_num(f"{f:.1f}"))
                allowed.add(str(int(round(f))))
            except ValueError:
                pass
    allowed |= {"2025", "2026", "2027"}
    return allowed


def _strict_contexts(body: str) -> list[str]:
    """Numbers that must be grounded even if small: prices, percentages, multipliers, social proof."""
    out = []
    for m in re.finditer(r"₹\s?(\d[\d,]*(?:\.\d+)?)", body):
        out.append(norm_num(m.group(1)))
    for m in re.finditer(r"(\d[\d,]*(?:\.\d+)?)\s?%", body):
        out.append(norm_num(m.group(1)))
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s?x\b", body):
        out.append(norm_num(m.group(1)))
    for m in SOCIAL_PROOF_RE.finditer(body):
        out.append(norm_num(m.group(1)))
    return out


_UNIT = {"day": "day", "days": "day", "din": "day", "week": "week", "weeks": "week", "hafte": "week", "hafton": "week",
         "month": "month", "months": "month", "mahine": "month", "mahina": "month", "year": "year", "years": "year",
         "yrs": "year", "saal": "year"}
_UNIT_WORDS = {"day": ("day", "din"), "week": ("week", "hafte", "hafton"), "month": ("month", "mahine", "mahina"), "year": ("year", "saal")}


def ungrounded_durations(body: str, plan: Plan) -> list[str]:
    """'3 months since…' is a claim: the same number AND unit must co-occur in one fact (or the draft)."""
    sources = [s.lower() for s in list(plan.facts) + [plan.draft]]
    bad = []
    for m in DURATION_RE.finditer(body):
        n, unit = norm_num(m.group(1)), _UNIT[m.group(2).lower()]
        ok = any(n in numbers_in(s) and any(w in s for w in _UNIT_WORDS[unit]) for s in sources)
        if not ok:
            bad.append(f"{m.group(1)} {m.group(2)}")
    return bad


DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:-\s*)?(day|days|week|weeks|month|months|year|years|yrs|din|hafte|hafton|mahine|mahina|saal)\b", re.I)


def grounded_numbers(text: str, plan: Plan) -> set[str]:
    allowed = allowed_numbers(plan)
    return {n for n in numbers_in(TIME_RE.sub(" ", text or "")) if n in allowed and n not in {"2025", "2026", "2027"}}


def validate(body: str, plan: Plan, taboos: list[str] | None = None, health: bool = False) -> list[str]:
    errs: list[str] = []
    b = (body or "").strip()
    if len(b) < 60:
        return ["body is empty or too short"]
    limit = 1100 if "\n•" in b or "\n-" in b else 750
    if len(b) > limit:
        errs.append(f"too long ({len(b)} chars, max {limit})")
    if URL_RE.search(b):
        errs.append("contains a URL/link — not allowed")
    if "{{" in b or "}}" in b or re.search(r"\[[A-Za-z _]+\]", b):
        errs.append("contains unfilled template placeholders")
    if JARGON_RE.search(b):
        errs.append(f"exposes internal field names ({JARGON_RE.search(b).group(0)}) — write in plain words")
    if INTERNAL_WORDS.search(b):
        errs.append(f"uses internal system words ('{INTERNAL_WORDS.search(b).group(0)}')")
    if PREAMBLE_RE.search(b):
        errs.append("has a preamble / self-introduction — start with the point")
    low = b.lower()
    for t in (taboos or []) + GLOBAL_TABOOS:
        if t and t.lower() in low:
            errs.append(f"uses taboo phrase '{t}'")
    if health:
        for pat in HEALTH_TABOOS:
            if re.search(pat, low):
                errs.append("uses the word 'cure' — not allowed for this category")
    # grounding
    allowed = allowed_numbers(plan)
    scrub = TIME_RE.sub(" ", b)
    strict = set(_strict_contexts(scrub))
    bad = []
    for n in numbers_in(scrub):
        if n in allowed:
            continue
        if n not in strict and re.fullmatch(r"\d", n):  # single-digit counts/effort ("3 posts", "5 min") are fine
            continue
        if n not in strict and n in {"10", "15", "20", "30", "60", "90"} and re.search(rf"\b{n}\s?(min|mins|minutes|seconds|sec)\b", scrub):
            continue
        bad.append(n)
    if bad:
        errs.append(f"numbers not grounded in FACTS: {', '.join(sorted(set(bad)))} — remove or use only given numbers")
    durs = ungrounded_durations(scrub, plan)
    if durs:
        errs.append(f"durations not grounded in FACTS: {', '.join(durs)} — use the date from FACTS instead")
    is_list = "\n•" in b or "\n-" in b
    distinct = {n for n in numbers_in(scrub) if n not in {"2025", "2026", "2027"}}
    if len(distinct) > (14 if is_list else 7):
        errs.append(f"too many numbers ({len(distinct)}) — keep only the 2-4 that matter most")
    # CTA shape
    if plan.cta_type != "none":
        tail = re.split(r"(?<=[.!?])\s+", b.strip())[-1]
        if not CTA_HINT_RE.search(tail):
            errs.append("the call-to-action must be the last sentence")
        if len(re.findall(r"\breply\b", low)) > 1 and plan.cta_type != "multi_choice_slot":
            errs.append("more than one 'Reply …' instruction — keep a single CTA")
        if b.count("?") > 2:
            errs.append("too many questions — one clear ask only")
    # salutation / names
    for must in plan.must_mention:
        core = must.replace("Dr. ", "").split()[0] if must else ""
        if core and core.lower() not in low:
            errs.append(f"must address/mention '{must}'")
    # specificity floor: the rewrite may not be vaguer than the grounded draft
    if plan.draft and b != plan.draft:
        want = min(2, len(grounded_numbers(plan.draft, plan)))
        if len(grounded_numbers(b, plan)) < want:
            errs.append("lost the concrete numbers from the draft — keep at least two of its specific figures")
    # shouting
    shout = [w for w in re.findall(r"\b[A-Z]{3,}\b", b) if w not in SHOUT_OK]
    if len(shout) > 1:
        errs.append("avoid ALL-CAPS hype words")
    if "!!" in b:
        errs.append("no double exclamation marks")
    # language
    words = set(re.findall(r"[a-z]+", low))
    hindi_hits = len(words & HINDI_MARKERS)
    if plan.lang in ("hinglish", "hindi") and hindi_hits < 2:
        errs.append("language: must be Hinglish/Hindi in Roman script (use natural Hindi connectors)")
    if plan.lang == "en" and hindi_hits >= 4:
        errs.append("language: must be English")
    if re.search(r"[ऀ-ॿ]", b):
        errs.append("use Roman script, not Devanagari")
    return errs
