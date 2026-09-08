"""Blob (file) storage.

The rest of the app only talks to `BlobStore` through a small surface
(put / open / delete_prefix / delete), so swapping the disk implementation for
Firebase Storage later is a matter of writing one new class with the same
methods and pointing the app at it.
"""
from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path
from typing import BinaryIO, Protocol

# Characters kept when deriving a safe filename for storage keys.
_SAFE_KEEP = re.compile(r"[^A-Za-z0-9._-]")


def storage_safe_name(name: str) -> str:
    """Best-effort safe filename for use inside a storage key."""
    name = unicodedata.normalize("NFKC", name or "file")
    name = _SAFE_KEEP.sub("_", name)
    name = name.strip(" ._")
    # Never allow empty or path-traversal results.
    return (name or "file")[:80]


class BlobStore(Protocol):
    def put(self, key: str, stream: BinaryIO, max_bytes: int | None = None) -> int: ...

    def open(self, key: str) -> BinaryIO: ...

    def delete_prefix(self, prefix: str) -> None: ...

    def delete(self, key: str) -> None: ...


class BlobTooLargeError(Exception):
    pass


class DiskBlobStore:
    """Files on disk under data_dir/blobs, mirroring Firebase-style keys.

    A key like ``shares/ABCDE/itemid/notes.pdf`` maps to
    ``data/blobs/shares/ABCDE/itemid/notes.pdf`` so that the whole folder for a
    code can be removed in one rmtree — the same deletion shape the PRD
    describes for Firebase Storage.
    """

    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        key = key.lstrip("/")
        path = (self.root / key).resolve()
        if not str(path).startswith(str(self.root.resolve())):
            raise ValueError(f"storage key escapes root: {key!r}")
        return path

    def put(self, key: str, stream: BinaryIO, max_bytes: int | None = None) -> int:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        with open(path, "wb") as out:
            while True:
                chunk = stream.read(1024 * 256)
                if not chunk:
                    break
                size += len(chunk)
                if max_bytes is not None and size > max_bytes:
                    out.close()
                    path.unlink(missing_ok=True)
                    raise BlobTooLargeError(size)
                out.write(chunk)
        return size

    def open(self, key: str) -> BinaryIO:
        return open(self._path(key), "rb")

    def delete_prefix(self, prefix: str) -> None:
        path = self._path(prefix)
        if os.path.isdir(path):
            import shutil

            shutil.rmtree(path, ignore_errors=True)
        # If the prefix happens to be a single file key, drop it too.
        path.unlink(missing_ok=True)

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()
