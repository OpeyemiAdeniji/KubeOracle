# AI Kubernetes Agent

AI-powered Kubernetes troubleshooting agent.

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
| `backend/.env` | `OPENROUTER_MODEL` | LLM model to use |
| `backend/.env` | `KUBECONFIG_PATH` | Path to kubeconfig file |
| `frontend/.env.local` | `NEXT_PUBLIC_API_BASE_URL` | Backend API URL |

## Project Structure

```
ai-kubernetes-agent/
├── backend/
│   ├── api/routes/      # FastAPI route handlers
│   ├── core/            # Config and settings
│   ├── kubernetes/      # Kubernetes investigation layer (placeholder)
│   ├── ai/              # AI reasoning layer (placeholder)
│   ├── services/        # Business logic (placeholder)
│   ├── models/          # Pydantic schemas
│   └── main.py          # FastAPI app entry point
├── frontend/
│   └── src/
│       ├── app/         # Next.js App Router pages
│       ├── components/  # React components (placeholder)
│       ├── services/    # API service layer (placeholder)
│       ├── hooks/       # Custom React hooks (placeholder)
│       └── types/       # TypeScript types (placeholder)
├── docs/
├── prompts/
└── docker-compose.yml
```
