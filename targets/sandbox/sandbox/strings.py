"""String helpers."""

import re


def slugify(text: str) -> str:
    """Lowercase ``text`` and join its alphanumeric words with single hyphens.

    >>> slugify("Hello, World!")
    'hello-world'
    """
    words = re.findall(r"[a-z0-9]+", text.lower())
    return "-".join(words)


def truncate(text: str, limit: int) -> str:
    """Shorten ``text`` to at most ``limit`` characters.

    If ``text`` is longer than ``limit``, keep the first ``limit - 1``
    characters and append a single ellipsis character "…" so the result is
    exactly ``limit`` characters long. Raise ValueError if ``limit < 1``.

    >>> truncate("abcdef", 4)
    'abc…'
    """
    if limit < 1:
        raise ValueError("limit must be >= 1")
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def is_palindrome(text: str) -> bool:
    """Return True if ``text`` reads the same backwards, ignoring case and non-alphanumerics.

    >>> is_palindrome("A man, a plan, a canal: Panama")
    True
    """
    cleaned = [c for c in text.lower() if c.isalnum()]
    return cleaned == cleaned[::-1]
