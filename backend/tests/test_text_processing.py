"""
文本处理模块测试

测试 clean_text、approx_token_count、build_fixed_chunks、build_chunks_from_segments。
"""
import pytest
from app.services.text_processing import (
    clean_text,
    clean_title_for_index,
    approx_token_count,
    build_fixed_chunks,
    build_chunks_from_segments,
    RawSegment,
)


class TestCleanTitleForIndex:
    """clean_title_for_index：去引流 tag 但保留主题词"""

    def test_trailing_hashtag_run_removed(self):
        assert clean_title_for_index("干货分享 #交易 #期货") == "干货分享"
        assert clean_title_for_index("搭建交易系统 #交易 #交易心得 #期货 #期货交易") == "搭建交易系统"

    def test_leading_topic_hashtag_keeps_word(self):
        assert clean_title_for_index("#Python 进阶：写装饰器") == "Python 进阶：写装饰器"

    def test_mixed_topic_and_trailing_tags(self):
        assert clean_title_for_index("#缠论：三买 #A股 #涨停") == "缠论：三买"

    def test_leading_tag_then_trailing_run_non_empty(self):
        # 句首 tag 当主题词保留，末尾引流串剔除；结果非空（绝不返回空串触发入库拒绝）
        assert clean_title_for_index("#短线交易 #量化模型 #Python炒股") == "短线交易"

    def test_pure_tag_list_no_prose_keeps_words(self):
        # 无正文、少量 tag：不满足「末尾串」条件，退化为仅去 # 号保留全部词
        assert clean_title_for_index("#交易 #期货") == "交易 期货"

    def test_plain_title_unchanged(self):
        assert clean_title_for_index("三买形成的核心条件是什么") == "三买形成的核心条件是什么"

    def test_empty(self):
        assert clean_title_for_index("") == ""
        assert clean_title_for_index("   ") == ""


class TestCleanText:
    """clean_text 函数测试"""

    def test_whitespace_normalization(self):
        """测试空白字符规范化"""
        assert clean_text("你好  世界") == "你好 世界"
        assert clean_text("你好\n\n世界") == "你好 世界"

    def test_fullwidth_space(self):
        """测试全角空格转半角"""
        assert clean_text("你好　　世界") == "你好 世界"

    def test_filler_removal(self):
        """测试无意义语气词移除"""
        result = clean_text("嗯嗯这个啊那个")
        assert "嗯嗯" not in result

    def test_empty_input(self):
        """测试空输入"""
        assert clean_text("") == ""


class TestApproxTokenCount:
    """approx_token_count 函数测试"""

    def test_chinese_only(self):
        """测试纯中文 Token 计数"""
        # "你好世界" = 4 CJK characters
        assert approx_token_count("你好世界") >= 4

    def test_english_only(self):
        """测试纯英文 Token 计数"""
        # "hello world" = 11 chars / 4 ≈ 2 tokens
        assert approx_token_count("hello world") == 2

    def test_mixed(self):
        """测试中英混合 Token 计数"""
        result = approx_token_count("hello 世界")
        # "hello 世界" = 5 latin chars / 4 = 1 + 2 cjk = 3
        assert result == 3

    def test_empty(self):
        """测试空文本"""
        assert approx_token_count("") == 0


class TestBuildFixedChunks:
    """build_fixed_chunks 函数测试"""

    def test_single_chunk(self):
        """测试短文本（一个块）"""
        chunks = build_fixed_chunks("短文本", chunk_size=100, overlap=20)
        assert len(chunks) == 1
        assert chunks[0] == "短文本"

    def test_multiple_chunks(self):
        """测试长文本（多个块）"""
        text = "测" * 250  # 250 characters
        chunks = build_fixed_chunks(text, chunk_size=100, overlap=20)
        assert len(chunks) == 4  # 0-100, 80-180, 160-260→160-250, stop
        assert all(len(c) > 0 for c in chunks)

    def test_empty_text(self):
        """测试空文本"""
        assert build_fixed_chunks("") == []
        assert build_fixed_chunks("   ") == []


class TestBuildChunksFromSegments:
    """build_chunks_from_segments 函数测试"""

    def test_empty_segments(self):
        """测试空片段列表"""
        assert build_chunks_from_segments([]) == []

    def test_short_segments(self):
        """测试少量片段（聚合到一个块）"""
        segments = [
            RawSegment(text="第一句话", start_ms=0, end_ms=1000, lang="zh"),
            RawSegment(text="第二句话", start_ms=1000, end_ms=2000, lang="zh"),
        ]
        chunks = build_chunks_from_segments(segments, max_tokens=100)
        assert len(chunks) == 1
        assert "第一句话" in chunks[0].text
        assert "第二句话" in chunks[0].text

    def test_time_range_preserved(self):
        """测试时间戳保留"""
        segments = [
            RawSegment(text="A" * 50, start_ms=1000, end_ms=3000, lang="zh"),
            RawSegment(text="B" * 50, start_ms=4000, end_ms=6000, lang="zh"),
        ]
        chunks = build_chunks_from_segments(segments, max_tokens=100)
        assert len(chunks) == 1
        assert chunks[0].start_ms == 1000
        assert chunks[0].end_ms == 6000
