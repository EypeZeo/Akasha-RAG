"""
应用配置模块

基于 Pydantic Settings 的环境变量管理。
所有配置项从环境变量及 Windows DPAPI 保护的本地配置读取，提供类型验证和默认值。
"""
import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings

from app.core.secure_storage import read_text


def _load_protected_dotenv() -> None:
    """Expose DPAPI-protected `.env` values to Pydantic for this process only."""
    raw = read_text(Path.cwd() / ".env")
    if raw is None:
        return
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))


_load_protected_dotenv()


class Settings(BaseSettings):
    """
    应用全局配置

    配置优先级：环境变量 > DPAPI 保护的 .env > 默认值
    """

    # ===== API Key =====
    deepseek_api_key: str = ""
    """DeepSeek API Key，用于 LLM 对话"""

    dashscope_api_key: str = ""
    """DashScope API Key，用于 ASR 语音转写和 Embedding 向量化"""

    # ===== LLM 配置 =====
    llm_model: str = "deepseek-chat"
    """LLM 模型名称"""

    llm_base_url: str = "https://api.deepseek.com"
    """LLM API 地址，兼容 OpenAI 协议"""

    # ===== Embedding 配置 =====
    embedding_model: str = "qwen3.7-text-embedding"
    """
    文本向量化模型（DashScope TextEmbedding）。
    qwen3.7-text-embedding 支持 dimension 参数；本项目在 llm_service 固定 dimension=1024，
    与既有 Chroma collection 对齐。切换模型后需重建向量库（见 MODEL_UPGRADE.md）。
    可选: qwen3.7-text-embedding / text-embedding-v4 / text-embedding-v3
    """

    # ===== ASR 配置 =====
    asr_model: str = "paraformer-v2"
    """
    语音识别模型。当前 ASR 走 DashScope 实时识别接口（Recognition），
    仅支持 paraformer-v2 / paraformer-v1（代码内映射为 paraformer-realtime-v2 / -v1）。
    迁移到 Fun-ASR / Qwen-Audio 录音文件识别是后续独立任务（见 MODEL_UPGRADE.md）。
    """

    asr_timeout_seconds: float = Field(default=300.0, gt=0, le=3600)
    """
    ASR 工作进程总时限（秒），包含启动、上传、识别及收尾。
    实时识别接口对长音频是整段串行上传，10 分钟以上的视频可继续上调（上限 3600）。
    """

    asr_max_audio_size_mb: float = Field(default=32.0, gt=0, le=256)
    """单条 ASR 音频体积上限（MB），约束 SDK 全量预载的内存占用"""

    # ===== 视觉识别配置 =====
    vision_model: str = "qwen3.7-flash"
    """
    多模态视觉识别模型，用于图文笔记OCR文字提取
    推荐 qwen3.7-flash（Qwen3系列，速度快、成本低、长期支持）
    可选:
      - qwen3.7-flash: 快速版，成本最低，适合95%场景（推荐）
      - qwen3.7-plus:  标准版，平衡性价比
      - qwen3.7-max:   高级版，适合复杂图表分析
      - qwen3.8-flash: 最新代，尝鲜使用（可能不稳定）
      - qwen3.6-flash: 老一代（不推荐）
    注意：qwen-vl-* 系列即将下线，请使用 qwen3.x 系列
    """

    vision_request_timeout_seconds: float = Field(default=30.0, ge=5.0, le=120.0)
    """单次 Qwen-VL 调用的请求超时（秒）；SDK 默认 300s 对逐图循环里的单次调用来说太长"""

    # ===== 检索参数 =====
    retrieval_top_k: int = Field(default=8, ge=1, le=50)
    """最终返回的检索结果数量"""

    retrieval_mmr_fetch_k: int = Field(default=32, ge=1, le=200)
    """MMR 检索的候选池大小"""

    retrieval_mmr_lambda: float = Field(default=0.55, ge=0.0, le=1.0)
    """MMR 多样性系数（0=最大多样性，1=最大相关性）"""

    rag_context_count: int = Field(default=5, ge=1, le=20)
    """注入 LLM 的上下文 chunk 数量"""

    rag_prompt_max_context_chars: int = Field(default=8000, ge=100, le=100000)
    """注入 LLM 的上下文最大字符数，超出截断"""

    # ===== 切块参数 =====
    chunk_size: int = Field(default=1000, ge=100, le=10000)
    """文本切块大小（字符数）"""

    chunk_overlap: int = Field(default=200, ge=0, le=2000)
    """相邻文本块重叠字符数"""

    # ===== 对话 =====
    chat_history_window: int = Field(default=6, ge=0, le=50)
    """注入上下文的历史消息数量（最近 N 条）"""

    chat_max_content_chars: int = Field(default=2000, ge=100, le=50000)
    """单条历史消息最大字符数"""

    # ===== 数据库 =====
    database_url: str = "sqlite:///app/storage/douyinrag.db"
    """SQLite 数据库连接 URL"""

    chroma_persist_dir: str = "app/storage/chroma"
    """ChromaDB 向量库持久化目录"""

    # ===== 日志 =====
    access_log_quiet: bool = True
    """静默前端高频轮询接口的成功访问日志（仅 200/304），4xx/5xx 仍会打印"""

    export_tmp_retention_minutes: int = Field(default=60, ge=5, le=1440)
    """浏览器模式批量导出产物在 export_tmp/ 的保留时长（分钟），过期后台清理"""

    worker_task_retention_minutes: int = Field(default=60, ge=5, le=1440)
    """入库 worker 已到终态（done/failed/cancelled）的任务记录保留时长（分钟），过期后台清理；仍在运行的任务不受影响"""

    # ===== 音频缓存生命周期管理 =====
    audio_cache_dir: str = "app/storage/audio_cache"
    """音频下载缓存目录"""

    audio_cache_retention_hours: float = Field(default=24.0, ge=0.0, le=720.0)
    """音频缓存自动保留时长（小时），超过该时长的临时音频自动清理"""

    audio_cache_max_size_mb: float = Field(default=300.0, ge=10.0, le=10000.0)
    """音频缓存最大允许磁盘占用上限（MB），超出时按 LRU 最久未修改时间自动淘汰清理"""

    audio_cache_clean_interval_seconds: int = Field(default=1800, ge=60, le=86400)
    """音频缓存后台自动轮询清理间隔（秒）"""

    download_browser_concurrency: int = Field(default=2, ge=1, le=4)
    """yt-dlp 失败后浏览器兜底解析/下载的并发上限（每路一个 headless Chromium）"""

    # ===== Playwright / 抖音采集 =====
    playwright_headless: bool = False
    """Playwright 是否无头模式（登录时需要可见浏览器扫码）"""

    playwright_user_data_dir: str = "app/storage/playwright_user_data"
    """Playwright 持久化用户数据目录（保存登录状态）"""

    # ===== 用户可编辑的 API 设置（设置面板）=====
    api_settings_path: str = "app/storage/api_settings.json"
    """DashScope Key + 已保存对话供应商列表的本地存储路径，仅保存在本机，不写入数据库"""

    # ===== Bilibili 配置 =====
    bilibili_state_path: str = "app/storage/bilibili_state.json"
    """Bilibili 凭证状态存储路径"""

    bilibili_max_concurrency: int = Field(default=3, ge=1, le=10)
    """Bilibili API 客户端最大并发限制"""

    wbi_cache_ttl_hours: float = Field(default=12.0, ge=1.0, le=72.0)
    """Bilibili WBI 混淆 Key 缓存时长（小时）"""

    bilibili_audio_cache_dir: str = "app/storage/audio_cache/bilibili"
    """Bilibili DASH 音轨流临时下载目录"""

    playwright_browsers_path: str = "app/storage/playwright_browsers"
    """Playwright 浏览器安装路径"""

    playwright_browser_channel: str = "chromium"
    """Playwright 浏览器渠道（chromium / msedge）"""

    douyin_home_url: str = "https://www.douyin.com"
    """抖音首页 URL"""

    douyin_favorites_url: str = "https://www.douyin.com/user/self?from_login=1"
    """抖音收藏夹页面 URL"""

    # ===== 项目路径 =====
    @property
    def project_root(self) -> Path:
        """
        获取项目根目录（backend/ 的上级目录）

        :return: 项目根目录的 Path 对象
        """
        return Path(__file__).resolve().parent.parent

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }


# 全局单例
settings = Settings()


def require_dashscope_key() -> str:
    """
    返回已校验的 DashScope API Key。

    优先读取设置面板保存的 Key（`app/storage/api_settings.json`），
    未保存过则回退到 `.env` 里的 `DASHSCOPE_API_KEY`。

    HTTP 请求头只能是 latin-1；若 key 仍是含中文的占位符（如「你的百炼API_KEY」），
    dashscope SDK 会在 20 层调用栈深处抛 `UnicodeEncodeError: 'latin-1'`，难以定位。
    这里在调用云端前提前给出可读错误。
    """
    from app.services.settings_store import get_dashscope_key
    key = get_dashscope_key().strip()
    if not key:
        raise ValueError("未配置 DASHSCOPE_API_KEY，请在 backend/.env 填入百炼 API Key")
    if not key.isascii():
        raise ValueError(
            "DASHSCOPE_API_KEY 含非 ASCII 字符（可能仍是占位符），请在 backend/.env 填入真实 API Key"
        )
    return key
