from types import SimpleNamespace
from unittest.mock import Mock

import josepy
import pytest
from acme import challenges, messages
from certbot import achallenges
from cryptography.hazmat.primitives.asymmetric import rsa

from certbot_dns_alias.compat import certbot_major_version
from certbot_dns_alias.dns_alias import Authenticator
from certbot_dns_alias.providers.base import DNSProvider, ZoneRouter


@pytest.fixture
def dns_challenges():
    """Use each host's real challenge API so mocks cannot hide field changes."""
    key = josepy.JWKRSA(key=rsa.generate_private_key(public_exponent=65537, key_size=2048))
    result = []
    for token in [b"apex-token-123456", b"wildcard-token-1"]:
        arguments = {"domain": "example.com"}
        if certbot_major_version() >= 5 and "identifier" in (
            achallenges.KeyAuthorizationAnnotatedChallenge.__slots__
        ):
            arguments = {
                "identifier": messages.Identifier(typ=messages.IDENTIFIER_FQDN, value="example.com")
            }
        result.append(
            achallenges.KeyAuthorizationAnnotatedChallenge(
                challb=messages.ChallengeBody(chall=challenges.DNS01(token=token)),
                account_key=key,
                **arguments,
            )
        )
    return result


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
