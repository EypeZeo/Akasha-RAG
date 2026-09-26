from app.core import network


def test_explicit_proxy_environment_is_normalized(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "127.0.0.1:7890")
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("ALL_PROXY", raising=False)
    monkeypatch.setattr(network.urllib.request, "getproxies", lambda: {})

    assert network.detect_network_proxy() == "http://127.0.0.1:7890"


def test_tun_or_direct_mode_has_no_explicit_proxy(monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(network.urllib.request, "getproxies", lambda: {})
    monkeypatch.setattr(network, "_windows_proxy", lambda: None)

    assert network.detect_network_proxy() is None
