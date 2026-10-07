"""Exercise the GoDaddy provider and Certbot lifecycle at the HTTP boundary."""

import json
from unittest.mock import Mock
from urllib.parse import urlencode

import dns.message
import dns.name
import dns.resolver
import dns.rrset
import httpx2
import pytest
from acme import challenges
from certbot import errors
from certbot.compat import filesystem
from certbot.plugins.dns_common import CredentialsConfiguration

from certbot_dns_alias.config import build_router, validate_credentials
from certbot_dns_alias.providers.base import ZoneRouter
from certbot_dns_alias.providers.godaddy import GoDaddyDNSProvider
from certbot_dns_alias.sdk.godaddy import GoDaddyClient

DOMAIN_PATH = "/v3/domains/domain-names"
RECORD_PATH = "/v3/domains/zones/example.com/dns-records"


def domain(name="example.com"):
    return {"domain": name, "status": "ACTIVE", "nameServers": ["ns01.domaincontrol.com"]}


def record(id_="created", name="host", data="token", type_="TXT", **extra):
    return {"recordId": id_, "name": name, "data": data, "type": type_, "ttl": 600, **extra}


def collection(items, *, total=None, next_=None, **extra):
    body = {"items": items, "links": [] if next_ is None else [{"rel": "next", "href": next_}]}
    if items or total is not None:
        total = len(items) if total is None else total
        body.update(totalItems=total, totalPages=(total + 1) // 2)
    return {**body, **extra}


def next_records(page=2, name="host", type_="TXT", zone="example.com"):
    params = {"page": page, "pageSize": 2, "totalRequired": "true", "type": type_}
    if name is not None:
        params["name"] = name
    return f"/v3/domains/zones/{zone}/dns-records?{urlencode(params)}"


@pytest.fixture
def godaddy(monkeypatch):
    replies, requests, clients = [], [], []

    def handle(request):
        requests.append(request)
        response = replies.pop(0)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, httpx2.Response):
            return response
        status, body = response if isinstance(response, tuple) else (200, response)
        return httpx2.Response(status, json=body)

    def client(*args, **kwargs):
        result = GoDaddyClient(*args, **kwargs, transport=httpx2.MockTransport(handle))
        clients.append(result)
        return result

    monkeypatch.setattr("certbot_dns_alias.providers.godaddy.GoDaddyClient", client)
    monkeypatch.setattr(GoDaddyDNSProvider, "page_size", 2)
    provider = GoDaddyDNSProvider("secret-token")
    yield provider, replies, requests
    for client in clients:
        client.close()


def credentials(tmp_path, text):
    path = tmp_path / "godaddy.ini"
    path.write_text(text)
    filesystem.chmod(str(path), 0o600)
    return CredentialsConfiguration(str(path), lambda key: "dns_alias_" + key)


def test_configuration_and_ote(godaddy, tmp_path):
    _, replies, requests = godaddy
    conf = credentials(
        tmp_path,
        "dns_alias_provider=GoDaddy\ndns_alias_godaddy_api_token=secret-token\n"
        "dns_alias_godaddy_ote=TRUE\ndns_alias_godaddy_zones=Example.COM., 中国.example",
    )
    validate_credentials(conf)
    router = build_router(conf)
    provider, zone = router.find("host.example.com")
    assert zone == "example.com"
    assert router.find("host.中国.example") == (provider, "xn--fiqs8s.example")
    replies.append(collection([]))
    assert provider.list_txt_records(zone, "host") == []
    assert requests[0].url.host == "api.ote-godaddy.com"
    assert requests[0].headers["Authorization"] == "Bearer secret-token"
    assert requests[0].url.path == RECORD_PATH
    assert provider.client._http.timeout.connect == 10
    assert provider.client._http.timeout.read == 30
    assert not provider.client._http.follow_redirects
    assert not provider.client._http.trust_env
    with pytest.raises(errors.PluginError, match="No managed zone"):
        router.find("host.other.com")
    assert len(requests) == 1


