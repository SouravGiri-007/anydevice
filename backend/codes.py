"""Code generation and normalisation.

Codes are uppercase alphanumeric strings drawn from an alphabet that excludes
ambiguous characters (0/O, 1/I), matching the PRD. This module provides code
generation, validation, and normalisation utilities.
"""
from __future__ import annotations

import random
import re
from typing import Callable

from .config import CODE_ALPHABET, CODE_LENGTH, CODE_LENGTH_LONG

_VALID_RE: re.Pattern[str] = re.compile(r"^[A-Z2-9]+$")


def generate_code(length: int = CODE_LENGTH) -> str:
    """Generate a random share code of the given length.

    Args:
        length: Number of characters in the code (default: CODE_LENGTH)

    Returns:
        Random uppercase code string
    """
    return "".join(random.choice(CODE_ALPHABET) for _ in range(length))


def generate_unique_code(is_taken: Callable[[str], bool]) -> str:
    """Generate a unique code that is not taken.

    Tries 5-character codes first, then falls back to 6-character codes
    if collisions occur. This avoids infinite loops on rare hash collisions.

    Args:
        is_taken: Callable that returns True if a code is already in use

    Returns:
        A unique code string

    Raises:
        RuntimeError: If unable to allocate a code after exhausting retries
    """
    for _ in range(12):
        code = generate_code(CODE_LENGTH)
        if not is_taken(code):
            return code
    for _ in range(20):
        code = generate_code(CODE_LENGTH_LONG)
        if not is_taken(code):
            return code
    raise RuntimeError("could not allocate a unique share code")


def normalise_code(raw: str) -> str | None:
    """Normalise user-typed input to a canonical code.

    Accepts lowercase input and whitespace padding, rejects anything outside
    the valid code alphabet (so ambiguous character typos and garbage fail
    fast instead of burning a rate-limited lookup).

    Args:
        raw: User-provided code string (may include whitespace, lowercase)

    Returns:
        Normalised uppercase code, or None if invalid
    """
    code = re.sub(r"\s+", "", raw or "").upper()
    if not _VALID_RE.match(code):
        return None
    return code
