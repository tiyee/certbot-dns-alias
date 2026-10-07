"""Cloudflare DNS adapter using the official Python SDK and scoped API tokens."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import dns.exception
import dns.rdata
import dns.rdataclass
import dns.rdatatype
import httpx
from certbot import errors
from cloudflare import APIError, APIStatusError, Cloudflare, Omit

from certbot_dns_alias.dns import normalize_name
from certbot_dns_alias.providers.base import DNSProvider, TxtRecord
from certbot_dns_alias.providers.safety import quiet_sdk_call


class CloudflareDNSProvider(DNSProvider):
    name = "cloudflare"
    page_size = 50

    def __init__(self, api_token: str, *, zone_ids: dict[str, str] | None = None) -> None:
        self.client = Cloudflare(
            api_token=api_token,
            # Prevent ambient SDK credentials or endpoint overrides from changing this account.
            default_headers={
                "Authorization": f"Bearer {api_token}",
                "X-Auth-Key": Omit(),
                "X-Auth-Email": Omit(),
                "X-Auth-User-Service-Key": Omit(),
            },
            base_url="https://api.cloudflare.com/client/v4",
            timeout=httpx.Timeout(30, connect=10),
            max_retries=0,
        )
        self._zone_ids = (
            {normalize_name(zone): zone_id for zone, zone_id in zone_ids.items()}
            if zone_ids is not None
            else None
        )

    def _call(self, operation: Callable, *, missing_ok: bool = False, **kwargs: Any):
        try:
            with quiet_sdk_call():
                return operation(**kwargs)
        except APIError as exc:
            codes = [
                item.code
                for item in exc.errors or []
                if isinstance(item.code, int) and not isinstance(item.code, bool)
            ]
            # 81044 specifically identifies an absent DNS record, unlike a missing zone.
            if missing_ok and isinstance(exc, APIStatusError) and 81044 in codes:
                return None
            code = ", ".join(str(code) for code in codes) or (
                str(exc.status_code) if isinstance(exc, APIStatusError) else type(exc).__name__
            )
            # SDK messages and chained exceptions can expose credentials or TXT contents.
            raise errors.PluginError(f"Cloudflare DNS API request failed ({code})") from None

    def _pages(self, operation: Callable, **kwargs: Any) -> Iterator[Any]:
        page_number = 1
        received = 0
        total = None
        while True:
            page = self._call(operation, page=page_number, per_page=self.page_size, **kwargs)
            info = page.result_info
            count = getattr(info, "total_count", None)
            items = page.result
            if (
                info is None
                or info.page != page_number
                or not isinstance(count, int)
                or count < 0
                or (total is not None and total != count)
                or getattr(info, "count", None) != len(items)
                or received + len(items) > count
                or (not items and received < count)
            ):
                raise errors.PluginError("Cloudflare DNS returned an incomplete result list")
            total = count
            received += len(items)
            yield from items
            if received == total:
                return
            page_number += 1

    def list_zones(self) -> list[str]:
        if self._zone_ids is None:
            discovered = {}
            for zone in self._pages(self.client.zones.list):
                name = normalize_name(zone.name)
                if not zone.id or name in discovered:
                    raise errors.PluginError("Cloudflare DNS returned invalid or ambiguous zones")
                discovered[name] = zone.id
            # Keep only a complete discovery, including IDs used during cleanup.
            self._zone_ids = discovered
        return list(self._zone_ids)

    def _zone_id(self, zone: str) -> str:
        self.list_zones()
        zone_id = self._zone_ids.get(normalize_name(zone))
        if not zone_id:
            raise errors.PluginError(f"No Cloudflare zone ID found for {zone}")
        return zone_id

    @staticmethod
    def _fqdn(zone: str, name: str) -> str:
        return normalize_name(zone if name == "@" else f"{name}.{zone}")

    @staticmethod
    def _txt_value(content: str) -> str:
        # Cloudflare can return RFC 1035 quoted strings; join their wire TXT value.
        if content.startswith('"'):
            try:
                record = dns.rdata.from_text(dns.rdataclass.IN, dns.rdatatype.TXT, content)
                return b"".join(record.strings).decode("utf-8")
            except (dns.exception.DNSException, UnicodeError):
                raise errors.PluginError("Cloudflare DNS returned invalid TXT content") from None
        return content

    def list_txt_records(self, zone: str, name: str) -> list[TxtRecord]:
        fqdn = self._fqdn(zone, name)
        records = []
        seen = set()
        for item in self._pages(
            self.client.dns.records.list,
            zone_id=self._zone_id(zone),
            type="TXT",
            name={"exact": fqdn},
        ):
            if not item.id or item.id in seen:
                raise errors.PluginError("Cloudflare DNS returned an incomplete TXT record list")
            seen.add(item.id)
            if (
                item.type == "TXT"
                and normalize_name(item.name) == fqdn
                and not getattr(item, "private_routing", False)
            ):
                if not isinstance(item.content, str):
                    raise errors.PluginError("Cloudflare DNS returned invalid TXT content")
                records.append(TxtRecord(item.id, name, self._txt_value(item.content)))
        return records

    def create_txt_record(self, zone: str, name: str, value: str, ttl: int) -> str:
        record = self._call(
            self.client.dns.records.create,
            zone_id=self._zone_id(zone),
            type="TXT",
            name=self._fqdn(zone, name),
            content=value,
            ttl=ttl,
        )
        if not record or not getattr(record, "id", None):
            raise errors.PluginError("Cloudflare DNS did not return a new TXT record ID")
        return record.id

    def delete_txt_record(self, zone: str, record_id: str) -> None:
        self._call(
            self.client.dns.records.delete,
            zone_id=self._zone_id(zone),
            dns_record_id=record_id,
            missing_ok=True,
        )
