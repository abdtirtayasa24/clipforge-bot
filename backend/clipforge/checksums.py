import hashlib
from pathlib import Path

_READ_CHUNK_BYTES = 1024 * 1024


def file_sha256(path: Path) -> str | None:
    """Hex SHA-256 of a file, or None if it does not exist. Blocking: call via a thread."""
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(_READ_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()
