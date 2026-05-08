"""macOS `security ... -w` returns hex-encoded values when the
stored value contains newlines (any PEM does). `secrets.get_pem`
must transparently decode."""

import binascii

import keyring

from taaad import secrets


PEM_FAKE = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "ZmFrZSBQRU0gZm9yIHRlc3RpbmcK\n"
    "-----END RSA PRIVATE KEY-----\n"
)


def test_round_trip_plain():
    secrets.set_pem("test-key", PEM_FAKE)
    assert secrets.get_pem("test-key") == PEM_FAKE


def test_hex_encoded_value_is_decoded():
    """Simulate the v0.4-via-`security` storage where the value is
    hex-encoded ASCII."""
    hex_value = binascii.hexlify(PEM_FAKE.encode()).decode()
    keyring.set_password("test-key", secrets._username(), hex_value)
    assert secrets.get_pem("test-key") == PEM_FAKE


def test_non_hex_passes_through():
    keyring.set_password("test-key", secrets._username(), "not-hex but-valid-pem")
    assert secrets.get_pem("test-key") == "not-hex but-valid-pem"


def test_has_pem_after_set():
    secrets.set_pem("test-key", PEM_FAKE)
    assert secrets.has_pem("test-key") is True
    assert secrets.has_pem("nonexistent-key") is False
