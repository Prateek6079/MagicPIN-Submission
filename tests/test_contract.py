"""HTTP contract tests (LLM disabled -> deterministic, fast)."""
import os

os.environ["LLM_ENABLED"] = "false"
os.environ["PRECOMPOSE"] = "false"

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app, engine

SEED = Path(__file__).resolve().parent.parent / "data" / "seed"


def _j(sub, name):
    return json.loads((SEED / sub / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture()
def client():
    with TestClient(app) as c:
        c.post("/v1/teardown")
        yield c
        c.post("/v1/teardown")


def push(c, scope, cid, payload, version=1):
    return c.post("/v1/context", json={"scope": scope, "context_id": cid, "version": version, "payload": payload,
                                       "delivered_at": "2026-04-26T10:00:00Z"})


def test_healthz_and_metadata(client):
    h = client.get("/v1/healthz").json()
    assert h["status"] == "ok" and h["contexts_loaded"] == {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}
    m = client.get("/v1/metadata").json()
    for k in ("team_name", "team_members", "model", "approach", "version", "submitted_at"):
        assert k in m


def test_context_idempotency_and_versioning(client):
    cat = _j("categories", "dentists")
    assert push(client, "category", "dentists", cat).status_code == 200
    r = push(client, "category", "dentists", cat)
    assert r.status_code == 409 and r.json() == {"accepted": False, "reason": "stale_version", "current_version": 1}
    cat2 = dict(cat, digest=cat["digest"][:1])
    assert push(client, "category", "dentists", cat2, version=2).json()["accepted"] is True
    assert push(client, "category", "dentists", cat, version=1).status_code == 409
    assert client.get("/v1/healthz").json()["contexts_loaded"]["category"] == 1


def test_context_bad_scope(client):
    r = push(client, "planet", "x", {})
    assert r.status_code == 400 and r.json()["reason"] == "invalid_scope"


def test_tick_empty_and_unknown(client):
    assert client.post("/v1/tick", json={"now": "2026-04-26T10:30:00Z", "available_triggers": []}).json() == {"actions": []}
    assert client.post("/v1/tick", json={"now": "2026-04-26T10:30:00Z", "available_triggers": ["nope"]}).json() == {"actions": []}


def _load_basics(c, mid, cat):
    push(c, "category", cat, _j("categories", cat))
    push(c, "merchant", mid, _j("merchants", mid))


def test_tick_action_schema_and_suppression(client):
    _load_basics(client, "m_001_drmeera_dentist_delhi", "dentists")
    trg = _j("triggers", "trg_001_research_digest_dentists")
    push(client, "trigger", trg["id"], trg)
    acts = client.post("/v1/tick", json={"now": "2026-04-26T10:30:00Z", "available_triggers": [trg["id"]]}).json()["actions"]
    assert len(acts) == 1
    a = acts[0]
    for k in ("conversation_id", "merchant_id", "customer_id", "send_as", "trigger_id", "template_name", "template_params",
              "body", "cta", "suppression_key", "rationale"):
        assert k in a, k
    assert a["send_as"] == "vera" and "JIDA" in a["body"] and "2,100" in a["body"]
    assert "http" not in a["body"]
    again = client.post("/v1/tick", json={"now": "2026-04-26T10:35:00Z", "available_triggers": [trg["id"]]}).json()
    assert again == {"actions": []}  # never re-send the same suppression key


def test_one_merchant_message_per_tick(client):
    _load_basics(client, "m_001_drmeera_dentist_delhi", "dentists")
    ids = []
    for t in ("trg_001_research_digest_dentists", "trg_002_compliance_dci_radiograph", "trg_022_cde_webinar_dentists"):
        trg = _j("triggers", t)
        push(client, "trigger", trg["id"], trg)
        ids.append(trg["id"])
    acts = client.post("/v1/tick", json={"now": "2026-04-26T10:30:00Z", "available_triggers": ids}).json()["actions"]
    assert len(acts) == 1 and acts[0]["trigger_id"] == "trg_002_compliance_dci_radiograph"  # highest urgency first


def test_customer_message_on_behalf(client):
    _load_basics(client, "m_001_drmeera_dentist_delhi", "dentists")
    push(client, "customer", "c_001_priya_for_m001", _j("customers", "c_001_priya_for_m001"))
    trg = _j("triggers", "trg_003_recall_due_priya")
    push(client, "trigger", trg["id"], trg)
    a = client.post("/v1/tick", json={"now": "2026-04-26T11:00:00Z", "available_triggers": [trg["id"]]}).json()["actions"][0]
    assert a["send_as"] == "merchant_on_behalf" and a["customer_id"] == "c_001_priya_for_m001"
    assert "Priya" in a["body"] and "Wed 5 Nov, 6pm" in a["body"] and "₹299" in a["body"]
    r = client.post("/v1/reply", json={"conversation_id": a["conversation_id"], "merchant_id": a["merchant_id"],
                                       "customer_id": a["customer_id"], "from_role": "customer", "message": "1",
                                       "received_at": "2026-04-26T11:05:00Z", "turn_number": 2}).json()
    assert r["action"] == "send" and "Wed 5 Nov, 6pm" in r["body"]


def test_reply_unknown_conversation_is_handled(client):
    r = client.post("/v1/reply", json={"conversation_id": "conv_never_seen", "merchant_id": "m_001_drmeera_dentist_delhi",
                                       "from_role": "merchant", "message": "Ok lets do it. Whats next?", "turn_number": 2}).json()
    assert r["action"] == "send"
    body = r["body"].lower()
    assert any(w in body for w in ("done", "draft", "confirm", "next")) and not any(
        w in body for w in ("would you", "do you", "can you tell", "what if", "how about"))


def test_auto_reply_hell_across_conversations(client):
    auto = "Thank you for contacting us! Our team will respond shortly."
    acts = [client.post("/v1/reply", json={"conversation_id": f"conv_auto_{i}", "merchant_id": "m_003_studio11_salon_hyderabad",
                                           "from_role": "merchant", "message": auto, "turn_number": i + 1}).json()["action"]
            for i in range(1, 5)]
    assert acts[:3] == ["send", "wait", "end"]


def test_explicit_stop_ends_and_suppresses(client):
    _load_basics(client, "m_002_bharat_dentist_mumbai", "dentists")
    r = client.post("/v1/reply", json={"conversation_id": "c1", "merchant_id": "m_002_bharat_dentist_mumbai",
                                       "from_role": "merchant", "message": "Not interested. Stop messaging me."}).json()
    assert r["action"] == "end"
    trg = _j("triggers", "trg_004_perf_dip_bharat")
    push(client, "trigger", trg["id"], trg)
    assert client.post("/v1/tick", json={"now": "2026-04-26T12:00:00Z", "available_triggers": [trg["id"]]}).json() == {"actions": []}


def test_malformed_reply_is_400_not_500(client):
    r = client.post("/v1/reply", json={"message": "hi"})
    assert r.status_code == 400
