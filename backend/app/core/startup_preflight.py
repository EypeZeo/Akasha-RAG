"""Local startup configuration checks shared by the Windows bootstrapper and launcher."""
from __future__ import annotations

import argparse
import shutil
from dataclasses import dataclass
from pathlib import Path


REQUIRED_API_KEYS = ("DASHSCOPE_API_KEY", "DEEPSEEK_API_KEY")
CONFIGURATION_REQUIRED_EXIT_CODE = 20


@dataclass(frozen=True)
class PreflightResult:
    config_created: bool
    missing_keys: tuple[str, ...]

    @property
    def is_ready(self) -> bool:
        return not self.missing_keys


def _read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name.strip()] = value.strip().strip('"').strip("'")
    return values


def is_placeholder_api_key(value: str | None) -> bool:
    normalized = (value or "").strip()
    if not normalized:
        return True
    lowered = normalized.casefold()
    markers = ("your", "your-", "placeholder", "example", "changeme", "<", ">", "你的", "百炼")
    return any(marker in lowered for marker in markers)


def ensure_and_validate(backend_dir: Path) -> PreflightResult:
    backend_dir = backend_dir.resolve()
    env_path = backend_dir / ".env"
    example_path = backend_dir / ".env.example"
    created = False

    if not env_path.exists():
        if not example_path.is_file():
            raise FileNotFoundError(f"Missing configuration template: {example_path}")
        shutil.copyfile(example_path, env_path)
        created = True

    values = _read_dotenv(env_path)
    missing = tuple(name for name in REQUIRED_API_KEYS if is_placeholder_api_key(values.get(name)))
    return PreflightResult(config_created=created, missing_keys=missing)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Akasha-RAG local startup configuration")
    parser.add_argument("--backend-dir", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    backend_dir = args.backend_dir.resolve()
    env_path = backend_dir / ".env"

    try:
        result = ensure_and_validate(backend_dir)
    except (OSError, UnicodeError) as exc:
        print(f"[CONFIG] Unable to prepare {env_path}: {exc}")
        return CONFIGURATION_REQUIRED_EXIT_CODE

    if result.config_created:
        print(f"[CONFIG] Created configuration template: {env_path}")
    if not result.is_ready:
        print(f"[CONFIG] Startup blocked. Fill these values in: {env_path}")
        for name in result.missing_keys:
            print(f"[CONFIG]   - {name}")
        print("[CONFIG] API key values are never printed by the launcher.")
        return CONFIGURATION_REQUIRED_EXIT_CODE

    print("[CONFIG] API key configuration verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
