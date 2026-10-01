"""知乎采集器的离线契约测试。"""
from __future__ import annotations

import pytest

from app.services.zhihu_collector import ZhihuCollector, ZhihuRequestError


def _collector_without_browser() -> ZhihuCollector:
    return object.__new__(ZhihuCollector)


def test_parse_item_accepts_numeric_ids_and_removes_provider_markup():
    collector = _collector_without_browser()
    item = collector._parse_item({
        "type": "answer",
        "id": 123456,
        "question": {"id": 42, "title": "如何验证数据"},
        "author": {"name": "作者"},
        "content": (
            "<p>第一段正文</p><script>不要索引</script>"
            "<div class='advert'>广告不应进入正文</div><p>第二段正文</p>"
        ),
    })

    assert item is not None
    assert item.platform_item_id == "zhihu:answer:123456"
    assert item.url == "https://www.zhihu.com/question/42/answer/123456"
    assert item.author == "作者"
    assert "第一段正文" in item.text_content
    assert "第二段正文" in item.text_content
    assert "不要索引" not in item.text_content
    assert "广告不应进入正文" not in item.text_content


def test_paginated_fetch_requires_provider_proof_of_completion():
    collector = _collector_without_browser()
    payloads = {
        "https://www.zhihu.com/api/v4/favlists?limit=2&offset=0": {
            "data": [{"id": 1}, {"id": 2}],
            "paging": {"is_end": False, "next": "/api/v4/favlists?limit=2&offset=2"},
        },
        "https://www.zhihu.com/api/v4/favlists?limit=2&offset=2": {
            "data": [{"id": 3}],
            "paging": {"is_end": True, "next": None},
        },
    }
    collector._fetch_json = lambda _page, url: payloads[url]

    rows, complete = collector._fetch_paginated(
        object(),
        "https://www.zhihu.com/api/v4/favlists?limit=2&offset=0",
        page_size=2,
        max_items=10,
        label="收藏夹",
    )

    assert [row["id"] for row in rows] == [1, 2, 3]
    assert complete is True


def test_page_without_provider_end_marker_is_incomplete():
    collector = _collector_without_browser()
    collector._fetch_json = lambda _page, _url: {"data": [{"id": 1}, {"id": 2}]}

    rows, complete = collector._fetch_paginated(
        object(),
        "https://www.zhihu.com/api/v4/favlists?limit=2&offset=0",
        page_size=2,
        max_items=10,
        label="收藏夹",
    )

    assert len(rows) == 2
    assert complete is False


def test_malformed_success_response_is_incomplete():
    collector = _collector_without_browser()
    collector._fetch_json = lambda _page, _url: {"data": {"unexpected": "shape"}}

    rows, complete = collector._fetch_paginated(
        object(),
        "https://www.zhihu.com/api/v4/favlists?limit=50&offset=0",
        page_size=50,
        max_items=100,
        label="收藏夹",
    )

    assert rows == []
    assert complete is False


def test_local_limit_prevents_complete_snapshot_when_provider_has_more_rows():
    collector = _collector_without_browser()
    payloads = {
        "https://www.zhihu.com/api/v4/favlists?limit=2&offset=0": {
            "data": [{"id": 1}, {"id": 2}],
            "paging": {"is_end": False, "next": "/api/v4/favlists?limit=2&offset=2"},
        },
        "https://www.zhihu.com/api/v4/favlists?limit=2&offset=2": {
            "data": [{"id": 3}, {"id": 4}],
            "paging": {"is_end": True},
        },
    }
    collector._fetch_json = lambda _page, url: payloads[url]

    rows, complete = collector._fetch_paginated(
        object(),
        "https://www.zhihu.com/api/v4/favlists?limit=2&offset=0",
        page_size=2,
        max_items=3,
        label="收藏夹",
    )

    assert [row["id"] for row in rows] == [1, 2, 3]
    assert complete is False


def test_page_collection_projection_deduplicates_and_rejects_untrusted_ids():
    rows = ZhihuCollector._normalise_page_collections([
        {"id": "42", "title": "工作"},
        {"id": "42", "title": "重复"},
        {"id": "not-an-id", "title": "忽略"},
        {"id": "99", "title": ""},
    ])

    assert rows == [
        {"id": "42", "title": "工作"},
        {"id": "99", "title": "收藏夹 99"},
    ]


def test_page_item_projection_accepts_only_public_answer_or_article_urls():
    rows = ZhihuCollector._normalise_page_items([
        {"type": "answer", "id": "10", "url": "https://www.zhihu.com/question/1/answer/10", "title": "回答", "excerpt": "正文"},
        {"type": "article", "id": "20", "url": "https://zhuanlan.zhihu.com/p/20", "title": "文章", "author": "作者", "excerpt": "内容"},
        {"type": "answer", "id": "10", "url": "https://www.zhihu.com/question/1/answer/10"},
        {"type": "answer", "id": "30", "url": "https://example.test/answer/30"},
    ])

    assert [row["content"]["id"] for row in rows] == ["10", "20"]
    assert rows[1]["content"]["author"] == {"name": "作者"}


