"""Exercise the real Cloudflare SDK with an offline HTTP transport."""

import json

import httpx
import pytest
from certbot import errors
from cloudflare import Cloudflare

from certbot_dns_alias.providers.base import ZoneRouter
from certbot_dns_alias.providers.cloudflare import CloudflareDNSProvider

ZONE_ID = "a" * 32
SUBZONE_ID = "b" * 32


def envelope(result, *, total=None, page=1, **extra):
    body = {"success": True, "errors": [], "messages": [], "result": result}
    if isinstance(result, list):
        body["result_info"] = {
            "page": page,
            "per_page": 2,
            "count": len(result),
            "total_count": len(result) if total is None else total,
            **extra,
        }
    return body


def record(id_="record-id", name="host.example.com", content="token", type_="TXT", **extra):
    return {"id": id_, "name": name, "content": content, "type": type_, "ttl": 600, **extra}


@pytest.fixture
def cloudflare(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_KEY", "ambient-key")
    monkeypatch.setenv("CLOUDFLARE_EMAIL", "ambient@example.com")
    monkeypatch.setenv("CLOUDFLARE_API_USER_SERVICE_KEY", "ambient-service-key")
    monkeypatch.setenv("CLOUDFLARE_BASE_URL", "https://invalid.example")
    responses = []
    requests = []

    def handle(request):
        requests.append(request)
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        status, body = response if isinstance(response, tuple) else (200, response)
        return httpx.Response(status, json=body)

    def client(**kwargs):
        return Cloudflare(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handle)))

    monkeypatch.setattr("certbot_dns_alias.providers.cloudflare.Cloudflare", client)
    provider = CloudflareDNSProvider("secret-token", zone_ids={"example.com": ZONE_ID})
    provider.page_size = 2
    yield provider, responses, requests
    provider.client.close()


def test_client_configuration(cloudflare):
    provider, replies, requests = cloudflare
    replies.append(envelope([]))
    provider.list_txt_records("example.com", "host")
    assert provider.client.max_retries == 0
    assert provider.client.timeout.connect == 10
    assert provider.client.timeout.read == 30
    assert str(provider.client.base_url) == "https://api.cloudflare.com/client/v4/"
    assert requests[0].headers["Authorization"] == "Bearer secret-token"
    assert "X-Auth-Key" not in requests[0].headers
    assert "X-Auth-Email" not in requests[0].headers
    assert "X-Auth-User-Service-Key" not in requests[0].headers


def test_discover_all_zones_and_cache_ids(cloudflare):
    provider, replies, requests = cloudflare
    provider._zone_ids = None
    replies.extend(
        [
            envelope(
                [
                    {"id": ZONE_ID, "name": "Example.COM."},
                    {"id": "c" * 32, "name": "例子.中国"},
                ],
                total=3,
            ),
            envelope([{"id": SUBZONE_ID, "name": "sub.example.com"}], total=3, page=2),
            envelope([]),
        ]
    )
    router = ZoneRouter({"cloudflare": provider})
    assert router.find("host.sub.example.com") == (provider, "sub.example.com")
    assert provider.list_zones() == ["example.com", "xn--fsqu00a.xn--fiqs8s", "sub.example.com"]
    provider.list_txt_records("sub.example.com", "host")
    assert [request.url.params.get("page") for request in requests[:2]] == ["1", "2"]
    assert all(request.url.params["per_page"] == "2" for request in requests[:2])
    assert requests[-1].url.path == f"/client/v4/zones/{SUBZONE_ID}/dns_records"


def test_record_pagination_exact_name_and_public_txt_filter(cloudflare):
    provider, replies, requests = cloudflare
    replies.extend(
        [
            envelope(
                [record("1", name="HOST.EXAMPLE.COM."), record("2", name="other.example.com")],
                total=6,
            ),
            envelope(
                [record("3", type_="CNAME"), record("4", private_routing=True)], total=6, page=2
            ),
            envelope(
                [record("5", content='"second-" "value"'), record("6", content='"token"')],
                total=6,
                page=3,
            ),
        ]
    )
    found = provider.list_txt_records("example.com", "host")
    assert [(item.id, item.name, item.value) for item in found] == [
        ("1", "host", "token"),
        ("5", "host", "second-value"),
        ("6", "host", "token"),
    ]
    assert [request.url.params["page"] for request in requests] == ["1", "2", "3"]
    assert all(request.url.params["name.exact"] == "host.example.com" for request in requests)
    assert all(request.url.params["type"] == "TXT" for request in requests)


def test_create_apex_and_delete_by_saved_ids(cloudflare):
    provider, replies, requests = cloudflare
    replies.extend([envelope(record()), envelope({"id": "record-id"})])
    assert provider.create_txt_record("EXAMPLE.com.", "@", "token", 600) == "record-id"
    provider.delete_txt_record("example.com", "record-id")
    assert json.loads(requests[0].content) == {
        "type": "TXT",
        "name": "example.com",
        "content": "token",
        "ttl": 600,
    }
    assert requests[0].method == "POST"
    assert requests[1].method == "DELETE"
    assert requests[1].url.path == f"/client/v4/zones/{ZONE_ID}/dns_records/record-id"
    assert len(requests) == 2


def test_empty_zone_and_record_lists(cloudflare):
    provider, replies, _ = cloudflare
    replies.append(envelope([]))
    assert provider.list_txt_records("example.com", "@") == []
    provider._zone_ids = None
    replies.append(envelope([]))
    assert provider.list_zones() == []


