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

    def test_info_hash_ignores_marker_inside_string(self) -> None:
        torrent = b"d7:comment11:xxx4:infoxx4:infod4:name4:testee"
        expected = hashlib.sha1(b"d4:name4:teste").hexdigest()
        assert info_hash(torrent) == expected

    def test_info_hash_rejects_trailing_garbage(self) -> None:
        with pytest.raises(BencodeError, match="Лишние данные"):
            info_hash(TORRENT + b"garbage")

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
    def test_login_ok_is_case_insensitive_and_trimmed(self, client: QBittorrentClient) -> None:
        responses.post(f"{API}/auth/login", body="  oK.\r\n")
        responses.get(f"{API}/app/version", body="v5.2.3")
        assert client.test_connection() == "v5.2.3"

    @pytest.mark.parametrize(
        ("status", "body"),
        [(200, ""), (200, "<html>Ok.</html>"), (201, "Ok.")],
    )
    @responses.activate
    def test_login_rejects_undocumented_success(
        self, client: QBittorrentClient, status: int, body: str
    ) -> None:
        responses.post(f"{API}/auth/login", status=status, body=body)
        with pytest.raises(TorrentClientError, match="Неожиданный ответ"):
            client.test_connection()

    @responses.activate
    def test_login_does_not_follow_redirect(self, client: QBittorrentClient) -> None:
        responses.post(
            f"{API}/auth/login",
            status=302,
            headers={"Location": "http://example.test/login"},
        )
        with pytest.raises(TorrentClientError, match="HTTP 302"):
            client.test_connection()
        assert len(responses.calls) == 1

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
    def test_connection_rejects_empty_version(self, client: QBittorrentClient) -> None:
        responses.post(f"{API}/auth/login", body="Ok.")
        responses.get(f"{API}/app/version", body=" \r\n")
        with pytest.raises(TorrentClientError, match="пустую версию"):
            client.test_connection()

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
    def test_pause_flag_sent_in_both_dialects(self, client: QBittorrentClient) -> None:
        """qBittorrent 5.x переименовал paused в stopped: без второго ключа
        «добавлять на паузе» молча не срабатывает."""
        client.settings.add_paused = True
        responses.post(f"{API}/auth/login", body="Ok.")
        responses.post(f"{API}/torrents/add", body="Ok.")

        client.add_magnet("magnet:?xt=urn:btih:" + "c" * 40)

        body = responses.calls[-1].request.body
        assert "paused=true" in body
        assert "stopped=true" in body

    @responses.activate
    def test_already_added_is_not_an_error(self, client: QBittorrentClient) -> None:
        """Повторная отправка той же раздачи даёт 409 — это «уже есть», не сбой."""
        responses.post(f"{API}/auth/login", body="Ok.")
        responses.post(f"{API}/torrents/add", status=409, body="")

        result = client.add_magnet("magnet:?xt=urn:btih:" + "d" * 40)

        assert result.ok is True
        assert "уже есть" in result.message

    @responses.activate
    def test_rejected_torrent(self, client: QBittorrentClient) -> None:
        responses.post(f"{API}/auth/login", body="Ok.")
        responses.post(f"{API}/torrents/add", body="Fails.")
        assert client.add_torrent_file(TORRENT, "x.torrent").ok is False

    @pytest.mark.parametrize(
        ("status", "body"),
        [(200, ""), (200, "<html>Ok.</html>"), (204, "")],
    )
    @responses.activate
    def test_add_accepts_any_2xx(
        self, client: QBittorrentClient, status: int, body: str
    ) -> None:
        """Раздача принята, а тело ответа не «Ok.» — раньше это звалось отказом."""
        responses.post(f"{API}/auth/login", body="Ok.")
        responses.post(f"{API}/torrents/add", status=status, body=body)
        assert client.add_magnet("magnet:?xt=urn:btih:" + "e" * 40).ok is True

    @responses.activate
    def test_add_does_not_follow_redirect(self, client: QBittorrentClient) -> None:
        responses.post(f"{API}/auth/login", body="Ok.")
        responses.post(
            f"{API}/torrents/add",
            status=302,
            headers={"Location": "http://example.test/add"},
        )
        with pytest.raises(TorrentClientError, match="HTTP 302"):
            client.add_magnet("magnet:?xt=urn:btih:" + "f" * 40)
        assert len(responses.calls) == 2

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


def test_sequential_download_flags() -> None:
    """Флаги потокового просмотра уходят в клиент только при включённой настройке."""
    from atsm.torrent.qbittorrent import QBittorrentClient

    off = QBittorrentClient(QBittorrentSettings())._add_options()
    assert "sequentialDownload" not in off

    on = QBittorrentClient(QBittorrentSettings(sequential_download=True))._add_options()
    assert on["sequentialDownload"] == "true" and on["firstLastPiecePrio"] == "true"


class FakeClient:
    def __init__(self) -> None:
        self.added: list[tuple[bytes, str]] = []
        self.result = AddResult(ok=True, message="Отправлено в qBittorrent")
        self.error: Exception | None = None
        self.states: dict[str, str] = {}
        self.deleted: list[tuple[list[str], bool]] = []
        self.settings = QBittorrentSettings(delete_replaced=True)

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

    def delete(self, hashes: list[str], delete_files: bool = False) -> None:
        self.deleted.append((hashes, delete_files))


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

    def test_replaced_pack_is_removed_from_client(self, service, repos, client) -> None:
        """Пачка «1-5» перекрывает «1-4»: старую раздачу убираем из клиента."""
        anime_id = repos.anime.list()[0].id
        repos.releases.add_many(
            anime_id, [release("p14", 1, episode_end=4)], seen=False
        )
        old = repos.releases.feed()[0]
        service.send(old)
        old_hash = repos.releases.get(old.id).info_hash
        assert old_hash

        repos.releases.add_many(
            anime_id, [release("p15", 1, episode_end=5)], seen=False
        )
        new = [r for r in repos.releases.feed() if r.external_id == "p15"][0]
        service.send(new)

        assert client.deleted == [([old_hash], False)]
        # Хеш снят: раздачи в клиенте больше нет, опрашивать нечего.
        assert not repos.releases.get(old.id).info_hash

    def test_single_episode_does_not_delete_neighbours(self, service, repos, client) -> None:
        """Обычная серия ничего не заменяет — удалять соседей нельзя."""
        target = repos.releases.feed()[0]
        service.send(target)
        assert client.deleted == []

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

    @pytest.mark.parametrize("completed_state", ["pausedUP", "stoppedUP"])
    def test_download_state_refresh(
        self, service, repos, client, completed_state: str
    ) -> None:
        target = repos.releases.feed()[0]
        service.send(target)
        stored = repos.releases.get(target.id)
        client.states = {stored.info_hash.lower(): completed_state}

        assert service.refresh_download_states() == 1
        assert repos.releases.get(target.id).state == ReleaseState.DOWNLOADED

    def test_magnet_send_stores_hash(self, service, repos, client) -> None:
        """У magnet хеш лежит в самой ссылке — качать файл ради него не нужно."""
        target = repos.releases.feed()[0]
        magnet_hash = "b" * 40
        with repos.db.transaction() as conn:
            conn.execute(
                "UPDATE release SET magnet = ? WHERE id = ?",
                (f"magnet:?xt=urn:btih:{magnet_hash.upper()}&dn=test", target.id),
            )

        assert service.send(repos.releases.get(target.id)) is True
        assert repos.releases.get(target.id).info_hash == magnet_hash

    def test_magnet_absent_returns_false(self, service, repos) -> None:
        assert service.open_magnet(repos.releases.feed()[0]) is False
