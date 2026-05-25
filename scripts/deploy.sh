#!/usr/bin/env bash
#
# Olympus deploy script.
#
# Idempotent installer for the Olympus chart, with an optional
# `--with-netdb` flag that brings up the sibling netdb compose stack
# in parallel. Designed to be run from the repo root.
#
# Usage:
#   scripts/deploy.sh                       # Olympus only
#   scripts/deploy.sh --with-netdb          # Olympus + netdb (../netdb)
#   scripts/deploy.sh --with-netdb --netdb-dir /path/to/netdb
#   scripts/deploy.sh --down                # Uninstall Olympus
#   scripts/deploy.sh --with-netdb --down   # Uninstall Olympus + netdb
#
# Tunables (env or flags — flags win):
#   RELEASE       (helm release name, default: olympus)
#   NAMESPACE     (kube namespace, default: default)
#   IMAGE_TAG     (Olympus image tag, default: llm)
#   NETDB_DIR     (path to netdb repo, default: ../netdb relative to repo root)
#   COMPOSE_FILES (compose files, default: docker-compose.yml + compose.prod.yaml)

set -euo pipefail

# ---------- defaults ----------
RELEASE="${RELEASE:-olympus}"
NAMESPACE="${NAMESPACE:-default}"
IMAGE_TAG="${IMAGE_TAG:-llm}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NETDB_DIR="${NETDB_DIR:-$REPO_ROOT/../netdb}"
COMPOSE_FILES="${COMPOSE_FILES:--f docker-compose.yml -f compose.prod.yaml}"
WITH_NETDB=0
DOWN=0

# ---------- arg parsing ----------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-netdb)    WITH_NETDB=1; shift ;;
    --netdb-dir)     NETDB_DIR="$2"; shift 2 ;;
    --down)          DOWN=1; shift ;;
    --release)       RELEASE="$2"; shift 2 ;;
    --namespace|-n)  NAMESPACE="$2"; shift 2 ;;
    --image-tag)     IMAGE_TAG="$2"; shift 2 ;;
    -h|--help)
      sed -n '3,18p' "$0"
      exit 0
      ;;
    *)
      echo "deploy.sh: unknown flag: $1" >&2
      echo "Try: $0 --help" >&2
      exit 2
      ;;
  esac
done

# ---------- pretty output ----------
bold()   { printf '\033[1m%s\033[0m\n' "$*"; }
green()  { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
red()    { printf '\033[31m%s\033[0m\n' "$*"; }
step()   { printf '\n\033[1;34m▸ %s\033[0m\n' "$*"; }

# ---------- pre-flight ----------
require() {
  if ! command -v "$1" >/dev/null 2>&1; then
    red "missing dependency: $1"
    exit 1
  fi
}

step "Pre-flight"
require helm
require kubectl
if [[ "$WITH_NETDB" == 1 ]]; then
  require docker
  # docker compose v2 ships as `docker compose` (not docker-compose). Probe.
  if ! docker compose version >/dev/null 2>&1; then
    red "'docker compose' v2 not available — install Docker Engine ≥ 20.10"
    exit 1
  fi
  if [[ ! -d "$NETDB_DIR" ]]; then
    red "netdb dir not found: $NETDB_DIR"
    red "pass --netdb-dir or set NETDB_DIR"
    exit 1
  fi
  if [[ ! -f "$NETDB_DIR/.env" ]]; then
    yellow "warning: $NETDB_DIR/.env missing — compose may fail on required vars"
    yellow "         (DNS_SERVER_ADMIN_PASSWORD is :?-required by docker-compose.yml)"
  fi
fi

# ---------- bring down ----------
if [[ "$DOWN" == 1 ]]; then
  step "Uninstalling Olympus release '$RELEASE' from namespace '$NAMESPACE'"
  helm uninstall "$RELEASE" -n "$NAMESPACE" || yellow "helm uninstall non-zero exit (may be already gone)"
  if [[ "$WITH_NETDB" == 1 ]]; then
    step "Stopping netdb compose at $NETDB_DIR"
    (cd "$NETDB_DIR" && docker compose $COMPOSE_FILES down) || yellow "compose down non-zero exit"
  fi
  green "done."
  exit 0
fi

# ---------- bring up ----------
step "Installing/upgrading Olympus chart"
echo "  release:   $RELEASE"
echo "  namespace: $NAMESPACE"
echo "  image:     olympus/dashboard:$IMAGE_TAG"

helm upgrade --install "$RELEASE" "$REPO_ROOT/infra/k8s/charts/olympus" \
  --namespace "$NAMESPACE" \
  --create-namespace \
  --set image.repository=olympus/dashboard \
  --set image.tag="$IMAGE_TAG"

step "Waiting for the dashboard pod to be Ready"
kubectl rollout status deploy/"$RELEASE-olympus" -n "$NAMESPACE" --timeout=180s

if [[ "$WITH_NETDB" == 1 ]]; then
  step "Bringing up netdb compose at $NETDB_DIR"
  (cd "$NETDB_DIR" && docker compose $COMPOSE_FILES up -d)

  step "Waiting for netdb /healthz"
  # netdb publishes 8080 to the host by default. Try the localhost
  # first; fall back to the docker-internal address if the host port
  # isn't reachable from this script's vantage point.
  netdb_ok=0
  for i in {1..30}; do
    if curl -sf http://127.0.0.1:8080/healthz >/dev/null 2>&1; then
      netdb_ok=1; break
    fi
    sleep 2
  done
  if [[ "$netdb_ok" == 1 ]]; then
    green "  netdb is up at http://127.0.0.1:8080"
  else
    yellow "  netdb didn't answer /healthz within 60s — check 'docker compose logs netdb' in $NETDB_DIR"
  fi
fi

# ---------- summary ----------
step "Summary"
kubectl get pod -l app.kubernetes.io/instance="$RELEASE" -n "$NAMESPACE" -o wide || true
if [[ "$WITH_NETDB" == 1 ]]; then
  (cd "$NETDB_DIR" && docker compose ps) || true
fi
green "deploy complete."

if [[ "$WITH_NETDB" == 1 ]]; then
  cat <<EOF

Next: wire the netdb MCP endpoint into Olympus
  1. open the Olympus dashboard
  2. go to the MCP tab
  3. paste the netdb host (e.g. 127.0.0.1 or 10.0.3.30) into the NetDB card
  4. click connect
EOF
fi
