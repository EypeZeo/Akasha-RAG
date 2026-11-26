"""Exercise the web reader against ordinary browser requests with offline responses."""

from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import sync_playwright

from app.services.douyin_favorites_page import DouyinFavoritesPageError, read_favorites_page

_URL = "https://www.douyin.com/user/self?showTab=favorite_collection"
_HTML = """<!doctype html><meta charset="utf-8">
<button role="tab" onclick="loadFolders()">收藏夹</button>
<main></main>
<script>
let folder;
let listed;
const root = document.querySelector('main');
const base = '/aweme/v1/web/collects/';
function preview(id) {
    return fetch(base + 'video/list/?collects_id=' + id + '&cursor=0');
}
function showFolders() {
    folder = null;
    root.innerHTML = '<ul data-e2e="scroll-list"></ul>';
    for (const row of listed.collects_list) {
        const li = document.createElement('li');
        const name = document.createElement('p');
        name.textContent = row.collects_name;
        name.onclick = () => {
            folder = row.collects_id_str;
            root.innerHTML = '<button onclick="showFolders()">返回</button>';
        };
        li.append(name);
        root.firstChild.append(li);
    }
}
async function loadFolders() {
    listed = await (await fetch(base + 'list/?cursor=0')).json();
    await Promise.all(listed.collects_list.map(row => preview(row.collects_id_str)));
    showFolders();
}
document.addEventListener('keydown', event => {
    if (event.key === 'End' && folder) {
        fetch(base + 'video/list/?collects_id=' + folder + '&cursor=10');
    }
});
</script>"""


@pytest.mark.parametrize("repeat_cursor", [False, True])
def test_reads_prefetched_first_page_and_follows_scroll_for_duplicate_folder_names(repeat_cursor):
    with sync_playwright() as runtime:
        if not Path(runtime.chromium.executable_path).is_file():
            pytest.skip("Playwright Chromium is not installed")
        browser = runtime.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            pages_seen = []

            def respond(route):
                url = urlsplit(route.request.url)
                if url.path == "/user/self":
                    route.fulfill(content_type="text/html; charset=utf-8", body=_HTML)
                    return
                if url.path.endswith("/collects/list/"):
                    route.fulfill(json={
                        "status_code": 0,
                        "cursor": 2,
                        "has_more": False,
                        "collects_list": [
                            {"collects_id_str": "100", "collects_name": "Saved", "total_number": 2},
                            {"collects_id_str": "200", "collects_name": "Saved", "total_number": 1},
                        ],
                    })
                    return
                if not url.path.endswith("/collects/video/list/"):
                    route.abort()
                    return
                query = parse_qs(url.query)
                folder, cursor = query["collects_id"][0], query["cursor"][0]
                pages_seen.append((folder, cursor))
                more = folder == "100" and (cursor == "0" or repeat_cursor)
                route.fulfill(json={
                    "status_code": 0,
                    "has_more": int(more),
                    "cursor": 10 if more else 0,
                    "aweme_list": [{"aweme_id": folder + cursor}],
                })

            page.route("**/*", respond)
            if repeat_cursor:
                with pytest.raises(DouyinFavoritesPageError, match="invalid_cursor"):
                    read_favorites_page(page, _URL, lambda _page: None, lambda: False)
            else:
                result = read_favorites_page(page, _URL, lambda _page: None, lambda: False)
                assert result.is_complete
                assert result.works == {
                    "100": [{"aweme_id": "1000"}, {"aweme_id": "10010"}],
                    "200": [{"aweme_id": "2000"}],
                }
            assert pages_seen.count(("100", "0")) == 1
            assert pages_seen.count(("100", "10")) == 1
            assert pages_seen.count(("200", "0")) == 1
        finally:
            browser.close()
