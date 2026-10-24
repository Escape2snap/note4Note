"""Small filesystem and time helpers shared by fileSearch."""

from __future__ import annotations

import hashlib
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp with a trailing Z."""

    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def atomic_write_text(path: Path, content: str, tmp_dir: Path | None = None) -> None:
    """Atomically write *content* to *path* using a temp file under tmp_dir."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = tmp_dir or path.parent
    temp_dir.mkdir(parents=True, exist_ok=True)

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(temp_dir),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def sha256_file(path: Path) -> str:
    """Hash a file without loading it all into memory."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def document_id(path: Path) -> str:
    """Stable document id derived from the resolved source path."""

    encoded = os.fsencode(str(path))
    return hashlib.sha256(encoded).hexdigest()[:24]
