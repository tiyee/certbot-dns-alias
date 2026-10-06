from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from certbot import errors

from certbot_dns_alias.providers.base import TxtRecord


def test_create_and_cleanup_uses_saved_destination(authenticator, provider):
    authenticator._perform("example.com", "_acme-challenge.example.com", "token")
    provider.create_txt_record.assert_called_once_with(
        "delegate.example.net",
        "customer",
        "token",
        600,
    )
    authenticator._resolver.resolve.side_effect = AssertionError("must not resolve during cleanup")
    authenticator._cleanup("example.com", "_acme-challenge.example.com", "token")
    authenticator._cleanup("example.com", "_acme-challenge.example.com", "token")
    provider.delete_txt_record.assert_called_once_with("delegate.example.net", "101")
    assert not authenticator._leases
    assert not authenticator._challenges


def test_apex_and_wildcard_keep_both_txt_values(authenticator, provider):
    fqdn = "_acme-challenge.example.com"
    authenticator._perform("example.com", fqdn, "apex-token")
    authenticator._perform("*.example.com", fqdn, "wildcard-token")
    assert [call.args[2] for call in provider.create_txt_record.call_args_list] == [
        "apex-token",
        "wildcard-token",
    ]
    authenticator._cleanup("example.com", fqdn, "apex-token")
    assert len(authenticator._leases) == 1
    authenticator._cleanup("*.example.com", fqdn, "wildcard-token")
    assert [call.args[1] for call in provider.delete_txt_record.call_args_list] == ["101", "102"]


def test_shared_record_lives_until_last_challenge(authenticator, provider):
    for source in ["one.com", "two.com"]:
        authenticator._perform(source, "_acme-challenge." + source, "shared-value")
    assert provider.create_txt_record.call_count == 1
    authenticator._cleanup("one.com", "_acme-challenge.one.com", "shared-value")
    provider.delete_txt_record.assert_not_called()
    authenticator._cleanup("two.com", "_acme-challenge.two.com", "shared-value")
    provider.delete_txt_record.assert_called_once()


def test_preexisting_record_is_not_owned(authenticator, provider):
    provider.list_txt_records.return_value = [TxtRecord("old-id", "customer", "token")]
    authenticator._perform("example.com", "_acme-challenge.example.com", "token")
    authenticator._cleanup("example.com", "_acme-challenge.example.com", "token")
    provider.create_txt_record.assert_not_called()
    provider.delete_txt_record.assert_not_called()


def test_unrelated_txt_is_preserved(authenticator, provider):
    provider.list_txt_records.return_value = [TxtRecord("old-id", "customer", "unrelated")]
    test_create_and_cleanup_uses_saved_destination(authenticator, provider)


def test_failed_perform_does_not_clean_unknown_records(authenticator, provider):
    provider.create_txt_record.side_effect = errors.PluginError("write failed")
    with pytest.raises(errors.PluginError, match="write failed"):
        authenticator._perform("example.com", "_acme-challenge.example.com", "token")
    authenticator._cleanup("example.com", "_acme-challenge.example.com", "token")
    provider.delete_txt_record.assert_not_called()
    assert not authenticator._leases


def test_cleanup_error_can_be_retried(authenticator, provider, caplog):
    authenticator._perform("example.com", "_acme-challenge.example.com", "token")
    provider.delete_txt_record.side_effect = [errors.PluginError("permission denied"), None]
    authenticator._cleanup("example.com", "_acme-challenge.example.com", "token")
    assert "permission denied" in caplog.text
    assert len(authenticator._leases) == 1
    authenticator._cleanup("example.com", "_acme-challenge.example.com", "token")
    assert not authenticator._leases


def test_certbot_perform_cleanup_lifecycle(authenticator, provider, monkeypatch):
    # Exercise Certbot's public API as well as our private challenge methods.
    monkeypatch.setattr(authenticator, "_setup_credentials", Mock())
    notify = Mock()
    sleep = Mock()
    monkeypatch.setattr("certbot.plugins.dns_common.display_util.notify", notify)
    monkeypatch.setattr("certbot.plugins.dns_common.sleep", sleep)
    challenges = []
    for token in ["apex-token", "wildcard-token"]:
        challenge = Mock()
        challenge.identifier = SimpleNamespace(value="example.com")
        challenge.validation_domain_name.return_value = "_acme-challenge.example.com"
        challenge.validation.return_value = token
        challenge.response.return_value = "response-" + token
        challenges.append(challenge)
    assert authenticator.perform(challenges) == ["response-apex-token", "response-wildcard-token"]
    sleep.assert_called_once_with(0)
    authenticator.cleanup(challenges)
    assert provider.delete_txt_record.call_count == 2
