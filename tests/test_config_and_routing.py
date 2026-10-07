from io import StringIO
from unittest.mock import Mock

import dns.resolver
import pytest
from certbot import errors
from certbot.compat import filesystem
from certbot.plugins.dns_common import CredentialsConfiguration

from certbot_dns_alias.config import build_router, validate_credentials
from certbot_dns_alias.providers.base import DNSProvider, ZoneRouter


def credentials(tmp_path, text):
    path = tmp_path / "credentials.ini"
    path.write_text(text)
    filesystem.chmod(str(path), 0o600)
    return CredentialsConfiguration(str(path), lambda key: "dns_alias_" + key)


@pytest.mark.parametrize(
    "text",
    [
        "dns_alias_provider=unknown",
        "dns_alias_provider=auto",
        "dns_alias_provider=aliyun\ndns_alias_aliyun_access_key_id=id",
        "dns_alias_provider=tencent\ndns_alias_tencent_secret_id=id\ndns_alias_tencent_secret_key=",
        "dns_alias_provider=tencent, aliyun",
        "dns_alias_provider=cloudflare",
        "dns_alias_provider=cloudflare\ndns_alias_cloudflare_api_token=",
        "dns_alias_provider=cloudflare\ndns_alias_cloudflare_api_token=one, two",
        "dns_alias_provider=auto\ndns_alias_cloudflare_api_token=",
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
    cloudflare = Mock(spec=DNSProvider)
    cloudflare.name = "cloudflare"
    ali_factory = Mock(return_value=aliyun)
    ten_factory = Mock(return_value=tencent)
    cf_factory = Mock(return_value=cloudflare)
    monkeypatch.setattr("certbot_dns_alias.config.AliyunDNSProvider", ali_factory)
    monkeypatch.setattr("certbot_dns_alias.config.TencentDNSProvider", ten_factory)
    monkeypatch.setattr("certbot_dns_alias.config.CloudflareDNSProvider", cf_factory)
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
dns_alias_cloudflare_api_token = cf-token
dns_alias_cloudflare_zone_ids = cloudflare.example.net:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
""",
    )
    validate_credentials(conf)
    router = build_router(conf)
    assert router.find("host.example.co.uk") == (aliyun, "example.co.uk")
    assert router.find("host.sub.example.co.uk") == (tencent, "sub.example.co.uk")
    assert router.find("other.net") == (aliyun, "other.net")
    assert router.find("host.cloudflare.example.net") == (cloudflare, "cloudflare.example.net")
    aliyun.list_zones.assert_not_called()
    tencent.list_zones.assert_not_called()
    cloudflare.list_zones.assert_not_called()
    ali_factory.assert_called_once_with(
        "ali-id", "ali-secret", region_id="cn-shanghai", security_token="ali-session"
    )
    ten_factory.assert_called_once_with("ten-id", "ten-secret", token="ten-session")
    cf_factory.assert_called_once_with("cf-token", zone_ids={"cloudflare.example.net": "a" * 32})


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
    filesystem.chmod(str(path), 0o600)
    authenticator.config.dns_alias_credentials = str(path)
    authenticator.config.dns_alias_resolvers = "1.1.1.1, 2606:4700:4700::1111"
    authenticator._setup_credentials()
    assert authenticator.credentials.conf("tencent_secret_id") == "fake-id"
    assert authenticator._resolver.resolver.nameservers == ["1.1.1.1", "2606:4700:4700::1111"]
    assert authenticator._router.find("customer.delegate.example.net")[1] == "delegate.example.net"


def configure_test_credentials(tmp_path, authenticator):
    conf = credentials(
        tmp_path,
        "dns_alias_provider=tencent\ndns_alias_tencent_secret_id=fake-id\n"
        "dns_alias_tencent_secret_key=fake-key\ndns_alias_tencent_zones=delegate.example.net",
    )
    authenticator.config.dns_alias_credentials = conf.confobj.filename


def test_explicit_resolvers_do_not_read_system_configuration(tmp_path, authenticator, monkeypatch):
    configure_test_credentials(tmp_path, authenticator)
    read = Mock(side_effect=dns.resolver.NoResolverConfiguration())
    monkeypatch.setattr(dns.resolver.Resolver, "read_resolv_conf", read)
    authenticator.config.dns_alias_resolvers = "1.1.1.1, 2606:4700:4700::1111"
    authenticator._setup_credentials()
    read.assert_not_called()
    assert authenticator._resolver.resolver.nameservers == ["1.1.1.1", "2606:4700:4700::1111"]


def test_default_resolvers_still_read_system_configuration(tmp_path, authenticator, monkeypatch):
    configure_test_credentials(tmp_path, authenticator)
    calls = []

    def read(resolver, filename):
        calls.append(filename)
        resolver.nameservers = ["9.9.9.9"]

    monkeypatch.setattr(dns.resolver.Resolver, "read_resolv_conf", read)
    authenticator._setup_credentials()
    assert len(calls) == 1
    assert authenticator._resolver.resolver.nameservers == ["9.9.9.9"]


@pytest.mark.parametrize(
    "exception", [dns.resolver.NoResolverConfiguration(), PermissionError(), ValueError()]
)
def test_resolver_initialization_failures_are_actionable(authenticator, monkeypatch, exception):
    monkeypatch.setattr(dns.resolver.Resolver, "read_resolv_conf", Mock(side_effect=exception))
    with pytest.raises(
        errors.PluginError, match="check system DNS.*--dns-alias-resolvers"
    ) as caught:
        authenticator._setup_credentials()
    assert caught.value.__suppress_context__


def test_malformed_system_nameserver_becomes_plugin_error(authenticator, monkeypatch):
    read = dns.resolver.Resolver.read_resolv_conf

    def malformed_config(resolver, filename):
        read(resolver, StringIO("nameserver invalid-ip\n"))

    monkeypatch.setattr(dns.resolver.Resolver, "read_resolv_conf", malformed_config)
    with pytest.raises(errors.PluginError, match="check system DNS.*--dns-alias-resolvers"):
        authenticator._setup_credentials()


@pytest.mark.parametrize("addresses", ["", "invalid-ip", "1.1.1.1,", " , "])
def test_invalid_explicit_resolvers_are_rejected_before_system_discovery(
    authenticator, monkeypatch, addresses
):
    read = Mock(side_effect=dns.resolver.NoResolverConfiguration())
    monkeypatch.setattr(dns.resolver.Resolver, "read_resolv_conf", read)
    authenticator.config.dns_alias_resolvers = addresses
    with pytest.raises(errors.PluginError, match="comma-separated IPv4/IPv6"):
        authenticator._setup_credentials()
    read.assert_not_called()


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


@pytest.mark.parametrize("mode", ["cloudflare", "auto"])
def test_cloudflare_token_only_discovers_zones(tmp_path, monkeypatch, mode):
    provider = Mock(spec=DNSProvider)
    provider.list_zones.return_value = ["example.com"]
    factory = Mock(return_value=provider)
    monkeypatch.setattr("certbot_dns_alias.config.CloudflareDNSProvider", factory)
    conf = credentials(tmp_path, f"dns_alias_provider={mode}\ndns_alias_cloudflare_api_token=token")
    validate_credentials(conf)
    router = build_router(conf)
    assert set(router.providers) == {"cloudflare"}
    assert router.find("host.example.com") == (provider, "example.com")
    provider.list_zones.assert_called_once()
    factory.assert_called_once_with("token", zone_ids=None)


@pytest.mark.parametrize("explicit_zones", ["", "dns_alias_cloudflare_zones=example.com"])
def test_cloudflare_explicit_ids_skip_discovery_and_restrict_zones(
    tmp_path, monkeypatch, explicit_zones
):
    factory = Mock()
    monkeypatch.setattr("certbot_dns_alias.config.CloudflareDNSProvider", factory)
    conf = credentials(
        tmp_path,
        "dns_alias_provider=cloudflare\ndns_alias_cloudflare_api_token=token\n"
        "dns_alias_cloudflare_zone_ids=Example.COM.:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n"
        + explicit_zones,
    )
    validate_credentials(conf)
    router = build_router(conf)
    assert router.find("host.example.com") == (factory.return_value, "example.com")
    factory.return_value.list_zones.assert_not_called()
    factory.assert_called_once_with("token", zone_ids={"example.com": "a" * 32})
    with pytest.raises(errors.PluginError, match="No managed zone"):
        router.find("host.other.com")


@pytest.mark.parametrize(
    "settings",
    [
        "dns_alias_cloudflare_zones=example.com",
        "dns_alias_cloudflare_zone_ids=",
        "dns_alias_cloudflare_zone_ids=example.com",
        "dns_alias_cloudflare_zone_ids=example.com:short-id",
        "dns_alias_cloudflare_zone_ids=example.com:" + "z" * 32,
        "dns_alias_cloudflare_zone_ids=example.com:" + "a" * 32 + ", Example.COM.:" + "b" * 32,
        "dns_alias_cloudflare_zone_ids=example.com:" + "a" * 32 + "\n"
        "dns_alias_cloudflare_zones=other.com",
        "dns_alias_cloudflare_zone_ids=*.example.com:" + "a" * 32,
    ],
)
def test_invalid_cloudflare_zone_configuration(tmp_path, settings):
    conf = credentials(
        tmp_path, "dns_alias_provider=cloudflare\ndns_alias_cloudflare_api_token=token\n" + settings
    )
    with pytest.raises(errors.PluginError):
        validate_credentials(conf)


def test_cloudflare_conflicting_zone_is_ambiguous(tmp_path, monkeypatch, provider):
    monkeypatch.setattr("certbot_dns_alias.config.TencentDNSProvider", Mock(return_value=provider))
    cloudflare = Mock(spec=DNSProvider)
    monkeypatch.setattr(
        "certbot_dns_alias.config.CloudflareDNSProvider", Mock(return_value=cloudflare)
    )
    conf = credentials(
        tmp_path,
        """
dns_alias_provider=auto
dns_alias_tencent_secret_id=id
dns_alias_tencent_secret_key=secret
dns_alias_tencent_zones=delegate.example.net
dns_alias_cloudflare_api_token=token
dns_alias_cloudflare_zone_ids=delegate.example.net:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
""",
    )
    with pytest.raises(errors.PluginError, match="Ambiguous"):
        build_router(conf).find("host.delegate.example.net")


def test_real_cloudflare_credentials_setup(tmp_path, authenticator):
    conf = credentials(
        tmp_path,
        """
dns_alias_provider=cloudflare
dns_alias_cloudflare_api_token=fake-token
dns_alias_cloudflare_zone_ids=delegate.example.net:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
""",
    )
    authenticator.config.dns_alias_credentials = conf.confobj.filename
    authenticator._setup_credentials()
    assert authenticator.credentials.conf("cloudflare_api_token") == "fake-token"
    assert set(authenticator._router.providers) == {"cloudflare"}
    assert authenticator._router.find("customer.delegate.example.net")[1] == "delegate.example.net"
