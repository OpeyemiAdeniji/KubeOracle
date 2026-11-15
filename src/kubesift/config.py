"""Settings, read from (lowest to highest priority): defaults, config file, environment.

Config file: $KUBESIFT_CONFIG or ~/.config/kubesift/config.yaml. Keys are the field
names below; environment variables are KUBESIFT_<FIELD> in upper case.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

_DEFAULT_MODELS = {
    "anthropic": "claude-opus-5-5",
    "openai": "gpt-5",
    "openrouter": "anthropic/claude-opus-5-5",
    "ollama": "llama3.1",
}
_DEFAULT_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "ollama": "http://localhost:11434/v1",
}
_KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


class Settings(BaseModel):
    # Cluster access
    kubeconfig: str | None = None
    context: str | None = None
    request_timeout: int = 20
    max_items_per_kind: int = 5000

    # LLM. provider: auto | anthropic | openai | openrouter | ollama | none
    llm_provider: str = "auto"
    llm_model: str | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = Field(default=None, repr=False)
    llm_effort: str = "medium"
    send_logs_to_llm: bool = True

    # Optional data sources
    prometheus_url: str | None = None
    prometheus_token: str | None = Field(default=None, repr=False)

    # Cost model (USD, on-demand list prices; override for your cloud)
    cost_cpu_hour: float = 0.0316
    cost_gb_hour: float = 0.0042
    cost_gpu_hour: float = 2.50

    # Storage and team knowledge
    data_dir: str = str(Path.home() / ".kubesift")
    database_url: str | None = Field(default=None, repr=False)  # postgresql://... to share state across replicas
    runbooks_dir: str | None = None

    # Server
    api_token: str | None = Field(default=None, repr=False)
    cors_origins: list[str] = Field(default_factory=list)
    allow_writes: bool = False
    leader_election: bool = False  # several replicas: only the Lease holder runs background scans
    leader_namespace: str | None = None  # defaults to the pod's namespace
    scan_interval: int = 0  # seconds; 0 disables background scans
    cache_ttl: int = 20  # seconds a cluster snapshot is reused by the server
    alert_cooldown: int = 600  # seconds before the same namespace is investigated again for alerts
    history_days: int = 30  # stored investigations older than this are deleted
    scan_namespace: str | None = None
    notify_min_severity: str = "high"

    # Notifications
    slack_webhook_url: str | None = Field(default=None, repr=False)
    teams_webhook_url: str | None = Field(default=None, repr=False)
    webhook_url: str | None = Field(default=None, repr=False)
    slack_signing_secret: str | None = Field(default=None, repr=False)  # enables /api/v1/slack/* endpoints
    slack_allowed_users: list[str] = Field(default_factory=list)  # Slack user IDs allowed to apply fixes (empty = nobody)

    # Namespaces skipped by default when scanning all namespaces for hygiene checks
    system_namespaces: list[str] = Field(
        default_factory=lambda: ["kube-system", "kube-public", "kube-node-lease", "local-path-storage"]
    )

    def resolved_provider(self) -> str:
        p = (self.llm_provider or "auto").lower()
        if p != "auto":
            return p
        for name in ("anthropic", "openrouter", "openai"):
            if os.environ.get(_KEY_ENV[name]):
                return name
        return "none"

    def resolved_model(self) -> str:
        return self.llm_model or _DEFAULT_MODELS.get(self.resolved_provider(), "")

    def resolved_base_url(self) -> str | None:
        return self.llm_base_url or _DEFAULT_BASE_URLS.get(self.resolved_provider())

    def resolved_api_key(self) -> str | None:
        if self.llm_api_key:
            return self.llm_api_key
        env = _KEY_ENV.get(self.resolved_provider())
        return os.environ.get(env) if env else None


def _coerce(value: str, annotation: object) -> object:
    text = str(annotation)
    if "bool" in text:
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if "list" in text:
        return [v.strip() for v in value.split(",") if v.strip()]
    if "int" in text and "str" not in text:
        return int(value)
    if "float" in text:
        return float(value)
    return value


def load_settings(**overrides: object) -> Settings:
    data: dict[str, object] = {}
    path = Path(os.environ.get("KUBESIFT_CONFIG", Path.home() / ".config" / "kubesift" / "config.yaml"))
    if path.is_file():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            data.update({k.replace("-", "_"): v for k, v in loaded.items()})
    for name, field in Settings.model_fields.items():
        env = os.environ.get(f"KUBESIFT_{name.upper()}")
        if env is not None and env != "":
            data[name] = _coerce(env, field.annotation)
    data.update({k: v for k, v in overrides.items() if v is not None})
    return Settings(**data)
