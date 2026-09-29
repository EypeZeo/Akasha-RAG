"""Validation for provider-owned remote image URLs.

The application displays provider avatars in the browser and downloads note
images for OCR.  Keep the validation *mechanism* in one place so an API payload
or stored legacy value cannot turn either path into an arbitrary URL fetch.

The domain *facts* (which domains belong to which platform) are owned by
``app/services/platform_registry.py`` and pushed in here at import time through
:func:`register_image_domains`.  Core must not import ``app.services`` (that
would invert the layering and create an import cycle), so registration goes in
the services -> core direction only.

Failure mode when nothing has been registered: the table is empty and every URL
is rejected.  That is fail-closed -- a missing registration loses images, it
never widens the trust boundary.
"""
from __future__ import annotations

import ipaddress
from collections.abc import Iterable
from urllib.parse import urlsplit, urlunsplit

#: platform -> allowed image domains.  Populated by
#: ``app.services.platform_registry`` at import time (see module docstring).
_image_domains: dict[str, frozenset[str]] = {}


def register_image_domains(platform: str, domains: Iterable[str]) -> None:
    """Register the image domains owned by one platform (idempotent).

    Called by the platform facts registry.  Keys are normalised the same way
    :func:`safe_platform_image_url` normalises its ``platform`` argument, so a
    registered platform is always found regardless of case/whitespace.
    """
    key = (platform or "").strip().lower()
    if not key:
        return
    normalised = frozenset(str(domain).strip().lower().rstrip(".") for domain in domains)
    _image_domains[key] = frozenset(domain for domain in normalised if domain)


def registered_image_platforms() -> tuple[str, ...]:
    """Platforms whose image domains are currently registered (drift check helper)."""
    return tuple(sorted(_image_domains))


def safe_platform_image_url(platform: str, value: str | None) -> str:
    """Return a canonical trusted HTTPS image URL, or an empty string.

    Allowlisting by a parsed, IDNA-normalised hostname avoids suffix tricks
    such as ``evilhdslb.com``.  IP literals, credentials, non-standard ports,
    and fragments are rejected or stripped before a URL can reach a browser
    image tag or a server-side OCR downloader.
    """
    if not isinstance(value, str) or len(value) > 2048:
        return ""
    allowed = _image_domains.get(platform.strip().lower())
    if not allowed:
        return ""
    try:
        parts = urlsplit(value.strip())
        if (
            parts.scheme.lower() != "https"
            or not parts.netloc
            or parts.username is not None
            or parts.password is not None
            or parts.port not in (None, 443)
            or not parts.hostname
        ):
            return ""
        host = parts.hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError):
        return ""

    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return ""

    if not any(host == domain or host.endswith("." + domain) for domain in allowed):
        return ""
    return urlunsplit(("https", host, parts.path or "/", parts.query, ""))
