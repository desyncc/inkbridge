"""The dashboard only answers to loopback names and refuses writes from other websites."""

import pytest
from fastapi.testclient import TestClient

from viwoods import server

ORIGIN = "http://127.0.0.1:8765"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv(server.ALLOWED_HOSTS_ENV, raising=False)
    return TestClient(server.app, base_url=ORIGIN)


def login(client, **headers):
    # An empty login is refused by the endpoint itself (400, "Must provide
    # ..."), so getting that far shows the request passed the access checks.
    return client.post("/api/login", json={}, headers=headers)


@pytest.mark.parametrize("host", ["127.0.0.1:8765", "localhost:8765", "localhost"])
def test_loopback_names_are_answered(client, host):
    assert client.get("/api/sync/status", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize("host", ["attacker.example:8765", "10.0.1.9:8765", "127.0.0.1.attacker.example"])
def test_other_names_are_refused(client, host):
    # A DNS-rebinding page reaches 127.0.0.1 but still sends its own domain.
    response = client.get("/api/sync/status", headers={"Host": host})
    assert response.status_code == 400
    assert server.ALLOWED_HOSTS_ENV in response.json()["detail"]


def test_extra_names_can_be_allowed(client, monkeypatch):
    monkeypatch.setenv(server.ALLOWED_HOSTS_ENV, "nas.local, 10.0.1.9")
    for host in ("nas.local:8765", "10.0.1.9:8765", "127.0.0.1:8765"):
        assert client.get("/api/sync/status", headers={"Host": host}).status_code == 200
    assert client.get("/api/sync/status", headers={"Host": "other.example"}).status_code == 400


def test_star_allows_any_name(client, monkeypatch):
    monkeypatch.setenv(server.ALLOWED_HOSTS_ENV, "*")
    assert client.get("/api/sync/status", headers={"Host": "anything.example"}).status_code == 200


def test_the_dashboards_own_writes_are_allowed(client):
    response = login(client, Origin=ORIGIN)
    assert response.status_code == 400
    assert "Must provide" in response.json()["detail"]


def test_writes_without_an_origin_are_allowed(client):
    # curl, scripts: not a browser acting for some web page.
    assert "Must provide" in login(client).json()["detail"]


@pytest.mark.parametrize("origin", [
    "https://attacker.example",
    "http://localhost:8765",   # same machine, but not the origin this request was sent to
    "http://127.0.0.1:9999",
    "null",                    # sandboxed iframes and some redirects
])
def test_writes_from_other_origins_are_refused(client, origin):
    response = login(client, Origin=origin)
    assert response.status_code == 403
    assert "another website" in response.json()["detail"]


def test_a_cross_site_transcribe_request_is_refused(client):
    # Takes its input from the URL, so a plain HTML form could send it.
    response = client.post("/api/transcribe/some-note/1", headers={"Origin": "https://attacker.example"})
    assert response.status_code == 403
    assert "some-note:1" not in server.transcribe_jobs


def test_reads_from_other_origins_are_left_to_the_browser(client):
    # Without CORS headers a browser won't hand the response to that page.
    response = client.get("/api/sync/status", headers={"Origin": "https://attacker.example"})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
