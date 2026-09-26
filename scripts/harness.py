#!/usr/bin/env python3
"""Local judge-harness simulator (no LLM needed).

Replays the magicpin lifecycle against a running bot and checks the *contract* and the
*conversation policy* — the things that cost operational points:

  warmup     : healthz/metadata, push 5+50+200 base contexts, verify counts, idempotency 409
  window     : push triggers in waves, tick with simulated time, validate every action schema,
               no duplicate suppression keys, no URLs, latency < budget
  replies    : scripted merchant replies (engaged / auto-reply / hard-no / curveball / later)
  injection  : category v2 with a NEW digest item + a trigger pointing at it -> must be used
  replays    : auto-reply hell (4x), intent transition, hostile -> off-topic
Writes every message to scripts/out/harness_transcript.jsonl for eyeballing.

Usage:  python scripts/harness.py [--url http://localhost:8080] [--seed-dir data/seed]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib import error, request

ROOT = Path(__file__).resolve().parent.parent
REQ_ACTION = {"conversation_id", "merchant_id", "send_as", "trigger_id", "body", "cta", "suppression_key", "rationale"}
PROBLEMS: list[str] = []
LAT: dict[str, list[float]] = {"tick": [], "reply": [], "context": []}
TRANSCRIPT: list[dict] = []


def call(url: str, method: str, path: str, body: dict | None = None, timeout: float = 30) -> tuple[int, dict | None, float]:
    data = json.dumps(body).encode() if body is not None else None
    req = request.Request(url + path, data=data, method=method, headers={"Content-Type": "application/json"})
    t = time.time()
    try:
        with request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"null"), time.time() - t
    except error.HTTPError as e:
        try:
            return e.code, json.loads(e.read()), time.time() - t
        except Exception:
            return e.code, None, time.time() - t
    except Exception as e:
        return 0, {"error": str(e)}, time.time() - t


def problem(msg: str) -> None:
    PROBLEMS.append(msg)
    print("  [FAIL]", msg)


def ok(msg: str) -> None:
    print("  [ok]  ", msg)


def load_dir(d: Path) -> list[dict]:
    return [json.loads(f.read_text(encoding="utf-8")) for f in sorted(d.glob("*.json"))]


def check_action(a: dict, seen_keys: set) -> None:
    missing = REQ_ACTION - set(a)
    if missing:
        problem(f"action missing fields {missing}: {a.get('trigger_id')}")
    body = a.get("body") or ""
    if not body.strip():
        problem(f"empty body for {a.get('trigger_id')}")
    if re.search(r"https?://|www\.", body):
        problem(f"URL in body for {a.get('trigger_id')}")
    if re.search(r"\b[a-z]+_[a-z_]+\b", body):
        problem(f"snake_case jargon in body for {a.get('trigger_id')}: {re.search(r'[a-z]+_[a-z_]+', body).group(0)}")
    if a.get("suppression_key") in seen_keys:
        problem(f"duplicate suppression_key {a.get('suppression_key')}")
    seen_keys.add(a.get("suppression_key"))
    if a.get("send_as") not in ("vera", "merchant_on_behalf"):
        problem(f"bad send_as {a.get('send_as')}")
    if a.get("customer_id") and a.get("send_as") != "merchant_on_behalf":
        problem(f"customer message not sent as merchant_on_behalf: {a.get('trigger_id')}")


def reply(url, conv, mid, msg, turn, cust=None, role="merchant", now=None):
    body = {"conversation_id": conv, "merchant_id": mid, "customer_id": cust, "from_role": role, "message": msg,
            "received_at": (now or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z"), "turn_number": turn}
    st, out, lat = call(url, "POST", "/v1/reply", body)
    LAT["reply"].append(lat)
    TRANSCRIPT.append({"type": "reply", "conversation_id": conv, "in": msg, "out": out, "latency": round(lat, 2)})
    if st != 200 or not isinstance(out, dict) or out.get("action") not in ("send", "wait", "end"):
        problem(f"bad reply response ({st}): {out}")
        return {}
    if out["action"] == "send" and not (out.get("body") or "").strip():
        problem("send with empty body")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8080")
    ap.add_argument("--seed-dir", default=str(ROOT / "data" / "seed"))
    ap.add_argument("--waves", type=int, default=12)
    args = ap.parse_args()
    url, seed = args.url.rstrip("/"), Path(args.seed_dir)
    cats, merchants = load_dir(seed / "categories"), load_dir(seed / "merchants")
    customers, triggers = load_dir(seed / "customers"), load_dir(seed / "triggers")
    t0 = datetime(2026, 4, 26, 10, 0, tzinfo=timezone.utc)

    print("\n== WARMUP")
    call(url, "POST", "/v1/teardown", {})
    st, h, _ = call(url, "GET", "/v1/healthz")
    ok(f"healthz {st} {h}") if st == 200 else problem(f"healthz {st}")
    st, md, _ = call(url, "GET", "/v1/metadata")
    ok(f"metadata team={md.get('team_name')} model={md.get('model')}") if st == 200 else problem("metadata")
    for scope, items, key in (("category", cats, "slug"), ("merchant", merchants, "merchant_id"), ("customer", customers, "customer_id")):
        for it in items:
            st, out, lat = call(url, "POST", "/v1/context", {"scope": scope, "context_id": it[key], "version": 1, "payload": it,
                                                             "delivered_at": t0.isoformat()})
            LAT["context"].append(lat)
            if st != 200 or not out.get("accepted"):
                problem(f"context push {scope}/{it[key]} -> {st} {out}")
    st, h, _ = call(url, "GET", "/v1/healthz")
    want = {"category": 5, "merchant": 50, "customer": 200, "trigger": 0}
    ok(f"contexts_loaded {h['contexts_loaded']}") if h["contexts_loaded"] == want else problem(f"counts {h['contexts_loaded']} != {want}")
    st, out, _ = call(url, "POST", "/v1/context", {"scope": "category", "context_id": cats[0]["slug"], "version": 1, "payload": cats[0]})
    ok("re-push same version -> 409 stale_version") if st == 409 and out.get("reason") == "stale_version" else problem(f"idempotency {st} {out}")

    print("\n== TEST WINDOW (ticks)")
    seen_keys: set = set()
    actions_all: list[dict] = []
    waves = [triggers[i::args.waves] for i in range(args.waves)]
    active: list[str] = []
    for w, wave in enumerate(waves):
        now = t0 + timedelta(minutes=5 * w)
        for trg in wave:
            call(url, "POST", "/v1/context", {"scope": "trigger", "context_id": trg["id"], "version": 1, "payload": trg,
                                              "delivered_at": now.isoformat()})
            active.append(trg["id"])
        st, out, lat = call(url, "POST", "/v1/tick", {"now": now.isoformat().replace("+00:00", "Z"), "available_triggers": active})
        LAT["tick"].append(lat)
        if st != 200 or "actions" not in (out or {}):
            problem(f"tick {w} -> {st} {out}")
            continue
        acts = out["actions"]
        if len(acts) > 20:
            problem(f"tick returned {len(acts)} > 20 actions")
        per_m = {}
        for a in acts:
            check_action(a, seen_keys)
            TRANSCRIPT.append({"type": "action", "tick": w, **a})
            if a.get("send_as") == "vera":
                per_m[a["merchant_id"]] = per_m.get(a["merchant_id"], 0) + 1
        if any(v > 1 for v in per_m.values()):
            problem(f"tick {w}: >1 merchant-facing message to the same merchant")
        actions_all += acts
        print(f"  tick {w:2d} @ {now:%H:%M}  active={len(active):3d}  actions={len(acts):2d}  {lat:.2f}s")
    sent = {a["trigger_id"] for a in actions_all}
    ok(f"{len(actions_all)} actions across {len(waves)} ticks; {len(set(triggers_ids := [t['id'] for t in triggers]) - sent)} triggers held back")

    print("\n== REPLIES on live conversations")
    scripts = ["Yes please, go ahead", "Thank you for contacting us! Our team will respond shortly.",
               "Not interested. Stop messaging me.", "Btw can you also help me with my GST filing this month?",
               "Busy right now, message me tomorrow", "How much will this cost me?", "Ok lets do it. Whats next?"]
    for i, a in enumerate([a for a in actions_all if a["send_as"] == "vera"][:14]):
        msg = scripts[i % len(scripts)]
        out = reply(url, a["conversation_id"], a["merchant_id"], msg, 2, now=t0 + timedelta(hours=1))
        print(f"  [{out.get('action')}] {msg[:40]!r:44} -> {(out.get('body') or out.get('rationale') or '')[:110]}")
    for a in [a for a in actions_all if a["send_as"] == "merchant_on_behalf"][:4]:
        out = reply(url, a["conversation_id"], a["merchant_id"], "1" if a.get("cta") == "multi_choice_slot" else "Yes please",
                    2, cust=a.get("customer_id"), role="customer", now=t0 + timedelta(hours=1))
        print(f"  [customer:{out.get('action')}] -> {(out.get('body') or out.get('rationale') or '')[:120]}")

    print("\n== ADAPTIVE INJECTION")
    den = next(c for c in cats if c["slug"] == "dentists")
    den2 = json.loads(json.dumps(den))
    den2["digest"].append({"id": "d_NEW_ortho_study", "kind": "research", "title": "Night-guard use cuts bruxism tooth wear 41% over 12 months",
                           "source": "IJDR Nov 2026, p.7", "trial_n": 640, "patient_segment": "adults_with_bruxism",
                           "summary": "Randomised trial of 640 adults: custom night-guards reduced enamel wear 41% vs no guard. Effect strongest in 18-30 age band.",
                           "actionable": "Offer night-guard fitting to bruxism patients this exam season"})
    st, out, _ = call(url, "POST", "/v1/context", {"scope": "category", "context_id": "dentists", "version": 2, "payload": den2})
    ok("category v2 accepted") if st == 200 else problem(f"category v2 {st} {out}")
    trg = {"id": "trg_inject_bruxism", "scope": "merchant", "kind": "research_digest", "source": "external",
           "merchant_id": "m_002_bharat_dentist_mumbai", "customer_id": None,
           "payload": {"category": "dentists", "top_item_id": "d_NEW_ortho_study"}, "urgency": 3,
           "suppression_key": "research:dentists:inject", "expires_at": "2026-12-01T00:00:00Z"}
    call(url, "POST", "/v1/context", {"scope": "trigger", "context_id": trg["id"], "version": 1, "payload": trg})
    now = t0 + timedelta(hours=3)
    st, out, lat = call(url, "POST", "/v1/tick", {"now": now.isoformat(), "available_triggers": [trg["id"]]})
    acts = (out or {}).get("actions", [])
    hit = [a for a in acts if a["trigger_id"] == trg["id"]]
    if hit and ("41%" in hit[0]["body"] or "640" in hit[0]["body"] or "IJDR" in hit[0]["body"]):
        ok(f"injected digest used: {hit[0]['body'][:140]}")
    else:
        problem(f"injected digest not used (merchant may be backing off?): {acts}")

    print("\n== REPLAY: auto-reply hell (same merchant, fresh conversations like the local simulator)")
    auto = "Thank you for contacting us! Our team will respond shortly."
    res = [reply(url, f"conv_auto_{i}", "m_003_studio11_salon_hyderabad", auto, i + 1).get("action") for i in range(1, 5)]
    print("  actions:", res)
    ok("auto-reply: send -> wait -> end") if "end" in res[:3] else problem(f"auto-reply never ended in 3 turns: {res}")

    print("\n== REPLAY: intent transition")
    out = reply(url, "conv_intent_1", "m_001_drmeera_dentist_delhi", "Ok lets do it. Whats next?", 2)
    body = (out.get("body") or "").lower()
    qual = ["would you", "do you", "can you tell", "what if", "how about"]
    act = ["done", "sending", "draft", "here", "confirm", "proceed", "next"]
    print("  bot:", out.get("body"))
    ok("switched to action mode") if any(w in body for w in act) and not any(w in body for w in qual) else problem("still qualifying after commit")

    print("\n== REPLAY: hostile -> off-topic")
    o1 = reply(url, "conv_hostile_1", "m_005_pizzajunction_restaurant_delhi", "Why are you bothering me. This is useless spam.", 2)
    o2 = reply(url, "conv_hostile_1", "m_005_pizzajunction_restaurant_delhi", "Can you also help me file my GST?", 3)
    print("  t1:", o1.get("action"), (o1.get("body") or o1.get("rationale"))[:120])
    print("  t2:", o2.get("action"), (o2.get("body") or o2.get("rationale") or "")[:140])
    ok("hostile handled politely") if o1.get("action") in ("end", "send") else problem("hostile")
    o3 = reply(url, "conv_stop_1", "m_007_powerhouse_gym_bangalore", "Stop messaging me. This is useless spam.", 2)
    ok("explicit stop -> end") if o3.get("action") == "end" else problem(f"explicit stop not ended: {o3}")

    print("\n== LATENCY")
    for k, v in LAT.items():
        if v:
            v2 = sorted(v)
            print(f"  {k:8s} n={len(v):3d}  p50={v2[len(v2) // 2]:.2f}s  max={v2[-1]:.2f}s")
            if v2[-1] > 10:
                problem(f"{k} max latency {v2[-1]:.1f}s > 10s budget")

    call(url, "POST", "/v1/teardown", {})  # leave the bot clean for the real judge
    out_dir = ROOT / "scripts" / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "harness_transcript.jsonl").write_text("\n".join(json.dumps(t, ensure_ascii=False) for t in TRANSCRIPT), encoding="utf-8")
    print(f"\n{'ALL CHECKS PASSED' if not PROBLEMS else f'{len(PROBLEMS)} PROBLEM(S)'} — transcript: scripts/out/harness_transcript.jsonl")
    return 1 if PROBLEMS else 0


if __name__ == "__main__":
    sys.exit(main())
