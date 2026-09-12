"""Validation for provider-owned remote image URLs.

The application displays provider avatars in the browser and downloads Douyin
note images for OCR.  Keep the trust boundary in one place so an API payload or
stored legacy value cannot turn either path into an arbitrary URL fetch.
"""
from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit, urlunsplit

_IMAGE_DOMAINS: dict[str, frozenset[str]] = {
    "bilibili": frozenset({"hdslb.com", "biliimg.com"}),
    "douyin": frozenset(
        {
            "douyinpic.com",
            "byteimg.com",
            "pstatp.com",
            "ibytedtos.com",
            "bytedance.com",
            "bytedcdn.com",
            "zjcdn.com",
        }
    ),
}


def safe_platform_image_url(platform: str, value: str | None) -> str:
    """Return a canonical trusted HTTPS image URL, or an empty string.

    Allowlisting by a parsed, IDNA-normalised hostname avoids suffix tricks
    such as ``evilhdslb.com``.  IP literals, credentials, non-standard ports,
    and fragments are rejected or stripped before a URL can reach a browser
    image tag or a server-side OCR downloader.
    """
    if not isinstance(value, str) or len(value) > 2048:
        return ""
    allowed = _IMAGE_DOMAINS.get(platform.strip().lower())
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
