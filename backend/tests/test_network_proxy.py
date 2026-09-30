from app.core import network

import pytest


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


@pytest.mark.parametrize("error", [network.socket.timeout(), network.socket.gaierror(), ConnectionRefusedError(), OSError("secret-user:secret-pass")])
def test_proxy_failures_never_disclose_credentials(monkeypatch, error):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(network.socket, "create_connection", fail)
    ok, message = network._test_proxy_connectivity("http://secret-user:secret-pass@localhost:7890")
    assert not ok
    assert "secret-user" not in message
    assert "secret-pass" not in message


def test_proxy_connectivity_closes_socket_and_supports_ipv6(monkeypatch):
    closed = []
    targets = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            closed.append(True)

    def connect(address, timeout):
        targets.append((address, timeout))
        return Connection()

    monkeypatch.setattr(network.socket, "create_connection", connect)
    assert network._test_proxy_connectivity("http://[::1]:7890") == (True, "")
    assert targets == [(("::1", 7890), 3.0)]
    assert closed == [True]
