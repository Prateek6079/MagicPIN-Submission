"""FastAPI surface: the 5 judge endpoints (+ optional teardown).

Every endpoint is defensive: malformed input -> 400 with a reason (never a 500), and tick/reply
always return a schema-valid body even if something inside fails.
"""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .config import settings
from .engine import Engine
from .store import SCOPES
from .timeutil import iso, utcnow

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("vera")

engine = Engine()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await engine.startup()
    log.info("Vera up. LLM enabled=%s models=%s", engine.llm.enabled, settings.llm_models)
    yield
    await engine.shutdown()


app = FastAPI(title="Vera — magicpin merchant AI", version=settings.bot_version, lifespan=lifespan)


# ---------------------------------------------------------------------------- schemas
class ContextPush(BaseModel):
    model_config = ConfigDict(extra="allow")
    scope: str
    context_id: str
    version: int
    payload: dict[str, Any]
    delivered_at: str | None = None


class TickBody(BaseModel):
    model_config = ConfigDict(extra="allow")
    now: str | None = None
    available_triggers: list[str] = Field(default_factory=list)


class ReplyBody(BaseModel):
    model_config = ConfigDict(extra="allow")
    conversation_id: str
    merchant_id: str | None = None
    customer_id: str | None = None
    from_role: str | None = "merchant"
    message: str = ""
    received_at: str | None = None
    turn_number: int | None = None


@app.exception_handler(RequestValidationError)
async def _bad_request(request: Request, exc: RequestValidationError):
    path = request.url.path
    details = "; ".join(f"{'.'.join(str(x) for x in e.get('loc', []))}: {e.get('msg')}" for e in exc.errors())[:500]
    if path.endswith("/tick"):
        return JSONResponse({"actions": []}, status_code=200)
    if path.endswith("/reply"):
        return JSONResponse({"accepted": False, "reason": "invalid_payload", "details": details}, status_code=400)
    return JSONResponse({"accepted": False, "reason": "invalid_payload", "details": details}, status_code=400)


# ---------------------------------------------------------------------------- endpoints
@app.get("/")
async def root():
    return {"service": "vera", "endpoints": ["/v1/context", "/v1/tick", "/v1/reply", "/v1/healthz", "/v1/metadata"]}


@app.get("/v1/healthz")
async def healthz():
    return {"status": "ok", "uptime_seconds": int(time.time() - engine.started), "contexts_loaded": engine.store.counts()}


@app.get("/v1/metadata")
async def metadata():
    model = settings.llm_models[0] if engine.llm.enabled else "deterministic-planner (LLM disabled)"
    meta = {
        "team_name": settings.team_name,
        "team_members": settings.team_members,
        "model": model,
        "approach": ("Deterministic planner per trigger kind (grounded fact selection, why-now angle, levers, single CTA, "
                     "language choice) -> Groq LLM writer with model rotation -> validator (anti-fabrication number grounding, "
                     "taboos, CTA shape, language) with repair + template fallback; background pre-composition keyed on "
                     "context versions; rule-first reply state machine (auto-reply, opt-out, hostile, commit->action mode)."),
        "version": settings.bot_version,
        "submitted_at": settings.submitted_at,
    }
    if settings.contact_email:
        meta["contact_email"] = settings.contact_email
    return meta


@app.post("/v1/context")
async def push_context(body: ContextPush):
    if body.scope not in SCOPES:
        return JSONResponse({"accepted": False, "reason": "invalid_scope",
                             "details": f"scope must be one of {list(SCOPES)}"}, status_code=400)
    if not body.context_id:
        return JSONResponse({"accepted": False, "reason": "invalid_context_id", "details": "context_id is required"}, status_code=400)
    ok, info = engine.store.put(body.scope, body.context_id, int(body.version), body.payload, body.delivered_at)
    if not ok:
        return JSONResponse({"accepted": False, "reason": "stale_version", "current_version": info["current_version"]}, status_code=409)
    return {"accepted": True, "ack_id": f"ack_{body.context_id}_v{body.version}", "stored_at": info["stored_at"]}


@app.post("/v1/tick")
async def tick(body: TickBody):
    try:
        return await engine.tick(body.now, body.available_triggers)
    except Exception:
        log.exception("tick failed")
        return {"actions": []}


@app.post("/v1/reply")
async def reply(body: ReplyBody):
    try:
        return await engine.reply(body.model_dump())
    except Exception:
        log.exception("reply failed")
        return {"action": "wait", "wait_seconds": 1800, "rationale": "Temporary issue on our side; backing off briefly instead of sending a broken reply."}


@app.post("/v1/teardown")
async def teardown():
    engine.teardown()
    return {"ok": True, "wiped_at": iso(utcnow())}
