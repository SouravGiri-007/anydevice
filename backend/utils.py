"""Utility functions for share serialization and blob handling."""
from __future__ import annotations

import base64
import mimetypes
import re
import zipfile
from typing import Any

from .crypto import decrypt_bytes


def clean_display_name(raw: str, fallback: str = "file") -> str:
    """Clean and validate a display name for files/items.

    Args:
        raw: Raw filename or item name
        fallback: Default name if raw is empty

    Returns:
        Cleaned display name (max 200 chars)
    """
    name = (raw or "").replace("\x00", "").strip()
    name = "".join(c for c in name if c not in "\r\n\t")
    name = name.strip()
    return name[:200] if name else fallback


def guess_mime(name: str) -> str:
    """Guess MIME type from filename.

    Args:
        name: Filename to guess MIME type for

    Returns:
        MIME type string, or "application/octet-stream" if unknown
    """
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


def item_json(item: dict, key: str | None = None) -> dict[str, Any]:
    """Serialize an item to JSON format.

    Server decrypts content at-rest → the API always returns plaintext.

    Args:
        item: Item dictionary from store
        key: Optional decryption key for text items

    Returns:
        JSON-serializable item dictionary
    """

    def unb64url(text: str) -> bytes:
        raw = text.replace("-", "+").replace("_", "/")
        raw += "=" * (-len(raw) % 4)
        return base64.urlsafe_b64decode(raw)

    content = item.get("content")
    if item["type"] == "text" and content and key:
        try:
            content = decrypt_bytes(unb64url(content), key).decode("utf-8")
        except Exception:
            content = ""
    return {
        "id": item["id"],
        "type": item["type"],
        "name": item.get("name", ""),
        "mime": item.get("mime", "application/octet-stream"),
        "size": item.get("size", 0),
        "downloaded": bool(item.get("downloaded")),
        "content": content if item["type"] == "text" else None,
    }


def share_json(share: dict) -> dict[str, Any]:
    """Serialize a share to JSON format.

    Args:
        share: Share dictionary from store

    Returns:
        JSON-serializable share dictionary
    """
    import time

    now = time.time()
    key = share.get("key")
    items = [item_json(i, key) for i in share["items"]]
    return {
        "code": share["code"],
        "created_at": share["created_at"],
        "expires_at": share["expires_at"],
        "expires_in": max(0.0, share["expires_at"] - now),
        "ttl_key": share["ttl_key"],
        "ttl_seconds": share["ttl_seconds"],
        "burn": share["burn"],
        "status": share.get("status", "pending"),
        "enc": False,
        "items": items,
        "bytes_total": sum(i["size"] for i in share["items"]),
    }


def zip_name(name: str, pos: int) -> str:
    """Sanitize a name for use in a zip archive.

    Args:
        name: Original filename
        pos: Position in archive (for fallback naming)

    Returns:
        Safe zip entry name (max 200 chars)
    """
    name = re.sub(r"[/\\]", "_", name or "").strip(". ").strip()
    if not name:
        name = f"note-{pos}.txt"
    return name[:200]


def zip_info(name: str) -> zipfile.ZipInfo:
    """Create a ZipInfo with UTF-8 filename flag set.

    Args:
        name: Filename for the zip entry

    Returns:
        ZipInfo configured for safe UTF-8 filenames
    """
    import time

    info = zipfile.ZipInfo(name)
    info.flag_bits |= 0x800  # 0x800 => filename/comment are UTF-8
    info.date_time = time.localtime(time.time())[:6]
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


def content_disposition(filename: str, inline: bool) -> str:
    """Format a Content-Disposition header.

    Args:
        filename: Filename to include in header
        inline: Whether to use inline disposition (vs attachment)

    Returns:
        Content-Disposition header value
    """
    from urllib.parse import quote

    ascii_name = filename.encode("ascii", "ignore").decode() or "download"
    kind = "inline" if inline else "attachment"
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


def attachment_header(filename: str) -> str:
    """Format a Content-Disposition attachment header.

    Args:
        filename: Filename to attach

    Returns:
        Content-Disposition header value
    """
    return content_disposition(filename, inline=False)
