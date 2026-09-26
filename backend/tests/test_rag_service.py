"""
RAG 服务模块测试

测试查询路由、答案清洗、提示词构建等核心逻辑。
"""
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.services import rag_service as rag_module
from app.services.rag_service import (
    RagService,
    _is_greeting,
    _is_list_query,
    _is_summary_query,
    _is_structured_query,
    _normalize_query,
    _sanitize_answer,
)


class TestQueryRouting:
    """查询路由规则测试"""

    def test_greeting_detection(self):
        """测试问候语识别"""
        assert _is_greeting("你好") is True
        assert _is_greeting("在吗") is True
        assert _is_greeting("hello") is True
        assert _is_greeting("hi") is True
        assert _is_greeting("Hi, can you help?") is True
        assert _is_greeting("我是谁") is False
        assert _is_greeting("介绍一下AI技术") is False

    def test_greeting_detection_does_not_misfire_on_english_substrings(self):
        """BUG-01: "hi" 子串误判——这些问题里的 hi 都不是独立单词，不应被判成问候语"""
        assert _is_greeting("which video mentions python?") is False
        assert _is_greeting("this is a question") is False
        assert _is_greeting("history of the project") is False
        assert _is_greeting("machine learning basics") is False
        assert _is_greeting("what is the architecture") is False
        assert _is_greeting("shipping details") is False

    def test_list_query_detection(self):
        """测试列表类查询识别"""
        assert _is_list_query("有哪些视频") is True
        assert _is_list_query("列出所有的") is True
        assert _is_list_query("给我一个清单") is True
        assert _is_list_query("讲讲AI技术") is False

    def test_summary_query_detection(self):
        """测试总结类查询识别"""
        assert _is_summary_query("总结一下") is True
        assert _is_summary_query("帮我概括") is True
        assert _is_summary_query("回顾一下") is True
        assert _is_summary_query("什么是RAG") is False

    def test_structured_query_detection(self):
        """测试结构化查询识别"""
        assert _is_structured_query("帮我对比A和B") is True
        assert _is_structured_query("归纳一下要点") is True
        assert _is_structured_query("你好") is False


class TestQueryNormalization:
    """查询规范化测试"""

    def test_whitespace_collapse(self):
        """测试多余空格合并"""
        assert _normalize_query("你好   世界") == "你好 世界"
        assert _normalize_query("  前面有空格") == "前面有空格"

    def test_no_change(self):
        """测试无需修改的查询"""
        assert _normalize_query("什么是RAG") == "什么是RAG"


class TestAnswerSanitization:
    """答案清洗测试"""

    def test_markdown_header_removal(self):
        """测试 Markdown 标题符号移除"""
        result = _sanitize_answer("### 重要结论\n这是内容", False)
        assert "###" not in result
        assert "重要结论" in result

    def test_bullet_formatting(self):
        """测试列表格式处理"""
        result = _sanitize_answer("- 第一点\n- 第二点", True)
        assert "第一点" in result
        assert "第二点" in result

    def test_empty_input(self):
        """测试空输入"""
        assert _sanitize_answer("", False) == ""

    def test_pipe_table_removal(self):
        """测试管道表格处理"""
        result = _sanitize_answer("| 列1 | 列2 |\n|数据|内容|", False)
        # 管道被转换为空格分隔
        assert "列1 列2" in result

    def test_fenced_code_block_preserved(self):
        """围栏代码块必须逐字保留：反引号、缩进、竖线、换行都不动。"""
        answer = (
            "这是一段说明文字。\n\n"
            "```python\n"
            "import csv\n"
            "with open('data.csv') as f:\n"
            "    reader = csv.reader(f)\n"
            "    for row in reader:\n"
            "        print(row)\n"
            "```\n\n"
            "结束语。"
        )
        result = _sanitize_answer(answer, False)
        assert "```python" in result
        assert "import csv" in result
        assert "    reader = csv.reader(f)" in result  # 缩进保留
        assert "        print(row)" in result
        # 代码块外的口语文字仍被合并 / 清洗
        assert "这是一段说明文字。" in result

    def test_inline_prose_still_merged_around_code(self):
        result = _sanitize_answer("第一行\n第二行\n```\nraw|code|line\n```\n收尾", False)
        assert "raw|code|line" in result       # 代码里的竖线不被当表格
        assert "```" in result


