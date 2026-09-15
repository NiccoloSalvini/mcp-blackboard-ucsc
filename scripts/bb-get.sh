#!/usr/bin/env bash
# Read-only probe against the Blackboard public REST API, for when the Ultra UI
# will not render and the MCP server has no token of its own yet.
#
# Deliberately narrow, so that allowing it in settings.json grants as little as
# possible: the host is fixed, the method is fixed to GET, and the argument is a
# path under /learn/api/public/v1 — not a URL. It cannot be pointed elsewhere.
#
#   export BB_UI_TOKEN=<token from the Ultra session>
#   scripts/bb-get.sh courses/_170037_1/contents
#
# Grab the token in Chrome, logged into Blackboard: DevTools -> Network, filter
# "tokeninfo", reload a course page, read access_token off the request URL. It
# belongs to Blackboard's own UI app and lasts about an hour, so this is a
# stopgap for looking now — not a replacement for registering the integration.

set -euo pipefail

HOST="https://blackboard.unicatt.it"
BASE="/learn/api/public/v1"

if [[ $# -lt 1 ]]; then
  echo "usage: ${0##*/} <api-path>   e.g. courses/_170037_1/contents" >&2
  exit 2
fi

if [[ -z "${BB_UI_TOKEN:-}" ]]; then
  echo "BB_UI_TOKEN is not set. See the header of this script." >&2
  exit 2
fi

path="${1#/}"

# Refuse anything that tries to climb out of the API base or name another host.
case "$path" in
  *://*|*..*)
    echo "refusing suspicious path: $path" >&2
    exit 2
    ;;
esac

response=$(curl -sS -G \
  --max-time 30 \
  --request GET \
  --header "Authorization: Bearer ${BB_UI_TOKEN}" \
  --header "Accept: application/json" \
  --write-out $'\n%{http_code}' \
  "${HOST}${BASE}/${path}")

status="${response##*$'\n'}"
body="${response%$'\n'*}"

echo "HTTP ${status}"
if command -v python3 >/dev/null 2>&1; then
  printf '%s' "$body" | python3 -m json.tool 2>/dev/null || printf '%s\n' "$body"
else
  printf '%s\n' "$body"
fi
