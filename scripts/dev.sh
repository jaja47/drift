#!/usr/bin/env bash
# Start Warrant from ./warrant/, wait for /health, run a Drift command, stop Warrant on exit.
#
#   scripts/dev.sh [--persist] [command ...]
#
# With no command, Warrant runs in the foreground until Ctrl-C (that is `make warrant`).
# The store is in-memory unless --persist is given, which uses state/ledger.jsonl.
# Warrant is stopped on every exit path: normal exit, a failing command, Ctrl-C or SIGTERM.
#
# Written for bash 3.2 (the macOS default).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"

log() { echo "dev.sh: $*" >&2; }

persist=0
if [[ "${1:-}" == "--persist" ]]; then
  persist=1
  shift
fi

# ── .env: create it from the example on first run, and give it a token if it has none ─────────
if [[ ! -f "$ENV_FILE" ]]; then
  cp "$ROOT/.env.example" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  log "created .env from .env.example"
fi
if ! grep -qE "^WARRANT_TOKEN=[^[:space:]\"']" "$ENV_FILE"; then
  token="$(openssl rand -hex 32)"
  tmp="$(mktemp "$ENV_FILE.XXXXXX")"
  awk -v t="$token" '
    /^WARRANT_TOKEN=/ { if (!done) print "WARRANT_TOKEN=" t; done = 1; next }
    { print }
    END { if (!done) print "WARRANT_TOKEN=" t }
  ' "$ENV_FILE" >"$tmp"
  chmod 600 "$tmp"
  mv "$tmp" "$ENV_FILE"
  log "generated a random WARRANT_TOKEN in .env"
fi

# Load .env. Variables already set in the environment win; empty values are left unset.
# Parsed by hand rather than sourced, because values such as a principal's name contain spaces.
key_re='^[A-Za-z_][A-Za-z0-9_]*$'
dq_re='^"(.*)"$'
sq_re="^'(.*)'$"
while IFS= read -r line || [[ -n "$line" ]]; do
  case "$line" in '' | \#*) continue ;; esac
  key="${line%%=*}"
  value="${line#*=}"
  [[ "$key" =~ $key_re ]] || continue
  if [[ "$value" =~ $dq_re ]] || [[ "$value" =~ $sq_re ]]; then
    value="${BASH_REMATCH[1]}"
  fi
  [[ -n "$value" ]] || continue
  [[ -n "${!key+x}" ]] || export "$key=$value"
done <"$ENV_FILE"

export WARRANT_URL="${WARRANT_URL:-http://localhost:8000}"
if [[ -z "${WARRANT_TOKEN:-}" ]]; then
  log "WARRANT_TOKEN is empty — refusing to start."
  exit 1
fi

hostport="${WARRANT_URL#*://}"
hostport="${hostport%%/*}"
port="${hostport##*:}"
[[ "$port" =~ ^[0-9]+$ ]] || port=8000

# Something already answering would be a different Warrant with a different token and ledger.
if curl -s -o /dev/null --max-time 1 "$WARRANT_URL/health"; then
  log "something is already listening at $WARRANT_URL — stop it first (another Warrant?)."
  exit 1
fi

# ── start Warrant and make sure it is stopped however we exit ─────────────────────────────────
warrant_pid=""
cleanup() {
  local rc=$?
  trap - EXIT INT TERM
  if [[ -n "$warrant_pid" ]] && kill -0 "$warrant_pid" 2>/dev/null; then
    kill -TERM "$warrant_pid" 2>/dev/null || true
    wait "$warrant_pid" 2>/dev/null || true
    log "Warrant stopped."
  fi
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if ((persist)); then
  mkdir -p "$ROOT/state"
  store_desc="ledger $ROOT/state/ledger.jsonl"
else
  store_desc="in-memory store"
fi

# The deno flags mirror the `dev` task in warrant/deno.json. Running deno directly (not through
# `deno task`) means $warrant_pid is the server itself, so the TERM in cleanup() reaches it.
(
  cd "$ROOT/warrant"
  export PORT="$port"
  if ((persist)); then
    unset WARRANT_STORE
    export WARRANT_LEDGER="$ROOT/state/ledger.jsonl"
  else
    export WARRANT_STORE=memory
  fi
  exec deno run --allow-net --allow-read --allow-write --allow-env src/server.ts
) &
warrant_pid=$!

up=0
for _ in $(seq 1 50); do # 50 × 0.2s = 10s
  if ! kill -0 "$warrant_pid" 2>/dev/null; then
    log "Warrant exited during startup."
    exit 1
  fi
  if curl -sf -o /dev/null --max-time 1 "$WARRANT_URL/health"; then
    up=1
    break
  fi
  sleep 0.2
done
if ((!up)); then
  log "Warrant did not answer $WARRANT_URL/health within 10s."
  exit 1
fi
log "Warrant up at $WARRANT_URL ($store_desc)."

# ── run the command, or hold Warrant in the foreground ────────────────────────────────────────
if (($# == 0)); then
  log "running Warrant alone. Ctrl-C to stop."
  wait "$warrant_pid"
else
  "$@"
fi
