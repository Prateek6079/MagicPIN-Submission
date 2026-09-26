"""Runtime settings, read once from the environment (and an optional local .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader so we don't need python-dotenv. Real env vars win."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


def _list(name: str, default: str) -> list[str]:
    return [x.strip() for x in _env(name, default).split(",") if x.strip()]


@dataclass(frozen=True)
class Settings:
    # --- LLM (Groq, OpenAI-compatible API) ---
    groq_api_key: str = field(default_factory=lambda: _env("GROQ_API_KEY"))
    groq_base_url: str = field(default_factory=lambda: _env("GROQ_BASE_URL", "https://api.groq.com/openai/v1"))
    # Ordered by preference. Each Groq model has its own free-tier rate-limit bucket,
    # so rotating across them multiplies usable throughput.
    llm_models: list[str] = field(default_factory=lambda: _list(
        "LLM_MODELS",
        "openai/gpt-oss-120b,qwen/qwen3.8-27b,openai/gpt-oss-20b",
    ))
    # Proactive messages are only written by the strongest models; weaker ones fall back to the template.
    compose_models: list[str] = field(default_factory=lambda: _list("COMPOSE_MODELS", "openai/gpt-oss-120b,qwen/qwen3.8-27b"))
    llm_enabled_flag: bool = field(default_factory=lambda: _bool("LLM_ENABLED", True))
    llm_call_timeout: float = field(default_factory=lambda: _float("LLM_CALL_TIMEOUT", 9.0))
    llm_max_concurrency: int = field(default_factory=lambda: int(_float("LLM_MAX_CONCURRENCY", 3)))

    # --- latency budgets (the judge waits 30s; API examples budget 10s) ---
    tick_budget: float = field(default_factory=lambda: _float("TICK_BUDGET_SECONDS", 8.0))
    reply_budget: float = field(default_factory=lambda: _float("REPLY_BUDGET_SECONDS", 8.0))
    precompose: bool = field(default_factory=lambda: _bool("PRECOMPOSE", True))

    # --- storage ---
    state_dir: Path = field(default_factory=lambda: Path(_env("STATE_DIR", str(ROOT / "state"))))
    seed_dir: Path = field(default_factory=lambda: Path(_env("SEED_DIR", str(ROOT / "data" / "seed"))))
    use_seed_fallback: bool = field(default_factory=lambda: _bool("USE_SEED_FALLBACK", True))
    # Off by default: a stale snapshot restored before the judge's warmup would cause spurious 409s.
    persist_snapshots: bool = field(default_factory=lambda: _bool("PERSIST_SNAPSHOTS", False))

    # Reference "today" when a caller gives no clock (offline compose()).
    default_now: str = field(default_factory=lambda: _env("DEFAULT_NOW", "2026-04-26T10:00:00Z"))

    # --- /v1/metadata ---
    team_name: str = field(default_factory=lambda: _env("TEAM_NAME", "Harshit Bindal"))
    team_members: list[str] = field(default_factory=lambda: _list("TEAM_MEMBERS", "Harshit Bindal"))
    contact_email: str = field(default_factory=lambda: _env("CONTACT_EMAIL", ""))
    bot_version: str = field(default_factory=lambda: _env("BOT_VERSION", "1.0.0"))
    submitted_at: str = field(default_factory=lambda: _env("SUBMITTED_AT", "2026-09-26T00:00:00Z"))

    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO"))

    @property
    def llm_enabled(self) -> bool:
        return self.llm_enabled_flag and bool(self.groq_api_key)


settings = Settings()
