"""
本地 CSRF 防护

后端没有账号鉴权、只监听 127.0.0.1，唯一现实的攻击面是：用户浏览器里另一个
标签页的恶意网页，趁应用运行时向 127.0.0.1:8000 发一个"简单请求"（表单
POST，或 fetch 的 no-cors 模式）——响应虽然读不到，但副作用已经真实发生
（清空知识库、强制登出等）。

浏览器的"简单请求"无法携带自定义请求头；一旦请求带了自定义头，浏览器会先
发 CORS 预检，而预检只有来自 CORS 白名单里的源（本项目的开发前端）才能通
过。所以：要求每个请求都带上这个约定头，等价于强制所有跨源尝试都必须先过
CORS 预检，天然免疫这类 CSRF，不需要维护 token/session。
"""
from __future__ import annotations

from fastapi import Header, HTTPException

REQUIRED_CLIENT_HEADER_VALUE = "1"


async def require_local_client(x_akasha_client: str | None = Header(default=None)) -> None:
    """挂在 api_router 上的全局依赖：缺失或值不对时直接 403。"""
    if x_akasha_client != REQUIRED_CLIENT_HEADER_VALUE:
        raise HTTPException(status_code=403, detail="Missing or invalid X-Akasha-Client header")
