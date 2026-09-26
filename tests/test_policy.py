"""Unit tests: intent classifier, validator guardrails, and template grounding across all 100 triggers."""
import json
from pathlib import Path

import pytest

from app.compose.planner import build_plan
from app.compose.validator import validate
from app.conversation.intents import classify, detect_lang

SEED = Path(__file__).resolve().parent.parent / "data" / "seed"


@pytest.mark.parametrize("text,intent", [
    ("Thank you for contacting Dr. Meera's Dental Clinic! Our team will respond shortly.", "auto_reply"),
    ("Aapki jaankari ke liye bahut-bahut shukriya. Main aapki yeh sabhi baatein team tak pahuncha deti hoon.", "auto_reply"),
    ("Not interested. Stop messaging me.", "opt_out"),
    ("Why are you bothering me. This is useless. Stop sending these.", "opt_out"),
    ("This is useless spam", "hostile"),
    ("Btw can you also help me with my GST filing this month?", "off_topic"),
    ("Ok lets do it. Whats next?", "commit"),
    ("Yes please send the abstract. Also draft the patient WhatsApp.", "commit"),
    ("Mujhe magicpin judna hai", "commit"),
    ("haan kar do", "commit"),
    ("Busy right now, message me tomorrow", "later"),
    ("No thanks", "decline"),
    ("How much does it cost?", "question"),
    ("thanks", "thanks"),
])
def test_classifier(text, intent):
    assert classify(text).intent == intent


def test_short_commit_repeated_is_not_auto_reply():
    assert classify("Ok lets do it. Whats next?", repeated=1).intent == "commit"
    assert classify("Hello we are open from 10 to 8 every day, visit us", repeated=1).intent == "auto_reply"


def test_customer_slot_pick():
    c = classify("2", audience="customer", slot_labels=["Wed 5 Nov, 6pm", "Thu 6 Nov, 5pm"])
    assert c.intent == "slot_choice" and c.slot_index == 1


def test_language_detection():
    assert detect_lang("Haan theek hai, kal baat karte hain") == "hinglish"
    assert detect_lang("Please send me the draft") == "en"
    assert detect_lang("ठीक है") == "hindi"


def _all_plans():
    cats = {json.loads(f.read_text(encoding="utf-8"))["slug"]: json.loads(f.read_text(encoding="utf-8")) for f in (SEED / "categories").glob("*.json")}
    for f in sorted((SEED / "triggers").glob("*.json")):
        t = json.loads(f.read_text(encoding="utf-8"))
        m = json.loads((SEED / "merchants" / f"{t['merchant_id']}.json").read_text(encoding="utf-8"))
        cp = SEED / "customers" / f"{t.get('customer_id')}.json"
        c = json.loads(cp.read_text(encoding="utf-8")) if t.get("customer_id") and cp.exists() else None
        yield t, cats[m["category_slug"]], build_plan(cats[m["category_slug"]], m, t, c)


def test_every_template_is_grounded_and_clean():
    bad = []
    for t, cat, plan in _all_plans():
        errs = validate(plan.draft, plan, [x for x in cat["voice"].get("vocab_taboo", [])], cat["slug"] in ("dentists", "pharmacies"))
        hard = [e for e in errs if any(k in e for k in ("grounded", "URL", "internal", "taboo", "preamble", "placeholder", "last sentence"))]
        if hard:
            bad.append((t["id"], hard))
    assert not bad, bad


def test_validator_catches_fabrication():
    _t, _cat, plan = next(p for p in _all_plans() if p[0]["id"] == "trg_001_research_digest_dentists")
    body = plan.draft.replace("2,100", "3,400")
    assert any("grounded" in e for e in validate(body, plan))
    body2 = plan.draft + " Also 3 dentists near you already did this."
    assert any("grounded" in e or "last sentence" in e for e in validate(body2, plan))
    assert any("URL" in e for e in validate(plan.draft + " See https://x.com", plan))
    assert any("grounded" in e for e in validate(plan.draft.replace("Dr. Meera,", "Dr. Meera, it's been 11 months —"), plan))


def test_duration_must_be_grounded_with_unit():
    # T08: the first LLM run invented "it's been 3 months since your last visit" for this customer
    _t, _cat, plan = next(p for p in _all_plans() if p[0]["id"].startswith("trg_081_chronic_refill_due"))
    body = plan.draft.replace("We haven't seen you since 1 Apr", "It's been 3 months since your last visit")
    assert any("durations not grounded" in e for e in validate(body, plan))
