# Prompt 01 — Project Setup & Monorepo Scaffold

## What This Prompt Was Trying to Achieve

Establish the full project skeleton for KubeOracle from scratch — a production-style AI Kubernetes troubleshooting tool. The goal was to create a working, runnable monorepo with a FastAPI backend, a Next.js frontend, and Docker Compose wiring them together, with all placeholder directories and config files in place so future prompts could build on a stable foundation.

---

## Key Requirements Covered

- Monorepo layout under `ai-kubernetes-agent/` with `backend/`, `frontend/`, `docs/`, and `prompts/` directories
- FastAPI backend with a `GET /health` endpoint returning service status
- Next.js 14 App Router frontend with TypeScript and Tailwind CSS
- Docker Compose setup to run both services together (`backend` on port 8000, `frontend` on port 3000)
- Environment variable scaffolding — `backend/.env.example` and `frontend/.env.local.example`
- Placeholder module directories (`kubernetes/`, `ai/`, `services/`, `models/`, `hooks/`, `types/`, `components/`) with `.gitkeep` files so the structure was committed to git before any logic was added
- A `README.md` with Quick Start, local dev, and environment variable documentation
- `.gitignore` for both Python and Node environments

---

## Tech Stack and Components Introduced

| Layer | Technology |
|-------|-----------|
| Backend framework | FastAPI + uvicorn |
| Backend config | `pydantic-settings` via `core/config.py` |
| Backend logging | `loguru` |
| Frontend framework | Next.js 14 (App Router) |
| Frontend language | TypeScript |
| Frontend styling | Tailwind CSS 3.4 (locked — do not upgrade to v4) |
| Containerisation | Docker Compose (two services) |
| BaaS | InsForge (`https://pus66i6p.eu-central.insforge.app`) — configured but not yet wired |

---

## Expected Result

After running this prompt:

- `docker compose up --build` starts both services with no errors
- `GET http://localhost:8000/health` returns `{"status": "ok"}`
- `http://localhost:3000` renders a placeholder Next.js page
- All module directories exist so future prompt code can `import` from them without path errors
- Environment variable files are documented but not committed to version control
