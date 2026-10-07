"""Validate Certbot INI credentials and construct provider accounts."""

from __future__ import annotations

import re

from certbot import errors
from certbot.plugins.dns_common import CredentialsConfiguration

from certbot_dns_alias.dns import normalize_name
from certbot_dns_alias.providers.aliyun import AliyunDNSProvider
from certbot_dns_alias.providers.base import DNSProvider, ZoneRouter
from certbot_dns_alias.providers.cloudflare import CloudflareDNSProvider
from certbot_dns_alias.providers.tencent import TencentDNSProvider


def setting(credentials: CredentialsConfiguration, key: str, default: str = "") -> str:
    value = credentials.conf(key)
    if value is None:
        return default
    if not isinstance(value, str):
        raise errors.PluginError(f"dns_alias_{key} must be a single value")
    return value.strip()


def provider_names(credentials: CredentialsConfiguration) -> list[str]:
    mode = setting(credentials, "provider").lower()
    if mode not in {"aliyun", "tencent", "cloudflare", "auto"}:
        raise errors.PluginError("dns_alias_provider must be aliyun, tencent, cloudflare, or auto")
    required = {
        "aliyun": {
            "aliyun_access_key_id": "Alibaba Cloud AccessKey ID",
            "aliyun_access_key_secret": "Alibaba Cloud AccessKey secret",
        },
        "tencent": {
            "tencent_secret_id": "Tencent Cloud SecretId",
            "tencent_secret_key": "Tencent Cloud SecretKey",
        },
        "cloudflare": {"cloudflare_api_token": "Cloudflare API Token"},
    }
    names = (
        [mode]
        if mode != "auto"
        else [
            name
            for name, keys in required.items()
            if any(credentials.conf(key) is not None for key in keys)
        ]
    )
    if not names:
        raise errors.PluginError("dns_alias_provider=auto requires at least one provider's keys")
    for name in names:
        credentials.require(required[name])
        for key in required[name]:
            if not setting(credentials, key):
                raise errors.PluginError(f"dns_alias_{key} must not be empty")
    return names


def configured_zones(credentials: CredentialsConfiguration, provider: str) -> list[str] | None:
    value = credentials.conf(f"{provider}_zones")
    if value is None:
        return None
    # ConfigObj parses an unquoted comma-separated INI value as a list.
    parts = value if isinstance(value, list) else value.split(",")
    if not parts or any(not isinstance(part, str) or not part.strip() for part in parts):
        raise errors.PluginError(f"dns_alias_{provider}_zones must contain DNS zone names")
    return [normalize_name(part) for part in parts]


def cloudflare_zone_ids(credentials: CredentialsConfiguration) -> dict[str, str] | None:
    """Parse an optional zone-name:ID allowlist without discovering zones."""
    value = credentials.conf("cloudflare_zone_ids")
    if value is None:
        return None
    parts = value if isinstance(value, list) else value.split(",")
    result = {}
    for part in parts:
        if not isinstance(part, str) or part.count(":") != 1:
            raise errors.PluginError(
                "dns_alias_cloudflare_zone_ids must contain zone-name:ID pairs"
            )
        zone, zone_id = part.split(":")
        zone = normalize_name(zone)
        zone_id = zone_id.strip()
        if not re.fullmatch(r"[0-9a-fA-F]{32}", zone_id) or zone in result:
            raise errors.PluginError(
                "dns_alias_cloudflare_zone_ids has an invalid or duplicate entry"
            )
        result[zone] = zone_id.lower()
    return result


def cloudflare_zones(credentials: CredentialsConfiguration) -> list[str] | None:
    """Require saved IDs when explicit names skip the zone enumeration API."""
    zones = configured_zones(credentials, "cloudflare")
    zone_ids = cloudflare_zone_ids(credentials)
    if zones is not None and (zone_ids is None or any(zone not in zone_ids for zone in zones)):
        raise errors.PluginError(
            "dns_alias_cloudflare_zones requires a dns_alias_cloudflare_zone_ids entry "
            "for each zone"
        )
    return zones if zones is not None else list(zone_ids) if zone_ids is not None else None


def validate_credentials(credentials: CredentialsConfiguration) -> None:
    for name in provider_names(credentials):
        if name == "cloudflare":
            cloudflare_zones(credentials)
        else:
            configured_zones(credentials, name)
        optional = {
            "aliyun": ["aliyun_region_id", "aliyun_security_token"],
            "tencent": ["tencent_token"],
            "cloudflare": [],
        }[name]
        for key in optional:
            setting(credentials, key)


def build_router(credentials: CredentialsConfiguration) -> ZoneRouter:
    providers: dict[str, DNSProvider] = {}
    zones = {}
    for name in provider_names(credentials):
        explicit = (
            cloudflare_zones(credentials)
            if name == "cloudflare"
            else configured_zones(credentials, name)
        )
        if explicit is not None:
            zones[name] = explicit
        if name == "aliyun":
            providers[name] = AliyunDNSProvider(
                setting(credentials, "aliyun_access_key_id"),
                setting(credentials, "aliyun_access_key_secret"),
                region_id=setting(credentials, "aliyun_region_id", "cn-hangzhou"),
                security_token=setting(credentials, "aliyun_security_token") or None,
            )
        elif name == "tencent":
            providers[name] = TencentDNSProvider(
                setting(credentials, "tencent_secret_id"),
                setting(credentials, "tencent_secret_key"),
                token=setting(credentials, "tencent_token") or None,
            )
        else:
            providers[name] = CloudflareDNSProvider(
                setting(credentials, "cloudflare_api_token"),
                zone_ids=cloudflare_zone_ids(credentials),
            )
    return ZoneRouter(providers, zones)
