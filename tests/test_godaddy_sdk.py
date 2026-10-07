"""Exercise GoDaddy v3 request contracts through an offline HTTPX2 transport."""

import json

import httpx2
import pytest
from certbot import errors

from certbot_dns_alias.sdk.godaddy import (
    DNSRecord,
    GoDaddyAPIError,
    GoDaddyClient,
    GoDaddyResponseError,
    GoDaddyTransportError,
)

DOMAIN_PATH = "/v3/domains/domain-names"
RECORD_PATH = "/v3/domains/zones/example.com/dns-records"


def domain(name="example.com", **extra):
    return {
        "domain": name,
        "status": "ACTIVE",
        "expiresAt": "2027-01-01T00:00:00Z",
        "createdAt": "2026-01-01T00:00:00Z",
        "autoRenew": True,
        "privacy": False,
        "nameServers": ["ns01.domaincontrol.com", "ns02.domaincontrol.com"],
        **extra,
    }


def record(id_="record-id", **extra):
    return {
        "recordId": id_,
        "name": "_acme-challenge",
        "type": "TXT",
        "data": "validation-value",
        "ttl": 600,
        **extra,
    }


def collection(items, *, next_=None, **extra):
    return {
        "items": items,
        "links": [] if next_ is None else [{"rel": "next", "href": next_}],
        **extra,
    }


@pytest.fixture
def godaddy():
    replies = []
    requests = []

    def handle(request):
        requests.append(request)
        response = replies.pop(0)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, httpx2.Response):
            return response
        status, payload = response if isinstance(response, tuple) else (200, response)
        return httpx2.Response(status, json=payload)

    with GoDaddyClient("secret-token", transport=httpx2.MockTransport(handle)) as client:
        yield client, replies, requests


def test_configuration_and_lifecycle(monkeypatch):
    monkeypatch.setenv("GODADDY_API_TOKEN", "ambient-token")
    monkeypatch.setenv("GODADDY_BASE_URL", "https://invalid.example")
    monkeypatch.setenv("HTTPS_PROXY", "http://invalid.example")
    captured = []

    def handle(request):
        captured.append(request)
        return httpx2.Response(200, json=collection([]))

    transport = httpx2.MockTransport(handle)
    with GoDaddyClient("secret-token", ote=True, transport=transport) as client:
        assert client.list_domains() == []
        assert client._http.timeout.connect == 10
        assert client._http.timeout.read == 30
        assert client._http.timeout.write == 30
        assert client._http.timeout.pool == 30
        assert not client._http.follow_redirects
        assert not client._http.trust_env
        assert captured[0].url.host == "api.ote-godaddy.com"
        assert captured[0].headers["Authorization"] == "Bearer secret-token"
        assert captured[0].headers["Accept"] == "application/json"
    assert client._http.is_closed
    client.close()


def test_default_transport_initialization_without_network():
    with GoDaddyClient("offline-configuration-check") as client:
        assert client._http.base_url.host == "api.godaddy.com"
        assert client._http.timeout.connect == 10
    assert client._http.is_closed


@pytest.mark.parametrize("token", ["", None, "token\n", "token with spaces", "中国", "token\x7f"])
def test_invalid_token(token):
    with pytest.raises(ValueError, match="Personal Access Token"):
        GoDaddyClient(token)


def test_invalid_environment():
    with pytest.raises(ValueError, match="boolean"):
        GoDaddyClient("token", ote="yes")


def test_domains_cursor_pagination(godaddy):
    client, replies, requests = godaddy
    replies.extend(
        [
            collection([domain()], next_=DOMAIN_PATH + "?pageSize=1&pageToken=opaque%2Bcursor"),
            collection([domain("sub.example.com")]),
        ]
    )
    domains = client.list_domains(page_size=1)
    assert [item.domain for item in domains] == ["example.com", "sub.example.com"]
    assert domains[0].name_servers == ("ns01.domaincontrol.com", "ns02.domaincontrol.com")
    assert dict(requests[0].url.params) == {"pageSize": "1"}
    assert dict(requests[1].url.params) == {"pageSize": "1", "pageToken": "opaque+cursor"}
    assert all(request.url.host == "api.godaddy.com" for request in requests)


