#!/usr/bin/env python3
"""Produce submission.jsonl for the 30 canonical test pairs (+ a human-readable review report).

    python scripts/generate_submission.py            # LLM writer (needs GROQ_API_KEY), falls back per-pair
    python scripts/generate_submission.py --no-llm   # deterministic template writer only

Outputs:
    submission.jsonl                 — one line per test pair (challenge §7.2)
    scripts/out/submission_review.md — body, source (llm/template), model, validator notes, template draft
Deterministic: temperature 0, fixed seed, and a prompt-hash cache at state/compose_cache.json.
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

from app.compose.composer import Composer  # noqa: E402
from app.config import settings  # noqa: E402
from app.llm.groq import LLMClient  # noqa: E402


def load(seed: Path, sub: str, ident: str | None) -> dict | None:
    if not ident:
        return None
    p = seed / sub / f"{ident}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed-dir", default=str(ROOT / "data" / "seed"))
    ap.add_argument("--out", default=str(ROOT / "submission.jsonl"))
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--only", default="", help="comma-separated test ids, e.g. T01,T21")
    ap.add_argument("--merge", action="store_true", help="with --only: update just those lines in the existing --out file")
    args = ap.parse_args()
    seed = Path(args.seed_dir)
    pairs = json.loads((seed / "test_pairs.json").read_text(encoding="utf-8"))["pairs"]
    if args.only:
        keep = {x.strip() for x in args.only.split(",")}
        pairs = [p for p in pairs if p["test_id"] in keep]
    cats = {json.loads(f.read_text(encoding="utf-8"))["slug"]: json.loads(f.read_text(encoding="utf-8"))
            for f in (seed / "categories").glob("*.json")}

    llm = LLMClient(settings.groq_api_key, settings.groq_base_url, settings.llm_models, 25,
                    cache_path=settings.state_dir / "compose_cache.json")
    llm.enabled = settings.llm_enabled and not args.no_llm
    composer = Composer(llm)
    lines, report = [], ["# Submission review\n"]
    t_all = time.time()
    for p in pairs:
        trg = load(seed, "triggers", p["trigger_id"])
        mer = load(seed, "merchants", p["merchant_id"])
        cus = load(seed, "customers", p.get("customer_id"))
        cat = cats[mer["category_slug"]]
        t = time.time()
        if llm.enabled:  # offline: wait out free-tier cooldowns instead of falling back to the template
            await llm.wait_available(120, settings.compose_models)
        comp = await composer.compose(cat, mer, trg, cus, deadline=time.monotonic() + 28, use_llm=llm.enabled)
        msg = comp.to_message()
        line = {"test_id": p["test_id"], "body": msg["body"], "cta": msg["cta"], "send_as": msg["send_as"],
                "suppression_key": msg["suppression_key"], "rationale": msg["rationale"],
                "trigger_id": p["trigger_id"], "merchant_id": p["merchant_id"], "customer_id": p.get("customer_id"),
                "template_name": msg["template_name"], "template_params": msg["template_params"]}
        lines.append(line)
        print(f"{p['test_id']} {trg['kind']:24s} {comp.source:8s} {comp.model or '-':22s} {time.time() - t:5.1f}s "
              f"{'ERR ' + '; '.join(comp.validation_errors) if comp.validation_errors else ''}")
        report.append(f"## {p['test_id']} — {trg['kind']} → {mer['identity']['name']}"
                      + (f" / customer {cus['identity']['name']}" if cus else "")
                      + f"\n\n*source:* `{comp.source}` {comp.model or ''} · *cta:* `{msg['cta']}` · *send_as:* `{msg['send_as']}`"
                      + (f" · *validator:* {comp.validation_errors}" if comp.validation_errors else "")
                      + f"\n\n{msg['body']}\n\n<details><summary>template draft</summary>\n\n{comp.plan.draft}\n\n</details>\n\n"
                      + f"*rationale:* {msg['rationale']}\n")
        llm.save_cache()
    if args.merge and Path(args.out).exists():
        new = {x["test_id"]: x for x in lines}
        old = [json.loads(l) for l in Path(args.out).read_text(encoding="utf-8").splitlines() if l.strip()]
        lines = [new.get(x["test_id"], x) for x in old]
    Path(args.out).write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n", encoding="utf-8")
    out_dir = ROOT / "scripts" / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "submission_review.md").write_text("\n".join(report), encoding="utf-8")
    print(f"\nwrote {len(lines)} lines -> {args.out} in {time.time() - t_all:.0f}s; llm stats: {dict(llm.stats)}")
    await llm.aclose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
