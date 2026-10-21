import pytest
from cryptography.fernet import Fernet

from clipforge.security.crypto import DecryptionError, TokenCipher

pytestmark = pytest.mark.security


def test_round_trip_never_stores_the_plaintext() -> None:
    cipher = TokenCipher(Fernet.generate_key().decode())

    encrypted = cipher.encrypt("1//refresh-token")

    assert "refresh-token" not in encrypted
    assert cipher.decrypt(encrypted) == "1//refresh-token"


def test_a_different_key_cannot_decrypt() -> None:
    encrypted = TokenCipher(Fernet.generate_key().decode()).encrypt("secret")

    with pytest.raises(DecryptionError):
        TokenCipher(Fernet.generate_key().decode()).decrypt(encrypted)


def test_malformed_key_is_rejected() -> None:
    with pytest.raises(ValueError, match="Fernet key"):
        TokenCipher("not-a-key")
