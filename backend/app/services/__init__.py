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
"""
