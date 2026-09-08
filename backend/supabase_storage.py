"""Blob storage over Supabase Storage (an S3-like object store).

Implements the same surface as ``DiskBlobStore`` (BlobStore protocol) so the
API layer is unaffected. Uses the project's Storage REST API with the
``service_role`` key — the backend is the only reader/writer, buckets stay
private.

Blobs are buffered in server memory (the API already reads whole files for
at-rest encryption), so the 50MB per-file cap fits comfortably.
"""
from __future__ import annotations

import io
from typing import BinaryIO

import requests

from .storage import BlobTooLargeError

_CHUNK_BULK_DELETE = 400  # Supabase Storage bulk-delete cap


def _sanitize_key(key: str) -> str:
    key = key.lstrip("/")
    if key in ("", ".") or "\x00" in key:
        raise ValueError(f"invalid storage key: {key!r}")
    return key


class SupabaseBlobStore:
    """Files in a private Supabase Storage bucket under ``shares/{code}/…``."""

    def __init__(self, url: str, service_key: str, bucket: str = "shares"):
        self.base = f"{url}/storage/v1"
        self.bucket = bucket
        self._headers = {
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
        }

    def _url(self, key: str) -> str:
        return f"{self.base}/object/{self.bucket}/{_sanitize_key(key)}"

    def put(self, key: str, stream: BinaryIO, max_bytes: int | None = None) -> int:
        data = stream.read()
        if max_bytes is not None and len(data) > max_bytes:
            raise BlobTooLargeError(len(data))
        headers = {**self._headers, "x-upsert": "true"}
        res = requests.post(self._url(key), headers=headers, data=data, timeout=60)
        res.raise_for_status()
        return len(data)

    def open(self, key: str) -> BinaryIO:
        res = requests.get(self._url(key), headers=self._headers, timeout=60)
        res.raise_for_status()
        return io.BytesIO(res.content)

    def delete_prefix(self, prefix: str) -> None:
        prefix = _sanitize_key(prefix)
        objects = self._collect_objects(prefix)
        for i in range(0, len(objects), _CHUNK_BULK_DELETE):
            self._delete_keys(objects[i : i + _CHUNK_BULK_DELETE], ok404=True)
        # A single-file prefix lists nothing; try it directly anyway.
        self._delete_keys([prefix], ok404=True)

    def delete(self, key: str) -> None:
        self._delete_keys([_sanitize_key(key)], ok404=True)

    def exists(self, key: str) -> bool:
        key = _sanitize_key(key)
        # Object GET is CDN-cached and can 200 for a deleted object; the list
        # API reflects real state, so match the leaf name against its parent.
        parent, _, leaf = key.rpartition("/")
        if not parent or not leaf:
            return False
        return any(e.get("name") == leaf for e in self._list(parent))

    # -- helpers ------------------------------------------------------------

    def _list(self, prefix: str) -> list[dict]:
        acc: list[dict] = []
        offset = 0
        while True:
            res = requests.post(
                f"{self.base}/object/list/{self.bucket}",
                headers=self._headers,
                json={"prefix": prefix, "limit": 1000, "offset": offset},
                timeout=60,
            )
            res.raise_for_status()
            chunk = res.json()
            if not chunk:
                break
            acc.extend(chunk)
            if len(chunk) < 1000:
                break
            offset += len(chunk)
        return acc

    def _collect_objects(self, prefix: str) -> list[str]:
        """Recursively find real object paths under a prefix.

        Supabase list returns one level: real files carry ``metadata``; folder
        markers don't, so descend into those to locate every object and let the
        bulk delete remove them (virtual folders disappear on their own).
        """
        found: list[str] = []
        for entry in self._list(prefix):
            name = entry["name"]
            path = (
                name
                if name.startswith(prefix.rstrip("/"))
                else f"{prefix.rstrip('/')}/{name.lstrip('/')}"
            )
            if entry.get("metadata"):
                found.append(path)
            else:
                found.extend(self._collect_objects(path))
        return found

    def _delete_keys(self, keys: list[str], ok404: bool = False) -> None:
        if not keys:
            return
        res = requests.delete(
            f"{self.base}/object/{self.bucket}",
            headers=self._headers,
            json={"prefixes": keys},
            timeout=60,
        )
        if ok404 and res.status_code in (400, 404):
            # Bulk-delete of a nonexistent prefix hangs some Supabase regions;
            # treat "not found" as success for idempotent deletes.
            return
        res.raise_for_status()