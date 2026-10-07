"""Shared provider contract and managed-zone selection."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from certbot import errors

from certbot_dns_alias.dns import normalize_name


@dataclass(frozen=True)
class TxtRecord:
    id: str
    name: str
    value: str


class DNSProvider(ABC):
    """Manage individual TXT records without replacing a TXT RRset."""

    name: str

    @abstractmethod
    def list_zones(self) -> list[str]:
        """List all managed DNS zones, including all result pages."""

    @abstractmethod
    def list_txt_records(self, zone: str, name: str) -> list[TxtRecord]:
        """List TXT records at an exact relative name, including all pages."""

    @abstractmethod
    def create_txt_record(self, zone: str, name: str, value: str, ttl: int) -> str:
        """Create one TXT value and return its record ID."""

    @abstractmethod
    def delete_txt_record(self, zone: str, record_id: str) -> None:
        """Delete the saved record ID. An already absent record is success."""


class ZoneRouter:
    """Select the longest matching zone across configured provider accounts."""

    def __init__(
        self,
        providers: dict[str, DNSProvider],
        zones: dict[str, list[str]] | None = None,
    ) -> None:
        self.providers = providers
        self.zones = zones or {}
        self._loaded_zones: dict[str, list[str]] | None = None

    def find(self, fqdn: str) -> tuple[DNSProvider, str]:
        fqdn = normalize_name(fqdn)
        if self._loaded_zones is None:
            # Cache only a complete discovery; API/permission errors must not
            # silently route to a less specific zone on another provider.
            discovered = {}
            for name, provider in self.providers.items():
                candidates = self.zones[name] if name in self.zones else provider.list_zones()
                discovered[name] = sorted({normalize_name(zone) for zone in candidates})
            self._loaded_zones = discovered

        matches = [
            (zone.count("."), name, zone)
            for name, zones in self._loaded_zones.items()
            for zone in zones
            if fqdn == zone or fqdn.endswith("." + zone)
        ]
        if not matches:
            raise errors.PluginError(
                f"No managed zone found for {fqdn}; check credentials and configured zones"
            )
        best_depth = max(item[0] for item in matches)
        best = [item for item in matches if item[0] == best_depth]
        if len(best) != 1:
            raise errors.PluginError(
                f"Ambiguous managed zone for {fqdn} on multiple providers; "
                "set provider-specific zones or select a single provider"
            )
        _, provider_name, zone = best[0]
        return self.providers[provider_name], zone
