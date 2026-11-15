"""Core data types shared by every part of KubeSift."""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

    @property
    def rank(self) -> int:
        return _RANK[self]

    @classmethod
    def parse(cls, value: str) -> Severity:
        return cls(value.lower())


_RANK = {Severity.CRITICAL: 4, Severity.HIGH: 3, Severity.MEDIUM: 2, Severity.LOW: 1, Severity.INFO: 0}


class Category(str, Enum):
    WORKLOAD = "workload"
    CONFIG = "config"
    NETWORK = "network"
    NODE = "node"
    STORAGE = "storage"
    SCHEDULING = "scheduling"
    RELIABILITY = "reliability"
    SECURITY = "security"
    RESOURCES = "resources"
    COST = "cost"
    UPGRADE = "upgrade"
    PLATFORM = "platform"


class ResourceRef(BaseModel):
    kind: str
    name: str
    namespace: str | None = None

    def __str__(self) -> str:
        if self.namespace:
            return f"{self.kind}/{self.namespace}/{self.name}"
        return f"{self.kind}/{self.name}"


class Finding(BaseModel):
    """One problem KubeSift can prove from cluster data."""

    check: str = Field(description="Stable check id, e.g. pod-crashloop")
    title: str
    severity: Severity
    category: Category
    kind: str = Field(default="problem", description="problem: broken now; risk: will hurt later (hygiene, security, cost, upgrade)")
    resource: ResourceRef
    message: str
    evidence: list[str] = Field(default_factory=list, description="Facts copied from the cluster")
    remediation: str = ""
    commands: list[str] = Field(default_factory=list)
    docs: str | None = None
    since: datetime | None = Field(default=None, description="When the problem started, if known")
    owner: ResourceRef | None = Field(default=None, description="Top-level workload that owns the resource")
    node: str | None = None
    source: str | None = Field(default=None, description="File and document index for lint results")
    related_changes: list[Change] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list, description="Team notes from past incidents")

    @property
    def fingerprint(self) -> str:
        raw = f"{self.check}|{self.owner or self.resource}"
        return hashlib.sha1(raw.encode()).hexdigest()[:12]


class Change(BaseModel):
    """Something that changed in the cluster (rollout, config edit, Helm upgrade...)."""

    time: datetime
    kind: str = Field(description="rollout | config | helm | argocd | scale | node | other")
    resource: ResourceRef
    summary: str
    details: list[str] = Field(default_factory=list)
    rollback_command: str | None = None


class Incident(BaseModel):
    """Findings grouped by what most likely caused them."""

    id: str
    title: str
    severity: Severity
    layer: str = Field(description="app | config | infra | platform | security | cost")
    root: ResourceRef
    findings: list[Finding]
    likely_trigger: Change | None = None
    summary: str = ""


class Diagnosis(BaseModel):
    summary: str
    root_causes: list[RootCause] = Field(default_factory=list)
    next_checks: list[str] = Field(default_factory=list)
    generated_by: str = "rules"


class RootCause(BaseModel):
    title: str
    explanation: str
    confidence: int = Field(ge=0, le=100)
    evidence: list[str] = Field(default_factory=list, description="Finding fingerprints that support this")
    fix_steps: list[str] = Field(default_factory=list)
    commands: list[str] = Field(default_factory=list)


class Report(BaseModel):
    cluster: str
    namespace: str | None
    collected_at: datetime
    server_version: str | None = None
    findings: list[Finding]
    incidents: list[Incident] = Field(default_factory=list)
    changes: list[Change] = Field(default_factory=list)
    diagnosis: Diagnosis | None = None
    warnings: list[str] = Field(default_factory=list, description="Data KubeSift could not read")
    question: str | None = None

    def counts(self) -> dict[str, int]:
        out = {s.value: 0 for s in Severity}
        for f in self.findings:
            out[f.severity.value] += 1
        return out


Finding.model_rebuild()
Diagnosis.model_rebuild()
