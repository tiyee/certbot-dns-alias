from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from certbot_dns_alias.dns_alias import Authenticator
from certbot_dns_alias.providers.base import DNSProvider, ZoneRouter


@pytest.fixture
def provider():
    result = Mock(spec=DNSProvider)
    result.name = "tencent"
    result.list_zones.return_value = ["delegate.example.net"]
    result.list_txt_records.return_value = []
    result.create_txt_record.side_effect = ["101", "102", "103"]
    return result


@pytest.fixture
def authenticator(provider):
    config = SimpleNamespace(
        dns_alias_credentials=None,
        dns_alias_ttl=600,
        dns_alias_cname_max_depth=8,
        dns_alias_dns_timeout=10.0,
        dns_alias_dns_retries=2,
        dns_alias_resolvers=None,
        dns_alias_require_cname=False,
        dns_alias_propagation_seconds=0,
        noninteractive_mode=True,
    )
    result = Authenticator(config, "dns-alias")
    result._resolver = Mock()
    result._resolver.resolve.return_value = "customer.delegate.example.net"
    result._router = ZoneRouter({"tencent": provider})
    return result
