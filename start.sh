#!/usr/bin/env bash
set -euo pipefail

# ANSI colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
RESET='\033[0m'

info()    { echo -e "${BLUE}[info]${RESET}  $*"; }
success() { echo -e "${GREEN}[ok]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[warn]${RESET}  $*"; }
error()   { echo -e "${RED}[error]${RESET} $*"; }
die()     { error "$*"; exit 1; }

echo -e "\n${BOLD}AI Kubernetes Agent — Starting up${RESET}\n"

# ── 1. Check kind ────────────────────────────────────────────────────────────
if ! command -v kind &>/dev/null; then
  die "kind is not installed. Install it from https://kind.sigs.k8s.io/docs/user/quick-start/#installation and re-run this script."
fi

# ── 2. kind cluster ──────────────────────────────────────────────────────────
CLUSTER_NAME="kubernetes-cluster"

if kind get clusters 2>/dev/null | grep -qx "$CLUSTER_NAME"; then
  success "kind cluster already running"
else
  info "Creating kind cluster '$CLUSTER_NAME'…"
  kind create cluster --name="$CLUSTER_NAME"
  success "kind cluster created"
fi

# ── 3. Regenerate internal kubeconfig ────────────────────────────────────────
mkdir -p ~/.kube
kind get kubeconfig --name="$CLUSTER_NAME" --internal > ~/.kube/kind-config
success "kubeconfig updated"

# ── 4. Check Docker ──────────────────────────────────────────────────────────
if ! docker info &>/dev/null; then
  die "Docker is not running. Start Docker Desktop (or your Docker daemon) and re-run this script."
fi

# ── 5–7. Start backend and wait for health ───────────────────────────────────
HEALTH_URL=http://localhost:8000/health
TIMEOUT=30

# Returns 0 if docker-compose.yml was modified after the backend container was created.
_compose_changed() {
  local compose_mtime container_id created container_epoch
  compose_mtime=$(stat -f "%m" "docker-compose.yml" 2>/dev/null) || return 1
  container_id=$(docker compose ps -q backend 2>/dev/null | head -1)
  [ -z "$container_id" ] && return 1
  created=$(docker inspect --format='{{.Created}}' "$container_id" 2>/dev/null) || return 1
  # Parse ISO-8601 to epoch (macOS date)
  container_epoch=$(date -jf "%Y-%m-%dT%H:%M:%S" "${created:0:19}" "+%s" 2>/dev/null) || return 1
  [ "$compose_mtime" -gt "$container_epoch" ]
}

needs_start=true

if curl -sf "$HEALTH_URL" &>/dev/null; then
  if _compose_changed; then
    info "docker-compose.yml changed — recreating backend…"
    docker compose down -q 2>/dev/null || true
  else
    success "Backend is already running"
    needs_start=false
  fi
fi

if $needs_start; then
  info "Starting backend…"
  docker compose up backend -d

  elapsed=0
  info "Waiting for backend at $HEALTH_URL…"
  until curl -sf "$HEALTH_URL" &>/dev/null; do
    if (( elapsed >= TIMEOUT )); then
      die "Backend did not become healthy within ${TIMEOUT}s. Check logs with: docker compose logs backend"
    fi
    sleep 2
    (( elapsed += 2 ))
  done
  success "Backend is ready"
fi

# ── 8. Instructions ──────────────────────────────────────────────────────────
echo
echo -e "${BOLD}Open a new terminal and run:${RESET}"
echo -e "  ${YELLOW}cd frontend && npm run dev${RESET}"
echo -e "Then open ${BLUE}http://localhost:3000${RESET}"
echo
