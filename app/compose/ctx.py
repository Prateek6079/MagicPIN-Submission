"""Ctx — a read-only view over (category, merchant, trigger, customer) with the
helpers every planner needs: salutations, language choice, offers, digest lookup,
peer comparisons, seasonal beats, humanised signals and customer relationship facts.

Nothing in here invents data: every helper returns None/[] when the field is missing.
"""
from __future__ import annotations

import re
from datetime import datetime

from ..config import settings
from ..timeutil import parse_ts
from . import fmt

SOUTH_LANGS = {"ta", "te", "kn", "ml"}
REGIONAL_GREETING = {"ta": "Vanakkam", "te": "Namaskaram", "kn": "Namaskara", "mr": "Namaskar"}

NOUNS = {
    "dentists": {"people": "patients", "one": "patient", "biz": "clinic", "visit": "check-up"},
    "salons": {"people": "clients", "one": "client", "biz": "salon", "visit": "appointment"},
    "restaurants": {"people": "customers", "one": "customer", "biz": "restaurant", "visit": "order"},
    "gyms": {"people": "members", "one": "member", "biz": "studio", "visit": "session"},
    "pharmacies": {"people": "customers", "one": "customer", "biz": "pharmacy", "visit": "refill"},
}
_DIGEST_ID_KEYS = ("top_item_id", "digest_item_id", "alert_id", "item_id", "digest_id")


def first_sentence(text: str | None) -> str:
    if not text:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return parts[0].strip()


def sentences(text: str | None) -> list[str]:
    if not text:
        return []
    return [p.strip() for p in re.split(r"(?<=[.!?])\s+", text.strip()) if p.strip()]


