"""Versioned context store.

- Idempotent by (scope, context_id, version): same-or-lower version -> rejected as stale (409).
- Higher version atomically replaces the previous payload.
- Lookups resolve by context_id, by the payload's own id field (aliases), and by the
  short "m_001"/"c_001" prefix, because the judge's context_id and the ids referenced
  inside triggers are not guaranteed to be spelled identically.
- A read-only *seed* layer (the public base dataset) backs lookups if something was never
  pushed or the process restarted. Seed entries are never counted in /healthz and never
  cause a 409, so the judge's own pushes always win.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any, Callable

from .timeutil import iso, utcnow

log = logging.getLogger(__name__)

SCOPES = ("category", "merchant", "customer", "trigger")
_ID_FIELDS = {"category": ("slug",), "merchant": ("merchant_id",), "customer": ("customer_id",), "trigger": ("id",)}
_PREFIX_RE = re.compile(r"^([a-z]+_\d{2,4})")


def _prefix(cid: str) -> str | None:
    m = _PREFIX_RE.match(cid or "")
    return m.group(1) if m else None


class ContextStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._items: dict[tuple[str, str], dict[str, Any]] = {}
        self._alias: dict[tuple[str, str], str] = {}
        self._seed: dict[tuple[str, str], dict[str, Any]] = {}
        self._seed_alias: dict[tuple[str, str], str] = {}
        self._listeners: list[Callable[[str, str, dict], None]] = []

    # ------------------------------------------------------------------ writes
    def put(self, scope: str, cid: str, version: int, payload: dict, delivered_at: str | None = None) -> tuple[bool, dict]:
        with self._lock:
            key = (scope, self._canonical(scope, cid, payload, create=True))
            cur = self._items.get(key)
            if cur is not None and cur["version"] >= version:
                return False, {"current_version": cur["version"]}
            stored_at = iso(utcnow())
            self._items[key] = {"version": version, "payload": payload, "stored_at": stored_at,
                                "delivered_at": delivered_at, "context_id": cid}
            self._index_aliases(self._alias, scope, key[1], cid, payload)
        for fn in list(self._listeners):
            try:
                fn(scope, key[1], payload)
            except Exception:  # listeners must never break ingestion
                log.exception("context listener failed")
        return True, {"stored_at": stored_at}

    def _canonical(self, scope: str, cid: str, payload: dict, create: bool) -> str:
        """Re-use an existing canonical key if this context_id or payload id already maps to one."""
        if (scope, cid) in self._items:
            return cid
        if (scope, cid) in self._alias:
            return self._alias[(scope, cid)]
        for f in _ID_FIELDS.get(scope, ()):
            pid = payload.get(f) if isinstance(payload, dict) else None
            if isinstance(pid, str) and (scope, pid) in self._alias:
                return self._alias[(scope, pid)]
        return cid

    @staticmethod
    def _index_aliases(alias: dict, scope: str, canonical: str, cid: str, payload: dict) -> None:
        alias[(scope, cid)] = canonical
        for f in _ID_FIELDS.get(scope, ()):
            pid = payload.get(f) if isinstance(payload, dict) else None
            if isinstance(pid, str) and pid:
                alias[(scope, pid)] = canonical

    def on_update(self, fn: Callable[[str, str, dict], None]) -> None:
        self._listeners.append(fn)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._alias.clear()

    # ------------------------------------------------------------------ reads
    def _resolve(self, items: dict, alias: dict, scope: str, cid: str | None) -> dict | None:
        if not cid:
            return None
        if (scope, cid) in items:
            return items[(scope, cid)]
        canon = alias.get((scope, cid))
        if canon and (scope, canon) in items:
            return items[(scope, canon)]
        pre = _prefix(cid)
        if pre:  # unique short-prefix match ("m_001" <-> "m_001_drmeera_dentist_delhi")
            hits = [v for (s, k), v in items.items() if s == scope and _prefix(k) == pre]
            if len(hits) == 1:
                return hits[0]
        return None

    def get_entry(self, scope: str, cid: str | None, allow_seed: bool = True) -> dict | None:
        with self._lock:
            hit = self._resolve(self._items, self._alias, scope, cid)
            if hit is None and allow_seed:
                hit = self._resolve(self._seed, self._seed_alias, scope, cid)
            return hit

    def get(self, scope: str, cid: str | None, allow_seed: bool = True) -> dict | None:
        entry = self.get_entry(scope, cid, allow_seed)
        return entry["payload"] if entry else None

    def version(self, scope: str, cid: str | None) -> int:
        """Pushed version, 0 for seed-only, -1 if unknown. Used in composition cache keys."""
        with self._lock:
            hit = self._resolve(self._items, self._alias, scope, cid)
            if hit is not None:
                return hit["version"]
            return 0 if self._resolve(self._seed, self._seed_alias, scope, cid) else -1

    def items(self, scope: str) -> dict[str, dict]:
        with self._lock:
            return {k: v["payload"] for (s, k), v in self._items.items() if s == scope}

    def counts(self) -> dict[str, int]:
        with self._lock:
            out = {s: 0 for s in SCOPES}
            for (s, _k) in self._items:
                out[s] = out.get(s, 0) + 1
            return out

    # ------------------------------------------------------------------ seed + snapshots
    def load_seed(self, seed_dir: Path) -> int:
        n = 0
        folders = {"category": "categories", "merchant": "merchants", "customer": "customers", "trigger": "triggers"}
        for scope, folder in folders.items():
            d = seed_dir / folder
            if not d.exists():
                continue
            for f in sorted(d.glob("*.json")):
                try:
                    payload = json.loads(f.read_text(encoding="utf-8"))
                except Exception:
                    continue
                cid = next((payload.get(x) for x in _ID_FIELDS[scope] if payload.get(x)), f.stem)
                self._seed[(scope, cid)] = {"version": 0, "payload": payload, "context_id": cid}
                self._index_aliases(self._seed_alias, scope, cid, f.stem, payload)
                n += 1
        log.info("seed fallback loaded: %d contexts from %s", n, seed_dir)
        return n

    def snapshot(self) -> dict:
        with self._lock:
            return {"items": [{"scope": s, "id": k, **v} for (s, k), v in self._items.items()]}

    def restore(self, snap: dict) -> None:
        with self._lock:
            for it in snap.get("items", []):
                key = (it["scope"], it["id"])
                self._items[key] = {k: it.get(k) for k in ("version", "payload", "stored_at", "delivered_at", "context_id")}
                self._index_aliases(self._alias, it["scope"], it["id"], it.get("context_id") or it["id"], it.get("payload") or {})
