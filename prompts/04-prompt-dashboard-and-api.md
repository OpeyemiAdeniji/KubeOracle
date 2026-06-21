# Prompt 04 — Dashboard & API Integration

## What This Prompt Was Trying to Achieve

Wire the backend investigation engine to a working frontend. This prompt connected all previously built pieces — FastAPI routes, InsForge BaaS, and the Next.js UI — into a single end-to-end flow: user clicks "Investigate", the backend runs all 11 inspectors, publishes live progress steps via InsForge Realtime, the AI produces a diagnosis, and the frontend renders the result with severity badges and kubectl commands.

---

## Key Requirements Covered

- **`POST /investigate`** route fully wired: accepts optional `session_id` and `context`, runs `run_full_investigation()`, calls `analyze()`, returns `diagnosis`, `healthy`, `cluster_reachable`
- Cluster reachability short-circuit: if `kubectl` cannot reach the API server, skip the LLM call entirely and return a descriptive `"critical"` diagnosis immediately
- **InsForge Realtime** integration for live step progress:
  - Frontend generates a `session_id` via `crypto.randomUUID()` on page load
  - Backend calls `publish_step(session_id, step_name, status)` at each stage, inserting to `investigation_progress` table
  - DB trigger fires `realtime.publish()` on insert
  - Frontend subscribes to `investigation:{session_id}` channel via `insforge.realtime`
- **InsForge Auth** integration:
  - `lib/insforge.ts` creates the InsForge client with `baseUrl` and `anonKey`
  - Login page (`/login`) with email/password using InsForge Auth SDK
  - Auth guard on the dashboard — unauthenticated users are redirected to `/login`
  - Root page (`/`) checks session and redirects to `/dashboard` or `/login`
- **Frontend components** built:
  - `InvestigationPanel.tsx` — triggers investigation, shows animated step progress (with simulated timer fallback at 2.4s/step), renders diagnosis on completion
  - `DiagnosisCard.tsx` — structured display of root cause, explanation, severity badge, fix steps, kubectl commands as copyable code blocks, confidence meter
  - `Header.tsx` — app header with user email and logout button
  - `HistoryTable.tsx` — table listing past investigations from InsForge
- **InsForge `investigations` table** — stores completed investigation records (user_id, session_id, diagnosis JSON, severity, confidence, created_at)
- Three result states on the frontend: **healthy** (green checkmark), **unreachable** (cluster connectivity error), **diagnosis** (full AI output card)

---

## Tech Stack and Components Introduced

| Component | Detail |
|-----------|--------|
| BaaS auth | InsForge Auth SDK (`insforge.auth.signIn`, `getCurrentUser`) |
| BaaS realtime | InsForge Realtime WebSocket (`insforge.realtime.subscribe`) |
| BaaS database | InsForge REST API — `investigations` table |
| Frontend routing | Next.js App Router (`/login`, `/dashboard`) |
| State management | React `useState` + `useEffect` — no external state library |
| Progress animation | CSS animation with simulated 2.4s/step timer; realtime events override when available |
| Diagnosis display | Tailwind-styled card components with severity colour coding |

---

## Expected Result

After running this prompt:

- The full end-to-end flow works: login → dashboard → investigate → live progress → diagnosis card
- Progress steps animate in real time as the backend publishes them to InsForge
- If InsForge Realtime is unavailable, the simulated timer keeps the UI responsive
- Completed investigations are persisted to the `investigations` table and visible in the history view
- Unauthenticated access to `/dashboard` redirects to `/login`
- The backend API key (`OPENROUTER_API_KEY`) is never exposed to the browser
