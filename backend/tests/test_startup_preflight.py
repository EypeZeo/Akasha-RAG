from pathlib import Path

from app.core.startup_preflight import (
    CONFIGURATION_REQUIRED_EXIT_CODE,
    ensure_and_validate,
    is_placeholder_api_key,
    main,
)


def _write_example(backend_dir: Path) -> None:
    (backend_dir / ".env.example").write_text(
        "DASHSCOPE_API_KEY=sk-your-dashscope-key\n"
        "DEEPSEEK_API_KEY=sk-your-deepseek-key\n",
        encoding="utf-8",
    )


def test_missing_env_is_created_from_template_and_blocks_startup(tmp_path: Path):
    _write_example(tmp_path)

    result = ensure_and_validate(tmp_path)

    assert result.config_created is True
    assert result.missing_keys == ("DASHSCOPE_API_KEY", "DEEPSEEK_API_KEY")
    assert (tmp_path / ".env").read_text(encoding="utf-8") == (
        tmp_path / ".env.example"
    ).read_text(encoding="utf-8")


def test_existing_env_is_preserved_and_valid_keys_pass(tmp_path: Path):
    _write_example(tmp_path)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "DASHSCOPE_API_KEY=sk-dashscope-real-key\n"
        "DEEPSEEK_API_KEY=sk-deepseek-real-key\n",
        encoding="utf-8",
    )

    result = ensure_and_validate(tmp_path)

    assert result.config_created is False
    assert result.is_ready is True
    assert not env_path.exists()
    assert (tmp_path / ".env.dpapi").is_file()
    assert ensure_and_validate(tmp_path).is_ready is True
    assert main(["--backend-dir", str(tmp_path)]) == 0


def test_placeholder_detection_and_failure_exit_code(tmp_path: Path, capsys):
    _write_example(tmp_path)
    (tmp_path / ".env").write_text(
        "DASHSCOPE_API_KEY=your-key-here\nDEEPSEEK_API_KEY=\n",
        encoding="utf-8",
    )

    assert is_placeholder_api_key("你的百炼 Key") is True
    assert is_placeholder_api_key("sk-real-key") is False
    assert main(["--backend-dir", str(tmp_path)]) == CONFIGURATION_REQUIRED_EXIT_CODE
    output = capsys.readouterr().out
    assert "DASHSCOPE_API_KEY" in output
    assert "DEEPSEEK_API_KEY" in output
    assert "your-key-here" not in output
