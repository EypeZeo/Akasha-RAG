"""Best-effort local proxy discovery for provider/browser traffic.

TUN mode normally needs no explicit proxy: traffic is intercepted by the OS.
For a conventional local proxy, prefer explicit environment variables and then
the Windows Internet Settings proxy. No proxy credentials are logged.
"""
from __future__ import annotations

import os
import socket
import urllib.request
from urllib.parse import urlsplit, urlunsplit

from loguru import logger


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


def _should_bypass_proxy(hostname: str) -> bool:
    """检查给定主机名是否应该绕过代理（根据 NO_PROXY 环境变量）"""
    no_proxy = os.environ.get("NO_PROXY") or os.environ.get("no_proxy")
    if not no_proxy:
        return False
    
    # 标准化主机名
    hostname = hostname.lower().strip()
    
    # 解析 NO_PROXY 列表（逗号分隔）
    bypass_list = [entry.strip().lower() for entry in no_proxy.split(",")]
    
    for pattern in bypass_list:
        if not pattern:
            continue
        
        # 精确匹配
        if pattern == hostname:
            return True
        
        # 域名后缀匹配（例如 .example.com 匹配 api.example.com）
        if pattern.startswith(".") and hostname.endswith(pattern):
            return True
        
        # 通配符匹配（例如 *.example.com）
        if pattern.startswith("*."):
            domain_suffix = pattern[1:]  # 去掉 *
            if hostname.endswith(domain_suffix):
                return True
        
        # localhost 特殊处理
        if pattern == "localhost" and hostname in ("localhost", "127.0.0.1", "::1"):
            return True
    
    return False


def _test_proxy_connectivity(proxy_url: str, timeout: float = 3.0) -> tuple[bool, str]:
    """
    测试代理连通性
    返回 (是否可用, 错误信息)
    """
    try:
        parsed = urlsplit(proxy_url)
        host = parsed.hostname
        port = parsed.port or 8080
        # Context management releases sockets on refused/timed-out connections
        # too; create_connection also supports IPv6 proxy addresses.
        with socket.create_connection((host, port), timeout=timeout):
            pass
        return True, ""
    except socket.timeout:
        return False, "代理连接超时"
    except socket.gaierror:
        return False, "无法解析代理地址"
    except ConnectionRefusedError:
        return False, "代理连接被拒绝"
    except Exception as e:
        # Proxy URLs and exception strings may contain username/password.
        return False, f"代理连接失败 ({type(e).__name__})"


def detect_network_proxy(validate: bool = False) -> str | None:
    """
    Return an explicit/local conventional proxy, or None for TUN/direct mode.
    
    Args:
        validate: 是否验证代理连通性（可能增加启动延迟）
    """
    proxies = urllib.request.getproxies()
    for key in ("https", "http", "all"):
        if proxy := _normalise_proxy(proxies.get(key)):
            if validate:
                is_available, error = _test_proxy_connectivity(proxy)
                if not is_available:
                    logger.warning(f"检测到代理但连通性测试失败: {error}")
                    continue
            return proxy
    
    # 检查大写和小写环境变量以提高兼容性
    for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        if proxy := _normalise_proxy(os.environ.get(key)):
            if validate:
                is_available, error = _test_proxy_connectivity(proxy)
                if not is_available:
                    logger.warning(f"环境变量 {key} 指定的代理连通性测试失败: {error}")
                    continue
            return proxy
    
    proxy = _windows_proxy()
    if proxy and validate:
        is_available, error = _test_proxy_connectivity(proxy)
        if not is_available:
            logger.warning(f"Windows 注册表代理连通性测试失败: {error}")
            return None
    
    return proxy
