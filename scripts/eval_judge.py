#!/usr/bin/env python3
"""Local LLM judge: scores the template draft (A) vs the submitted body (B) for each test pair,
using the challenge rubric (same dimensions as judge_simulator.py) with FULL context, in one call per pair.

    python scripts/eval_judge.py                 # all pairs in submission.jsonl
    python scripts/eval_judge.py --only T08,T10  # subset

Writes scripts/out/judge_report.md and prints per-pair + average scores.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.compose.planner import build_plan  # noqa: E402
from app.config import settings  # noqa: E402
from app.llm.groq import LLMClient  # noqa: E402

SYSTEM = """You are a STRICT judge for the magicpin AI Challenge (merchant WhatsApp engagement). Score two candidate
messages, A and B, for the same situation. 0-10 per dimension; 5 is average, 7+ good, 9+ excellent.

1. specificity — verifiable facts (numbers, dates, prices, source citations) taken from the context; generic = low.
2. category_fit — voice right for the business type (dentists clinical-peer; salons warm-practical; restaurants operator-to-operator; gyms coach; pharmacies trustworthy-precise). No hype, no taboo words.
3. merchant_fit — personalised to THIS merchant/customer (name, their numbers, offers, history); language preference honoured.
4. decision_quality — clearly answers "why now" from the trigger; picks the best signal; shows judgement.
5. engagement_compulsion — would they reply? levers (loss aversion, curiosity, social proof, effort externalisation), one clear low-friction CTA last.
PENALTIES (subtract from total, list them): fabricated fact not in context (-3 each), URL (-3), multiple CTAs (-2), internal jargon exposed (-1), wrong language (-2), preamble (-1).
Check every number/claim against the CONTEXT JSON. Be strict and concrete.

Return JSON only:
{"A": {"specificity":n,"category_fit":n,"merchant_fit":n,"decision_quality":n,"engagement_compulsion":n,"penalties":n,"notes":"..."},
 "B": {...same...}, "better": "A"|"B"|"tie", "fix_hint": "<one concrete improvement for the better one>"}"""


def load(seed: Path, sub: str, ident: str | None):
    if not ident:
        return None
    p = seed / sub / f"{ident}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def total(s: dict) -> int:
    return sum(int(s.get(k, 0)) for k in ("specificity", "category_fit", "merchant_fit", "decision_quality", "engagement_compulsion")) - abs(int(s.get("penalties", 0)))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--submission", default=str(ROOT / "submission.jsonl"))
    ap.add_argument("--seed-dir", default=str(ROOT / "data" / "seed"))
    ap.add_argument("--only", default="")
    ap.add_argument("--model", default="openai/gpt-oss-120b")
    ap.add_argument("--apply", action="store_true", help="swap in the template draft where it beats the submitted body by >= 2")
    args = ap.parse_args()
    seed = Path(args.seed_dir)
    subs = [json.loads(l) for l in Path(args.submission).read_text(encoding="utf-8").splitlines() if l.strip()]
    if args.only:
        keep = {x.strip() for x in args.only.split(",")}
        subs = [s for s in subs if s["test_id"] in keep]
    cats = {json.loads(f.read_text(encoding="utf-8"))["slug"]: json.loads(f.read_text(encoding="utf-8")) for f in (seed / "categories").glob("*.json")}
    judge = LLMClient(settings.groq_api_key, settings.groq_base_url, [args.model, "qwen/qwen3.8-27b"], 40,
                      cache_path=settings.state_dir / "judge_cache.json")
    rows, report, swapped = [], ["# Local judge report\n"], []
    for s in subs:
        trg = load(seed, "triggers", s["trigger_id"])
        mer = load(seed, "merchants", s["merchant_id"])
        cus = load(seed, "customers", s.get("customer_id"))
        cat = cats[mer["category_slug"]]
        plan = build_plan(cat, mer, trg, cus)
        ctx = {"category": {k: cat.get(k) for k in ("slug", "voice", "offer_catalog", "peer_stats", "digest", "seasonal_beats")},
               "merchant": mer, "trigger": trg, "customer": cus}
        user = (f"CONTEXT JSON:\n{json.dumps(ctx, ensure_ascii=False)[:9000]}\n\nsend_as: {s['send_as']}\n\n"
                f"MESSAGE A:\n\"\"\"{plan.draft}\"\"\"\n\nMESSAGE B:\n\"\"\"{s['body']}\"\"\"")
        await judge.wait_available(120)
        out, meta = await judge.chat_json(SYSTEM, user, max_tokens=900, deadline=time.monotonic() + 60)
        if not out or "A" not in out or "B" not in out:
            print(s["test_id"], "judge failed", meta)
            continue
        ta, tb = total(out["A"]), total(out["B"])
        rows.append((s["test_id"], ta, tb, out.get("better")))
        if args.apply and ta - tb >= 2 and plan.draft != s["body"]:
            s["body"] = plan.draft
            s["rationale"] = plan.rationale
            s["cta"] = plan.cta_type
            swapped.append(s["test_id"])
        print(f"{s['test_id']} {trg['kind']:24s} template={ta:2d}  submitted={tb:2d}  better={out.get('better')}  | {out.get('fix_hint', '')[:110]}")
        report.append(f"## {s['test_id']} {trg['kind']} — A(template)={ta} B(submitted)={tb} better={out.get('better')}\n\n"
                      f"**A notes:** {out['A'].get('notes')}\n\n**B notes:** {out['B'].get('notes')}\n\n**fix:** {out.get('fix_hint')}\n")
        judge.save_cache()
    if rows:
        n = len(rows)
        print(f"\nAVG template={sum(r[1] for r in rows) / n:.1f}/50  submitted={sum(r[2] for r in rows) / n:.1f}/50  "
              f"(B better {sum(1 for r in rows if r[3] == 'B')}, A better {sum(1 for r in rows if r[3] == 'A')}, n={n})")
    if args.apply and swapped:
        all_rows = [json.loads(l) for l in Path(args.submission).read_text(encoding="utf-8").splitlines() if l.strip()]
        by_id = {s["test_id"]: s for s in subs}
        merged = [by_id.get(r["test_id"], r) for r in all_rows]
        Path(args.submission).write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in merged) + "\n", encoding="utf-8")
        print(f"applied template for: {', '.join(swapped)}")
    (ROOT / "scripts" / "out").mkdir(parents=True, exist_ok=True)
    (ROOT / "scripts" / "out" / "judge_report.md").write_text("\n".join(report), encoding="utf-8")
    print("tokens:", {k: v for k, v in judge.stats.items() if "tokens" in k})
    await judge.aclose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
