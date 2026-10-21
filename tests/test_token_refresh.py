"""
A rejected token is renewed and the request retried once; a token close to
its expiry is renewed before it is rejected. The new token is written back to
config.json without touching any other setting.
"""

import base64
import json
import time

import httpx
import pytest

import viwoods.config as config_module
from viwoods.client import AuthError, ViwoodsClient
from viwoods.config import Config, load_config

DAY = 86400


def jwt(expires_in_days):
    def part(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
    return f"{part({'alg': 'HS256'})}.{part({'exp': int(time.time() + expires_in_days * DAY)})}.sig"


class Cloud:
    """A fake Viwoods API that only accepts `valid_token` and can renew one token."""

    def __init__(self, valid_token="new", renews="old", renewed_to="new", refresh_works=True):
        self.valid_token = valid_token
        self.renews = renews
        self.renewed_to = renewed_to
        self.refresh_works = refresh_works
        self.requests = []   # (uri, token used)

    def __call__(self, request):
        body = json.loads(request.content)
        uri = body["uri"]
        token = request.headers.get("Access-Token")
        self.requests.append((uri, token))
        if uri.endswith("refreshLogin"):
            if self.refresh_works and token == self.renews:
                return httpx.Response(200, json={"code": 200, "data": {"token": self.renewed_to}})
            return httpx.Response(200, json={"code": 401, "msg": "token expired"})
        if token != self.valid_token:
            return httpx.Response(200, json={"code": 401, "msg": "token expired"})
        return httpx.Response(200, json={"code": 200, "data": {"list": [{"modelName": "AiPaper"}]}})

    def refreshes(self):
        return [r for r in self.requests if r[0].endswith("refreshLogin")]


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(config_module, "CONFIG_PATH", path)
    cfg = Config(token="old", vault_path="/my/vault", ollama_model="mine")
    config_module.save_config(cfg)
    return path


def client_for(cloud, token="old"):
    client = ViwoodsClient(Config(token=token))
    client.http = httpx.Client(transport=httpx.MockTransport(cloud))
    return client


def test_a_rejected_token_is_renewed_and_the_request_retried(config_file):
    cloud = Cloud()
    client = client_for(cloud)

    assert client.get_devices() == [{"modelName": "AiPaper"}]

    assert client.config.token == "new"
    assert [uri.split("/")[-1] for uri, _ in cloud.requests] == ["userDevice", "refreshLogin", "userDevice"]
    assert cloud.requests[-1][1] == "new"


def test_the_renewed_token_is_saved_without_touching_other_settings(config_file):
    client_for(Cloud()).get_devices()

    saved = load_config()
    assert saved.token == "new"
    assert saved.vault_path == "/my/vault"
    assert saved.ollama_model == "mine"


def test_a_token_that_cannot_be_renewed_still_raises_auth_error(config_file):
    cloud = Cloud(refresh_works=False)
    client = client_for(cloud)

    with pytest.raises(AuthError):
        client.get_devices()

    assert client.config.token == "old"
    assert load_config().token == "old"


def test_a_failed_renewal_is_not_retried_for_every_request(config_file):
    cloud = Cloud(refresh_works=False)
    client = client_for(cloud)

    for _ in range(5):
        with pytest.raises(AuthError):
            client.get_devices()

    assert len(cloud.refreshes()) == 1


def test_a_rejected_request_signature_is_not_mistaken_for_an_expired_token(config_file):
    cloud = Cloud()

    def handler(request):
        cloud(request)
        return httpx.Response(200, json={"code": 401, "msg": "Incorrect signature"})

    client = ViwoodsClient(Config(token="old"))
    client.http = httpx.Client(transport=httpx.MockTransport(handler))

    with pytest.raises(RuntimeError, match="Incorrect signature") as excinfo:
        client.get_devices()

    assert not isinstance(excinfo.value, AuthError)
    assert cloud.refreshes() == []


def test_signing_in_never_triggers_a_renewal(config_file):
    cloud = Cloud(valid_token="never")
    client = client_for(cloud, token="old")

    with pytest.raises(AuthError):
        client.login("me@example.com", "pw")

    assert cloud.refreshes() == []


def test_no_renewal_is_attempted_without_a_token(config_file):
    cloud = Cloud()
    client = client_for(cloud, token="")

    with pytest.raises(AuthError):
        client.get_devices()

    assert cloud.requests == []


def test_a_token_close_to_expiry_is_renewed_before_it_is_rejected(config_file):
    old = jwt(expires_in_days=10)
    cloud = Cloud(valid_token="new", renews=old)
    client = client_for(cloud, token=old)

    assert client.refresh_if_expiring() is True

    assert client.config.token == "new"
    assert load_config().token == "new"


def test_a_token_with_plenty_of_time_left_is_left_alone(config_file):
    cloud = Cloud()
    client = client_for(cloud, token=jwt(expires_in_days=200))

    assert client.refresh_if_expiring() is False
    assert cloud.requests == []


@pytest.mark.parametrize("token", ["", "test-token", "a.b.c", "x.eyJub3QiOiJqc29uIn0.y"])
def test_a_token_that_is_not_a_readable_jwt_is_left_alone(config_file, token):
    cloud = Cloud()
    client = client_for(cloud, token=token)

    assert client.refresh_if_expiring() is False
    assert cloud.requests == []


def test_a_failed_proactive_renewal_does_not_raise(config_file):
    old = jwt(expires_in_days=5)
    cloud = Cloud(renews="someone-else")
    client = client_for(cloud, token=old)

    assert client.refresh_if_expiring() is False
    assert client.config.token == old