def test_read_domain_normalizes_idn(godaddy):
    client, replies, requests = godaddy
    replies.append(domain("XN--FIQS8S.example"))
    result = client.get_domain("中国.Example.")
    assert result.domain == "xn--fiqs8s.example"
    assert requests[0].url.path == DOMAIN_PATH + "/xn--fiqs8s.example"


def test_records_offset_pagination_preserves_filters(godaddy):
    client, replies, requests = godaddy
    replies.extend(
        [
            collection(
                [record("first"), record("second")],
                totalItems=3,
                totalPages=2,
                next_=(
                    "https://api.godaddy.com"
                    + RECORD_PATH
                    + "?page=2&pageSize=2&totalRequired=true&type=TXT&name=%40"
                ),
            ),
            collection([record("third")], totalItems=3, totalPages=2),
        ]
    )
    records = client.list_records("Example.COM.", type="TXT", name="@", page_size=2)
    assert [item.record_id for item in records] == ["first", "second", "third"]
    assert records[0].data == "validation-value"
    assert dict(requests[0].url.params) == {
        "page": "1",
        "pageSize": "2",
        "totalRequired": "true",
        "type": "TXT",
        "name": "@",
    }
    assert requests[1].url.params["page"] == "2"
    assert requests[1].url.params["name"] == "@"


def test_empty_collections_without_totals(godaddy):
    client, replies, requests = godaddy
    replies.extend([collection([]), collection([])])
    assert client.list_domains() == []
    assert client.list_records("example.com") == []
    assert "type" not in requests[1].url.params
    assert "name" not in requests[1].url.params


def test_create_and_replace_single_records(godaddy):
    client, replies, requests = godaddy
    txt = DNSRecord("_acme-challenge", "TXT", '"exact" Unicode 中国 value', 600)
    response = record(data=txt.data)
    replies.extend([(201, response), response])
    created = client.create_record("Example.COM.", txt)
    replaced = client.replace_record("example.com", created.record_id, created)
    assert replaced == created
    assert created.record_id == "record-id"
    assert created.data == txt.data
    assert [request.method for request in requests] == ["POST", "PUT"]
    assert requests[0].url.path == RECORD_PATH
    assert requests[1].url.path == RECORD_PATH + "/record-id"
    assert json.loads(requests[0].content) == {
        "name": txt.name,
        "type": "TXT",
        "data": txt.data,
        "ttl": 600,
    }
    assert json.loads(requests[1].content) == json.loads(requests[0].content)
    assert "recordId" not in json.loads(requests[1].content)
    assert txt.data not in repr(txt)


@pytest.mark.parametrize(
    "type_,extra",
    [
        ("MX", {"priority": 10}),
        (
            "SRV",
            {"priority": 10, "weight": 20, "port": 443, "service": "_https", "protocol": "_tcp"},
        ),
        ("CAA", {"flag": 0, "tag": "issue"}),
    ],
)
def test_optional_record_fields(godaddy, type_, extra):
    client, replies, requests = godaddy
    replies.append((201, record(type=type_, **extra)))
    result = client.create_record("example.com", DNSRecord("@", type_, "value", 86400, **extra))
    body = json.loads(requests[0].content)
    for key, value in extra.items():
        assert body[key] == value
        assert getattr(result, key) == value


