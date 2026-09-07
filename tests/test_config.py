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


class _FakeStore:
    """Подмена keyring: тесты не должны трогать реальное хранилище Windows."""

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, user: str) -> str | None:
        return self.values.get((service, user))

    def set_password(self, service: str, user: str, password: str) -> None:
        self.values[(service, user)] = password

    def delete_password(self, service: str, user: str) -> None:
        del self.values[(service, user)]


def test_password_not_stored_in_file(tmp_path: Path, monkeypatch) -> None:
    """Пароль уходит в хранилище системы, в settings.json остаётся пустая строка."""
    store = _FakeStore()
    monkeypatch.setattr("atsm.config._keyring", lambda: store)

    path = tmp_path / "settings.json"
    settings = Settings()
    settings.qbittorrent.password = "тайна"
    save_settings(settings, path)

    assert json.loads(path.read_text(encoding="utf-8"))["qbittorrent"]["password"] == ""
    assert "тайна" not in path.read_text(encoding="utf-8")
    assert load_settings(path).qbittorrent.password == "тайна"


def test_plaintext_password_migrates_out_of_file(tmp_path: Path, monkeypatch) -> None:
    """Файл от старой версии: пароль переезжает в хранилище при первом чтении."""
    store = _FakeStore()
    monkeypatch.setattr("atsm.config._keyring", lambda: store)

    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"qbittorrent": {"password": "старый"}}), encoding="utf-8"
    )

    assert load_settings(path).qbittorrent.password == "старый"
    assert "старый" not in path.read_text(encoding="utf-8")
    assert store.values[("ATSM", "qbittorrent")] == "старый"


def test_without_store_password_stays_in_file(tmp_path: Path, monkeypatch) -> None:
    """Без хранилища пароль остаётся в файле — иначе он потеряется между запусками."""
    monkeypatch.setattr("atsm.config._keyring", lambda: None)

    path = tmp_path / "settings.json"
    settings = Settings()
    settings.qbittorrent.password = "тайна"
    save_settings(settings, path)

    assert load_settings(path).qbittorrent.password == "тайна"


def test_rutracker_cookies_not_stored_in_file(tmp_path: Path, monkeypatch) -> None:
    """Куки RuTracker — тот же секрет, что и пароль: в файле их быть не должно."""
    store = _FakeStore()
    monkeypatch.setattr("atsm.config._keyring", lambda: store)

    path = tmp_path / "settings.json"
    settings = Settings()
    settings.sources.rutracker_cookies = "bb_session=тайна; cf_clearance=тайна"
    save_settings(settings, path)

    assert "тайна" not in path.read_text(encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8"))["sources"]["rutracker_cookies"] == ""
    assert load_settings(path).sources.rutracker_cookies.startswith("bb_session=")
