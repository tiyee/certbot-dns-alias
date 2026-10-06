from unittest.mock import Mock

import pytest
from certbot import errors
from certbot.plugins.dns_common import CredentialsConfiguration

from certbot_dns_alias.config import build_router, validate_credentials
from certbot_dns_alias.providers.base import DNSProvider, ZoneRouter


def credentials(tmp_path, text):
    path = tmp_path / "credentials.ini"
    path.write_text(text)
    path.chmod(0o600)
    return CredentialsConfiguration(str(path), lambda key: "dns_alias_" + key)


@pytest.mark.parametrize(
    "text",
    [
        "dns_alias_provider=unknown",
        "dns_alias_provider=auto",
        "dns_alias_provider=aliyun\ndns_alias_aliyun_access_key_id=id",
        "dns_alias_provider=tencent\ndns_alias_tencent_secret_id=id\ndns_alias_tencent_secret_key=",
        "dns_alias_provider=tencent, aliyun",
    ],
)
def test_invalid_credentials(tmp_path, text):
    with pytest.raises(errors.PluginError):
        validate_credentials(credentials(tmp_path, text))


def test_mixed_credentials_and_zone_allowlist(tmp_path, monkeypatch):
    aliyun = Mock(spec=DNSProvider)
    aliyun.name = "aliyun"
    tencent = Mock(spec=DNSProvider)
    tencent.name = "tencent"
    ali_factory = Mock(return_value=aliyun)
    ten_factory = Mock(return_value=tencent)
    monkeypatch.setattr("certbot_dns_alias.config.AliyunDNSProvider", ali_factory)
    monkeypatch.setattr("certbot_dns_alias.config.TencentDNSProvider", ten_factory)
    conf = credentials(
        tmp_path,
        """
dns_alias_provider = auto
dns_alias_aliyun_access_key_id = ali-id
dns_alias_aliyun_access_key_secret = ali-secret
dns_alias_aliyun_security_token = ali-session
dns_alias_aliyun_region_id = cn-shanghai
dns_alias_aliyun_zones = Example.co.uk., other.net
dns_alias_tencent_secret_id = ten-id
dns_alias_tencent_secret_key = ten-secret
dns_alias_tencent_token = ten-session
dns_alias_tencent_zones = sub.example.co.uk
""",
    )
    validate_credentials(conf)
    router = build_router(conf)
    assert router.find("host.example.co.uk") == (aliyun, "example.co.uk")
    assert router.find("host.sub.example.co.uk") == (tencent, "sub.example.co.uk")
    assert router.find("other.net") == (aliyun, "other.net")
    aliyun.list_zones.assert_not_called()
    tencent.list_zones.assert_not_called()
    ali_factory.assert_called_once_with(
        "ali-id", "ali-secret", region_id="cn-shanghai", security_token="ali-session"
    )
    ten_factory.assert_called_once_with("ten-id", "ten-secret", token="ten-session")


def test_auto_with_one_provider_and_defaults(tmp_path, monkeypatch):
    factory = Mock()
    monkeypatch.setattr("certbot_dns_alias.config.AliyunDNSProvider", factory)
    conf = credentials(
        tmp_path,
        """
dns_alias_provider=auto
dns_alias_aliyun_access_key_id=id
dns_alias_aliyun_access_key_secret=secret
""",
    )
    validate_credentials(conf)
    router = build_router(conf)
    assert set(router.providers) == {"aliyun"}
    factory.assert_called_once_with("id", "secret", region_id="cn-hangzhou", security_token=None)


@pytest.mark.parametrize("value", ["", "*.example.com", "a..com"])
def test_invalid_explicit_zones(tmp_path, value):
    conf = credentials(
        tmp_path,
        f"""
dns_alias_provider=tencent
dns_alias_tencent_secret_id=id
dns_alias_tencent_secret_key=secret
dns_alias_tencent_zones={value}
""",
    )
    with pytest.raises(errors.PluginError):
        validate_credentials(conf)


def test_discovery_cached_and_label_boundary(provider):
    router = ZoneRouter({"tencent": provider})
    router.find("one.delegate.example.net")
    router.find("two.delegate.example.net")
    provider.list_zones.assert_called_once()
    with pytest.raises(errors.PluginError, match="No managed zone"):
        router.find("notdelegate.example.net")


def test_same_zone_on_two_providers_is_ambiguous(provider):
    other = Mock(spec=DNSProvider)
    other.list_zones.return_value = ["delegate.example.net"]
    router = ZoneRouter({"tencent": provider, "aliyun": other})
    with pytest.raises(errors.PluginError, match="Ambiguous"):
        router.find("host.delegate.example.net")


def test_discovery_failure_cannot_fall_back_to_another_provider(provider):
    other = Mock(spec=DNSProvider)
    other.list_zones.side_effect = [errors.PluginError("permission denied"), ["example.org"]]
    router = ZoneRouter({"tencent": provider, "aliyun": other})
    with pytest.raises(errors.PluginError, match="permission denied"):
        router.find("host.delegate.example.net")
    assert router.find("host.delegate.example.net") == (provider, "delegate.example.net")
    assert other.list_zones.call_count == 2


def test_real_credentials_setup(tmp_path, authenticator):
    path = tmp_path / "tencent.ini"
    path.write_text("""
dns_alias_provider=tencent
dns_alias_tencent_secret_id=fake-id
dns_alias_tencent_secret_key=fake-key
dns_alias_tencent_zones=delegate.example.net
""")
    path.chmod(0o600)
    authenticator.config.dns_alias_credentials = str(path)
    authenticator.config.dns_alias_resolvers = "1.1.1.1, 2606:4700:4700::1111"
    authenticator._setup_credentials()
    assert authenticator.credentials.conf("tencent_secret_id") == "fake-id"
    assert authenticator._resolver.resolver.nameservers == ["1.1.1.1", "2606:4700:4700::1111"]
    assert authenticator._router.find("customer.delegate.example.net")[1] == "delegate.example.net"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("dns_alias_ttl", 0),
        ("dns_alias_cname_max_depth", 0),
        ("dns_alias_dns_retries", -1),
        ("dns_alias_dns_timeout", 0),
        ("dns_alias_dns_timeout", float("nan")),
        ("dns_alias_propagation_seconds", -1),
        ("dns_alias_resolvers", "invalid-ip"),
    ],
)
def test_invalid_cli_options(authenticator, key, value):
    setattr(authenticator.config, key, value)
    with pytest.raises(errors.PluginError):
        authenticator._setup_credentials()
