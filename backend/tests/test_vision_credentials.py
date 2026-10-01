"""Vision must use protected storage and handle fully rejected image lists."""
from app.core.config import settings
from app.core.secure_storage import write_json
from app.services.vision_service import VisionService


def test_vision_reads_credentials_through_protected_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "playwright_user_data_dir", str(tmp_path))
    write_json(tmp_path / "state.json", {"cookies": [
        {"name": "sessionid", "value": "unit-cookie"},
        {"missing": "name"},
    ]})
    assert VisionService()._get_cookies() == {"sessionid": "unit-cookie"}


def test_vision_returns_empty_when_all_image_urls_are_rejected(monkeypatch):
    monkeypatch.setattr(settings, "dashscope_api_key", "unit-test-key")
    assert VisionService().extract_text_from_images(["http://127.0.0.1/private"]) == ""