@pytest.mark.parametrize("kind", ["zones", "records"])
@pytest.mark.parametrize(
    "body",
    [
        envelope([], total=10),
        envelope([], page=2),
        envelope([], count=1),
        {"result": [], "result_info": {"page": 1, "count": 0}},
    ],
)
def test_incomplete_pagination_is_an_error(cloudflare, kind, body):
    provider, replies, _ = cloudflare
    replies.append(body)
    if kind == "zones":
        provider._zone_ids = None
    with pytest.raises(errors.PluginError, match="incomplete"):
        provider.list_zones() if kind == "zones" else provider.list_txt_records(
            "example.com", "host"
        )
    if kind == "zones":
        assert provider._zone_ids is None


def test_discovery_failure_can_retry_without_partial_cache(cloudflare):
    provider, replies, _ = cloudflare
    provider._zone_ids = None
    replies.extend(
        [
            envelope([{"id": ZONE_ID, "name": "example.com"}], total=2),
            envelope([], total=2, page=2),
            envelope([{"id": SUBZONE_ID, "name": "sub.example.com"}]),
        ]
    )
    with pytest.raises(errors.PluginError, match="incomplete"):
        provider.list_zones()
    assert provider._zone_ids is None
    assert provider.list_zones() == ["sub.example.com"]


@pytest.mark.parametrize("kind", ["zones", "records", "create", "delete"])
def test_api_errors_are_sanitized(cloudflare, kind):
    provider, replies, requests = cloudflare
    replies.append((403, {"errors": [{"code": 10000, "message": "secret-token sensitive-token"}]}))
    with pytest.raises(errors.PluginError, match="10000") as exc:
        if kind == "zones":
            provider._zone_ids = None
            provider.list_zones()
        elif kind == "records":
            provider.list_txt_records("example.com", "host")
        elif kind == "create":
            provider.create_txt_record("example.com", "host", "sensitive-token", 600)
        else:
            provider.delete_txt_record("example.com", "record-id")
    assert "token" not in str(exc.value)
    assert exc.value.__suppress_context__
    assert len(requests) == 1


@pytest.mark.parametrize("method", ["create", "delete"])
@pytest.mark.parametrize(
    "failure",
    [
        (503, {"errors": [{"code": 1000, "message": "sensitive"}]}),
        httpx.ReadTimeout("sensitive"),
    ],
)
def test_writes_are_never_retried(cloudflare, method, failure):
    provider, replies, requests = cloudflare
    replies.append(failure)
    with pytest.raises(errors.PluginError) as exc:
        if method == "create":
            provider.create_txt_record("example.com", "host", "token", 600)
        else:
            provider.delete_txt_record("example.com", "record-id")
    assert "sensitive" not in str(exc.value)
    assert len(requests) == 1


def test_absent_record_is_success_but_missing_zone_is_error(cloudflare):
    provider, replies, _ = cloudflare
    replies.extend(
        [
            (404, {"errors": [{"code": 81044, "message": "Record does not exist."}]}),
            (404, {"errors": [{"code": 7003, "message": "Zone does not exist."}]}),
        ]
    )
    provider.delete_txt_record("example.com", "record-id")
    with pytest.raises(errors.PluginError, match="7003"):
        provider.delete_txt_record("example.com", "record-id")


def test_create_requires_record_id(cloudflare):
    provider, replies, _ = cloudflare
    replies.append(envelope({"name": "host.example.com", "type": "TXT"}))
    with pytest.raises(errors.PluginError, match="record ID"):
        provider.create_txt_record("example.com", "host", "token", 600)


def test_public_lifecycle_preserves_existing_values_and_cleans_saved_target(
    cloudflare, authenticator, dns_challenges, monkeypatch
):
    provider, replies, requests = cloudflare
    authenticator._router = ZoneRouter({"cloudflare": provider})
    authenticator._resolver.resolve.return_value = "host.example.com"
    monkeypatch.setattr(authenticator, "_setup_credentials", lambda: None)
    monkeypatch.setattr("certbot.plugins.dns_common.sleep", lambda seconds: None)
    monkeypatch.setattr("certbot.plugins.dns_common.display_util.notify", lambda message: None)
    first, second = [challenge.validation(challenge.account_key) for challenge in dns_challenges]
    replies.extend(
        [
            envelope([record("existing", content=f'"{first}"')]),
            envelope([record("existing", content=first), record("unrelated", content="keep-me")]),
            envelope(record("created", content=second)),
            envelope({"id": "created"}),
        ]
    )
    assert len(authenticator.perform(dns_challenges)) == 2
    authenticator._resolver.resolve.side_effect = AssertionError("must not resolve during cleanup")
    authenticator.cleanup(dns_challenges)
    assert [request.method for request in requests] == ["GET", "GET", "POST", "DELETE"]
    assert requests[-1].url.path.endswith("/dns_records/created")
    assert not authenticator._leases


def test_public_lifecycle_keeps_cleanup_state_for_retry(
    cloudflare, authenticator, dns_challenges, monkeypatch
):
    provider, replies, _ = cloudflare
    authenticator._router = ZoneRouter({"cloudflare": provider})
    authenticator._resolver.resolve.return_value = "host.example.com"
    monkeypatch.setattr(authenticator, "_setup_credentials", lambda: None)
    monkeypatch.setattr("certbot.plugins.dns_common.sleep", lambda seconds: None)
    monkeypatch.setattr("certbot.plugins.dns_common.display_util.notify", lambda message: None)
    replies.extend(
        [
            envelope([]),
            envelope(record("created")),
            (403, {"errors": [{"code": 10000, "message": "denied"}]}),
            envelope({"id": "created"}),
        ]
    )
    authenticator.perform(dns_challenges[:1])
    authenticator.cleanup(dns_challenges[:1])
    assert len(authenticator._leases) == 1
    authenticator.cleanup(dns_challenges[:1])
    assert not authenticator._leases
