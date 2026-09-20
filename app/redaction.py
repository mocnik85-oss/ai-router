from __future__ import annotations

import re


_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)[^\s,;]+"),
    re.compile(r"(?i)(token\s*[=:]\s*)[^\s,;]+"),
    re.compile(r"(?i)(password\s*[=:]\s*)[^\s,;]+"),
    re.compile(r"(?i)(secret\s*[=:]\s*)[^\s,;]+"),
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s]+"),
)


def redact(text: str) -> str:
    """Remove common credential values from text before logging/display."""
    if not isinstance(text, str):
        raise TypeError("redact() expects a string.")

    result = text

    for pattern in _SECRET_PATTERNS:
        result = pattern.sub(r"\1[REDACTED]", result)

    return result
