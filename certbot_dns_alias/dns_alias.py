"""Certbot DNS authenticator with CNAME delegation to Aliyun and DNSPod."""

import ipaddress
import logging
import math
from dataclasses import dataclass
from typing import Any

import dns.resolver
from certbot import errors
from certbot.plugins import dns_common

from certbot_dns_alias.config import build_router, validate_credentials
from certbot_dns_alias.dns import CnameResolver, normalize_name, relative_name
from certbot_dns_alias.providers.base import DNSProvider, ZoneRouter

logger = logging.getLogger(__name__)


@dataclass
class _RecordLease:
    provider: DNSProvider
    zone: str
    target: str
    record_id: str
    created: bool
    users: int = 1


class Authenticator(dns_common.DNSAuthenticator):
    """Place each ACME TXT value in its final CNAME target's managed zone."""

    description = "DNS-01 with CNAME delegation to Alibaba Cloud DNS or Tencent Cloud DNSPod."
    ttl = 600

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.credentials: dns_common.CredentialsConfiguration | None = None
        self._router: ZoneRouter | None = None
        self._resolver: CnameResolver | None = None
        self._challenges: dict[tuple[str, str], list[tuple[str, str, str, str]]] = {}
        self._leases: dict[tuple[str, str, str, str], _RecordLease] = {}

    @classmethod
    def add_parser_arguments(cls, add, default_propagation_seconds: int = 60) -> None:
        super().add_parser_arguments(add, default_propagation_seconds)
        add("credentials", help="Path to the dns-alias provider credentials INI file.")
        add("ttl", type=int, default=600, help="TXT record TTL in seconds (default: 600).")
        add("cname-max-depth", type=int, default=8, help="Maximum CNAME links (default: 8).")
        add("dns-timeout", type=float, default=10, help="DNS query lifetime (default: 10 seconds).")
        add("dns-retries", type=int, default=2, help="Retries per failed DNS query (default: 2).")
        add("resolvers", help="Comma-separated DNS resolver IPv4/IPv6 addresses.")
        add(
            "require-cname",
            action="store_true",
            default=False,
            help="Fail if the original challenge name has no CNAME delegation.",
        )

    def more_info(self) -> str:
        return (
            "Follows _acme-challenge CNAME chains and manages individual TXT records "
            "in Alibaba Cloud DNS or Tencent Cloud DNSPod. Select aliyun, tencent, or auto "
            "in the credentials file. Cleanup uses saved record IDs and delegation targets."
        )

    def _setup_credentials(self) -> None:
        limits = {"ttl": 1, "cname-max-depth": 1, "dns-retries": 0}
        for option, minimum in limits.items():
            value = self.conf(option)
            if value is None or value < minimum:
                raise errors.PluginError(f"--dns-alias-{option} must be at least {minimum}")
        timeout = self.conf("dns-timeout")
        if timeout is None or not math.isfinite(timeout) or timeout <= 0:
            raise errors.PluginError("--dns-alias-dns-timeout must be a finite positive number")
        propagation = self.conf("propagation-seconds")
        if propagation is None or propagation < 0:
            raise errors.PluginError("--dns-alias-propagation-seconds must be non-negative")

        resolver = dns.resolver.Resolver()
        if self.conf("resolvers"):
            try:
                resolver.nameservers = [
                    str(ipaddress.ip_address(address.strip()))
                    for address in self.conf("resolvers").split(",")
                ]
            except ValueError as exc:
                raise errors.PluginError(
                    "--dns-alias-resolvers must be comma-separated IPv4/IPv6 addresses"
                ) from exc
        self._resolver = CnameResolver(
            resolver,
            max_depth=self.conf("cname-max-depth"),
            timeout=timeout,
            retries=self.conf("dns-retries"),
        )
        self.credentials = self._configure_credentials(
            "credentials",
            "DNS alias credentials INI file",
            None,
            validate_credentials,
        )
        self._router = build_router(self.credentials)
        self.ttl = self.conf("ttl")

    def _perform(self, domain: str, validation_name: str, validation: str) -> None:
        if self._resolver is None or self._router is None:
            raise errors.PluginError("DNS alias credentials have not been configured")
        challenge = (normalize_name(validation_name), validation)
        target = self._resolver.resolve(
            validation_name,
            require_cname=self.conf("require-cname"),
        )
        provider, zone = self._router.find(target)
        record_key = (provider.name, zone, target, validation)
        if record_key in self._leases:
            self._leases[record_key].users += 1
        else:
            host = relative_name(target, zone)
            records = provider.list_txt_records(zone, host)
            existing = next((record for record in records if record.value == validation), None)
            record_id = (
                existing.id
                if existing
                else provider.create_txt_record(
                    zone,
                    host,
                    validation,
                    self.ttl,
                )
            )
            self._leases[record_key] = _RecordLease(
                provider,
                zone,
                target,
                record_id,
                created=existing is None,
            )
            logger.info(
                "TXT challenge ready: %s -> %s (%s)", validation_name, target, provider.name
            )
        # Keep no cleanup state for a write that failed. Keys include the TXT
        # value because apex and wildcard challenges share validation names.
        self._challenges.setdefault(challenge, []).append(record_key)

    def _cleanup(self, domain: str, validation_name: str, validation: str) -> None:
        challenge = (normalize_name(validation_name), validation)
        keys = self._challenges.get(challenge)
        if not keys:
            return
        record_key = keys[-1]
        lease = self._leases[record_key]
        if lease.users == 1 and lease.created:
            try:
                lease.provider.delete_txt_record(lease.zone, lease.record_id)
            except errors.PluginError as exc:
                # Certbot continues cleaning other challenges. Keep our saved
                # ID so a subsequent cleanup call can retry this deletion.
                logger.warning(
                    "Unable to clean TXT record %s (%s): %s", lease.target, lease.provider.name, exc
                )
                return
            logger.info("Cleaned TXT challenge at %s (%s)", lease.target, lease.provider.name)
        lease.users -= 1
        keys.pop()
        if not keys:
            del self._challenges[challenge]
        if lease.users == 0:
            del self._leases[record_key]