class TestDbContentPromptSurvivesSanitization:
    """BUG-07: db_content 提示词要求的输出格式必须是 _sanitize_answer 之后
    真的还在的东西，不能承诺一个马上被剥离的 Markdown 结构。"""

    def test_prompt_does_not_ask_for_markdown_headings(self):
        service = RagService()
        system, _user, _is_structured = service._build_prompts(
            "db_content", "总结一下", context="内容片段", history=""
        )
        assert "## " not in system

    def test_plain_text_section_label_survives_sanitization(self):
        answer = (
            "TL;DR：\n"
            "- 核心结论一\n"
            "- 核心结论二\n\n"
            "共同主题：\n"
            "都提到了效率\n\n"
            "各视频要点：\n"
            "视频A讲了X [来源: 视频A]"
        )
        result = _sanitize_answer(answer, True)
        assert "TL;DR：" in result
        assert "共同主题：" in result
        assert "各视频要点：" in result


class TestDbContentRoutingIsUnifiedAcrossAskAndStream:
    """BUG-08: /ask 与 /ask/stream 对 db_content 路由的检索行为不能各走一套。"""

    def test_retrieve_hits_for_route_does_dense_retrieval_for_vector_and_db_content(self, monkeypatch):
        service = RagService()
        fake_hits = [{"platform_item_id": "x", "content_item_id": 1}]
        monkeypatch.setattr(service, "_dense_retrieve", Mock(return_value=fake_hits))
        monkeypatch.setattr(service, "_filter_hits_to_done_items", lambda db, hits: hits)

        for route in ("vector", "db_content"):
            result = service._retrieve_hits_for_route(route, "q", db=None, scope_ids=None, platform=None)
            assert result == fake_hits

    def test_retrieve_hits_for_route_skips_retrieval_for_other_routes(self, monkeypatch):
        service = RagService()
        dense = Mock(side_effect=AssertionError("should not retrieve for this route"))
        monkeypatch.setattr(service, "_dense_retrieve", dense)

        for route in ("direct", "db_list"):
            assert service._retrieve_hits_for_route(route, "q", db=None, scope_ids=None, platform=None) == []
        dense.assert_not_called()

    def test_ask_and_ask_stream_call_the_shared_retrieval_helper_identically(self, monkeypatch):
        """端到端级别的接线测试：同一个 db_content 场景下，非流式与流式接口
        必须都调用同一个共享方法、传相同的参数——不能一个悄悄多检索一次。"""
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)

        recorded_calls = []

        def _fake_retrieve(self, route, query, db, scope_ids, platform):
            recorded_calls.append(route)
            return []

        monkeypatch.setattr(rag_module, "get_chroma_service", lambda: Mock(count=lambda: 5))
        monkeypatch.setattr(rag_module.llm_client, "chat", lambda **kw: "回答内容")
        monkeypatch.setattr(RagService, "_retrieve_hits_for_route", _fake_retrieve)

        service = RagService()
        with factory() as db:
            service.answer(db, "总结一下", session_id=None)
            list(service.answer_stream(db, "总结一下", session_id=None))

        assert recorded_calls == ["db_content", "db_content"]

    def test_online_traces_do_not_expose_retrieved_chunk_text(self, monkeypatch):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        hits = [{
            "chunk_id": "chunk-1",
            "platform": "douyin",
            "platform_item_id": "item-1",
            "content_item_id": 1,
            "canonical_url": "https://example.test/item-1",
            "title": "Synthetic title",
            "score": 0.91,
            "text": "private transcript sentence that must not enter the trace",
        }]
        monkeypatch.setattr(rag_module, "get_chroma_service", lambda: Mock(count=lambda: 1))
        monkeypatch.setattr(RagService, "_retrieve_hits_for_route", lambda *a, **kw: hits)
        monkeypatch.setattr(rag_module.llm_client, "chat", lambda **kw: "safe answer")
        monkeypatch.setattr(rag_module.llm_client, "stream_chat", lambda **kw: (value for value in ["safe answer"]))

        service = RagService()
        with factory() as db:
            answer_trace = service.answer(db, "question", session_id=None)["trace"]
        with factory() as db:
            stream_events = list(service.answer_stream(db, "question", session_id=None))
        stream_trace = next(payload["trace"] for event, payload in stream_events if event == "meta")

        for trace in (answer_trace, stream_trace):
            assert trace["chunks"] == [{"chunk_id": "chunk-1", "title": "Synthetic title", "score": 0.91}]
            assert all("text" not in chunk for chunk in trace["chunks"])


