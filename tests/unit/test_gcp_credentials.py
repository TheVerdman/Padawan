"""Authentication preconditions only; no model or cloud requests."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from padawan.adapters.openai_compatible.gcp_credentials import (
    CredentialRefreshError,
    parse_credential,
)


def payload(expiry):
    return json.dumps(
        {"credential": {"access_token": "credential-fixture", "token_expiry": expiry}}
    )


@pytest.mark.parametrize(
    "expiry", [None, "", "not-a-date", "2020-01-01T00:00:00Z", "2099-01-01T00:00:00"]
)
def test_unknown_expired_or_naive_lifetime_fails_without_echoing_credential(expiry):
    with pytest.raises(CredentialRefreshError) as captured:
        parse_credential(payload(expiry), 120)
    assert "credential-fixture" not in str(captured.value)


def test_cached_token_must_cover_the_actual_long_request():
    expiry = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
    assert parse_credential(payload(expiry), 120).usable(120)
    with pytest.raises(CredentialRefreshError):
        parse_credential(payload(expiry), 2460)


def test_valid_expiry_is_preserved_and_token_is_hidden_from_representation():
    expiry = datetime.now(UTC) + timedelta(hours=1)
    credential = parse_credential(payload(expiry.isoformat()), 2460)
    assert credential.expires_at == expiry
    assert "credential-fixture" not in repr(credential)