def test_delete_saved_id_no_body(godaddy):
    client, replies, requests = godaddy
    replies.append(httpx2.Response(204))
    assert client.delete_record("example.com", "saved_id-123") is None
    assert requests[0].method == "DELETE"
    assert requests[0].url.path == RECORD_PATH + "/saved_id-123"
    assert requests[0].content == b""


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422, 429, 500, 503])
@pytest.mark.parametrize("method", ["read", "create", "replace", "delete"])
def test_api_errors_and_no_retries(godaddy, status, method):
    client, replies, requests = godaddy
    replies.append(
        (
            status,
            {
                "name": "dns_record_not_found",
                "message": "secret-token validation-value",
                "details": [{"message": "secret-token validation-value"}],
            },
        )
    )
    with pytest.raises(GoDaddyAPIError) as caught:
        invoke(client, method)
    assert caught.value.status_code == status
    assert caught.value.code == "dns_record_not_found"
    assert isinstance(caught.value, errors.PluginError)
    assert "secret-token" not in str(caught.value)
    assert "validation-value" not in str(caught.value)
    assert caught.value.__suppress_context__
    assert len(requests) == 1


def invoke(client, method):
    txt = DNSRecord("_acme-challenge", "TXT", "validation-value", 600)
    if method == "read":
        return client.list_domains()
    if method == "create":
        return client.create_record("example.com", txt)
    if method == "replace":
        return client.replace_record("example.com", "record-id", txt)
    return client.delete_record("example.com", "record-id")


@pytest.mark.parametrize("method", ["read", "create", "replace", "delete"])
@pytest.mark.parametrize(
    "exception",
    [
        httpx2.ConnectTimeout,
        httpx2.ReadTimeout,
        httpx2.WriteError,
        httpx2.RemoteProtocolError,
    ],
)
def test_transport_errors_hide_sensitive_context(godaddy, method, exception):
    client, replies, requests = godaddy
    replies.append(exception("secret-token validation-value"))
    with pytest.raises(GoDaddyTransportError) as caught:
        invoke(client, method)
    assert str(caught.value) == f"GoDaddy API transport failed ({exception.__name__})"
    assert caught.value.__suppress_context__
    assert len(requests) == 1


@pytest.mark.parametrize(
    "payload,code",
    [
        ({"code": "NOT_FOUND", "message": "secret-token"}, "NOT_FOUND"),
        ({"name": "secret-token\nvalidation-value"}, "HTTP_ERROR"),
        ({"name": ["secret-token"]}, "HTTP_ERROR"),
        (["secret-token"], "HTTP_ERROR"),
    ],
)
def test_invalid_error_envelopes(godaddy, payload, code):
    client, replies, _ = godaddy
    replies.append((403, payload))
    with pytest.raises(GoDaddyAPIError) as caught:
        client.list_domains()
    assert caught.value.code == code


def test_non_json_error_response(godaddy):
    client, replies, _ = godaddy
    replies.append(httpx2.Response(502, text="secret-token validation-value"))
    with pytest.raises(GoDaddyAPIError, match="502, HTTP_ERROR"):
        client.list_domains()


def test_redirect_is_not_followed(godaddy):
    client, replies, requests = godaddy
    replies.append(httpx2.Response(307, headers={"Location": "https://invalid.example"}))
    with pytest.raises(GoDaddyAPIError, match="307"):
        client.create_record("example.com", DNSRecord("@", "TXT", "value", 600))
    assert len(requests) == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(200, text="not json"),
        httpx2.Response(200, json=[]),
        httpx2.Response(200, json={}),
        httpx2.Response(200, json={"items": None, "links": []}),
        httpx2.Response(200, json={"items": [], "links": None}),
        httpx2.Response(200, json={"items": [], "links": [None]}),
    ],
)
def test_malformed_collection_responses(godaddy, response):
    client, replies, _ = godaddy
    replies.append(response)
    with pytest.raises(GoDaddyResponseError):
        client.list_domains()