@pytest.mark.parametrize("mode", ["godaddy", "auto"])
def test_token_only_discovers_zones_with_production_default(godaddy, tmp_path, mode):
    _, replies, requests = godaddy
    conf = credentials(tmp_path, f"dns_alias_provider={mode}\ndns_alias_godaddy_api_token=token")
    validate_credentials(conf)
    router = build_router(conf)
    replies.extend([collection([domain()]), collection([record(name="@", type_="SOA")])])
    provider, zone = router.find("host.example.com")
    assert set(router.providers) == {"godaddy"}
    assert zone == "example.com"
    assert provider.name == "godaddy"
    assert requests[0].url.host == "api.godaddy.com"
    assert requests[0].url.path == DOMAIN_PATH
    assert requests[1].url.params["type"] == "SOA"
    assert requests[1].url.params["name"] == "@"


@pytest.mark.parametrize(
    "text",
    [
        "dns_alias_provider=godaddy",
        "dns_alias_provider=godaddy\ndns_alias_godaddy_api_key=key\ndns_alias_godaddy_api_secret=secret",
        "dns_alias_provider=godaddy\ndns_alias_godaddy_api_token=",
        "dns_alias_provider=auto\ndns_alias_godaddy_api_token=",
        "dns_alias_provider=godaddy\ndns_alias_godaddy_api_token=one, two",
        "dns_alias_provider=godaddy\ndns_alias_godaddy_api_token=invalid token",
        "dns_alias_provider=godaddy\ndns_alias_godaddy_api_token=中国",
        "dns_alias_provider=godaddy\ndns_alias_godaddy_api_token=token\ndns_alias_godaddy_ote=",
        "dns_alias_provider=godaddy\ndns_alias_godaddy_api_token=token\ndns_alias_godaddy_ote=yes",
        "dns_alias_provider=godaddy\ndns_alias_godaddy_api_token=token\n"
        "dns_alias_godaddy_ote=true, false",
        "dns_alias_provider=godaddy\ndns_alias_godaddy_api_token=token\ndns_alias_godaddy_zones=",
        "dns_alias_provider=godaddy\ndns_alias_godaddy_api_token=token\ndns_alias_godaddy_zones=*.com",
        "dns_alias_provider=godaddy\ndns_alias_godaddy_api_token=token\ndns_alias_godaddy_zones=a..com",
    ],
)
def test_invalid_credentials_are_rejected_without_http(godaddy, tmp_path, text):
    _, _, requests = godaddy
    with pytest.raises(errors.PluginError):
        validate_credentials(credentials(tmp_path, text))
    assert not requests


def test_invalid_client_arguments_become_plugin_errors():
    with pytest.raises(errors.PluginError, match="Invalid GoDaddy") as caught:
        GoDaddyDNSProvider("sensitive token")
    assert "sensitive" not in str(caught.value)
    assert caught.value.__suppress_context__


def test_zone_discovery_paginates_verifies_access_and_caches(godaddy):
    provider, replies, requests = godaddy
    replies.extend(
        [
            collection(
                [domain("Example.COM."), domain("中国.example")],
                next_=DOMAIN_PATH + "?pageSize=2&pageToken=cursor",
            ),
            collection([domain("sub.example.com"), domain("external.example")]),
            collection([record(name="@", type_="SOA")]),
            collection([record(name="@", type_="SOA")]),
            collection([record("soa", name="other", type_="SOA"), record("txt", name="@")]),
            collection([]),
        ]
    )
    router = ZoneRouter({"godaddy": provider})
    assert router.find("host.example.com") == (provider, "example.com")
    assert router.find("host.中国.example") == (provider, "xn--fiqs8s.example")
    assert provider.list_zones() == ["example.com", "xn--fiqs8s.example"]
    assert requests[1].url.params["pageToken"] == "cursor"
    assert len(requests) == 6
    with pytest.raises(errors.PluginError, match="No managed zone"):
        router.find("host.external.example")
    with pytest.raises(errors.PluginError, match="No managed zone"):
        router.find("notexample.com")


def test_longest_discovered_zone_wins(godaddy):
    provider, replies, _ = godaddy
    replies.extend(
        [
            collection([domain(), domain("sub.example.com")]),
            collection([record(name="@", type_="SOA")]),
            collection([record(name="@", type_="SOA")]),
        ]
    )
    assert ZoneRouter({"godaddy": provider}).find("host.sub.example.com") == (
        provider,
        "sub.example.com",
    )


