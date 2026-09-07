"""qBittorrent Web API v2 (ТЗ §10).

Сессия живёт в cookie SID. Клиент может перезапуститься и разлогинить нас
в любой момент, поэтому на 403 выполняется один повторный вход.
"""

from __future__ import annotations

import requests
from loguru import logger

from ..config import QBittorrentSettings
from .base import (
    AddResult,
    BaseTorrentClient,
    TorrentClientError,
    TorrentClientUnavailable,
)

# qBittorrent отвечает 409, если раздача с таким хешем уже добавлена.
ALREADY_ADDED = 409


class QBittorrentClient(BaseTorrentClient):
    name = "qbittorrent"
    display_name = "qBittorrent"

    def __init__(self, settings: QBittorrentSettings, timeout: float = 10.0) -> None:
        self.settings = settings
        self.timeout = timeout
        self.session = requests.Session()
        self._logged_in = False

    # --- авторизация -----------------------------------------------------

    @property
    def api(self) -> str:
        return f"{self.settings.base_url}/api/v2"

    def login(self) -> None:
        try:
            response = self.session.post(
                f"{self.api}/auth/login",
                data={"username": self.settings.username, "password": self.settings.password},
                headers={"Referer": self.settings.base_url},
                timeout=self.timeout,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            # Полный стек urllib3 идёт в лог, пользователю — что делать.
            logger.debug("Подключение к qBittorrent не удалось: {}", exc)
            raise TorrentClientUnavailable(
                f"qBittorrent недоступен по адресу {self.settings.base_url}. "
                "Проверьте, что клиент запущен и в его настройках включён веб-интерфейс."
            ) from exc

        if response.status_code == 403:
            raise TorrentClientUnavailable(
                "Вход в qBittorrent заблокирован: слишком много неудачных попыток. "
                "Подождите или перезапустите клиент."
            )

        body = response.text.strip()
        if response.status_code == 200 and body.casefold() == "fails.":
            raise TorrentClientUnavailable("Неверный логин или пароль qBittorrent")
        if not (
            response.status_code == 204
            or response.status_code == 200
            and body.casefold() == "ok."
        ):
            raise TorrentClientError(
                f"Неожиданный ответ qBittorrent при входе: HTTP {response.status_code}"
            )

        self._logged_in = True
        logger.debug("Вход в qBittorrent выполнен (ответ: {!r})", body or response.status_code)

    def _request(
        self, method: str, path: str, allow: tuple[int, ...] = (), **kwargs
    ) -> requests.Response:
        if not self._logged_in:
            self.login()

        url = f"{self.api}{path}"
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("allow_redirects", False)
        kwargs.setdefault("headers", {}).setdefault("Referer", self.settings.base_url)

        try:
            response = self.session.request(method, url, **kwargs)
            # Клиент перезапустился — сессия протухла, входим заново.
            if response.status_code == 403:
                logger.debug("Сессия qBittorrent истекла, повторный вход")
                self._logged_in = False
                self.login()
                response = self.session.request(method, url, **kwargs)
        except requests.RequestException as exc:
            logger.debug("Запрос к qBittorrent не прошёл: {}", exc)
            raise TorrentClientUnavailable(
                f"Потеряна связь с qBittorrent ({self.settings.base_url})"
            ) from exc

        if not 200 <= response.status_code < 300 and response.status_code not in allow:
            raise TorrentClientError(f"qBittorrent вернул HTTP {response.status_code}")
        return response

    # --- контракт --------------------------------------------------------

    def test_connection(self) -> str:
        version = self._request("GET", "/app/version").text.strip()
        if not version:
            raise TorrentClientError("qBittorrent вернул пустую версию")
        logger.info("qBittorrent {} доступен", version)
        return version

    def add_torrent_file(self, data: bytes, filename: str) -> AddResult:
        response = self._request(
            "POST",
            "/torrents/add",
            allow=(ALREADY_ADDED,),
            files={"torrents": (filename, data, "application/x-bittorrent")},
            data=self._add_options(),
        )
        return self._interpret(response, filename)

    def add_magnet(self, magnet: str) -> AddResult:
        response = self._request(
            "POST",
            "/torrents/add",
            allow=(ALREADY_ADDED,),
            data={"urls": magnet, **self._add_options()},
        )
        return self._interpret(response, magnet[:60])

    def _add_options(self) -> dict[str, str]:
        # qBittorrent 5.x (Web API 2.11+) переименовал paused в stopped.
        # Шлём оба ключа: незнакомый параметр клиент игнорирует, а иначе
        # «добавлять на паузе» молча не срабатывает на новых версиях.
        flag = "true" if self.settings.add_paused else "false"
        options: dict[str, str] = {"paused": flag, "stopped": flag}
        if self.settings.category:
            options["category"] = self.settings.category
        if self.settings.save_path:
            options["savepath"] = self.settings.save_path
        if self.settings.sequential_download:
            # Первая и последняя части несут заголовки контейнера — без них
            # плеер не откроет растущий файл.
            options["sequentialDownload"] = "true"
            options["firstLastPiecePrio"] = "true"
        return options

    @staticmethod
    def _interpret(response: requests.Response, label: str) -> AddResult:
        # Повторная отправка той же раздачи — не сбой: клиент уже её знает.
        if response.status_code == ALREADY_ADDED:
            return AddResult(ok=True, message="Раздача уже есть в торрент-клиенте")

        # Успех — любой 2xx, кроме явного «Fails.». На магнит-ссылки и на части
        # версий клиент отвечает 200 с пустым телом, и проверка на строку «Ok.»
        # объявляла принятую раздачу отклонённой.
        body = response.text.strip()
        if body.casefold().startswith("fail"):
            return AddResult(ok=False, message=f"qBittorrent отклонил раздачу: {label}")
        return AddResult(ok=True, message="Отправлено в qBittorrent")

    def delete(self, hashes: list[str], delete_files: bool = False) -> None:
        """Убирает раздачи из клиента. Файлы по умолчанию остаются на диске."""
        if not hashes:
            return
        self._request(
            "POST",
            "/torrents/delete",
            data={
                "hashes": "|".join(h.lower() for h in hashes),
                "deleteFiles": "true" if delete_files else "false",
            },
        )
        logger.info("Удалено раздач из qBittorrent: {}", len(hashes))

    def torrent_info(self, info_hash: str) -> dict:
        """Полная карточка раздачи: прогресс, пути, флаги последовательной загрузки."""
        response = self._request("GET", "/torrents/info", params={"hashes": info_hash.lower()})
        try:
            items = response.json()
        except ValueError as exc:
            raise TorrentClientError(f"Неожиданный ответ qBittorrent: {exc}") from exc
        if not items:
            raise TorrentClientError("Раздачи нет в торрент-клиенте")
        return items[0]

    def torrent_files(self, info_hash: str) -> list[dict]:
        """Файлы раздачи с их прогрессом — из них выбирается, что можно смотреть."""
        response = self._request("GET", "/torrents/files", params={"hash": info_hash.lower()})
        try:
            return response.json()
        except ValueError as exc:
            raise TorrentClientError(f"Неожиданный ответ qBittorrent: {exc}") from exc

    def start(self, info_hash: str) -> None:
        """Снимает раздачу с паузы. В 5.x ручка переименована, пробуем обе."""
        for path in ("/torrents/resume", "/torrents/start"):
            try:
                self._request("POST", path, data={"hashes": info_hash.lower()})
                return
            except TorrentClientError as exc:
                logger.debug("{} не сработал: {}", path, exc)

    def ensure_sequential(self, info_hash: str) -> None:
        """Включает последовательную загрузку у уже добавленной раздачи.

        В API есть только переключатели, поэтому сначала смотрим текущее
        состояние: слепой вызов выключил бы уже включённый режим.
        """
        info = self.torrent_info(info_hash)
        for flag, path in (
            ("seq_dl", "/torrents/toggleSequentialDownload"),
            ("f_l_piece_prio", "/torrents/toggleFirstLastPiecePrio"),
        ):
            if not info.get(flag):
                self._request("POST", path, data={"hashes": info_hash.lower()})

    def torrent_states(self, hashes: list[str]) -> dict[str, str]:
        """Состояния раздач по info_hash — для статуса «скачана» (ТЗ §13)."""
        if not hashes:
            return {}
        response = self._request(
            "GET", "/torrents/info", params={"hashes": "|".join(h.lower() for h in hashes)}
        )
        try:
            return {item["hash"].lower(): item["state"] for item in response.json()}
        except (ValueError, KeyError, TypeError) as exc:
            raise TorrentClientError(f"Неожиданный ответ qBittorrent: {exc}") from exc
