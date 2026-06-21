# Prompt 07 — Professional UI Redesign (KubeOracle)

## What This Prompt Was Trying to Achieve

Transform the single-page prototype dashboard into a production-quality, multi-page DevOps tool. This prompt introduced a persistent navigation layout, a coherent design system, a product rename from "AI Kubernetes Agent" to **KubeOracle**, and five distinct pages — each with a focused purpose. All existing functionality (auth, investigation, realtime, NL query, history) was preserved; only the structure and styling changed.

---

## Key Requirements Covered

- **Product rename to KubeOracle** — updated everywhere: browser tab title, topbar, login page, README, and `package.json` (`name: "kubeoracle-frontend"`)
- **New application layout** — `dashboard/layout.tsx` replaces the old single-page structure:
  - Fixed `Topbar` (56px height) — app name, user email, logout
  - Fixed `Sidebar` (220px width) — navigation links to all five pages
  - Scrollable main content area for each page
  - Auth guard: `getCurrentUser()` check on load; unauthenticated users redirected to `/login`
  - `UserContext` exported from `layout.tsx` so sub-pages can access the current user via `useUser()` hook without prop drilling

- **Design system via CSS custom properties** in `globals.css`:

| Variable | Value | Usage |
|----------|-------|-------|
| `--bg-primary` | `#0f0f1a` | Page background |
| `--bg-surface` | `#1a1a2e` | Card backgrounds |
| `--bg-elevated` | `#242444` | Elevated surfaces |
| `--primary` | `#7c3aed` | Primary accent (purple) |
| `--primary-hover` | `#6d28d9` | Button hover |
| `--text-primary` | `#f1f5f9` | Body text |
| `--text-muted` | `#94a3b8` | Secondary text |
| `--border` | `#2d2d4e` | Card borders |

- **Typography** — `Inter` for UI text and `JetBrains Mono` for code/kubectl commands, both loaded via `next/font/google`
- **Flat card design** — glassmorphism removed; clean bordered cards with `--bg-surface` background and `--border` borders

- **Five pages** under `src/app/dashboard/`:

| Page | Route | Purpose |
|------|-------|---------|
| Overview | `/dashboard` | `ClusterHealthGrid` showing all kubeconfig contexts with health status; clicking a cluster navigates to Investigate with context pre-selected |
| Query | `/dashboard/query` | Natural language query box, answer card, save/history integration |
| Investigate | `/dashboard/investigate` | Full investigation trigger, realtime step progress, diagnosis card; reads `?context=` URL param set by Overview |
| History | `/dashboard/history` | Table of past investigations and saved queries; CSV export button |
| Settings | `/dashboard/settings` | Backend URL, model, and environment configuration display |

- **New components**:
  - `Sidebar.tsx` — nav links with active state highlighting using `usePathname()`
  - `Topbar.tsx` — app branding + user info + logout
  - `ClusterHealthGrid.tsx` — card grid of all kubeconfig contexts; polls `/clusters` and `/investigate` per-cluster to show healthy/degraded/unreachable status badges; click-to-investigate with URL param handoff

- **History CSV export** in `history/page.tsx` — button that serialises the history rows to CSV and triggers a browser download; no backend endpoint needed

- **`investigate/page.tsx`** — full investigation logic extracted from old `dashboard/page.tsx`; reads `?context=` from `useSearchParams()` to pre-populate the cluster selector when navigating from the Overview grid

---

## Tech Stack and Components Introduced

| Component | Detail |
|-----------|--------|
| Layout pattern | Next.js App Router nested layout (`dashboard/layout.tsx`) |
| Design tokens | CSS custom properties in `globals.css` |
| Typography | `next/font/google` — Inter + JetBrains Mono |
| Navigation state | `usePathname()` for active link highlighting |
| Context sharing | React `createContext` + `useContext` (`UserContext`) |
| CSV export | Client-side `Blob` + `URL.createObjectURL()` — no backend |
| URL params | `useSearchParams()` for cluster context handoff between pages |

---

## Expected Result

After running this prompt:

- The app feels like a real DevOps product — persistent sidebar, clear page hierarchy, consistent purple-on-dark colour scheme
- The Overview page gives a cluster health summary at a glance; clicking any cluster tile jumps straight to an investigation pre-scoped to that context
- Each page is independently navigable and bookmarkable
- All logic from previous prompts (auth, investigation, realtime, NL query, history) continues to work unchanged — the redesign is purely structural and visual
- History CSV export allows teams to share investigation results offline
- `JetBrains Mono` makes kubectl command blocks easy to read and copy
