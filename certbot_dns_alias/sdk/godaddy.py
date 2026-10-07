"""Synchronous GoDaddy Domains v3 DNS client using HTTPX2.

Reference: https://developer.godaddy.com/openapi/domains-v3.json
This client uses a Personal Access Token, not legacy sso-key credentials.
List operations return complete collections or raise; writes are never retried.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import parse_qs, quote, urljoin, urlsplit

import httpx2
from certbot import errors

from certbot_dns_alias.dns import normalize_name

RECORD_TYPES = frozenset({"A", "AAAA", "CNAME", "MX", "TXT", "SRV", "NS", "SOA", "CAA"})


class GoDaddyError(errors.PluginError):
    """Base SDK failure whose message is safe to display through Certbot."""


class GoDaddyAPIError(GoDaddyError):
    """HTTP failure exposing only the status and sanitized API error code."""

    def __init__(self, status_code: int, code: str) -> None:
        self.status_code = status_code
        self.code = code
        super().__init__(f"GoDaddy API request failed ({status_code}, {code})")


class GoDaddyTransportError(GoDaddyError):
    """Network failure without the sensitive request or original exception."""


class GoDaddyResponseError(GoDaddyError):
    """Malformed response or incomplete pagination; never a partial success."""

    def __init__(self) -> None:
        super().__init__("GoDaddy API returned an invalid or incomplete response")


def _integer(value: Any, minimum: int, maximum: int) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _text(value: Any, maximum: int = 255) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= maximum


def _record_id(value: Any) -> bool:
    return _text(value, 1024) and value not in {".", ".."}


@dataclass(frozen=True)
class DNSRecord:
    """A relative DNS record; record_id is assigned by GoDaddy on creation."""

    name: str
    type: str
    data: str = field(repr=False)
    ttl: int
    record_id: str | None = None
    priority: int | None = None
    service: str | None = None
    port: int | None = None
    weight: int | None = None
    protocol: str | None = None
    flag: int | None = None
    tag: str | None = None

    def __post_init__(self) -> None:
        if (
            not _text(self.name)
            or not isinstance(self.type, str)
            or self.type not in RECORD_TYPES
            or not _text(self.data, 512)
            or not _integer(self.ttl, 600, 86400)
            or (self.record_id is not None and not _record_id(self.record_id))
            or any(
                value is not None and not _integer(value, 0, 65535)
                for value in (self.priority, self.port, self.weight)
            )
            or (self.flag is not None and not _integer(self.flag, 0, 255))
            or any(
                value is not None and not _text(value)
                for value in (self.service, self.protocol, self.tag)
            )
        ):
            raise ValueError("Invalid GoDaddy DNS record fields")

    def to_payload(self) -> dict[str, Any]:
        """Serialize writable fields only, preserving the exact record value."""
        return {
            key: value
            for key, value in asdict(self).items()
            if key != "record_id" and value is not None
        }

    @classmethod
    def from_payload(cls, payload: Any) -> DNSRecord:
        """Require a usable server-assigned ID; ignore future response fields."""
        if not isinstance(payload, dict) or not _record_id(payload.get("recordId")):
            raise GoDaddyResponseError()
        try:
            return cls(
                name=payload["name"],
                type=payload["type"],
                data=payload["data"],
                ttl=payload["ttl"],
                record_id=payload["recordId"],
                **{
                    key: payload[key]
                    for key in ("priority", "service", "port", "weight", "protocol", "flag", "tag")
                    if key in payload
                },
            )
        except (KeyError, TypeError, ValueError):
            raise GoDaddyResponseError() from None


@dataclass(frozen=True)
class Domain:
    """Domain identity, lifecycle status, and current authoritative nameservers.

    Registered domains are not proof of GoDaddy DNS hosting. A provider must
    verify DNS access separately or use an explicitly configured zone list.
    """

    domain: str
    status: str
    name_servers: tuple[str, ...]

    @classmethod
    def from_payload(cls, payload: Any) -> Domain:
        if not isinstance(payload, dict):
            raise GoDaddyResponseError()
        servers = payload.get("nameServers", [])
        if (
            not _text(payload.get("domain"))
            or not _text(payload.get("status"))
            or not isinstance(servers, list)
            or any(not _text(server) for server in servers)
        ):
            raise GoDaddyResponseError()
        try:
            domain = normalize_name(payload["domain"])
        except errors.PluginError:
            raise GoDaddyResponseError() from None
        return cls(domain=domain, status=payload["status"], name_servers=tuple(servers))


class GoDaddyClient:
    """Own a bounded HTTP session for GoDaddy domain reads and individual DNS CRUD.

    Use as a context manager or call close(). The token needs domains.domain:read
    and domains.dns:update scopes. OTE credentials and production credentials are
    separate; no environment variables can override the endpoint or authentication.
    The optional transport is an HTTPX2 test boundary and must not retry writes.
    """

    def __init__(
        self,
        token: str,
        *,
        ote: bool = False,
        transport: httpx2.BaseTransport | None = None,
    ) -> None:
        if not isinstance(token, str) or not token or not all("!" <= c <= "~" for c in token):
            raise ValueError("A nonempty GoDaddy Personal Access Token is required")
        if not isinstance(ote, bool):
            raise ValueError("ote must be a boolean")
        self._http = httpx2.Client(
            base_url="https://api.ote-godaddy.com" if ote else "https://api.godaddy.com",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=httpx2.Timeout(30, connect=10),
            transport=(
                transport
                if transport is not None
                else httpx2.HTTPTransport(retries=0, trust_env=False)
            ),
            follow_redirects=False,
            trust_env=False,
        )

    def __enter__(self) -> GoDaddyClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def close(self) -> None:
        """Close this client's transport and connection pool."""
        self._http.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        expected_status: int = 200,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        try:
            response = self._http.request(method, path, params=params, json=body)
        except httpx2.RequestError as exc:
            raise GoDaddyTransportError(
                f"GoDaddy API transport failed ({type(exc).__name__})"
            ) from None
        if response.status_code != expected_status:
            code = "HTTP_ERROR"
            try:
                payload = response.json()
                # v3 uses name; accept the legacy code envelope as well.
                candidate = payload.get("name", payload.get("code"))
                if isinstance(candidate, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", candidate):
                    code = candidate
            except (ValueError, AttributeError):
                pass
            raise GoDaddyAPIError(response.status_code, code) from None
        if expected_status == 204:
            return None
        try:
            payload = response.json()
        except ValueError:
            raise GoDaddyResponseError() from None
        if not isinstance(payload, dict):
            raise GoDaddyResponseError()
        return payload

    def _next_params(
        self, payload: dict[str, Any], path: str, params: dict[str, Any]
    ) -> dict[str, str] | None:
        """Validate navigation links without sending credentials to their URLs."""
        links = payload.get("links")
        if not isinstance(links, list) or any(
            not isinstance(link, dict)
            or not _text(link.get("rel"))
            or not _text(link.get("href"), 8192)
            for link in links
        ):
            raise GoDaddyResponseError()
        following = [link["href"] for link in links if link["rel"] == "next"]
        if not following:
            return None
        if len(following) != 1:
            raise GoDaddyResponseError()
        base = str(self._http.base_url)
        try:
            url = urlsplit(urljoin(base.rstrip("/") + path, following[0]))
            query = parse_qs(url.query, keep_blank_values=True, strict_parsing=True)
        except ValueError:
            raise GoDaddyResponseError() from None
        if (
            url.scheme != "https"
            or url.netloc != urlsplit(base).netloc
            or url.path != path
            or url.fragment
            or any(len(values) != 1 or not values[0] for values in query.values())
        ):
            raise GoDaddyResponseError()
        result = {key: values[0] for key, values in query.items()}
        # Only pagination parameters may differ; filters must be preserved exactly.
        variable = (
            {"pageToken", "pageTokenDirection"} if path.endswith("/domain-names") else {"page"}
        )
        fixed = {key: str(value) for key, value in params.items() if key not in variable}
        if {key: value for key, value in result.items() if key not in variable} != fixed:
            raise GoDaddyResponseError()
        return result

    @staticmethod
    def _items(payload: dict[str, Any]) -> list[Any]:
        items = payload.get("items")
        if not isinstance(items, list):
            raise GoDaddyResponseError()
        return items

    def list_domains(self, *, page_size: int = 100) -> list[Domain]:
        """List registered domains through every cursor page, without caching."""
        if not _integer(page_size, 1, 200):
            raise ValueError("GoDaddy domain page_size must be between 1 and 200")
        path = "/v3/domains/domain-names"
        params: dict[str, Any] = {"pageSize": page_size}
        domains: list[Domain] = []
        seen_domains: set[str] = set()
        seen_tokens: set[str] = set()
        while True:
            payload = self._request("GET", path, params=params)
            items = self._items(payload)
            if len(items) > page_size:
                raise GoDaddyResponseError()
            for item in items:
                domain = Domain.from_payload(item)
                if domain.domain in seen_domains:
                    raise GoDaddyResponseError()
                seen_domains.add(domain.domain)
                domains.append(domain)
            following = self._next_params(payload, path, params)
            if following is None:
                return domains
            token = following.get("pageToken")
            if (
                not items
                or not token
                or token in seen_tokens
                or following.get("pageTokenDirection", "forward") != "forward"
            ):
                raise GoDaddyResponseError()
            seen_tokens.add(token)
            params = following

    def get_domain(self, domain: str) -> Domain:
        """Read a registered domain, with Unicode and absolute names normalized."""
        domain = normalize_name(domain)
        result = Domain.from_payload(self._request("GET", f"/v3/domains/domain-names/{domain}"))
        if result.domain != domain:
            raise GoDaddyResponseError()
        return result

    @staticmethod
    def _records_path(zone: str) -> str:
        return f"/v3/domains/zones/{normalize_name(zone)}/dns-records"

    @classmethod
    def _record_path(cls, zone: str, record_id: str) -> str:
        # IDs are opaque path segments, not caller-supplied URLs or path fragments.
        if not _record_id(record_id):
            raise ValueError("Invalid GoDaddy DNS record ID")
        return f"{cls._records_path(zone)}/{quote(record_id, safe='')}"

    def list_records(
        self,
        zone: str,
        *,
        type: str | None = None,
        name: str | None = None,
        page_size: int = 100,
    ) -> list[DNSRecord]:
        """Read all record pages with optional API type and relative-name filters."""
        if not _integer(page_size, 1, 100):
            raise ValueError("GoDaddy record page_size must be between 1 and 100")
        if type is not None and (not isinstance(type, str) or type not in RECORD_TYPES):
            raise ValueError("Invalid GoDaddy DNS record type")
        if name is not None and not _text(name):
            raise ValueError("Invalid GoDaddy DNS record name")
        path = self._records_path(zone)
        params: dict[str, Any] = {"page": 1, "pageSize": page_size, "totalRequired": "true"}
        if type is not None:
            params["type"] = type
        if name is not None:
            params["name"] = name
        records: list[DNSRecord] = []
        seen_ids: set[str] = set()
        totals: tuple[int, int] | None = None
        page = 1
        while True:
            payload = self._request("GET", path, params=params)
            items = self._items(payload)
            following = self._next_params(payload, path, params)
            count, pages = payload.get("totalItems"), payload.get("totalPages")
            # The API omits both totals for an empty result set.
            if not items and not records and count is None and pages is None and following is None:
                return []
            if (
                not _integer(count, 1, 2**63 - 1)
                or not _integer(pages, 1, 2**63 - 1)
                or pages != (count + page_size - 1) // page_size
                or (totals is not None and totals != (count, pages))
                or len(items) != min(page_size, count - len(records))
                or page > pages
            ):
                raise GoDaddyResponseError()
            totals = count, pages
            for item in items:
                record = DNSRecord.from_payload(item)
                if record.record_id in seen_ids:
                    raise GoDaddyResponseError()
                seen_ids.add(record.record_id)
                records.append(record)
            if page == pages:
                if following is not None or len(records) != count:
                    raise GoDaddyResponseError()
                return records
            if following is None or following.get("page") != str(page + 1):
                raise GoDaddyResponseError()
            params = following
            page += 1

    def create_record(self, zone: str, record: DNSRecord) -> DNSRecord:
        """Create one value and return its saved ID; never replace an RRset."""
        return self._write_record("POST", self._records_path(zone), record, 201)

    def replace_record(self, zone: str, record_id: str, record: DNSRecord) -> DNSRecord:
        """Replace only the selected record, including all of its writable fields."""
        result = self._write_record("PUT", self._record_path(zone, record_id), record, 200)
        if result.record_id != record_id:
            raise GoDaddyResponseError()
        return result

    def _write_record(self, method: str, path: str, record: DNSRecord, status: int) -> DNSRecord:
        if record.type == "SOA":
            raise ValueError("GoDaddy SOA records are read-only")
        return DNSRecord.from_payload(
            self._request(method, path, expected_status=status, body=record.to_payload())
        )

    def delete_record(self, zone: str, record_id: str) -> None:
        """Delete one saved ID. A 404 remains an error for the provider to classify."""
        self._request("DELETE", self._record_path(zone, record_id), expected_status=204)
