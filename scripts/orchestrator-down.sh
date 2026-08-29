#!/usr/bin/env bash
set -euo pipefail

SESSION="darkagent-ralph-esteira-videos"
if ! tmux has-session -t "$SESSION" 2>/dev/null; then
  printf "tmux session '%s' does not exist.\n" "$SESSION"
  exit 0
fi
tmux send-keys -t "$SESSION:ralph" C-c 2>/dev/null || true
tmux kill-session -t "$SESSION" 2>/dev/null || true
printf '%s\n' 'Ralph loop stopped.'

