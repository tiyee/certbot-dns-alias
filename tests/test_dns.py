import socket
from types import SimpleNamespace
from unittest.mock import Mock

import dns.exception
import dns.flags
import dns.message
import dns.name
import dns.query
import dns.rcode
import dns.resolver
import dns.rrset
import pytest
from certbot import errors

from certbot_dns_alias.dns import CnameResolver, normalize_name, relative_name


def response(owner="a.example.com", target=None, *, nxdomain=False):
    message = dns.message.make_response(dns.message.make_query(owner, "CNAME"))
    if target:
        message.answer.append(dns.rrset.from_text(owner + ".", 60, "IN", "CNAME", target))
    if nxdomain:
        message.set_rcode(dns.rcode.NXDOMAIN)
    return message


def answer(owner="a.example.com", target=None):
    return SimpleNamespace(response=response(owner, target))


@pytest.mark.parametrize(
    ("input_name", "expected"),
    [
        ("_ACME-CHALLENGE.Example.COM.", "_acme-challenge.example.com"),
        ("例子.测试", "xn--fsqu00a.xn--0zwm56d"),
        ("faß.de", "xn--fa-hia.de"),
        ("_ACME-CHALLENGE.Faß.DE.", "_acme-challenge.xn--fa-hia.de"),
        ("_acme-challenge.xn--fa-hia.de", "_acme-challenge.xn--fa-hia.de"),
    ],
)
def test_normalize(input_name, expected):
    assert normalize_name(input_name) == expected


@pytest.mark.parametrize("name", ["", ".", "*.example.com", "a..com", "a b.com", "a" * 64 + ".com"])
def test_invalid_names(name):
    with pytest.raises(errors.PluginError, match="Invalid DNS name"):
        normalize_name(name)


def test_relative_name_and_suffix_boundary():
    assert relative_name("a.b.example.co.uk.", "example.co.uk") == "a.b"
    assert relative_name("example.co.uk", "example.co.uk.") == "@"
    with pytest.raises(errors.PluginError, match="outside zone"):
        relative_name("badexample.com", "example.com")


def test_idna2008_relative_name_preserves_distinct_zones():
    assert relative_name("_acme-challenge.xn--fa-hia.de", "faß.de") == "_acme-challenge"
    with pytest.raises(errors.PluginError, match="outside zone"):
        relative_name("_acme-challenge.fass.de", "faß.de")


def test_truncated_udp_response_falls_back_to_tcp(monkeypatch):
    # Exercise real DNS wire parsing and fallback. dnspython 2.6.0 swallowed
    # Truncated when ignore_errors=True (as used by its resolver).
    query = dns.message.make_query("_acme-challenge.example.com.", "CNAME")
    truncated = dns.message.make_response(query)
    truncated.flags |= dns.flags.TC
    complete = dns.message.make_response(query)
    source = ("192.0.2.1", 53)
    receive = Mock(side_effect=[(truncated.to_wire(), source), dns.exception.Timeout()])
    tcp = Mock(return_value=complete)
    monkeypatch.setattr(dns.query, "send_udp", Mock())
    monkeypatch.setattr(dns.query, "_udp_recv", receive)
    monkeypatch.setattr(dns.query, "tcp", tcp)
    result, used_tcp = dns.query.udp_with_fallback(
        query,
        source[0],
        timeout=1,
        udp_sock=SimpleNamespace(family=socket.AF_INET),
        ignore_errors=True,
    )
    assert result is complete
    assert used_tcp
    assert receive.call_count == 1
    assert tcp.call_count == 1


def test_multihop_and_exact_depth():
    resolver = Mock()
    resolver.resolve.side_effect = [
        answer(target="b.example.net."),
        answer("b.example.net", "c.example.org."),
        answer("c.example.org"),
    ]
    assert CnameResolver(resolver, max_depth=2).resolve("a.example.com") == "c.example.org"
    assert [call.args[0] for call in resolver.resolve.call_args_list] == [
        "a.example.com.",
        "b.example.net.",
        "c.example.org.",
    ]
    assert resolver.resolve.call_args.kwargs == {
        "lifetime": 10,
        "search": False,
        "raise_on_no_answer": False,
    }


def test_depth_exceeded():
    resolver = Mock()
    resolver.resolve.side_effect = [
        answer(target="b.example.net."),
        answer("b.example.net", "c.example.org."),
    ]
    with pytest.raises(errors.PluginError, match="exceeds 1 links"):
        CnameResolver(resolver, max_depth=1).resolve("a.example.com")


def test_cycle_case_insensitive():
    resolver = Mock()
    resolver.resolve.side_effect = [
        answer(target="B.example.net."),
        answer("b.example.net", "A.EXAMPLE.COM."),
    ]
    with pytest.raises(errors.PluginError, match="loop"):
        CnameResolver(resolver).resolve("a.example.com")


@pytest.mark.parametrize("negative", ["NXDOMAIN", "NoAnswer", "empty"])
def test_terminal_negative_answer(negative):
    resolver = Mock()
    message = response()
    if negative == "NXDOMAIN":
        owner = dns.name.from_text("a.example.com.")
        resolver.resolve.side_effect = dns.resolver.NXDOMAIN(
            qnames=[owner],
            responses={owner: message},
        )
    elif negative == "NoAnswer":
        resolver.resolve.side_effect = dns.resolver.NoAnswer(response=message)
    else:
        resolver.resolve.return_value = SimpleNamespace(response=message)
    assert CnameResolver(resolver).resolve("a.example.com") == "a.example.com"
    with pytest.raises(errors.PluginError, match="delegation required"):
        CnameResolver(resolver).resolve("a.example.com", require_cname=True)


def test_nxdomain_preserves_delegation_to_nonexistent_txt_host():
    owner = dns.name.from_text("a.example.com.")
    resolver = Mock()
    resolver.resolve.side_effect = [
        dns.resolver.NXDOMAIN(
            qnames=[owner],
            responses={
                owner: response(target="fresh.example.net.", nxdomain=True),
            },
        ),
        answer("fresh.example.net"),
    ]
    assert (
        CnameResolver(resolver).resolve("a.example.com", require_cname=True) == "fresh.example.net"
    )


@pytest.mark.parametrize("exception", [dns.exception.Timeout, dns.resolver.NoNameservers])
def test_transient_retry_then_success(monkeypatch, exception):
    sleep = Mock()
    monkeypatch.setattr("certbot_dns_alias.dns.time.sleep", sleep)
    resolver = Mock()
    resolver.resolve.side_effect = [exception(), answer()]
    assert CnameResolver(resolver, retries=1).resolve("a.example.com") == "a.example.com"
    sleep.assert_called_once_with(1)


def test_timeout_is_not_a_terminal_name(monkeypatch):
    monkeypatch.setattr("certbot_dns_alias.dns.time.sleep", Mock())
    resolver = Mock()
    resolver.resolve.side_effect = dns.exception.Timeout()
    with pytest.raises(errors.PluginError, match="after 3 attempts"):
        CnameResolver(resolver).resolve("a.example.com")
    assert resolver.resolve.call_count == 3


def test_invalid_cname_rrset():
    resolver = Mock()
    message = response()
    message.answer = [
        dns.rrset.RRset(
            dns.name.from_text("a.example.com."),
            dns.rdataclass.IN,
            dns.rdatatype.CNAME,
        )
    ]
    resolver.resolve.return_value = SimpleNamespace(response=message)
    with pytest.raises(errors.PluginError, match="Invalid CNAME RRset"):
        CnameResolver(resolver).resolve("a.example.com")
