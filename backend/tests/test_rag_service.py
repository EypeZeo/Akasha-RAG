"""
RAG 服务模块测试

测试查询路由、答案清洗、提示词构建等核心逻辑。
"""
from unittest.mock import Mock

import pytest
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
