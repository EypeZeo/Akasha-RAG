"""
本地 CSRF 防护

后端默认只监听 127.0.0.1。即使用户误以 0.0.0.0 启动，API 层也拒绝非回环
客户端；局域网访问必须以后续的 HTTPS 配对功能显式开启，不能靠改监听地址绕过。

本地浏览器的另一现实攻击面是：用户浏览器里另一个
标签页的恶意网页，趁应用运行时向 127.0.0.1:8000 发一个"简单请求"（表单
POST，或 fetch 的 no-cors 模式）——响应虽然读不到，但副作用已经真实发生
（清空知识库、强制登出等）。

浏览器的"简单请求"无法携带自定义请求头；一旦请求带了自定义头，浏览器会先
发 CORS 预检，而预检只有来自 CORS 白名单里的源（本项目的开发前端）才能通
过。所以：要求每个请求都带上这个约定头，等价于强制所有跨源尝试都必须先过
CORS 预检，天然免疫这类 CSRF，不需要维护 token/session。
"""
from __future__ import annotations

from ipaddress import ip_address
from urllib.parse import urlsplit

from fastapi import Header, HTTPException, Request

REQUIRED_CLIENT_HEADER_VALUE = "1"
# "GET 无副作用" 不成立——/api/auth/platforms 和 /api/auth/bilibili/qrcode/poll
# 都是会写库的 GET，且都是前端 fetch() 轮询的（本来就带这个头，豁免它们没有
# 任何好处，只会重新打开一个跨源 GET 就能触发写状态的口子）。真正需要豁免的
# 只有下面这两个：原生浏览器导航（<a>.click() / window.open()）结构上不可能
# 带自定义头，而它们本身是只读下载，不构成 CSRF 风险。按路径模板（而不是按
# HTTP 方法整体豁免）精确匹配，避免误伤同样是 GET 但走 fetch() 的其它路由。
_EXEMPT_GET_ROUTES = frozenset({
    "/knowledge/export/{platform_item_id}",
    "/knowledge/export/batch/{task_id}/download",
})


def is_loopback_client(request: Request) -> bool:
    """Fail closed when a manually exposed Uvicorn server receives LAN traffic.

    Proxy forwarding headers are deliberately ignored: this desktop app has no
    trusted reverse proxy.  ``testclient`` is Starlette's in-process test host,
    never a routable peer in a real Uvicorn connection.
    """
    client = request.client
    if client is None:
        return False
    if client.host == "testclient":
        return True
    try:
        return ip_address(client.host).is_loopback
    except ValueError:
        return False


def is_local_host(request: Request) -> bool:
    """Reject DNS rebinding even when the TCP peer is the local browser.

    A hostile hostname can re-resolve to loopback after serving JavaScript.
    Its requests are then same-origin and can include our fixed client header.
    Forwarded host headers are deliberately not trusted.
    """
    hosts = request.headers.getlist("host")
    if len(hosts) != 1:
        return False
    try:
        parsed = urlsplit("//" + hosts[0])
        host = parsed.hostname
        if (
            not host or parsed.username is not None or parsed.password is not None
            or parsed.path or parsed.query or parsed.fragment
        ):
            return False
        # Accessing port also rejects invalid/out-of-range port spellings.
        parsed.port
        if host.lower() == "localhost":
            return True
        if host == "testserver" and request.client and request.client.host == "testclient":
            return True
        return ip_address(host).is_loopback
    except ValueError:
        return False


async def require_local_client(
    request: Request, x_akasha_client: str | None = Header(default=None)
) -> None:
    """挂在 api_router 上的全局依赖：缺失或值不对时直接 403。"""
    if not is_loopback_client(request):
        raise HTTPException(status_code=403, detail="Akasha-RAG API accepts loopback clients only")
    if not is_local_host(request):
        raise HTTPException(status_code=403, detail="Akasha-RAG API requires a local Host header")
    if request.method == "GET":
        route = request.scope.get("route")
        route_path = getattr(route, "path", None)
        # AI export creates/caches a summary and can incur provider charges.
        # Only the original, read-only download may use native navigation.
        generates_summary = (
            route_path == "/knowledge/export/{platform_item_id}"
            and "ai" in request.query_params.getlist("mode")
        )
        if route_path in _EXEMPT_GET_ROUTES and not generates_summary:
            return
    if x_akasha_client != REQUIRED_CLIENT_HEADER_VALUE:
        raise HTTPException(status_code=403, detail="Missing or invalid X-Akasha-Client header")