class TestAnswerStreamExplicitGeneratorCleanup:
    """PR2B-5: answer_stream 必须显式 close 内层 stream_chat 生成器，不能
    只靠 CPython 引用计数回收时机来触发它的 close()（进而释放模型调用
    闸门名额）。"""

    def _service_and_db(self, monkeypatch):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(rag_module, "get_chroma_service", lambda: Mock(count=lambda: 5))
        monkeypatch.setattr(RagService, "_retrieve_hits_for_route", lambda *a, **kw: [])
        return RagService(), factory

    def test_closing_answer_stream_early_closes_the_inner_llm_generator(self, monkeypatch):
        service, factory = self._service_and_db(monkeypatch)

        inner_state = {"closed": False}

        def fake_stream_chat(**kwargs):
            try:
                yield "a"
                yield "b"
                yield "c"
            finally:
                inner_state["closed"] = True

        monkeypatch.setattr(rag_module.llm_client, "stream_chat", fake_stream_chat)

        with factory() as db:
            gen = service.answer_stream(db, "总结一下", session_id=None)
            first = next(gen)  # ("sources", {...})
            assert first[0] == "sources"
            second = next(gen)  # ("delta", {"text": "a"})
            assert second == ("delta", {"text": "a"})
            assert inner_state["closed"] is False

            gen.close()
            assert inner_state["closed"] is True

    def test_normal_exhaustion_still_closes_the_inner_generator_without_error(self, monkeypatch):
        service, factory = self._service_and_db(monkeypatch)

        inner_state = {"closed": False}

        def fake_stream_chat(**kwargs):
            try:
                yield "answer text"
            finally:
                inner_state["closed"] = True

        monkeypatch.setattr(rag_module.llm_client, "stream_chat", fake_stream_chat)

        with factory() as db:
            events = list(service.answer_stream(db, "总结一下", session_id=None))

        assert inner_state["closed"] is True
        assert events[-1][0] == "done"


class TestDenseRetrieveEmptyScope:
    """BUG-03: 空 scope（收藏夹存在但没有内容）必须直接返回空，不能退化成全库检索"""

    def test_empty_scope_short_circuits_before_embedding_or_search(self, monkeypatch):
        fake_embed = Mock(side_effect=AssertionError("不应该为空 scope 计算 embedding"))
        fake_chroma = Mock()
        fake_chroma.search.side_effect = AssertionError("不应该为空 scope 调用向量检索")
        monkeypatch.setattr(rag_module, "embedding_client", Mock(embed_text=fake_embed))
        monkeypatch.setattr(rag_module, "get_chroma_service", lambda: fake_chroma)

        service = RagService()
        result = service._dense_retrieve("随便问点什么", scope_ids=set())

        assert result == []
        fake_embed.assert_not_called()
        fake_chroma.search.assert_not_called()

    def test_none_scope_still_searches_normally(self, monkeypatch):
        fake_embed = Mock(return_value=[0.1] * 8)
        fake_chroma = Mock()
        fake_chroma.search.return_value = [{"platform_item_id": "x"}]
        monkeypatch.setattr(rag_module, "embedding_client", Mock(embed_text=fake_embed))
        monkeypatch.setattr(rag_module, "get_chroma_service", lambda: fake_chroma)

        service = RagService()
        result = service._dense_retrieve("随便问点什么", scope_ids=None)

        assert result == [{"platform_item_id": "x"}]
        fake_chroma.search.assert_called_once()