@pytest.mark.parametrize("failure", ["domains", "zone_access", "incomplete"])
def test_discovery_failure_never_caches_partial_zones_and_can_retry(godaddy, failure):
    provider, replies, requests = godaddy
    if failure == "domains":
        replies.extend(
            [
                collection([domain()], next_=DOMAIN_PATH + "?pageSize=2&pageToken=cursor"),
                (403, {"name": "FORBIDDEN"}),
            ]
        )
    else:
        replies.extend(
            [
                collection([domain(), domain("sub.example.com")]),
                collection([record(name="@", type_="SOA")]),
                (404, {"name": "NOT_FOUND"})
                if failure == "zone_access"
                else collection([], total=1),
            ]
        )
    router = ZoneRouter({"godaddy": provider})
    with pytest.raises(errors.PluginError):
        router.find("host.example.com")
    assert provider._zones is None
    assert router._loaded_zones is None
    replies.extend([collection([domain()]), collection([record(name="@", type_="SOA")])])
    assert router.find("host.example.com") == (provider, "example.com")
    assert requests[-2].url.path == DOMAIN_PATH


def test_empty_zone_discovery_is_cached(godaddy):
    provider, replies, requests = godaddy
    replies.append(collection([]))
    assert provider.list_zones() == []
    assert provider.list_zones() == []
    assert len(requests) == 1


def test_auto_routes_all_four_providers_with_explicit_zones(godaddy, tmp_path):
    _, _, requests = godaddy
    conf = credentials(
        tmp_path,
        """
dns_alias_provider=auto
dns_alias_aliyun_access_key_id=id
dns_alias_aliyun_access_key_secret=secret
dns_alias_aliyun_zones=example.com
dns_alias_tencent_secret_id=id
dns_alias_tencent_secret_key=secret
dns_alias_tencent_zones=ten.example.com
dns_alias_cloudflare_api_token=token
dns_alias_cloudflare_zone_ids=cf.example.com:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
dns_alias_godaddy_api_token=token
dns_alias_godaddy_zones=sub.example.com
""",
    )
    validate_credentials(conf)
    router = build_router(conf)
    assert set(router.providers) == {"aliyun", "tencent", "cloudflare", "godaddy"}
    for provider, zone in [
        ("aliyun", "example.com"),
        ("tencent", "ten.example.com"),
        ("cloudflare", "cf.example.com"),
        ("godaddy", "sub.example.com"),
    ]:
        assert router.find("host." + zone) == (router.providers[provider], zone)
    assert not requests


