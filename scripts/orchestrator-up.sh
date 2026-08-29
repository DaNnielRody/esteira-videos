#!/usr/bin/env bash
set -euo pipefail

SESSION="darkagent-ralph-esteira-videos"
WORKDIR="${1:-$(pwd)}"

if ! command -v tmux >/dev/null 2>&1; then
  printf '%s\n' 'tmux not found. Install it before starting Ralph.' >&2
  exit 1
fi
if tmux has-session -t "$SESSION" 2>/dev/null; then
  printf "session '%s' already exists: tmux attach -t %s\n" "$SESSION" "$SESSION"
  exit 0
fi

mkdir -p "$WORKDIR/.claude/tmp/orchestrator"
loop_command=$(printf 'ORCH_RUN_BASE=%q python3 %q --port 8765' \
  "$WORKDIR/.claude/tmp/orchestrator" "$WORKDIR/scripts/orchestrator.py")
tmux new-session -d -s "$SESSION" -n ralph -c "$WORKDIR"
tmux send-keys -t "$SESSION:ralph" "$loop_command" C-m
printf '%s\n' 'Ralph loop listening on http://127.0.0.1:8765'

