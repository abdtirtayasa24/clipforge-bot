"""Encryption at rest for stored credentials (ADR-015)."""

from cryptography.fernet import Fernet, InvalidToken


class DecryptionError(Exception):
    pass


class TokenCipher:
    def __init__(self, key: str) -> None:
        try:
            self._fernet = Fernet(key.encode())
        except ValueError:
            raise ValueError("the token encryption key is not a valid Fernet key") from None

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken:
            raise DecryptionError("stored token cannot be decrypted with the current key") from None
