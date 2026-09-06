"""Request signing, pagination and auth errors."""

import hashlib
import json

import httpx
import pytest

from viwoods.client import AuthError, ViwoodsClient, sign_data
from viwoods.config import Config

SECRET = "O9EfpIx4g9o8TKuCv2n5msBHucSrAf"


def md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def make_client(handler, token="test-token") -> ViwoodsClient:
    client = ViwoodsClient(Config(token=token))
    client.http = httpx.Client(transport=httpx.MockTransport(handler))
    return client


# --- sign_data ------------------------------------------------------------

def test_sign_data_matches_known_good_signature():
    payload = {
        "pageIndex": 1,
        "pageSize": 100,
        "appType": 1,
        "resourceId": "",
        "uri": "/api/v1/resourceSync/getPage",
    }

    expected = md5(
        "appType=1&pageIndex=1&pageSize=100&resourceId=&uri=/api/v1/resourceSync/getPage"
        + SECRET
    )
    assert sign_data(payload, SECRET) == expected


def test_sign_data_sorts_keys_alphabetically():
    assert sign_data({"b": "2", "a": "1"}, SECRET) == md5("a=1&b=2" + SECRET)


def test_sign_data_renders_booleans_and_null_as_json():
    payload = {"star": True, "off": False, "empty": None}

    assert sign_data(payload, SECRET) == md5("empty=null&off=false&star=true" + SECRET)


def test_sign_data_renders_nested_values_as_compact_json():
    payload = {"opts": {"a": [1, "x"]}}

    assert sign_data(payload, SECRET) == md5('opts={"a":[1,"x"]}' + SECRET)


def test_sign_data_passes_strings_through_verbatim():
    assert sign_data({"name": "a b&c"}, SECRET) == md5("name=a b&c" + SECRET)


def test_post_signs_exactly_what_it_sends():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["sign"] = request.headers["Sign"]
        return httpx.Response(200, json={"code": 200, "data": {}})

    make_client(handler).post("api/v1/userDevice", {"pageSize": "100"})

    assert seen["body"]["uri"] == "/api/v1/userDevice"
    assert seen["sign"] == sign_data(seen["body"], SECRET)


# --- pagination -----------------------------------------------------------

def paging_handler(pages, calls, total=None):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body["pageIndex"])
        data = {"list": pages.get(body["pageIndex"], [])}
        if total is not None:
            data["total"] = total
        return httpx.Response(200, json={"code": 200, "data": data})

    return handler


def test_get_folder_items_walks_every_page():
    pages = {
        1: [{"uuid": f"u{i}"} for i in range(100)],
        2: [{"uuid": f"u{i}"} for i in range(100, 200)],
        3: [{"uuid": f"u{i}"} for i in range(200, 237)],
    }
    calls = []

    items = make_client(paging_handler(pages, calls, total=237)).get_folder_items()

    assert calls == [1, 2, 3]
    assert len(items) == 237
    assert items[0]["uuid"] == "u0" and items[-1]["uuid"] == "u236"


def test_get_folder_items_stops_on_a_short_page():
    calls = []
    pages = {1: [{"uuid": "a"}, {"uuid": "b"}]}

    items = make_client(paging_handler(pages, calls)).get_folder_items()

    assert calls == [1]
    assert len(items) == 2


def test_get_folder_items_stops_at_an_exact_multiple_of_the_page_size():
    calls = []
    pages = {1: [{"uuid": f"u{i}"} for i in range(100)], 2: []}

    items = make_client(paging_handler(pages, calls)).get_folder_items()

    assert calls == [1, 2]
    assert len(items) == 100


def test_get_folder_items_deduplicates_across_pages():
    calls = []
    pages = {
        1: [{"uuid": f"u{i}"} for i in range(100)],
        2: [{"uuid": "u99"}] + [{"uuid": f"u{i}"} for i in range(100, 105)],
    }

    items = make_client(paging_handler(pages, calls)).get_folder_items()

    assert len(items) == 105
    assert len({i["uuid"] for i in items}) == 105


# --- auth -----------------------------------------------------------------

def test_missing_token_raises_auth_error():
    client = make_client(lambda r: httpx.Response(200, json={"code": 200}), token="")

    with pytest.raises(AuthError, match="No Viwoods token configured"):
        client.get_devices()


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401, json={"msg": "unauthorized"}),
        httpx.Response(403, json={"msg": "forbidden"}),
        httpx.Response(200, json={"code": 4001, "msg": "token expired"}),
        httpx.Response(200, json={"code": 500, "msg": "Please login again"}),
    ],
)
def test_expired_token_raises_auth_error(response):
    client = make_client(lambda r: response)

    with pytest.raises(AuthError, match="Settings"):
        client.get_devices()


def test_other_api_errors_are_not_auth_errors():
    client = make_client(
        lambda r: httpx.Response(200, json={"code": 500, "msg": "server exploded"})
    )

    with pytest.raises(RuntimeError, match="server exploded") as excinfo:
        client.get_devices()
    assert not isinstance(excinfo.value, AuthError)
