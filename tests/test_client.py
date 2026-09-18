import asyncio
import httpx
import pytest

from bb_mcp import client


def run(coro):
    return asyncio.run(coro)


class FakeClient:
    """Stands in for httpx.AsyncClient: scripted responses, records calls."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        return self.responses.pop(0)

    async def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        return self.responses.pop(0)


def resp(status, json=None, text=""):
    return httpx.Response(status, json=json, text=text if json is None else None,
                          request=httpx.Request("GET", "https://x"))


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    monkeypatch.setattr(client, "APP_KEY", "k")
    monkeypatch.setattr(client, "APP_SECRET", "s")
    monkeypatch.setattr(client, "ALLOW_WRITES", False)
    monkeypatch.setattr(client, "ALLOW_GRADE_WRITES", False)
    monkeypatch.setattr(client, "_token", None)
    monkeypatch.setattr(client, "_token_expires_at", 0.0)


def test_token_is_fetched_once_and_cached(monkeypatch):
    fake = FakeClient([
        resp(200, {"access_token": "t1", "expires_in": 3600}),
        resp(200, {"ok": 1}),
        resp(200, {"ok": 2}),
    ])
    monkeypatch.setattr(client.httpx, "AsyncClient", lambda **kw: fake)
    run(client._request("GET", "/a"))
    run(client._request("GET", "/b"))
    posts = [c for c in fake.calls if c[0] == "POST"]
    assert len(posts) == 1
    assert fake.calls[-1][2]["headers"]["Authorization"] == "Bearer t1"


def test_401_refreshes_token_and_retries_once(monkeypatch):
    fake = FakeClient([
        resp(200, {"access_token": "old", "expires_in": 3600}),
        resp(401, text="API request is not authenticated"),
        resp(200, {"access_token": "new", "expires_in": 3600}),
        resp(200, {"ok": True}),
    ])
    monkeypatch.setattr(client.httpx, "AsyncClient", lambda **kw: fake)
    out = run(client._request("GET", "/x"))
    assert out == {"ok": True}
    assert fake.calls[-1][2]["headers"]["Authorization"] == "Bearer new"


def test_second_401_raises(monkeypatch):
    fake = FakeClient([
        resp(200, {"access_token": "a", "expires_in": 3600}),
        resp(401, text="nope"),
        resp(200, {"access_token": "b", "expires_in": 3600}),
        resp(401, text="still nope"),
    ])
    monkeypatch.setattr(client.httpx, "AsyncClient", lambda **kw: fake)
    with pytest.raises(client.BlackboardError, match="401"):
        run(client._request("GET", "/x"))


def test_write_switch_checked_before_network(monkeypatch):
    fake = FakeClient([])
    monkeypatch.setattr(client.httpx, "AsyncClient", lambda **kw: fake)
    with pytest.raises(client.BlackboardError, match="BB_ALLOW_WRITES"):
        run(client._request("POST", "/x", json={}, write=True))
    assert fake.calls == []


def test_paged_follows_next_and_stops_at_limit(monkeypatch):
    pages = {
        "/v1/items": {"results": [1, 2], "paging": {"nextPage": "/learn/api/public/v1/items?offset=2"}},
        "/v1/items?offset=2": {"results": [3, 4], "paging": {"nextPage": "/learn/api/public/v1/items?offset=4"}},
        "/v1/items?offset=4": {"results": [5]},
    }
    seen = []

    async def fake_request(method, path, **kw):
        seen.append(path)
        return pages[path]

    monkeypatch.setattr(client, "_request", fake_request)
    assert run(client._paged("/v1/items")) == [1, 2, 3, 4, 5]
    assert run(client._paged("/v1/items", limit=3)) == [1, 2, 3]


def test_bbml_rejects_b_and_names_replacement():
    with pytest.raises(client.BlackboardError, match=r"<b> \(use <strong>\)"):
        client.check_bbml("<p><b>x</b></p>")
    client.check_bbml("<p><strong>x</strong> <a href='u'>y</a></p>")
