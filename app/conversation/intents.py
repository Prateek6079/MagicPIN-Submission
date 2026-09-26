"""Deterministic intent classification for inbound merchant/customer messages.

Fast (<1ms), explainable, and tuned for the replay scenarios: WhatsApp-Business auto-replies,
explicit opt-outs, hostility, intent transitions ("ok let's do it"), deferrals, off-topic asks
and customer slot picks. Works on English, Hinglish (Roman) and Devanagari.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

AUTO_REPLY_PATTERNS = [
    r"thank(s| you) for (contacting|reaching|your (message|enquiry|inquiry)|messaging|writing)",
    r"we (will|'ll|shall) (get back|respond|revert|reply|contact)", r"will get back to you", r"get back to you (shortly|soon|asap)",
    r"our (team|executive|representative)s? will", r"team will (respond|reply|contact|get back|call)",
    r"respond (to you )?(shortly|soon|at the earliest)", r"reply (you )?(shortly|soon)", r"(currently|presently) (unavailable|away|closed|busy)",
    r"out of (the )?office", r"(business|working|office) hours", r"this is an? auto(mated)?[- ]?(reply|response|message)",
    r"auto[- ]?reply", r"automated (assistant|message|response)", r"i am an? (automated|virtual) assistant",
    r"main ek automated assistant", r"aapki jaankari ke liye", r"(hamari|humari) team (tak|aapse)", r"team tak pahuncha",
    r"we have received your (message|query|request)", r"your (message|query|request) (has been|is) received",
    r"welcome to .{2,40}(how (can|may) we help|we are open)", r"how (can|may) (i|we) (help|assist) you( today)?\??$",
    r"for (bookings|appointments|orders),? (please )?(call|visit|contact)", r"kindly (wait|hold)",
]
_AUTO = re.compile("|".join(AUTO_REPLY_PATTERNS), re.I)

OPT_OUT = re.compile(
    r"\bstop (messag|send|text|contact|spamm|bother|this|these|it\b|now\b)|^\s*stop\b|\bplease stop\b|\bstop\W*$|"
    r"\bunsubscribe\b|\bnot interested\b|\bno interest\b|\bdon'?t (message|text|contact|send|call|bother)|"
    r"\bdo not (message|text|contact|send|call|bother)|\bno more (messages|msgs|texts)\b|\bremove me\b|"
    r"\bleave me alone\b|\bblock(ing)? you\b|\bband karo\b|\bmat bhej|\bmessage mat\b|\bmsg mat\b|\bbhejna band\b|"
    r"\bnahi chahiye\b.*\b(message|msg)|\binterest nahi\b|\bpareshan mat\b", re.I)
HOSTILE = re.compile(
    r"\bspam(ming)?\b|\buseless\b|\bbothering\b|\birritat|\bnonsense\b|\bstupid\b|\bidiot|\bfraud\b|\bscam\b|\bbakwas\b|"
    r"\bpagal\b|\bbekaar\b|\bfaltu\b|\bshut up\b|\bwtf\b|\bf+u+c+k|\bdamn\b|\bbloody\b|\bchutiya|\bbewakoof|\bharass|"
    r"\bwaste of (my )?time\b|\bwhy (are|r) (you|u) (bothering|messaging|disturbing)|\bdisturb", re.I)
LATER = re.compile(
    r"\blater\b|\bnot now\b|\bbusy\b|\bbaad mein\b|\bbaad me\b|\babhi nahi\b|\btomorrow\b|\bnext week\b|\bafter some time\b|"
    r"\bin a meeting\b|\bcall (you|u) back\b|\bwill (check|see|think|revert)\b|\blet me (think|check|see)\b|\bsoch(ta|ti|ke|kar)\b|"
    r"\bthodi der\b|\bkal baat\b|\bshaam ko\b|\bevening\b|\bweekend\b", re.I)
DECLINE = re.compile(
    r"^\s*(no|nope|nah|nahi|nahin|na)\b(?!.*\b(problem|worries)\b)|\bno thanks\b|\bno thank you\b|\bnot needed\b|\bdon'?t need\b|\bzaroorat nahi\b|"
    r"\bnot required\b|\bnahi chahiye\b|\bpass\b\s*$|\bskip (it|this)\b", re.I)
COMMIT = re.compile(
    r"\blet'?s do (it|this)\b|\blets do (it|this)\b|\bgo ahead\b|\bgo for it\b|\bdo it\b|\bproceed\b|\bconfirm(ed)?\b|"
    r"\bsend (it|me|the|over)\b|\bplease (do|send|share|draft|go|start|proceed|publish|post|set|update|add|book|register)\b|"
    r"\bsounds good\b|\bwhat'?s next\b|\bwhats next\b|\bstart (it|now|karo)\b|\bi want to (join|start|do|go|renew|verify)\b|"
    r"\bjudna\b|\bjudna hai\b|\bjoin karna\b|\bkar do\b|\bkardo\b|\bkar dijiye\b|\bkar dein\b|\bchalo\b|\bchalega\b|\bbhej do\b|"
    r"\bbhejo\b|\bbhej dijiye\b|\bshare (it|the)\b|\bdraft (it|the)\b|\bset (it )?up\b|\bbook (it|me)\b|"
    r"^\s*(yes|yes please|yess+|yeah|yep|yup|sure|haan|haa|han|ha ji|haan ji|ji haan|ji|ok|okay|okk+|theek hai|thik hai|"
    r"theek h|go|y|done|deal|publish|👍|✅)\W*$|^\s*(yes|haan|sure|yeah|yep|ok|okay|absolutely|definitely|of course)\b", re.I)
QUESTION_START = re.compile(
    r"^\s*(what|how|why|when|where|which|who|whom|can|could|will|would|is|are|do|does|did|should|kya|kaise|kab|kitna|kitne|"
    r"kitni|kaun|kyun|kyon|kahan)\b", re.I)
THANKS = re.compile(r"^\s*(thanks|thank you|thx|ty|dhanyavaad|shukriya|thank u)\W*(so much|a lot|ji)?\W*$", re.I)
OFF_TOPIC = {
    "tax": re.compile(r"\bgst\b|\bincome tax\b|\bitr\b|\btax (filing|return)|\bfile (my )?(taxes|return)\b|\btds\b|\baudit of accounts\b", re.I),
    "finance": re.compile(r"\bloan\b|\bcredit card\b|\binsurance\b|\bemi\b|\bmutual fund\b|\bstock market\b|\bshare market\b", re.I),
    "legal": re.compile(r"\blawyer\b|\blegal notice\b|\bcourt\b|\bcase against\b|\bpolice\b|\bfir\b", re.I),
    "personal": re.compile(r"\bvisa\b|\bpassport\b|\belectricity bill\b|\bphone recharge\b|\bcricket score\b|\bmovie\b|\bjob for\b|\bmy son\b.*\bjob\b", re.I),
}
HINDI_MARKERS = {
    "hai", "hain", "kya", "aap", "aapka", "aapke", "aapki", "aapko", "karo", "karein", "kar", "karna", "doon", "chahiye",
    "abhi", "mein", "se", "ko", "bhi", "toh", "haan", "nahi", "nahin", "theek", "thik", "chalega", "bataiye", "bata", "bhej",
    "bhejo", "ji", "sakte", "hoga", "wala", "liye", "aur", "ka", "ki", "ke", "hum", "mujhe", "mera", "meri", "hamara",
    "raha", "rahi", "kal", "aaj", "sirf", "bas", "yeh", "woh", "dijiye", "hoon", "ho", "kaise", "kitna", "kyun", "matlab",
    "accha", "achha", "acha", "bahut", "jaldi", "judna", "baad", "haa", "bhai",
}


@dataclass
class Classified:
    intent: str            # auto_reply | opt_out | hostile | off_topic | slot_choice | later | decline | commit | question | thanks | other
    lang: str              # en | hinglish | hindi
    has_question: bool
    off_topic_kind: str | None = None
    slot_index: int | None = None
    reason: str = ""


def norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", (s or "").lower())).strip()


def detect_lang(text: str) -> str:
    if re.search(r"[ऀ-ॿ]", text or ""):
        return "hindi"
    words = set(re.findall(r"[a-z]+", (text or "").lower()))
    hits = len(words & HINDI_MARKERS)
    if hits >= 2 or (hits == 1 and len(words) <= 3):
        return "hinglish"
    return "en"


def looks_auto_reply(text: str) -> bool:
    return bool(_AUTO.search(text or ""))


def classify(text: str, *, audience: str = "merchant", repeated: int = 0, slot_labels: list[str] | None = None) -> Classified:
    """`repeated` = how many times this exact (normalised, substantial) text was already received from this party."""
    base = _classify(text, audience, slot_labels)
    if base.intent in ("auto_reply", "opt_out"):
        return base
    # Verbatim repeats are the strongest auto-reply tell, but a human can repeat a short "ok let's do it".
    if repeated >= 2 or (repeated >= 1 and base.intent in ("other", "question", "thanks")):
        return Classified("auto_reply", base.lang, base.has_question, reason=f"verbatim repeat x{repeated + 1}")
    return base


def _classify(text: str, audience: str, slot_labels: list[str] | None) -> Classified:
    t = (text or "").strip()
    low = t.lower()
    lang = detect_lang(t)
    has_q = "?" in t or bool(QUESTION_START.search(t))
    if not t:
        return Classified("other", lang, False, reason="empty")
    if looks_auto_reply(t):
        return Classified("auto_reply", lang, has_q, reason="canned auto-reply phrasing")
    if OPT_OUT.search(t):
        return Classified("opt_out", lang, has_q, reason="explicit opt-out")
    for kind, rx in OFF_TOPIC.items():
        if rx.search(t):
            if HOSTILE.search(t):
                return Classified("hostile", lang, has_q, off_topic_kind=kind, reason="hostile + off-topic")
            return Classified("off_topic", lang, has_q, off_topic_kind=kind, reason=f"out-of-scope ask ({kind})")
    if HOSTILE.search(t):
        return Classified("hostile", lang, has_q, reason="frustration/abuse")
    if audience == "customer":
        idx = _slot_pick(low, slot_labels or [])
        if idx is not None:
            return Classified("slot_choice", lang, has_q, slot_index=idx, reason="picked a slot")
    if THANKS.search(t):
        return Classified("thanks", lang, False, reason="thanks only")
    commit = bool(COMMIT.search(t))
    if LATER.search(t) and not re.search(r"\b(send|draft|do it|go ahead|let'?s)\b", low):
        return Classified("later", lang, has_q, reason="deferral")
    if DECLINE.search(t) and not commit:
        return Classified("decline", lang, has_q, reason="declined")
    if commit:
        return Classified("commit", lang, has_q, reason="commitment / go-ahead")
    if has_q:
        return Classified("question", lang, True, reason="question")
    return Classified("other", lang, has_q, reason="statement")


def _slot_pick(low: str, labels: list[str]) -> int | None:
    m = re.fullmatch(r"\s*(option\s*)?([1-3])\W*\s*", low)
    if m:
        return int(m.group(2)) - 1
    if re.search(r"\b(first|pehla|pehle wala)\b", low):
        return 0
    if re.search(r"\b(second|doosra|dusra)\b", low):
        return 1
    for i, lab in enumerate(labels):
        day = lab.split()[0].lower() if lab else ""
        if day and re.search(rf"\b{re.escape(day)}", low):
            return i
    return None


OFF_TOPIC_REPLY = {
    "tax": ("GST/tax filing is best done by your CA — that's outside what I can handle.",
            "GST/tax filing ke liye aapke CA sahi rahenge — yeh mere scope se bahar hai."),
    "finance": ("Loans and insurance are outside what I can help with.", "Loan/insurance mere scope se bahar hai."),
    "legal": ("Legal matters need a lawyer — I'm not the right help there.", "Legal matters ke liye lawyer se baat karna sahi rahega."),
    "personal": ("That one's outside what I can help with.", "Yeh mere scope se bahar hai."),
}