def test_fetch_json_rejects_a_success_status_with_html_content():
    class _HtmlPage:
        def evaluate(self, *_args):
            return {"status": 200, "contentType": "text/html; charset=utf-8", "text": "null"}

    with pytest.raises(ZhihuRequestError, match="没有返回 JSON"):
        ZhihuCollector._fetch_json(_HtmlPage(), "https://www.zhihu.com/api/v4/favlists")

class _QrElement:
    def __init__(self, *, visible=True, box=None, image_ready=True, payload=None):
        self.visible = visible
        self.box = box or {'width': 120, 'height': 120}
        self.image_ready = image_ready
        if payload is None:
            import io
            import qrcode
            buffer = io.BytesIO()
            qrcode.make('https://www.zhihu.com/test-login').save(buffer, format='PNG')
            payload = buffer.getvalue()
        self.payload = payload

    def is_visible(self):
        return self.visible

    def bounding_box(self):
        return self.box

    def evaluate(self, script):
        return self.image_ready

    def screenshot(self, *, type):
        assert type == 'png'
        return self.payload


class _QrLocator:
    def __init__(self, elements):
        self.elements = elements

    def count(self):
        return len(self.elements)

    def nth(self, index):
        return self.elements[index]


class _QrPage:
    def __init__(self, canvas=(), image=()):
        self.canvas = _QrLocator(canvas)
        self.image = _QrLocator(image)
        self.selectors = []

    def locator(self, selector):
        self.selectors.append(selector)
        if selector == 'div.Qrcode-container div.Qrcode-img canvas.Qrcode-qrcode':
            return self.canvas
        if selector == 'div.Qrcode-container div.Qrcode-img img':
            return self.image
        raise AssertionError(f'unexpected broad selector: {selector}')


def test_qrcode_capture_only_reads_the_exact_zhihu_canvas():
    element = _QrElement()
    page = _QrPage(canvas=[element])

    encoded = ZhihuCollector._capture_qrcode(page)

    assert encoded is not None
    import base64
    assert base64.b64decode(encoded).startswith(b'\x89PNG\r\n\x1a\n')
    assert page.selectors == ['div.Qrcode-container div.Qrcode-img canvas.Qrcode-qrcode']


def test_qrcode_capture_rejects_hidden_or_wrong_shaped_canvas():
    hidden = _QrElement(visible=False)
    narrow = _QrElement(box={'width': 20, 'height': 120})

    assert ZhihuCollector._capture_qrcode(_QrPage(canvas=[hidden])) is None
    assert ZhihuCollector._capture_qrcode(_QrPage(canvas=[narrow])) is None


def test_qrcode_capture_accepts_a_cross_origin_tainted_canvas():
    class _TaintedCanvas(_QrElement):
        def evaluate(self, _script):
            raise RuntimeError('SecurityError: canvas is tainted by cross-origin data')

    encoded = ZhihuCollector._capture_qrcode(_QrPage(canvas=[_TaintedCanvas()]))

    assert encoded is not None


def test_qrcode_wait_requires_two_matching_complete_captures(monkeypatch):
    collector = _collector_without_browser()
    collector._cancel = __import__('threading').Event()
    collector._lock = __import__('threading').RLock()
    collector._qrcode_image_base64 = None
    collector.message = ''
    captures = iter(['stable-qr', 'stable-qr'])
    monkeypatch.setattr(collector, '_capture_qrcode', lambda _page: next(captures))
    monkeypatch.setattr('app.services.zhihu_collector.time.sleep', lambda _seconds: None)

    collector._wait_for_qrcode(object())

    assert collector._qrcode_image_base64 == 'stable-qr'


def test_start_login_keeps_an_existing_valid_session_without_worker():
    collector = _collector_without_browser()
    collector._lock = __import__('threading').RLock()
    collector._thread = None
    collector._qrcode_image_base64 = 'old-qr'
    collector.status = 'idle'
    collector.message = ''
    collector._check_saved_login = lambda: True

    success, message = collector.start_login()

    assert success is True
    assert '凭证有效' in message
    assert collector.status == 'logged_in'
    assert collector._qrcode_image_base64 is None


def test_expired_session_invalidation_removes_local_credentials(monkeypatch, tmp_path):
    collector = _collector_without_browser()
    collector._lock = __import__('threading').RLock()
    collector._state_path = tmp_path / 'zhihu_state.json'
    collector._profile_path = tmp_path / 'zhihu_profile.json'
    collector._profile = {'nickname': 'test'}
    collector._qrcode_image_base64 = 'old-qr'
    collector.status = 'logged_in'
    collector.message = '已登录（凭证有效）'
    deleted = []
    monkeypatch.setattr('app.services.zhihu_collector.delete_json', lambda path: deleted.append(path))

    collector._invalidate_saved_login('知乎登录态已失效，请重新登录')

    assert deleted == [collector._state_path, collector._profile_path]
    assert collector.status == 'idle'
    assert collector._profile == {}
    assert collector._qrcode_image_base64 is None
