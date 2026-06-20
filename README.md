# KubeOracle

AI-powered Kubernetes troubleshooting and diagnostics.

## Quick Start

```bash
docker compose up --build
```

- Frontend: http://localhost:3000
- Backend API: http://localhost:8000
- Health check: http://localhost:8000/health

## Development (without Docker)

**Backend:**
```bash
cd backend
pip install -r requirements.txt
cp .env.example .env
uvicorn main:app --reload
```

**Frontend:**
```bash
cd frontend
npm install
cp .env.local.example .env.local
npm run dev
```

## Environment Variables

| File | Variable | Description |
|------|----------|-------------|
| `backend/.env` | `OPENROUTER_API_KEY` | OpenRouter API key |
| `backend/.env` | `OPENROUTER_MODEL` | LLM model (default: claude-sonnet-4-6) |
| `backend/.env` | `KUBECONFIG_PATH` | Path to kubeconfig file |
| `frontend/.env.local` | `NEXT_PUBLIC_API_BASE_URL` | Backend API URL |

## Features

- **Investigation**: 13 automated inspectors across 11 Kubernetes domains
- **AI Reasoning**: Claude Sonnet 4.6 via OpenRouter identifies root causes
- **Natural Language Query**: Ask questions about your cluster in plain English
- **Targeted Inspection**: Only relevant inspectors run for NL queries
- **History**: Full investigation history stored in InsForge, filterable and exportable
- **Multi-cluster**: Select any kubectl context via kubeconfig

## UI Layout

- **Topbar** (56px): Navigation toggle, search, user menu
- **Sidebar** (220px): Overview · Query · Investigate · History · Settings
- **Overview**: Cluster health grid, stats, recent activity
- **Query**: Natural language questions with kubectl fix commands
- **Investigate**: Full 13-inspector deep scan with AI diagnosis
- **History**: Paginated investigation log with CSV export
- **Settings**: Account, cluster, AI, and app configuration

## Project Structure

```
ai-kubernetes-agent/
├── backend/
│   ├── api/routes/          # FastAPI route handlers
│   │   ├── health.py
│   │   ├── clusters.py
│   │   ├── investigate.py
│   │   └── query.py
│   ├── ai/                  # AI reasoning layer
│   │   ├── llm_client.py
│   │   ├── prompt_builder.py
│   │   ├── confidence_engine.py
│   │   ├── intent_classifier.py
│   │   └── conversational_answer.py
│   ├── services/            # Business logic
│   │   ├── investigation_service.py
│   │   └── targeted_investigation_service.py
│   ├── kubernetes/          # Kubernetes inspectors
│   ├── models/              # Pydantic schemas
│   └── main.py
├── frontend/
│   └── src/
│       ├── app/
│       │   ├── layout.tsx           # Root layout (Inter + JetBrains Mono)
│       │   ├── globals.css          # Design tokens + animations
│       │   ├── login/page.tsx       # Auth (sign in / sign up / verify)
│       │   └── dashboard/
│       │       ├── layout.tsx       # Auth guard + Sidebar + Topbar
│       │       ├── page.tsx         # Overview
│       │       ├── query/page.tsx   # Natural language query
│       │       ├── investigate/page.tsx  # Deep investigation
│       │       ├── history/page.tsx # Investigation history
│       │       └── settings/page.tsx
│       ├── components/
│       │   ├── Sidebar.tsx
│       │   ├── Topbar.tsx
│       │   ├── ClusterHealthGrid.tsx
│       │   ├── ClusterSelector.tsx
│       │   ├── InvestigationPanel.tsx
│       │   ├── DiagnosisCard.tsx
│       │   ├── HistoryTable.tsx
│       │   ├── QueryBox.tsx
│       │   └── QueryResultCard.tsx
│       └── types/index.ts
├── start.sh
└── docker-compose.yml
```
