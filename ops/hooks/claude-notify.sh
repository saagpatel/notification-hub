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
SESSION_LABEL=$(basename "$CWD")
BRANCH=$(git -C "$CWD" branch --show-current 2>/dev/null || echo "")
REMOTE_URL=$(git -C "$CWD" config --get remote.origin.url 2>/dev/null || echo "")
if [ "$CWD" = "$HOME" ]; then
  REPO="home-adhoc"
elif [ -n "$REMOTE_URL" ]; then
  REMOTE_CLEAN=${REMOTE_URL%.git}
  REPO=$(printf '%s' "$REMOTE_CLEAN" | sed -E 's#^.*github.com[:/]([^/[:space:]]+)/([^/[:space:]]+)$#\1/\2#')
  if [ "$REPO" = "$REMOTE_CLEAN" ] || ! printf '%s' "$REPO" | grep -Eq '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$'; then
    REPO="unresolved"
  fi
else
  REPO="unresolved"
fi
REPO=${REPO:0:100}
SESSION_LABEL=${SESSION_LABEL:0:200}
EVENT_ID="cc:$(printf '%s' "$TRANSCRIPT|$FIRST_TS|Session Complete" | shasum -a 256 | cut -d' ' -f1 | cut -c1-32)"

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
  --arg session_label "$SESSION_LABEL" \
  --arg branch "$BRANCH" \
  --arg elapsed "$ELAPSED" \
  --arg event_id "$EVENT_ID" \
  --arg source_revision "$FIRST_TS" \
  '{
    event_id: $event_id,
    event_type: "claude.session.completed",
    source_revision: $source_revision,
    source: $source,
    level: $level,
    title: $title,
    body: (($repo + (if $branch == "" then "" else " (" + $branch + ")" end) + ": Done")[:2000]),
    project: ($repo[:100]),
    session_label: ($session_label[:200])
  }')

printf '%s' "$HUB_PAYLOAD" | python3 "$(dirname "$0")/notification-hub-producer.py" \
  >/dev/null 2>&1 || true

exit 0