class Ctx:
    def __init__(self, category: dict | None, merchant: dict | None, trigger: dict | None,
                 customer: dict | None = None, now: datetime | None = None):
        self.cat = category or {}
        self.m = merchant or {}
        self.t = trigger or {}
        self.c = customer or None
        self.now = now or parse_ts(settings.default_now)
        self.p = self.t.get("payload") or {}
        self.kind = (self.t.get("kind") or "generic").strip()
        self.placeholder = bool(self.p.get("placeholder"))
        self.slug = self.cat.get("slug") or self.m.get("category_slug") or ""
        is_customer_scope = self.t.get("scope") == "customer" or bool(self.t.get("customer_id"))
        self.audience = "customer" if (self.c and is_customer_scope) else "merchant"
        self.lang = self.customer_lang() if self.audience == "customer" else self.merchant_lang()
        self.nouns = NOUNS.get(self.slug, {"people": "customers", "one": "customer", "biz": "business", "visit": "visit"})

    # ------------------------------------------------------------ identity
    @property
    def ident(self) -> dict:
        return self.m.get("identity") or {}

    @property
    def name(self) -> str:
        return self.ident.get("name") or self.m.get("merchant_id") or "your business"

    @property
    def owner(self) -> str | None:
        return (self.ident.get("owner_first_name") or "").strip() or None

    @property
    def locality(self) -> str | None:
        return self.ident.get("locality")

    @property
    def city(self) -> str | None:
        return self.ident.get("city")

    @property
    def langs(self) -> list[str]:
        return [str(x).lower() for x in (self.ident.get("languages") or [])]

    def merchant_salutation(self) -> str:
        o = self.owner
        if self.slug == "dentists":
            if o:
                return o if o.lower().startswith("dr") else f"Dr. {o}"
            m = re.match(r"(Dr\.?\s+\w+)", self.name)
            return m.group(1) if m else "Doctor"
        return o or f"{self.name} team"

    def sender_intro(self) -> str:
        """How a customer-facing message introduces the merchant (sent from the merchant's number)."""
        loc = f", {self.locality}" if self.locality else ""
        if self.slug in ("salons", "gyms") and self.owner and not self.owner.lower().startswith("dr"):
            return f"{self.owner} from {self.name}{loc}"
        return f"{self.name}{loc}"

    # ------------------------------------------------------------ language
    def merchant_lang(self) -> str:
        return "hinglish" if "hi" in self.langs else "en"

    def customer_lang(self) -> str:
        pref = str(((self.c or {}).get("identity") or {}).get("language_pref") or "").lower()
        if pref in ("hi", "hindi"):
            return "hindi"
        if re.search(r"\bhi\b", pref) or "hindi" in pref:
            return "hinglish"
        return "en"

    def regional_greeting(self) -> str | None:
        pref = str(((self.c or {}).get("identity") or {}).get("language_pref") or "").lower()
        for code, word in REGIONAL_GREETING.items():
            if re.match(rf"^{code}\b", pref):
                return word
        return None

    @property
    def south(self) -> bool:
        return bool(SOUTH_LANGS & set(self.langs))

    def L(self, en: str, hi: str) -> str:
        return hi if self.lang in ("hinglish", "hindi") else en

    # ------------------------------------------------------------ customer
    @property
    def cid(self) -> dict:
        return (self.c or {}).get("identity") or {}

    @property
    def rel(self) -> dict:
        return (self.c or {}).get("relationship") or {}

    @property
    def prefs(self) -> dict:
        return (self.c or {}).get("preferences") or {}

    def customer_names(self) -> tuple[str | None, str | None]:
        """Returns (who we address, who the service is for if different)."""
        name = str(self.cid.get("name") or "").strip()
        if not name or name.startswith("("):
            return None, None
        m = re.match(r"(.+?)\s*\(parent:\s*(.+?)\)", name)
        if m:
            return m.group(2).strip().split()[0], m.group(1).strip().split()[0]
        channel = str(self.prefs.get("channel") or "")
        m2 = re.match(r"(Mr|Mrs|Ms|Shri|Smt)\.?\s+(\w+)", name)
        if m2:
            ji = f"{m2.group(2)} ji"
            if "via_" in channel:  # message goes to a family member's phone
                return None, ji
            return ji, None
        return name.split()[0], None

    def last_visit(self) -> datetime | None:
        return parse_ts(self.rel.get("last_visit"))

    def days_since_last_visit(self) -> int | None:
        lv = self.last_visit()
        if not lv or not self.now:
            return None
        d = (self.now - lv).days
        return d if d >= 14 else None

    def services(self) -> list[str]:
        return [fmt.humanize(s) for s in (self.rel.get("services_received") or []) if s and s != "..."]

    def preferred_slot(self) -> str | None:
        s = self.prefs.get("preferred_slots")
        return fmt.humanize(s) if s else None

    def customer_consented(self) -> bool:
        consent = (self.c or {}).get("consent") or {}
        return bool(consent.get("scope")) or bool(self.prefs.get("reminder_opt_in"))

    # ------------------------------------------------------------ offers
    def active_offers(self) -> list[str]:
        return [o.get("title") for o in (self.m.get("offers") or []) if o.get("status") == "active" and o.get("title")]

    def expired_offers(self) -> list[str]:
        return [o.get("title") for o in (self.m.get("offers") or []) if o.get("status") != "active" and o.get("title")]

    def catalog(self, types: tuple[str, ...] | None = None) -> list[dict]:
        items = self.cat.get("offer_catalog") or []
        return [o for o in items if not types or o.get("type") in types]

    def catalog_title(self, *keywords: str) -> str | None:
        for o in self.catalog():
            if any(k.lower() in (o.get("title") or "").lower() for k in keywords):
                return o.get("title")
        return None

    def best_service_offer(self) -> str | None:
        """An active offer if present, else the category's first service+price template."""
        act = self.active_offers()
        if act:
            return act[0]
        for o in self.catalog(("service_at_price",)):
            return o.get("title")
        return None

    # ------------------------------------------------------------ digest / knowledge
    def digest(self) -> list[dict]:
        return self.cat.get("digest") or []

    def digest_by_id(self, did: str | None) -> dict | None:
        if not did:
            return None
        for d in self.digest():
            if d.get("id") == did:
                return d
        return None

    def digest_item(self) -> dict | None:
        for k in _DIGEST_ID_KEYS:
            hit = self.digest_by_id(self.p.get(k))
            if hit:
                return hit
        for k in ("item_ids", "items", "digest_item_ids", "digest_items"):  # list-shaped payloads
            for v in self.p.get(k) or []:
                hit = self.digest_by_id(v if isinstance(v, str) else (v or {}).get("id"))
                if hit:
                    return hit
        return None

    def pick_digest(self, kinds: tuple[str, ...], keywords: tuple[str, ...] = ()) -> dict | None:
        """Deterministically pick the most relevant digest item of the given kinds."""
        best, best_score = None, -1
        hay = " ".join([self.name] + self.active_offers() +
                       [str(r.get("theme", "")) for r in self.review_themes()] +
                       [str(h.get("body", "")) for h in self.history()]).lower()
        for i, d in enumerate(self.digest()):
            if kinds and d.get("kind") not in kinds:
                continue
            text = f"{d.get('title', '')} {d.get('summary', '')}".lower()
            score = sum(3 for k in keywords if k.lower() in text)
            score += sum(1 for w in re.findall(r"[a-z]{5,}", text) if w in hay)
            score += (len(kinds) - kinds.index(d.get("kind"))) if kinds else 0
            score = score * 100 - i  # stable tie-break by dataset order
            if score > best_score:
                best, best_score = d, score
        return best

    def trend_signals(self) -> list[dict]:
        return sorted(self.cat.get("trend_signals") or [], key=lambda t: -(t.get("delta_yoy") or 0))

    def seasonal_now(self) -> list[dict]:
        if not self.now:
            return []
        return [b for b in (self.cat.get("seasonal_beats") or []) if self.now.month in fmt.month_set(b.get("month_range", ""))]

    def seasonal_matching(self, *keywords: str) -> dict | None:
        for b in self.cat.get("seasonal_beats") or []:
            if any(k in (b.get("note") or "").lower() for k in keywords):
                return b
        return None

    def voice(self) -> dict:
        return self.cat.get("voice") or {}

    def taboos(self) -> list[str]:
        out = []
        for t in self.voice().get("vocab_taboo") or self.voice().get("taboos") or []:
            t = re.sub(r"\(.*?\)", "", str(t)).strip()
            if t:
                out.append(t)
        return out

    # ------------------------------------------------------------ performance
    @property
    def perf(self) -> dict:
        return self.m.get("performance") or {}

    @property
    def delta(self) -> dict:
        return self.perf.get("delta_7d") or {}

    def peer(self, key: str):
        return (self.cat.get("peer_stats") or {}).get(key)

    def peer_compare(self) -> list[tuple[str, float, float, float]]:
        """[(metric, mine, peer, ratio)] for views/calls/directions/ctr where both exist."""
        pairs = [("views", "avg_views_30d"), ("calls", "avg_calls_30d"),
                 ("directions", "avg_directions_30d"), ("ctr", "avg_ctr")]
        out = []
        for mine_k, peer_k in pairs:
            mine, peer = self.perf.get(mine_k), self.peer(peer_k)
            if isinstance(mine, (int, float)) and isinstance(peer, (int, float)) and peer:
                out.append((mine_k, float(mine), float(peer), float(mine) / float(peer)))
        return out

    # ------------------------------------------------------------ signals, reviews, history
    def signals(self) -> list[str]:
        return [str(s) for s in (self.m.get("signals") or [])]

    def has_signal(self, prefix: str) -> str | None:
        for s in self.signals():
            if s.startswith(prefix):
                return s
        return None

    def signal_value(self, prefix: str) -> int | None:
        s = self.has_signal(prefix)
        if not s:
            return None
        m = re.search(r"(\d+)\s*d?$", s)
        return int(m.group(1)) if m else None

    def review_themes(self) -> list[dict]:
        return self.m.get("review_themes") or []

    def top_review(self, sentiment: str) -> dict | None:
        items = [r for r in self.review_themes() if r.get("sentiment") == sentiment]
        return max(items, key=lambda r: r.get("occurrences_30d") or 0) if items else None

    def history(self) -> list[dict]:
        return self.m.get("conversation_history") or []

    def last_merchant_message(self) -> str | None:
        for h in reversed(self.history()):
            if h.get("from") == "merchant" and h.get("body"):
                return h["body"]
        return None

    def last_vera_message(self) -> dict | None:
        for h in reversed(self.history()):
            if h.get("from") == "vera":
                return h
        return None

    def agg(self) -> dict:
        return self.m.get("customer_aggregate") or {}

    def sub(self) -> dict:
        return self.m.get("subscription") or {}
