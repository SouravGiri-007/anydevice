"""Code generation / normalisation.

Codes are uppercase alphanumeric strings drawn from an alphabet that excludes
ambiguous characters (0/O, 1/I), matching the PRD.
"""
from __future__ import annotations

import random
import re

from .config import CODE_ALPHABET, CODE_LENGTH, CODE_LENGTH_LONG

_VALID_RE = re.compile(r"^[A-Z2-9]+$")


def generate_code(length: int = CODE_LENGTH) -> str:
    return "".join(random.choice(CODE_ALPHABET) for _ in range(length))


def generate_unique_code(is_taken) -> str:
    """Generate a code that `is_taken(code)` returns False for.

    Tries 5-char codes first, then falls back to a longer one (6 chars) so a
    rare collision doesn't keep us looping.
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

    Accepts lowercase / whitespace padding, rejects anything outside the
    code alphabet (so "0O1I" typos and garbage fail fast instead of burning a
    rate-limited lookup).
    """
    code = re.sub(r"\s+", "", raw or "").upper()
    if not _VALID_RE.match(code):
        return None
    return code
