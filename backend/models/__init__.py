from pydantic import BaseModel
from typing import Any


class HealthResponse(BaseModel):
    status: str
    service: str


class InvestigateRequest(BaseModel):
    session_id: str = ""
    context: str = ""


class DiagnosisResult(BaseModel):
    root_cause: str
    explanation: str
    fix: str
    kubectl_commands: list[str]
    prevention: str
    contributing_factors: list[str]
    severity: str
    confidence: int


class DiagnosisResponse(BaseModel):
    status: str
    diagnosis: dict[str, Any]


class QueryRequest(BaseModel):
    question: str
    context: str = ""
    session_id: str = ""


class QueryInvestigation(BaseModel):
    domains_checked: list[str]
    target_resource: str | None
    target_namespace: str


class QueryResponse(BaseModel):
    status: str
    question: str
    answer: str
    investigation: QueryInvestigation
    kubectl_commands: list[str]
    confidence: int
    severity: str
