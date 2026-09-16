#!/usr/bin/env bash
# Write-side sibling of bb-get.sh, for the two things a course needs done from
# the shell before the MCP server is wired in: flip a content item's
# availability, post an announcement. Same shape on purpose — fixed host, a
# path under /learn/api/public, never a URL — so allowing it grants only this.
#
#   export BB_UI_TOKEN=<token from the Ultra session>
#   scripts/bb-send.sh PATCH courses/_170037_1/contents/_6975481_1 '{"availability":{"available":"Yes"}}'
#   scripts/bb-send.sh POST  courses/_170037_1/announcements '{"title":"...","body":"..."}'
#
# It refuses DELETE, and it refuses to touch the gradebook: grades go through
# the MCP server, behind BB_ALLOW_GRADE_WRITES, not through a one-liner.

set -euo pipefail

HOST="https://blackboard.unicatt.it"
BASE="/learn/api/public"

if [[ $# -lt 3 ]]; then
  echo "usage: ${0##*/} PATCH|POST <api-path> '<json body>'" >&2
  exit 2
fi

method="$1"; path="${2#/}"; body="$3"

case "$method" in
  PATCH|POST) ;;
  *) echo "only PATCH and POST are allowed here, not $method" >&2; exit 2 ;;
esac

if [[ -z "${BB_UI_TOKEN:-}" ]]; then
  echo "BB_UI_TOKEN is not set. See the header of this script." >&2
  exit 2
fi

case "$path" in
  *://*|*..*)      echo "refusing suspicious path: $path" >&2; exit 2 ;;
  *gradebook*)     echo "refusing: gradebook writes go through the MCP server" >&2; exit 2 ;;
esac

case "$path" in
  v1/*|v2/*|v3/*) ;;
  *) path="v1/${path}" ;;
esac

# Fail on malformed JSON here, not with a 400 from the other side.
printf '%s' "$body" | python3 -c 'import json,sys; json.load(sys.stdin)' \
  || { echo "body is not valid JSON" >&2; exit 2; }

response=$(curl -sS \
  --max-time 30 \
  --request "$method" \
  --header "Authorization: Bearer ${BB_UI_TOKEN}" \
  --header "Content-Type: application/json" \
  --header "Accept: application/json" \
  --data "$body" \
  --write-out $'\n%{http_code}' \
  "${HOST}${BASE}/${path}")

status="${response##*$'\n'}"
out="${response%$'\n'*}"

echo "HTTP ${status}"
printf '%s' "$out" | python3 -m json.tool 2>/dev/null || printf '%s\n' "$out"
