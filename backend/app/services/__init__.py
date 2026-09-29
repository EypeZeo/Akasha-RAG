"""
services 服务层

各阶段逐步实现：
- douyin_collector.py：抖音登录与收藏抓取
- favorites_service.py：收藏差异同步
- knowledge_service.py：入库任务
- media_service.py：音频下载
- asr_service.py：语音识别
- text_processing.py：清洗与切块
- chroma_service.py：向量库操作
- llm_service.py：LLM/Embedding 客户端
- rag_service.py：RAG 检索与答案生成
- worker.py：后台任务队列

另外：import 本包即注册平台事实（platform_registry -> core/external_urls）。
core 不能反向 import services（会成环、且颠倒分层），所以「图片域名白名单」这类
core 需要的事实只能在 services 侧被 import 时推过去。放在包 __init__ 里是为了让
**任何** app.services.* 的 import 都覆盖到——只放在 platform_registry 里的话，
`from app.core.external_urls import safe_platform_image_url` 这种用法会拿到一张
空表，从而静默拒绝所有图片。
"""
from app.services import platform_registry as _platform_registry  # noqa: F401  (import 即注册)
