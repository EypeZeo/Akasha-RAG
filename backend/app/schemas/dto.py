"""
API 数据传输对象（DTO）

定义所有 API 的请求体和响应体 schema。
各阶段逐步补充。
"""
from pydantic import BaseModel


class HealthResponse(BaseModel):
    """健康检查响应"""
    status: str
    service: str
    version: str
