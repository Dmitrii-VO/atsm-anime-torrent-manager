from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import requests
import responses

from atsm.config import QBittorrentSettings
from atsm.core.models import ReleaseState
from atsm.core.subscription_service import SubscriptionService
from atsm.core.torrent_service import TorrentService
from atsm.db.repositories import Repositories
from atsm.torrent.base import AddResult, TorrentClientError
from atsm.torrent.bencode import BencodeError, decode, info_hash
from atsm.torrent.qbittorrent import QBittorrentClient

from .fakes import URL, FakeParser, FakeRegistry, release

TORRENT = b"d8:announce19:http://tracker/annc4:infod4:name8:test.mkv6:lengthi123eee"
INFO_SLICE = b"d4:name8:test.mkv6:lengthi123ee"
API = "http://127.0.0.1:8080/api/v2"


class TestBencode:
    def test_decode_basic_types(self) -> None:
        assert decode(b"i42e") == 42
        assert decode(b"4:test") == b"test"
        assert decode(b"li1ei2ee") == [1, 2]
        assert decode(b"d3:key5:valuee") == {b"key": b"value"}

    def test_info_hash_matches_sha1_of_info_slice(self) -> None:
        expected = hashlib.sha1(INFO_SLICE).hexdigest()
        assert info_hash(TORRENT) == expected

    def test_info_hash_is_stable(self) -> None:
        assert info_hash(TORRENT) == info_hash(TORRENT)

    def test_rejects_garbage(self) -> None:
        with pytest.raises(BencodeError):
            info_hash(b"<html>404</html>")


class TestQBittorrentClient:
    @pytest.fixture
    def client(self) -> QBittorrentClient:
        return QBittorrentClient(QBittorrentSettings(password="secret"))

    @responses.activate
    def test_login_then_version(self, client: QBittorrentClient) -> None:
        responses.post(f"{API}/auth/login", body="Ok.")
        responses.get(f"{API}/app/version", body="v4.6.5")
        assert client.test_connection() == "v4.6.5"

    @responses.activate
    def test_wrong_credentials(self, client: QBittorrentClient) -> None:
        responses.post(f"{API}/auth/login", body="Fails.")
        with pytest.raises(TorrentClientError, match="логин или пароль"):
            client.test_connection()

    @responses.activate
    def test_localhost_auth_bypass(self, client: QBittorrentClient) -> None:
        """qBittorrent 5.x при обходе авторизации для localhost отвечает 204
        с пустым телом вместо «Ok.» — это успех, а не неверный пароль."""
        responses.post(f"{API}/auth/login", status=204, body="")
        responses.get(f"{API}/app/version", body="v5.2.3")

        assert client.test_connection() == "v5.2.3"

    @responses.activate
    def test_login_ban(self, client: QBittorrentClient) -> None:
        responses.post(f"{API}/auth/login", status=403, body="banned")
        with pytest.raises(TorrentClientError, match="заблокирован"):
            client.test_connection()

    @responses.activate
    def test_offline_client(self, client: QBittorrentClient) -> None:
        """Пользователю нужен понятный совет, а не дамп urllib3."""
        responses.post(
            f"{API}/auth/login",
            body=requests.ConnectionError(
                "HTTPConnectionPool(host='127.0.0.1', port=8080): Max retries exceeded "
                "with url: /api/v2/auth/login (Caused by NewConnectionError(...))"
            ),
        )
        with pytest.raises(TorrentClientError) as exc:
            client.test_connection()

        message = str(exc.value)
        assert "недоступен" in message and "веб-интерфейс" in message
        assert "HTTPConnectionPool" not in message
        assert "urllib3" not in message
        assert len(message) < 200

    @responses.activate
    def test_add_torrent_file_with_options(self, client: QBittorrentClient) -> None:
        client.settings.category = "anime"
        client.settings.save_path = "D:/Anime"
        responses.post(f"{API}/auth/login", body="Ok.")
        responses.post(f"{API}/torrents/add", body="Ok.")

        result = client.add_torrent_file(TORRENT, "test.torrent")

        assert result.ok
        body = responses.calls[-1].request.body
        assert b"anime" in body and b"D:/Anime" in body

    @responses.activate
    def test_rejected_torrent(self, client: QBittorrentClient) -> None:
        responses.post(f"{API}/auth/login", body="Ok.")
        responses.post(f"{API}/torrents/add", body="Fails.")
        assert client.add_torrent_file(TORRENT, "x.torrent").ok is False

    @responses.activate
    def test_expired_session_triggers_relogin(self, client: QBittorrentClient) -> None:
        """qBittorrent перезапустился — сессия протухла, нужен повторный вход."""
        responses.post(f"{API}/auth/login", body="Ok.")
        responses.get(f"{API}/app/version", status=403)
        responses.get(f"{API}/app/version", body="v4.6.5")

        assert client.test_connection() == "v4.6.5"
        logins = [c for c in responses.calls if c.request.url.endswith("/auth/login")]
        assert len(logins) == 2

    @responses.activate
    def test_torrent_states(self, client: QBittorrentClient) -> None:
        responses.post(f"{API}/auth/login", body="Ok.")
        responses.get(
            f"{API}/torrents/info", json=[{"hash": "ABC123", "state": "stalledUP"}]
        )
        assert client.torrent_states(["abc123"]) == {"abc123": "stalledUP"}


