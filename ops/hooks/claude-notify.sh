#!/bin/bash
set -uo pipefail

INPUT=$(cat)
STOP_ACTIVE=$(echo "$INPUT" | jq -r '.stop_hook_active // false')
if [ "$STOP_ACTIVE" = "true" ]; then exit 0; fi

TRANSCRIPT=$(echo "$INPUT" | jq -r '.transcript_path // empty')
if [ -z "$TRANSCRIPT" ] || [ ! -f "$TRANSCRIPT" ]; then exit 0; fi

NOW=$(date +%s)
FIRST_TS=$(jq -r 'first(.[] | select(.timestamp != null and .timestamp != "") | .timestamp) // empty' "$TRANSCRIPT" 2>/dev/null)
if [ -z "$FIRST_TS" ]; then exit 0; fi

START_EPOCH=$(date -j -f "%Y-%m-%dT%H:%M:%S" "${FIRST_TS%%.*}" +%s 2>/dev/null \
  || date -d "$FIRST_TS" +%s 2>/dev/null \
  || echo "$NOW")
ELAPSED=$((NOW - START_EPOCH))
if [ "$ELAPSED" -lt 30 ]; then exit 0; fi

CWD=$(echo "$INPUT" | jq -r '.cwd // "."')
REPO=$(basename "$CWD")
BRANCH=$(git -C "$CWD" branch --show-current 2>/dev/null || echo "")
REPO=${REPO:0:100}

terminal-notifier \
  -title "Claude Code" \
  -subtitle "${REPO}${BRANCH:+ ($BRANCH)}" \
  -message "Done (${ELAPSED}s)" \
  -sound Hero \
  -group "claude-stop" >/dev/null 2>&1 || true

HUB_PAYLOAD=$(jq -n \
  --arg source "cc" \
  --arg level "normal" \
  --arg title "Session Complete" \
  --arg repo "$REPO" \
  --arg branch "$BRANCH" \
  --arg elapsed "$ELAPSED" \
  '{
    source: $source,
    level: $level,
    title: $title,
    body: (($repo + (if $branch == "" then "" else " (" + $branch + ")" end) + ": Done (" + $elapsed + "s)")[:2000]),
    project: ($repo[:100])
  }')

curl -s --max-time 2 -X POST http://127.0.0.1:9199/events \
  -H "Content-Type: application/json" \
  -d "$HUB_PAYLOAD" \
  >/dev/null 2>&1 &

exit 0
