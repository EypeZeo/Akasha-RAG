"""Read favorites responses produced by ordinary navigation in the Douyin web app."""

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import Error as PlaywrightError, Page, Response

_FOLDERS = "/aweme/v1/web/collects/list/"
_WORKS = "/aweme/v1/web/collects/video/list/"
_MAX_FOLDERS = 100
_MAX_WORKS = 500


class DouyinFavoritesPageError(RuntimeError):
    def __init__(self, code: str, provider_status_code: int | None = None) -> None:
        super().__init__(f"Unable to read Douyin favorites from the web page ({code})")
        self.code = code
        self.provider_status_code = provider_status_code


@dataclass
class FavoritePages:
    folders: list[dict]
    works: dict[str, list[dict]]
    is_complete: bool


class _FavoritesReader:
    def __init__(self, page: Page, hostname: str, cancelled: Callable[[], bool]) -> None:
        self.page = page
        self.hostname = hostname
        self.cancelled = cancelled
        self.responses: dict[tuple[str, str, str], Response] = {}

    def _key(self, response: Response) -> tuple[str, str, str] | None:
        url = urlsplit(response.url)
        if url.scheme != "https" or url.hostname != self.hostname or url.path not in (_FOLDERS, _WORKS):
            return None
        if response.request.method != "GET":
            return None
        query = parse_qs(url.query)
        return url.path, query.get("collects_id", [""])[0], query.get("cursor", ["0"])[0]

    def remember(self, response: Response) -> None:
        key = self._key(response)
        if key is not None:
            self.responses[key] = response

    def _response(self, key: tuple[str, str, str]) -> Response:
        response = self.responses.get(key)
        if response is not None:
            return response
        return self.page.wait_for_event("response", predicate=lambda item: self._key(item) == key, timeout=12_000)

    def pages(self, path: str, field: str, limit: int, max_pages: int, folder_id: str = "") -> tuple[list[dict], bool]:
        rows: list[dict] = []
        cursors: set[str] = set()
        cursor = "0"
        for _ in range(max_pages):
            if self.cancelled():
                raise DouyinFavoritesPageError("cancelled")
            key = path, folder_id, cursor
            if cursors and key not in self.responses:
                self.page.keyboard.press("End")
            response = self._response(key)
            if not response.ok:
                raise DouyinFavoritesPageError("http_status", response.status)
            try:
                data = response.json()
            except (ValueError, PlaywrightError):
                raise DouyinFavoritesPageError("invalid_response") from None
            if not isinstance(data, dict) or type(data.get("status_code")) is not int:
                raise DouyinFavoritesPageError("invalid_response")
            if data["status_code"] != 0:
                raise DouyinFavoritesPageError("provider_status", data["status_code"])
            more = data.get("has_more")
            page_rows = data.get(field)
            # On an empty final page the provider can return a literal null list.
            if field in data and page_rows is None and (more is False or more == 0):
                page_rows = []
            if type(more) not in (bool, int) or more not in (0, 1) or not isinstance(page_rows, list):
                raise DouyinFavoritesPageError("invalid_response")
            if any(not isinstance(row, dict) for row in page_rows):
                raise DouyinFavoritesPageError("invalid_response")
            rows.extend(page_rows)
            if len(rows) > limit:
                return rows[:limit], False
            if not more:
                return rows, True
            if len(rows) == limit:
                return rows, False
            cursors.add(cursor)
            cursor = str(data.get("cursor"))
            if not cursor.isascii() or not cursor.isdigit() or cursor in cursors:
                raise DouyinFavoritesPageError("invalid_cursor")
        return rows, False


def read_favorites_page(
    page: Page,
    target_url: str,
    after_navigation: Callable[[Page], None],
    cancelled: Callable[[], bool],
) -> FavoritePages:
    reader = _FavoritesReader(page, urlsplit(target_url).hostname or "", cancelled)
    page.on("response", reader.remember)
    try:
        page.goto(target_url, wait_until="commit", timeout=35_000)
        after_navigation(page)
        page.get_by_role("tab", name="收藏夹", exact=True).click(timeout=25_000)
        folders, complete = reader.pages(_FOLDERS, "collects_list", _MAX_FOLDERS, 30)

        works: dict[str, list[dict]] = {}
        seen_titles: Counter[str] = Counter()
        returned_folders = []
        for folder in folders:
            if cancelled():
                raise DouyinFavoritesPageError("cancelled")
            folder_id = str(folder.get("collects_id_str") or folder.get("collects_id") or "")
            title = folder.get("collects_name")
            if not folder_id.isascii() or not folder_id.isdigit() or not isinstance(title, str) or not title.strip():
                raise DouyinFavoritesPageError("invalid_folder")
            if folder_id in works:
                complete = False
                continue
            title = title.strip()
            # The folder card may have already loaded cursor zero for its preview.
            cards = page.locator('[data-e2e="scroll-list"] li')
            cards.get_by_text(title, exact=True).nth(seen_titles[title]).click(timeout=12_000)
            back = page.get_by_text("返回", exact=True)
            back.wait_for(state="visible", timeout=12_000)
            rows, folder_complete = reader.pages(_WORKS, "aweme_list", _MAX_WORKS, 120, folder_id)
            works[folder_id] = rows
            returned_folders.append(folder)
            complete = complete and folder_complete
            seen_titles[title] += 1
            back.click(timeout=12_000)
        return FavoritePages(returned_folders, works, complete)
    except PlaywrightError:
        raise DouyinFavoritesPageError("page_unavailable") from None
    finally:
        page.remove_listener("response", reader.remember)
