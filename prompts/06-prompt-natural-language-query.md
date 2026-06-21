# Prompt 06 — Natural Language Query Engine

## What This Prompt Was Trying to Achieve

Add a conversational query interface so users can ask plain-English questions about their cluster ("Why are my pods crashing?", "Is there a memory issue in the payments namespace?") and get focused, readable answers — without running a full 11-inspector investigation every time. This prompt introduced intent classification to route each question to only the relevant inspectors, a dedicated answer-generation layer that produces prose rather than a JSON diagnosis, and a persistent query history stored in InsForge.

---

## Key Requirements Covered

- **`POST /query`** route — the main NL query endpoint:
  - Accepts `question`, optional `context` (cluster), and optional `session_id`
  - Classifies intent to identify relevant domains and extract a target resource/namespace
  - Runs only the inspectors needed for that intent (targeted investigation)
  - Generates a conversational prose answer via the LLM
  - Returns answer, kubectl commands, confidence, severity, and domains checked

- **`ai/intent_classifier.py`** — uses the LLM to classify a natural language question into:
  - `relevant_domains` — which inspectors to run (`pods`, `logs`, `events`, `deployments`, `network`, `nodes`, `resources`, `config_secrets`, `probes`, `rbac`, `scheduling`)
  - `target_resource` — specific pod/deployment/service name if mentioned
  - `target_namespace` — specific namespace or `"all"`

- **`services/targeted_investigation_service.py`** — runs only the inspectors in `relevant_domains` instead of all 11. Narrows log collection to the target resource when one is specified. Significantly faster than the full investigation for focused queries

- **`ai/conversational_answer.py`** — second LLM call that takes the investigation results and the original question and produces a friendly, readable prose answer (not a JSON diagnosis). Includes suggested kubectl commands and a confidence score

- **`POST /query/save`** — persists a query result to the InsForge `saved_queries` table:
  - Uses the InsForge REST API at `/api/database/records/saved_queries`
  - Request body sent as an array (InsForge requirement)
  - Returns the new row `id` on success

- **`GET /query/history`** — fetches saved queries for a user, filtered by `user_id`, ordered by `created_at` desc, limited to 50 results

- **`saved_queries` table** schema in InsForge:

| Column | Type | Notes |
|--------|------|-------|
| `id` | bigint | Auto-increment primary key |
| `user_id` | text | InsForge auth user ID |
| `question` | text | Original NL question |
| `answer` | text | Prose answer from LLM |
| `kubectl_commands` | jsonb | Array of suggested commands |
| `domains_checked` | jsonb | Inspectors that ran |
| `confidence` | integer | 0–100 |
| `severity` | text | `low` / `medium` / `high` / `critical` |
| `context` | text | Cluster context used |
| `created_at` | timestamptz | Auto-set to `now()` |

- **`QueryBox.tsx`** frontend component — text input with submit button, shows the prose answer in a readable card, offers a "Save" button that calls `/query/save`
- **`QueryResultCard.tsx`** — displays the answer, kubectl commands, confidence bar, and domains checked

---

## Tech Stack and Components Introduced

| Component | Detail |
|-----------|--------|
| Intent routing | LLM-based classification in `intent_classifier.py` |
| Targeted execution | `targeted_investigation_service.py` — selective inspector dispatch |
| Conversational LLM | Second OpenRouter call in `conversational_answer.py` |
| Persistence | InsForge `saved_queries` table via `/api/database/records/` |
| History retrieval | `GET /query/history?user_id=…` with PostgREST filter syntax |
| Migration | `backend/migrations/001_saved_queries.sql` |

---

## Expected Result

After running this prompt:

- A user can type "Why is the nginx pod restarting?" and receive a plain-English diagnosis in under 10 seconds, because only the pods, logs, and events inspectors run — not all 11
- Saved queries appear in the query history tab, persisted across sessions
- The intent classifier correctly routes node-related questions to the `nodes` inspector, network questions to `network`, etc.
- If a target resource is mentioned (e.g. "payments deployment"), log collection is narrowed to that deployment's pods only
- Query history can be fetched by `user_id` and displayed in the frontend history table
