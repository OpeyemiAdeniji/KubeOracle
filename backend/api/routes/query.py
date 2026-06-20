"""
Query routes:
  POST /query          — natural language cluster query
  POST /query/save     — persist a query result to saved_queries table
  GET  /query/history  — fetch saved queries for a user
"""

import httpx
from fastapi import APIRouter
from pydantic import BaseModel
from loguru import logger

from ai.intent_classifier import classify_intent
from ai.conversational_answer import generate_answer
from services.targeted_investigation_service import run_targeted_investigation
from core.config import settings

router = APIRouter()


# ── Models ────────────────────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    question: str
    context: str = ""
    session_id: str = ""


class SaveQueryRequest(BaseModel):
    user_id: str
    question: str
    answer: str
    kubectl_commands: list = []
    domains_checked: list = []
    confidence: int = 0
    severity: str = "low"
    context: str = ""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _insforge_headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.insforge_anon_key}",
        "Content-Type": "application/json",
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/query")
def query_cluster(req: QueryRequest):
    logger.info(f"[/query] {req.question[:100]}")

    try:
        intent = classify_intent(req.question)
        investigation = run_targeted_investigation(
            relevant_domains=intent["relevant_domains"],
            context=req.context,
            target_resource=intent.get("target_resource"),
            target_namespace=intent.get("target_namespace", "all"),
        )
        answer_data = generate_answer(
            question=req.question,
            intent=intent,
            investigation=investigation,
            context=req.context,
        )

        return {
            "status": "success",
            "question": req.question,
            "answer": answer_data["answer"],
            "investigation": {
                "domains_checked": intent["relevant_domains"],
                "target_resource": intent.get("target_resource"),
                "target_namespace": intent.get("target_namespace", "all"),
            },
            "kubectl_commands": answer_data.get("kubectl_commands", []),
            "confidence": answer_data.get("confidence", 0),
            "severity": answer_data.get("severity", "low"),
        }

    except Exception as exc:
        logger.error(f"[/query] failed: {exc}")
        return {
            "status": "error",
            "question": req.question,
            "answer": f"Query failed: {exc}",
            "investigation": {"domains_checked": [], "target_resource": None, "target_namespace": "all"},
            "kubectl_commands": [],
            "confidence": 0,
            "severity": "low",
        }


@router.post("/query/save")
def save_query(req: SaveQueryRequest):
    if not settings.insforge_anon_key:
        logger.warning("[/query/save] InsForge anon key not configured")
        return {"status": "error", "message": "InsForge not configured — set INSFORGE_ANON_KEY in backend/.env"}

    url = f"{settings.insforge_url}/api/database/records/saved_queries"
    payload = [{
        "user_id": req.user_id,
        "question": req.question,
        "answer": req.answer,
        "kubectl_commands": req.kubectl_commands,
        "domains_checked": req.domains_checked,
        "confidence": req.confidence,
        "severity": req.severity,
        "context": req.context,
    }]
    logger.info(f"[/query/save] POST {url} | user={req.user_id[:8]}… | q={req.question[:60]}")

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                url,
                headers={**_insforge_headers(), "Prefer": "return=representation"},
                json=payload,
            )

        logger.info(f"[/query/save] InsForge response: status={resp.status_code} body={resp.text[:300]}")

        if resp.status_code not in (200, 201):
            logger.error(f"[/query/save] Insert failed: HTTP {resp.status_code} — {resp.text[:400]}")
            return {"status": "error", "message": f"DB insert failed (HTTP {resp.status_code}): {resp.text[:200]}"}

        rows = resp.json() if resp.text else []

        # PostgREST returns the inserted row with Prefer: return=representation.
        # Empty list means RLS blocked the INSERT or the table doesn't exist.
        if not isinstance(rows, list) or len(rows) == 0:
            logger.error(
                "[/query/save] Insert returned 0 rows — possible causes: "
                "(1) RLS policy on saved_queries table is blocking anon key inserts, "
                "(2) saved_queries table does not exist. "
                "Run backend/migrations/001_saved_queries.sql and check RLS settings."
            )
            return {
                "status": "error",
                "message": (
                    "Insert returned 0 rows. Likely causes: "
                    "(1) saved_queries table missing — run backend/migrations/001_saved_queries.sql, "
                    "(2) RLS policy blocking anonymous inserts — disable RLS or add permissive policy."
                ),
            }

        record_id = rows[0].get("id")
        logger.info(f"[/query/save] ✓ Saved successfully — id={record_id}")
        return {"status": "success", "id": record_id}

    except Exception as exc:
        logger.error(f"[/query/save] Exception: {exc}")
        return {"status": "error", "message": str(exc)}


@router.get("/query/history")
def get_query_history(user_id: str):
    if not settings.insforge_anon_key:
        return {"status": "error", "queries": []}

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(
                f"{settings.insforge_url}/api/database/records/saved_queries",
                headers=_insforge_headers(),
                params={
                    "user_id": f"eq.{user_id}",
                    "order": "created_at.desc",
                    "limit": "50",
                },
            )

        logger.info(f"[/query/history] InsForge response: status={resp.status_code} rows={len(resp.json()) if resp.status_code == 200 else 'N/A'}")

        if resp.status_code == 200:
            return {"status": "success", "queries": resp.json()}

        logger.error(f"[/query/history] DB error {resp.status_code}: {resp.text[:300]}")
        return {"status": "error", "queries": []}

    except Exception as exc:
        logger.error(f"[/query/history] {exc}")
        return {"status": "error", "queries": []}
