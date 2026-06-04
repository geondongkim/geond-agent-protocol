#!/usr/bin/env bash
set -euo pipefail

: "${GEOND_WORKSPACE:?Set GEOND_WORKSPACE to a workspace URI such as file:///repo}"
: "${GEOND_AGENT_SESSION_ID:?Set GEOND_AGENT_SESSION_ID to the Claude session id}"

event_type="${1:-heartbeat}"
summary="${GEOND_HOOK_SUMMARY:-claude hook event}"

args=(
  hook record
  --workspace "$GEOND_WORKSPACE"
  --agent claude
  --event "$event_type"
  --session-external-id "$GEOND_AGENT_SESSION_ID"
  --summary "$summary"
)

if [[ -n "${GEOND_RUN_ID:-}" ]]; then
  args+=(--run "$GEOND_RUN_ID")
fi
if [[ -n "${GEOND_TASK_ID:-}" ]]; then
  args+=(--task "$GEOND_TASK_ID")
fi
if [[ -n "${GEOND_WORKER_SESSION_ID:-}" ]]; then
  args+=(--worker-session-id "$GEOND_WORKER_SESSION_ID")
fi
if [[ -n "${GEOND_LEASE_ID:-}" ]]; then
  args+=(--lease-id "$GEOND_LEASE_ID")
fi
if [[ -n "${GEOND_VALIDATION_COMMAND:-}" ]]; then
  args+=(--command "$GEOND_VALIDATION_COMMAND")
fi
if [[ -n "${GEOND_VALIDATION_EXIT_CODE:-}" ]]; then
  args+=(--exit-code "$GEOND_VALIDATION_EXIT_CODE")
fi

geond "${args[@]}"
