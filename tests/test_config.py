from __future__ import annotations

import json
from pathlib import Path

from atsm.config import ENV_DATA_DIR, Settings, data_dir, load_settings, save_settings


def test_defaults_match_spec() -> None:
    settings = Settings()
    assert settings.check_interval_minutes == 60
    assert settings.theme == "dark"
    # Без браузерного User-Agent источник отвечает 403 (ТЗ §11, §26).
    assert "Mozilla/5.0" in settings.http.user_agent
    assert settings.sources.astar_host.endswith("astar.bz")
    assert len(settings.sources.astar_mirrors) > 1


def test_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    settings = Settings()
    settings.check_interval_minutes = 15
    settings.qbittorrent.category = "аниме"
    save_settings(settings, path)

    loaded = load_settings(path)
    assert loaded.check_interval_minutes == 15
    assert loaded.qbittorrent.category == "аниме"
    assert json.loads(path.read_text(encoding="utf-8"))["theme"] == "dark"


def test_broken_file_falls_back_to_defaults(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{ не json", encoding="utf-8")
    assert load_settings(path).check_interval_minutes == 60


def test_missing_file_falls_back_to_defaults(tmp_path: Path) -> None:
    assert load_settings(tmp_path / "нет.json").theme == "dark"


def test_unknown_keys_do_not_break_load(tmp_path: Path) -> None:
    """Файл от будущей версии не должен ронять запуск."""
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"theme": "light", "future_option": 1}), encoding="utf-8")
    assert load_settings(path).theme == "light"


def test_data_dir_env_override(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "portable"))
    assert data_dir() == tmp_path / "portable"


def test_qbittorrent_base_url() -> None:
    settings = Settings()
    assert settings.qbittorrent.base_url == "http://127.0.0.1:8080"
    settings.qbittorrent.use_https = True
    assert settings.qbittorrent.base_url.startswith("https://")