@pytest.mark.parametrize(
    "next_",
    [
        "https://invalid.example" + DOMAIN_PATH + "?pageSize=1&pageToken=cursor",
        "http://api.godaddy.com" + DOMAIN_PATH + "?pageSize=1&pageToken=cursor",
        "https://secret-token@api.godaddy.com" + DOMAIN_PATH + "?pageSize=1&pageToken=cursor",
        DOMAIN_PATH + "/other?pageSize=1&pageToken=cursor",
        DOMAIN_PATH + "?pageSize=1&pageToken=cursor#fragment",
        DOMAIN_PATH + "?pageSize=1&pageToken=a&pageToken=b",
        DOMAIN_PATH + "?pageSize=1&pageToken=",
        DOMAIN_PATH + "?pageSize=1&pageToken=cursor&bad",
        DOMAIN_PATH + "?pageSize=2&pageToken=cursor",
        DOMAIN_PATH + "?pageSize=1",
        DOMAIN_PATH + "?pageSize=1&pageToken=cursor&pageTokenDirection=backward",
    ],
)
def test_invalid_navigation_never_sends_another_request(godaddy, next_):
    client, replies, requests = godaddy
    replies.append(collection([domain()], next_=next_))
    with pytest.raises(GoDaddyResponseError):
        client.list_domains(page_size=1)
    assert len(requests) == 1


@pytest.mark.parametrize("failure", ["repeated_cursor", "duplicate_domain", "empty_page"])
def test_domain_pagination_failures(godaddy, failure):
    client, replies, _ = godaddy
    next_ = DOMAIN_PATH + "?pageSize=1&pageToken=cursor"
    replies.append(collection([domain()], next_=next_))
    replies.append(
        collection(
            []
            if failure == "empty_page"
            else [domain("example.com" if failure == "duplicate_domain" else "other.com")],
            next_=next_ if failure != "duplicate_domain" else None,
        )
    )
    with pytest.raises(GoDaddyResponseError):
        client.list_domains(page_size=1)


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"totalItems": 1},
        {"totalItems": True, "totalPages": 1},
        {"totalItems": -1, "totalPages": 1},
        {"totalItems": 1, "totalPages": 2},
        {"totalItems": 3, "totalPages": 2},
    ],
)
def test_records_require_complete_totals(godaddy, extra):
    client, replies, _ = godaddy
    replies.append(collection([record()], **extra))
    with pytest.raises(GoDaddyResponseError):
        client.list_records("example.com", page_size=2)


@pytest.mark.parametrize(
    "failure",
    [
        "changed_total",
        "duplicate_id",
        "empty_page",
        "missing_next",
        "wrong_page",
        "changed_filter",
        "extra_next",
    ],
)
def test_record_pagination_failures(godaddy, failure):
    client, replies, _ = godaddy
    next_ = RECORD_PATH + "?page=2&pageSize=1&totalRequired=true&type=TXT"
    if failure == "missing_next":
        next_ = None
    elif failure == "wrong_page":
        next_ = next_.replace("page=2", "page=3")
    elif failure == "changed_filter":
        next_ = next_.replace("type=TXT", "type=A")
    replies.extend(
        [
            collection([record()], totalItems=2, totalPages=2, next_=next_),
            collection(
                []
                if failure == "empty_page"
                else [record("record-id" if failure == "duplicate_id" else "other-id")],
                totalItems=3 if failure == "changed_total" else 2,
                totalPages=3 if failure == "changed_total" else 2,
                next_=next_ if failure == "extra_next" else None,
            ),
        ]
    )
    with pytest.raises(GoDaddyResponseError):
        client.list_records("example.com", type="TXT", page_size=1)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        record(recordId=None),
        record(recordId=""),
        record(ttl=True),
        record(data=123),
        record(type="UNKNOWN"),
        record(priority=-1),
        record(ttl=599),
        record(ttl=86401),
    ],
)
def test_invalid_record_response(godaddy, payload):
    client, replies, _ = godaddy
    replies.append((201, payload))
    with pytest.raises(GoDaddyResponseError):
        client.create_record("example.com", DNSRecord("@", "TXT", "value", 600))


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        domain(domain="bad/name"),
        domain(status=None),
        domain(nameServers=None),
        domain(nameServers=[None]),
    ],
)
def test_invalid_domain_response(godaddy, payload):
    client, replies, _ = godaddy
    replies.append(payload)
    with pytest.raises(GoDaddyResponseError):
        client.get_domain("example.com")


