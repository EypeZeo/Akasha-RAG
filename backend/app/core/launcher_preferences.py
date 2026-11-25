"""Local preferences read by both the launcher and the web application."""

import locale
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal, cast, get_args

LauncherLanguage = Literal["zh", "en", "ja", "fr", "de", "ko", "ru", "hi"]
SUPPORTED_LAUNCHER_LANGUAGES = get_args(LauncherLanguage)
TERMINAL_LANGUAGE_ENV = "AKASHA_TERMINAL_LANGUAGE"
LAUNCHER_LANGUAGE_PATH = Path(__file__).resolve().parents[3] / ".runtime" / "launcher-language.txt"


def get_launcher_language() -> LauncherLanguage | None:
    try:
        language = LAUNCHER_LANGUAGE_PATH.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None
    if language in SUPPORTED_LAUNCHER_LANGUAGES:
        return cast(LauncherLanguage, language)
    return None


def resolve_launcher_language() -> LauncherLanguage:
    saved = get_launcher_language()
    if saved is not None:
        return saved
    env = (os.environ.get("AKASHA_LANG") or "").strip().lower()
    if env[:2] in SUPPORTED_LAUNCHER_LANGUAGES:
        return cast(LauncherLanguage, env[:2])
    try:
        probe = (locale.getlocale()[0] or "").lower()
    except Exception:
        probe = ""
    probe = f"{probe} {os.environ.get('LANG', '').lower()}"
    if "zh" in probe or "chinese" in probe:
        return "zh"
    if "ja" in probe or "japan" in probe:
        return "ja"
    if "ko" in probe or "korea" in probe:
        return "ko"
    if "fr" in probe or "french" in probe:
        return "fr"
    if "de" in probe or "german" in probe:
        return "de"
    if "ru" in probe or "russ" in probe:
        return "ru"
    if "hi" in probe or "hind" in probe:
        return "hi"
    return "en"


def set_launcher_language(language: LauncherLanguage | None) -> None:
    path = LAUNCHER_LANGUAGE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, mode="w", encoding="utf-8", delete=False) as file:
        temporary = Path(file.name)
        try:
            file.write(language or "")
            file.close()
            temporary.replace(path)
        finally:
            file.close()
            temporary.unlink(missing_ok=True)
