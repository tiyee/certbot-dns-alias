"""GoDaddy DNS adapter using the bundled Domains v3 PAT client."""

from __future__ import annotations

from certbot import errors

from certbot_dns_alias.dns import normalize_name
from certbot_dns_alias.providers.base import DNSProvider, TxtRecord
from certbot_dns_alias.sdk.godaddy import DNSRecord, GoDaddyAPIError, GoDaddyClient


class GoDaddyDNSProvider(DNSProvider):
    name = "godaddy"
    page_size = 100

    def __init__(self, api_token: str, *, ote: bool = False) -> None:
        try:
            self.client = GoDaddyClient(api_token, ote=ote)
        except ValueError:
            raise errors.PluginError(
                "Invalid GoDaddy Personal Access Token or environment"
            ) from None
        self._zones: list[str] | None = None

    @staticmethod
    def _name(name: str) -> str:
        return "@" if name == "@" else normalize_name(name)

    def list_zones(self) -> list[str]:
        if self._zones is None:
            discovered = []
            for domain in self.client.list_domains(page_size=self.page_size):
                # Registration alone does not establish DNS hosting. Require an
                # accessible zone with an apex SOA; never cache partial discovery.
                records = self.client.list_records(
                    domain.domain, type="SOA", name="@", page_size=self.page_size
                )
                if any(record.type == "SOA" and record.name == "@" for record in records):
                    discovered.append(domain.domain)
            self._zones = discovered
        return list(self._zones)

    def list_txt_records(self, zone: str, name: str) -> list[TxtRecord]:
        name = self._name(name)
        return [
            TxtRecord(record.record_id, name, record.data)
            for record in self.client.list_records(
                zone, type="TXT", name=name, page_size=self.page_size
            )
            if record.type == "TXT" and self._name(record.name) == name
        ]

    def create_txt_record(self, zone: str, name: str, value: str, ttl: int) -> str:
        try:
            record = DNSRecord(self._name(name), "TXT", value, ttl)
        except ValueError:
            raise errors.PluginError(
                "Invalid GoDaddy TXT fields; TTL must be between 600 and 86400 seconds"
            ) from None
        created = self.client.create_record(zone, record)
        if (
            created.type != record.type
            or self._name(created.name) != record.name
            or created.data != record.data
        ):
            raise errors.PluginError("GoDaddy DNS returned a mismatched TXT record")
        return created.record_id

    def delete_txt_record(self, zone: str, record_id: str) -> None:
        try:
            self.client.delete_record(zone, record_id)
        except GoDaddyAPIError as exc:
            if exc.status_code != 404:
                raise
            # A 404 can also mean a missing/inaccessible zone. Verify access and
            # absence by saved ID, without resolving delegation or guessing names.
            records = self.client.list_records(zone, type="TXT", page_size=self.page_size)
            if any(record.record_id == record_id for record in records):
                raise
