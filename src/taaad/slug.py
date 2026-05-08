"""Slug validation. GitHub App slugs match ^[a-z0-9][a-z0-9-]{0,38}$."""

import re

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,38}$")


def validate(slug: str) -> str:
    if not isinstance(slug, str) or not _SLUG_RE.fullmatch(slug):
        raise ValueError(
            f"invalid slug {slug!r}: must match ^[a-z0-9][a-z0-9-]{{0,38}}$"
        )
    return slug
