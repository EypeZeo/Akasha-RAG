"""
RAG 服务模块测试

测试查询路由、答案清洗、提示词构建等核心逻辑。
"""
import pytest
from app.services.rag_service import (
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
        assert _is_greeting("我是谁") is False
        assert _is_greeting("介绍一下AI技术") is False

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