@pytest.mark.parametrize(
    "fields",
    [
        {"ttl": 599},
        {"ttl": 86401},
        {"ttl": True},
        {"name": ""},
        {"type": "BAD"},
        {"data": ""},
        {"data": "a" * 513},
        {"priority": 65536},
        {"flag": 256},
        {"service": ""},
        {"protocol": 123},
        {"record_id": ""},
    ],
)
def test_invalid_record_input(fields):
    with pytest.raises(ValueError, match="fields"):
        DNSRecord(**{"name": "@", "type": "TXT", "data": "value", "ttl": 600, **fields})


@pytest.mark.parametrize(
    "method,kwargs",
    [
        ("list_domains", {"page_size": 0}),
        ("list_domains", {"page_size": 201}),
        ("list_records", {"zone": "example.com", "page_size": 101}),
        ("list_records", {"zone": "example.com", "page_size": True}),
        ("list_records", {"zone": "example.com", "type": "BAD"}),
        ("list_records", {"zone": "example.com", "name": ""}),
        ("delete_record", {"zone": "example.com", "record_id": None}),
        ("delete_record", {"zone": "example.com", "record_id": ""}),
        ("delete_record", {"zone": "example.com", "record_id": ".."}),
        (
            "replace_record",
            {
                "zone": "example.com",
                "record_id": ".",
                "record": DNSRecord("@", "TXT", "value", 600),
            },
        ),
    ],
)
def test_invalid_request_input_does_not_write(godaddy, method, kwargs):
    client, _, requests = godaddy
    with pytest.raises(ValueError):
        getattr(client, method)(**kwargs)
    assert not requests


def test_readonly_record_rejected_without_request(godaddy):
    client, _, requests = godaddy
    with pytest.raises(ValueError, match="read-only"):
        client.create_record("example.com", DNSRecord("@", "SOA", "soa-value", 600))
    assert not requests


def test_second_page_api_failure_is_not_partial_success(godaddy):
    client, replies, _ = godaddy
    replies.extend(
        [
            collection([domain()], next_=DOMAIN_PATH + "?pageSize=1&pageToken=cursor"),
            (403, {"name": "FORBIDDEN"}),
        ]
    )
    with pytest.raises(GoDaddyAPIError, match="FORBIDDEN"):
        client.list_domains(page_size=1)


def test_opaque_record_id_is_encoded_as_one_path_segment(godaddy):
    client, replies, requests = godaddy
    id_ = "record:id+with/=suffix"
    replies.append(httpx2.Response(204))
    client.delete_record("example.com", id_)
    assert requests[0].url.raw_path == (RECORD_PATH + "/record%3Aid%2Bwith%2F%3Dsuffix").encode()


def test_replace_rejects_mismatched_record_identity(godaddy):
    client, replies, _ = godaddy
    replies.append(record("other-id"))
    with pytest.raises(GoDaddyResponseError):
        client.replace_record("example.com", "record-id", DNSRecord("@", "TXT", "value", 600))


def test_domain_read_rejects_mismatched_identity(godaddy):
    client, replies, _ = godaddy
    replies.append(domain("other.example"))
    with pytest.raises(GoDaddyResponseError):
        client.get_domain("example.com")


def test_domain_model_rejects_non_object():
    from certbot_dns_alias.sdk.godaddy import Domain

    with pytest.raises(GoDaddyResponseError):
        Domain.from_payload(None)


def test_duplicate_next_links_are_rejected(godaddy):
    client, replies, requests = godaddy
    next_ = {"rel": "next", "href": DOMAIN_PATH + "?pageSize=1&pageToken=cursor"}
    replies.append(collection([domain()], links=[next_, next_]))
    with pytest.raises(GoDaddyResponseError):
        client.list_domains(page_size=1)
    assert len(requests) == 1


def test_oversized_domain_page_is_rejected(godaddy):
    client, replies, _ = godaddy
    replies.append(collection([domain(), domain("other.com")]))
    with pytest.raises(GoDaddyResponseError):
        client.list_domains(page_size=1)