class FakeClient:
    def __init__(self) -> None:
        self.added: list[tuple[bytes, str]] = []
        self.result = AddResult(ok=True, message="Отправлено в qBittorrent")
        self.error: Exception | None = None
        self.states: dict[str, str] = {}

    def add_torrent_file(self, data: bytes, filename: str) -> AddResult:
        if self.error:
            raise self.error
        self.added.append((data, filename))
        return self.result

    def add_magnet(self, magnet: str) -> AddResult:
        return self.result

    def test_connection(self) -> str:
        return "fake"

    def torrent_states(self, hashes: list[str]) -> dict[str, str]:
        return self.states


class TestTorrentService:
    @pytest.fixture
    def repos(self, db) -> Repositories:
        return Repositories(db)

    @pytest.fixture
    def parser(self) -> FakeParser:
        return FakeParser([release("1", 1)])

    @pytest.fixture
    def client(self) -> FakeClient:
        return FakeClient()

    @pytest.fixture
    def seeded(self, repos, parser) -> Repositories:
        """Подписка с одной новой раздачей в ленте."""
        SubscriptionService(repos, FakeRegistry(parser)).add(URL)
        repos.releases.mark_all_seen()
        repos.releases.add_many(repos.anime.list()[0].id, [release("2", 2)], seen=False)
        return repos

    @pytest.fixture
    def service(self, seeded, parser, client, tmp_path) -> TorrentService:
        return TorrentService(seeded, FakeRegistry(parser), client, tmp_path / "cache")

    def test_send_marks_release_and_history(self, service, repos, client) -> None:
        target = repos.releases.feed()[0]

        assert service.send(target) is True

        updated = repos.releases.get(target.id)
        assert updated.state == ReleaseState.SENT
        assert updated.is_seen is True
        assert repos.releases.feed_count() == 0
        assert any(e.action == "sent" for e in repos.history.recent())
        assert client.added

    def test_send_stores_info_hash(self, service, repos) -> None:
        target = repos.releases.feed()[0]
        service.send(target)
        assert repos.releases.get(target.id).info_hash

    def test_client_error_marks_release_and_keeps_it_visible(
        self, service, repos, client
    ) -> None:
        client.error = TorrentClientError("нет связи")
        target = repos.releases.feed()[0]

        with pytest.raises(TorrentClientError):
            service.send(target)

        updated = repos.releases.get(target.id)
        assert updated.state == ReleaseState.ERROR
        assert updated.is_seen is False
        assert [r.id for r in repos.releases.feed()] == [target.id]

    def test_rejected_by_client(self, service, repos, client) -> None:
        client.result = AddResult(ok=False, message="отклонено")
        target = repos.releases.feed()[0]
        assert service.send(target) is False
        assert repos.releases.get(target.id).state == ReleaseState.ERROR

    def test_send_many_reports_partial_failure(self, service, repos, client) -> None:
        anime_id = repos.anime.list()[0].id
        repos.releases.add_many(anime_id, [release("3", 3)], seen=False)
        feed = repos.releases.feed()

        sent, errors = service.send_many(feed)
        assert sent == 2 and errors == []

    def test_missing_client(self, seeded, parser, tmp_path) -> None:
        service = TorrentService(seeded, FakeRegistry(parser), None, tmp_path)
        with pytest.raises(TorrentClientError, match="не настроен"):
            service.send(seeded.releases.feed()[0])

    def test_save_to_disk(self, service, repos, tmp_path) -> None:
        path = service.save_to(repos.releases.feed()[0], tmp_path / "out")
        assert path.exists() and path.suffix == ".torrent"
        assert path.read_bytes().startswith(b"d")

    def test_filename_is_windows_safe(self, service, repos) -> None:
        target = repos.releases.feed()[0]
        target.anime_title = 'A/B:C*D?"E<F>G|H'
        name = service.filename_for(target)
        assert not set(name) & set('<>:"/\\|?*')
        assert name.endswith(".torrent")

    def test_download_state_refresh(self, service, repos, client) -> None:
        target = repos.releases.feed()[0]
        service.send(target)
        stored = repos.releases.get(target.id)
        client.states = {stored.info_hash.lower(): "stalledUP"}

        assert service.refresh_download_states() == 1
        assert repos.releases.get(target.id).state == ReleaseState.DOWNLOADED

    def test_magnet_absent_returns_false(self, service, repos) -> None:
        assert service.open_magnet(repos.releases.feed()[0]) is False
