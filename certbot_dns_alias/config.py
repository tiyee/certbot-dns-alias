"""Validate Certbot INI credentials and construct provider accounts."""

from __future__ import annotations

from certbot import errors
from certbot.plugins.dns_common import CredentialsConfiguration

from certbot_dns_alias.dns import normalize_name
from certbot_dns_alias.providers.aliyun import AliyunDNSProvider
from certbot_dns_alias.providers.base import DNSProvider, ZoneRouter
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
    if mode not in {"aliyun", "tencent", "auto"}:
        raise errors.PluginError("dns_alias_provider must be aliyun, tencent, or auto")
    required = {
        "aliyun": {
            "aliyun_access_key_id": "Alibaba Cloud AccessKey ID",
            "aliyun_access_key_secret": "Alibaba Cloud AccessKey secret",
        },
        "tencent": {
            "tencent_secret_id": "Tencent Cloud SecretId",
            "tencent_secret_key": "Tencent Cloud SecretKey",
        },
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


def validate_credentials(credentials: CredentialsConfiguration) -> None:
    for name in provider_names(credentials):
        configured_zones(credentials, name)
        optional = (
            ["aliyun_region_id", "aliyun_security_token"] if name == "aliyun" else ["tencent_token"]
        )
        for key in optional:
            setting(credentials, key)


def build_router(credentials: CredentialsConfiguration) -> ZoneRouter:
    providers: dict[str, DNSProvider] = {}
    zones = {}
    for name in provider_names(credentials):
        explicit = configured_zones(credentials, name)
        if explicit is not None:
            zones[name] = explicit
        if name == "aliyun":
            providers[name] = AliyunDNSProvider(
                setting(credentials, "aliyun_access_key_id"),
                setting(credentials, "aliyun_access_key_secret"),
                region_id=setting(credentials, "aliyun_region_id", "cn-hangzhou"),
                security_token=setting(credentials, "aliyun_security_token") or None,
            )
        else:
            providers[name] = TencentDNSProvider(
                setting(credentials, "tencent_secret_id"),
                setting(credentials, "tencent_secret_key"),
                token=setting(credentials, "tencent_token") or None,
            )
    return ZoneRouter(providers, zones)
