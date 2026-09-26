"""Best-effort local proxy discovery for provider/browser traffic.

TUN mode normally needs no explicit proxy: traffic is intercepted by the OS.
For a conventional local proxy, prefer explicit environment variables and then
the Windows Internet Settings proxy. No proxy credentials are logged.
"""
from __future__ import annotations

import os
import urllib.request
from urllib.parse import urlsplit, urlunsplit


def _normalise_proxy(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    if "://" not in value:
        value = f"http://{value}"
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"http", "https", "socks5", "socks5h"}:
            return None
        if not parsed.hostname or parsed.port is None:
            return None
        return urlunsplit((parsed.scheme.lower(), parsed.netloc, "", "", ""))
    except ValueError:
        return None


def _windows_proxy() -> str | None:
    if os.name != "nt":
        return None
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            enabled = winreg.QueryValueEx(key, "ProxyEnable")[0]
            server = winreg.QueryValueEx(key, "ProxyServer")[0]
        if not enabled or not isinstance(server, str):
            return None
        entries = {}
        for part in server.split(";"):
            name, separator, target = part.partition("=")
            if separator:
                entries[name.strip().casefold()] = target.strip()
            elif target or part.strip():
                entries["default"] = part.strip()
        return _normalise_proxy(entries.get("https") or entries.get("http") or entries.get("default"))
    except (OSError, ValueError):
        return None


def detect_network_proxy() -> str | None:
    """Return an explicit/local conventional proxy, or None for TUN/direct mode."""
    proxies = urllib.request.getproxies()
    for key in ("https", "http", "all"):
        if proxy := _normalise_proxy(proxies.get(key)):
            return proxy
    for key in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"):
        if proxy := _normalise_proxy(os.environ.get(key)):
            return proxy
    return _windows_proxy()
