"""HTTP side of the Blackboard server: token, requests, paging, BBML.

Auth is OAuth2 client credentials only. The application is registered on the
site and bound to the instructor's user, so the token carries that user's
permissions and lives an hour. Tokens are cached and fetched again once when
a request answers 401, which is what happens when one expires mid-call.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

import httpx


def _load_dotenv() -> None:
    """Read .env at the project root. Existing environment variables win."""
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


_load_dotenv()

BASE_URL = os.environ.get("BB_BASE_URL", "https://blackboard.unicatt.it").rstrip("/")
APP_KEY = os.environ.get("BB_APP_KEY", "")
APP_SECRET = os.environ.get("BB_APP_SECRET", "")
ALLOW_WRITES = os.environ.get("BB_ALLOW_WRITES", "0") == "1"
ALLOW_GRADE_WRITES = os.environ.get("BB_ALLOW_GRADE_WRITES", "0") == "1"
API = f"{BASE_URL}/learn/api/public"


class BlackboardError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------

_token: str | None = None
_token_expires_at: float = 0.0


async def _access_token(client: httpx.AsyncClient, *, force: bool = False) -> str:
    global _token, _token_expires_at

    if not force and _token and time.time() < _token_expires_at:
        return _token

    if not APP_KEY or not APP_SECRET:
        raise BlackboardError(
            "Set BB_APP_KEY / BB_APP_SECRET in .env: the key and secret of the "
            "application registered at developer.anthology.com and added by the "
            "Blackboard administrator under Admin > REST API Integrations."
        )

    resp = await client.post(
        f"{API}/v1/oauth2/token",
        auth=(APP_KEY, APP_SECRET),
        data={"grant_type": "client_credentials"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if resp.status_code != 200:
        raise BlackboardError(f"Token request failed ({resp.status_code}): {resp.text[:400]}")

    payload = resp.json()
    _token = payload["access_token"]
    _token_expires_at = time.time() + payload.get("expires_in", 3600) - 60
    return _token


async def _request(
    method: str,
    path: str,
    *,
    json: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    content: bytes | None = None,
    content_type: str | None = None,
    write: bool = False,
    grade_write: bool = False,
) -> Any:
    """One call to the public API. Raises BlackboardError on any 4xx/5xx."""
    if grade_write and not ALLOW_GRADE_WRITES:
        raise BlackboardError("Grade writes are disabled. Set BB_ALLOW_GRADE_WRITES=1 to allow them.")
    if write and not ALLOW_WRITES:
        raise BlackboardError("Writes are disabled. Set BB_ALLOW_WRITES=1 to allow them.")

    async with httpx.AsyncClient(timeout=60) as client:
        token = await _access_token(client)
        resp = await _send(client, token, method, path, json, params, content, content_type)
        if resp.status_code == 401:
            token = await _access_token(client, force=True)
            resp = await _send(client, token, method, path, json, params, content, content_type)

    if resp.status_code == 404:
        raise BlackboardError(f"Not found: {method} {path}")
    if resp.status_code >= 400:
        raise BlackboardError(f"{resp.status_code} on {method} {path}: {resp.text[:500]}")
    if not resp.content:
        return {"ok": True}
    return resp.json()


async def _send(client, token, method, path, json, params, content, content_type) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}"}
    if content_type:
        headers["Content-Type"] = content_type
    return await client.request(
        method, f"{API}{path}", headers=headers, json=json, params=params, content=content,
    )


async def _paged(path: str, params: dict[str, Any] | None = None, limit: int = 200) -> list[dict]:
    """Follow Blackboard's paging links until exhausted or `limit` rows collected."""
    out: list[dict] = []
    query = dict(params or {})
    query.setdefault("limit", 100)
    next_path: str | None = path

    while next_path and len(out) < limit:
        payload = await _request("GET", next_path, params=query)
        out.extend(payload.get("results", []))
        next_url = payload.get("paging", {}).get("nextPage")
        next_path = next_url.replace("/learn/api/public", "") if next_url else None
        query = {}

    return out[:limit]


# --------------------------------------------------------------------------
# Blackboard Markup Language
# --------------------------------------------------------------------------

# The subset of HTML a content or announcement body may contain. Anything else
# is a 400 that quotes the whole body and names no tag, so the check is here.
BBML_TAGS = {"a", "br", "del", "div", "em", "h4", "h5", "h6", "li", "ol", "p",
             "span", "strong", "sub", "sup", "ul"}
_BBML_HINT = {"b": "strong", "i": "em", "h1": "h4", "h2": "h4", "h3": "h4"}


def check_bbml(html: str) -> None:
    used = set(re.findall(r"</?([a-zA-Z][a-zA-Z0-9]*)", html))
    bad = sorted(t for t in used if t.lower() not in BBML_TAGS)
    if bad:
        hints = ", ".join(f"<{t}> (use <{_BBML_HINT[t]}>)" if t in _BBML_HINT else f"<{t}>" for t in bad)
        raise BlackboardError(
            f"Body uses tags outside Blackboard Markup Language: {hints}. "
            f"Allowed: {', '.join(sorted(BBML_TAGS))}."
        )
