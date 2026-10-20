import hashlib
from collections.abc import Callable
from pathlib import Path

_READ_CHUNK_BYTES = 1024 * 1024


def _file_digest(path: Path, new_hash: Callable[[], "hashlib._Hash"]) -> str | None:
    if not path.is_file():
        return None
    digest = new_hash()
    with path.open("rb") as file:
        while chunk := file.read(_READ_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def file_sha256(path: Path) -> str | None:
    """Hex SHA-256 of a file, or None if it does not exist. Blocking: call via a thread."""
    return _file_digest(path, hashlib.sha256)


def file_md5(path: Path) -> str | None:
    """Hex MD5, the checksum Google Drive reports (ADR-014). Blocking: call via a thread."""
    return _file_digest(path, lambda: hashlib.md5(usedforsecurity=False))
