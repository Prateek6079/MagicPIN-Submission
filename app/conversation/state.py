"""In-memory conversation + per-merchant/customer engagement state (snapshot-able to JSON)."""
from __future__ import annotations

import re
import threading
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime

from ..timeutil import iso, parse_ts


def norm_body(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s₹%]", "", (s or "").lower())).strip()


@dataclass
class Conversation:
    id: str
    merchant_id: str | None
    customer_id: str | None = None
    trigger_id: str | None = None
    kind: str = "unknown"
    audience: str = "merchant"          # merchant | customer
    send_as: str = "vera"
    lang: str = "en"
    salutation: str | None = None
    action_offer: str = ""
    cta_type: str = "binary_yes_no"
    facts: list[str] = field(default_factory=list)
    slots: list[str] = field(default_factory=list)
    turns: list[dict] = field(default_factory=list)       # {role, text, ts}
    status: str = "open"                # open | waiting | ended
    stage: str = "pitch"                # pitch | action | confirmed
    auto_count: int = 0
    hostile_count: int = 0
    nudges_unanswered: int = 0
    created_at: str | None = None
    ended_reason: str | None = None

    def bot_bodies(self) -> set[str]:
        return {norm_body(t["text"]) for t in self.turns if t["role"] == "bot"}

    def bot_turns(self) -> int:
        return sum(1 for t in self.turns if t["role"] == "bot")

    def add(self, role: str, text: str, ts: str | None = None) -> None:
        self.turns.append({"role": role, "text": text, "ts": ts})

    def transcript(self, last: int = 8) -> str:
        who = {"bot": "Vera" if self.audience == "merchant" else "Business", "merchant": "Merchant", "customer": "Customer"}
        return "\n".join(f"{who.get(t['role'], t['role'])}: {t['text']}" for t in self.turns[-last:])


@dataclass
class PartyState:
    """Engagement state for one merchant (or one customer)."""
    opted_out: bool = False
    backoff_until: str | None = None
    auto_streak: int = 0
    inbound: Counter = field(default_factory=Counter)
    last_sent_at: str | None = None
    sends: int = 0

    def backing_off(self, now: datetime | None) -> bool:
        until = parse_ts(self.backoff_until)
        return bool(until and now and now < until)


class StateStore:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.conversations: dict[str, Conversation] = {}
        self.merchants: dict[str, PartyState] = {}
        self.customers: dict[str, PartyState] = {}
        self.sent_suppression: set[str] = set()
        self.sent_triggers: set[str] = set()

    def merchant(self, mid: str | None) -> PartyState:
        key = mid or "_unknown"
        with self.lock:
            return self.merchants.setdefault(key, PartyState())

    def customer(self, cid: str | None) -> PartyState:
        key = cid or "_unknown"
        with self.lock:
            return self.customers.setdefault(key, PartyState())

    def clear(self) -> None:
        with self.lock:
            self.conversations.clear()
            self.merchants.clear()
            self.customers.clear()
            self.sent_suppression.clear()
            self.sent_triggers.clear()

    # ------------------------------------------------------------ snapshots
    def snapshot(self) -> dict:
        with self.lock:
            return {
                "conversations": {k: asdict(v) for k, v in self.conversations.items()},
                "merchants": {k: {**asdict(v), "inbound": dict(v.inbound)} for k, v in self.merchants.items()},
                "customers": {k: {**asdict(v), "inbound": dict(v.inbound)} for k, v in self.customers.items()},
                "sent_suppression": sorted(self.sent_suppression),
                "sent_triggers": sorted(self.sent_triggers),
                "saved_at": iso(datetime.now().astimezone()),
            }

    def restore(self, snap: dict) -> None:
        with self.lock:
            for k, v in (snap.get("conversations") or {}).items():
                self.conversations[k] = Conversation(**v)
            for bucket, target in (("merchants", self.merchants), ("customers", self.customers)):
                for k, v in (snap.get(bucket) or {}).items():
                    v = dict(v)
                    v["inbound"] = Counter(v.get("inbound") or {})
                    target[k] = PartyState(**v)
            self.sent_suppression |= set(snap.get("sent_suppression") or [])
            self.sent_triggers |= set(snap.get("sent_triggers") or [])