def test_auto_conflicting_zone_is_ambiguous(godaddy, tmp_path):
    conf = credentials(
        tmp_path,
        "dns_alias_provider=auto\ndns_alias_godaddy_api_token=token\n"
        "dns_alias_godaddy_zones=example.com\ndns_alias_cloudflare_api_token=token\n"
        "dns_alias_cloudflare_zone_ids=example.com:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )
    with pytest.raises(errors.PluginError, match="Ambiguous"):
        build_router(conf).find("host.example.com")


def test_record_pagination_requires_exact_relative_name_and_type(godaddy):
    provider, replies, requests = godaddy
    replies.extend(
        [
            collection(
                [record("1", name="HOST."), record("2", name="other")],
                total=5,
                next_=next_records(),
            ),
            collection(
                [record("3", type_="CNAME"), record("4", data='"literal" 中国')],
                total=5,
                next_=next_records(3),
            ),
            collection([record("5", name="host.example.com")], total=5),
        ]
    )
    found = provider.list_txt_records("Example.COM.", "HOST.")
    assert [(item.id, item.name, item.value) for item in found] == [
        ("1", "host", "token"),
        ("4", "host", '"literal" 中国'),
    ]
    assert [request.url.params["page"] for request in requests] == ["1", "2", "3"]
    assert all(request.url.params["name"] == "host" for request in requests)
    assert all(request.url.params["type"] == "TXT" for request in requests)


def test_apex_records_and_idn_names(godaddy):
    provider, replies, requests = godaddy
    replies.extend([collection([record(name="@")]), collection([record(name="XN--FIQS8S")])])
    assert provider.list_txt_records("example.com", "@")[0].name == "@"
    assert provider.list_txt_records("中国.example", "中国")[0].name == "xn--fiqs8s"
    assert requests[1].url.path == "/v3/domains/zones/xn--fiqs8s.example/dns-records"
    assert requests[1].url.params["name"] == "xn--fiqs8s"


def test_empty_txt_response(godaddy):
    provider, replies, _ = godaddy
    replies.append(collection([]))
    assert provider.list_txt_records("example.com", "host") == []


@pytest.mark.parametrize(
    "body",
    [
        collection([], total=1),
        collection([record()], total=3),
        collection([record("duplicate"), record("duplicate")]),
        {"items": [record()], "links": []},
        collection([record(recordId=None)]),
    ],
)
def test_invalid_or_incomplete_txt_list_is_an_error(godaddy, body):
    provider, replies, _ = godaddy
    replies.append(body)
    with pytest.raises(errors.PluginError, match="invalid or incomplete"):
        provider.list_txt_records("example.com", "host")


@pytest.mark.parametrize("name", ["@", "host", "HOST.", "中国"])
@pytest.mark.parametrize("ttl", [600, 86400])
def test_create_one_value_and_delete_saved_id(godaddy, name, ttl):
    provider, replies, requests = godaddy
    canonical = "@" if name == "@" else "xn--fiqs8s" if name == "中国" else "host"
    replies.extend([(201, record(name=canonical, ttl=ttl)), httpx2.Response(204)])
    assert provider.create_txt_record("Example.COM.", name, "token", ttl) == "created"
    provider.delete_txt_record("example.com", "created")
    assert [request.method for request in requests] == ["POST", "DELETE"]
    assert json.loads(requests[0].content) == {
        "name": canonical,
        "type": "TXT",
        "data": "token",
        "ttl": ttl,
    }
    assert requests[0].url.path == RECORD_PATH
    assert requests[1].url.path == RECORD_PATH + "/created"


@pytest.mark.parametrize("ttl", [0, 599, 86401, True])
def test_invalid_ttl_is_a_safe_plugin_error_without_http(godaddy, ttl):
    provider, _, requests = godaddy
    with pytest.raises(errors.PluginError, match="TTL must be between 600 and 86400") as caught:
        provider.create_txt_record("example.com", "host", "sensitive-value", ttl)
    assert "sensitive-value" not in str(caught.value)
    assert not requests


@pytest.mark.parametrize(
    "body", [record(recordId=None), record(name="other"), record(type_="A"), record(data="other")]
)
def test_create_rejects_missing_id_or_mismatched_identity(godaddy, body):
    provider, replies, requests = godaddy
    replies.append((201, body))
    with pytest.raises(errors.PluginError):
        provider.create_txt_record("example.com", "host", "token", 600)
    assert len(requests) == 1


@pytest.mark.parametrize("operation", ["zones", "records", "create", "delete"])
@pytest.mark.parametrize(
    "failure",
    [
        (403, {"name": "FORBIDDEN", "message": "secret-token sensitive-value"}),
        httpx2.ReadTimeout("secret-token sensitive-value"),
        (503, {"name": "UNAVAILABLE"}),
    ],
)
def test_failures_are_sanitized_and_writes_are_never_retried(godaddy, operation, failure):
    provider, replies, requests = godaddy
    replies.append(failure)
    with pytest.raises(errors.PluginError) as caught:
        if operation == "zones":
            provider.list_zones()
        elif operation == "records":
            provider.list_txt_records("example.com", "host")
        elif operation == "create":
            provider.create_txt_record("example.com", "host", "sensitive-value", 600)
        else:
            provider.delete_txt_record("example.com", "saved")
    assert "secret-token" not in str(caught.value)
    assert "sensitive-value" not in str(caught.value)
    assert caught.value.__suppress_context__
    assert len(requests) == 1


def test_delete_404_succeeds_only_after_complete_saved_id_absence_check(godaddy):
    provider, replies, requests = godaddy
    replies.extend(
        [
            (404, {"name": "NOT_FOUND"}),
            collection(
                [record("unrelated1"), record("unrelated2")],
                total=3,
                next_=next_records(name=None),
            ),
            collection([record("unrelated3")], total=3),
        ]
    )
    provider.delete_txt_record("example.com", "saved")
    assert [request.method for request in requests] == ["DELETE", "GET", "GET"]
    assert all(request.url.path == RECORD_PATH for request in requests[1:])
    assert all("name" not in request.url.params for request in requests[1:])


@pytest.mark.parametrize(
    "verification",
    [
        (404, {"name": "ZONE_NOT_FOUND"}),
        (403, {"name": "FORBIDDEN"}),
        httpx2.ReadTimeout("sensitive-value"),
        collection([], total=1),
        collection([record("saved")]),
    ],
)
def test_delete_404_retains_errors_for_missing_zone_or_unconfirmed_absence(godaddy, verification):
    provider, replies, requests = godaddy
    replies.extend([(404, {"name": "NOT_FOUND"}), verification])
    with pytest.raises(errors.PluginError):
        provider.delete_txt_record("example.com", "saved")
    assert len(requests) == 2


@pytest.fixture
def lifecycle(godaddy, authenticator, monkeypatch):
    provider, replies, requests = godaddy
    authenticator._router = ZoneRouter({"godaddy": provider}, {"godaddy": ["example.com"]})
    authenticator._resolver.resolve.return_value = "host.example.com"
    monkeypatch.setattr(authenticator, "_setup_credentials", lambda: None)
    sleep = Mock()
    monkeypatch.setattr("certbot.plugins.dns_common.sleep", sleep)
    monkeypatch.setattr("certbot.plugins.dns_common.display_util.notify", lambda message: None)
    return authenticator, replies, requests, sleep


def test_public_lifecycle_with_real_credentials_and_dns_boundary(
    godaddy, authenticator, dns_challenges, tmp_path, monkeypatch
):
    _, replies, requests = godaddy
    conf = credentials(
        tmp_path,
        "dns_alias_provider=godaddy\ndns_alias_godaddy_api_token=secret-token\n"
        "dns_alias_godaddy_zones=example.com",
    )
    authenticator.config.dns_alias_credentials = conf.confobj.filename

    def resolve(resolver, qname, rdtype, **kwargs):
        assert kwargs["search"] is False
        query = dns.message.make_query(qname, rdtype)
        response = dns.message.make_response(query)
        if qname == "_acme-challenge.example.com.":
            response.answer.append(
                dns.rrset.from_text(qname, 300, "IN", "CNAME", "host.example.com.")
            )
        return dns.resolver.Answer(dns.name.from_text(qname), dns.rdatatype.CNAME, 1, response)

    monkeypatch.setattr(dns.resolver.Resolver, "resolve", resolve)
    sleep = Mock()
    monkeypatch.setattr("certbot.plugins.dns_common.sleep", sleep)
    monkeypatch.setattr("certbot.plugins.dns_common.display_util.notify", lambda message: None)
    value = dns_challenges[0].validation(dns_challenges[0].account_key)
    replies.extend([collection([]), (201, record(data=value)), httpx2.Response(204)])
    responses = authenticator.perform(dns_challenges[:1])
    assert isinstance(responses[0], challenges.DNS01Response)
    assert authenticator.credentials.conf("godaddy_api_token") == "secret-token"
    sleep.assert_called_once_with(0)
    authenticator.cleanup(dns_challenges[:1])
    assert [request.method for request in requests] == ["GET", "POST", "DELETE"]
    assert not authenticator._leases
    assert not authenticator._challenges


def test_public_lifecycle_preserves_preexisting_and_unrelated_values(lifecycle, dns_challenges):
    auth, replies, requests, sleep = lifecycle
    first, second = [challenge.validation(challenge.account_key) for challenge in dns_challenges]
    replies.extend(
        [
            collection([record("existing", data=first)]),
            collection([record("existing", data=first), record("unrelated", data="keep-me")]),
            (201, record(data=second)),
            httpx2.Response(204),
        ]
    )
    assert len(auth.perform(dns_challenges)) == 2
    sleep.assert_called_once_with(0)
    auth._resolver.resolve.side_effect = AssertionError("must not resolve during cleanup")
    auth.cleanup(dns_challenges)
    assert [request.method for request in requests] == ["GET", "GET", "POST", "DELETE"]
    assert requests[-1].url.path == RECORD_PATH + "/created"
    assert not auth._leases


def test_public_lifecycle_creates_multiple_values_at_same_name(lifecycle, dns_challenges):
    auth, replies, requests, _ = lifecycle
    first, second = [challenge.validation(challenge.account_key) for challenge in dns_challenges]
    replies.extend(
        [
            collection([]),
            (201, record("first", data=first)),
            collection([record("first", data=first)]),
            (201, record("second", data=second)),
            httpx2.Response(204),
            httpx2.Response(204),
        ]
    )
    auth.perform(dns_challenges)
    auth.cleanup(dns_challenges[:1])
    assert len(auth._leases) == 1
    auth.cleanup(dns_challenges[1:])
    assert [
        json.loads(request.content)["data"] for request in requests if request.method == "POST"
    ] == [first, second]
    assert [request.url.path for request in requests if request.method == "DELETE"] == [
        RECORD_PATH + "/first",
        RECORD_PATH + "/second",
    ]
    assert not auth._leases


def test_public_lifecycle_shared_value_uses_one_record_until_last_user(lifecycle, dns_challenges):
    auth, replies, requests, _ = lifecycle
    challenge = dns_challenges[0]
    value = challenge.validation(challenge.account_key)
    replies.extend([collection([]), (201, record(data=value)), httpx2.Response(204)])
    auth.perform([challenge, challenge])
    assert len(requests) == 2
    auth.cleanup([challenge])
    assert len(requests) == 2
    assert next(iter(auth._leases.values())).users == 1
    auth.cleanup([challenge])
    assert [request.method for request in requests] == ["GET", "POST", "DELETE"]
    assert not auth._leases


@pytest.mark.parametrize("failure", [(503, {"name": "UNAVAILABLE"}), (201, record(recordId=None))])
def test_public_lifecycle_cleans_only_successful_writes_after_partial_failure(
    lifecycle, dns_challenges, failure
):
    auth, replies, requests, sleep = lifecycle
    value = dns_challenges[0].validation(dns_challenges[0].account_key)
    replies.extend(
        [collection([]), (201, record(data=value)), collection([]), failure, httpx2.Response(204)]
    )
    with pytest.raises(errors.PluginError):
        auth.perform(dns_challenges)
    sleep.assert_not_called()
    assert len(auth._leases) == 1
    auth.cleanup(dns_challenges)
    assert [request.method for request in requests] == ["GET", "POST", "GET", "POST", "DELETE"]
    assert requests[-1].url.path == RECORD_PATH + "/created"
    assert not auth._leases
    assert not auth._challenges


@pytest.mark.parametrize("absent", [False, True])
def test_public_lifecycle_retains_cleanup_state_for_retry(
    lifecycle, dns_challenges, absent, caplog
):
    auth, replies, requests, _ = lifecycle
    value = dns_challenges[0].validation(dns_challenges[0].account_key)
    replies.extend(
        [
            collection([]),
            (201, record(data=value)),
            (403, {"name": "FORBIDDEN", "message": "secret-token"}),
        ]
    )
    auth.perform(dns_challenges[:1])
    auth.cleanup(dns_challenges[:1])
    assert len(auth._leases) == 1
    assert len(auth._challenges) == 1
    assert "FORBIDDEN" in caplog.text
    assert "secret-token" not in caplog.text
    assert value not in caplog.text
    replies.extend(
        [(404, {"name": "NOT_FOUND"}), collection([])] if absent else [httpx2.Response(204)]
    )
    auth._resolver.resolve.side_effect = AssertionError("must not resolve during cleanup")
    auth.cleanup(dns_challenges[:1])
    assert not auth._leases
    assert not auth._challenges
    assert all(
        request.url.path == RECORD_PATH + "/created"
        for request in requests
        if request.method == "DELETE"
    )
