# ATSM — План реализации MVP

Версия плана: 1.0
Основано на: `ТЗ.txt` (v3.0), `Доп.txt`, разведке структуры `astar.bz`.

---

## 0. Разведка источника (уже выполнена)

Страница `https://v19.astar.bz/7788-pozhiratel-zvezd-swallowed-star.html` изучена. Факты, определяющие архитектуру MVP:

| Факт | Следствие для реализации |
|---|---|
| Список раздач присутствует в **статическом HTML**, без JS | Хватит `requests` + `BeautifulSoup`/`lxml`. Playwright/Selenium **не нужны**. |
| Каждая раздача: `div.torrent#torrent_<id>_info` | Стабильный селектор + готовый уникальный `id` раздачи. |
| Внутри: `div.title > a[href="/engine/gettorrent.php?id=<id>"]` | Прямая ссылка на `.torrent`. |
| Текст ссылки: `Серия 235 (844.05 Mb)` | Номер серии парсится регуляркой. |
| Блок `div.cont`: `Раздают: 210`, `Качают: 12`, `Скачали: 1869`, `Размер: 844.05 Mb`, `Дата: 03-08-2026` | Есть сиды/личи/дата в формате `DD-MM-YYYY`. |
| На странице 235 раздач одной страницей, пагинации нет | Один GET = полный список серий. |
| **Magnet-ссылок нет**, только `.torrent`-файлы | qBittorrent кормим файлом (`torrents/add` → multipart), не magnet. Пункты ТЗ «Открыть magnet» / «Копировать magnet» в MVP неактивны. |
| `gettorrent.php` отдаёт `application/x-bittorrent` **без авторизации** | Логин/куки в MVP не нужны. |
| **Нет поля качества** (1080p/HEVC) в разметке | Правила выбора релиза (§7 ТЗ) в MVP вырождаются в «одна серия = одна раздача». Модель данных поле `quality` держит, логика правил — версия 2. |
| Прямой запрос `requests` без заголовков → **HTTP 403** | Обязательны реалистичные `User-Agent`, `Accept-Language`, `Referer`. Вынести в конфиг парсера. |
| Сайт сообщает «Актуальный адрес: **V30**.ASTAR.BZ» (ссылка из ТЗ — v19) | Домен ротируется. Нужен **резолвер зеркал**: хранить `slug` страницы, а хост подставлять из настроек + фоллбэк по списку зеркал. Критично для долгой жизни подписок. |

---

## 1. Границы MVP

**Входит:** подписка по ссылке, библиотека в SQLite, парсинг списка раздач, определение новых серий, лента новинок, ручное скачивание `.torrent`, отправка в qBittorrent, автопроверка по расписанию, Windows-уведомления, системный трей, логи.

**Не входит (v2+):** поиск по названию, парсеры Nyaa/AniLibria/RuTracker, правила выбора релиза, Telegram/Discord, импорт/экспорт, Transmission/Deluge, темы оформления кроме тёмной.

---

## 2. Технологический стек

```
Python 3.12   PySide6   SQLite (sqlite3 + свой слой репозиториев)
requests   beautifulsoup4   lxml   loguru   APScheduler   pydantic (конфиг/DTO)
pytest + pytest-qt + responses (тесты)   PyInstaller (сборка .exe)
```

ORM не берём: сущностей мало, схема простая, «сырой» SQL с репозиториями быстрее и прозрачнее.

---

## 3. Структура проекта

```
anistarapp/
├─ atsm/
│  ├─ __main__.py               точка входа, сборка DI-контейнера
│  ├─ config.py                 пути (%APPDATA%\ATSM), pydantic-настройки
│  ├─ core/
│  │  ├─ models.py              dataclass: Anime, Release, HistoryEntry, SourceStatus
│  │  ├─ update_service.py      цикл проверки подписок, диффинг новых серий
│  │  ├─ subscription_service.py  добавление подписки по ссылке
│  │  └─ events.py              шина сигналов Core → GUI (Qt Signal)
│  ├─ db/
│  │  ├─ schema.sql             DDL + версия схемы
│  │  ├─ database.py            connection, миграции, WAL
│  │  └─ repositories.py        AnimeRepo, ReleaseRepo, HistoryRepo, SettingsRepo
│  ├─ parsers/
│  │  ├─ base.py                BaseParser (ABC) + ParseResult/ParserError
│  │  ├─ registry.py            Plugin Manager: автозагрузка, match_url()
│  │  └─ astar.py               парсер astar.bz
│  ├─ torrent/
│  │  ├─ base.py                BaseTorrentClient (ABC)
│  │  └─ qbittorrent.py         qBittorrent Web API v2
│  ├─ services/
│  │  ├─ scheduler.py           APScheduler-обёртка
│  │  ├─ notifier.py            Windows-уведомления + счётчик
│  │  └─ http.py                общая requests.Session: UA, retry, таймауты
│  ├─ gui/
│  │  ├─ main_window.py         оболочка + навигация
│  │  ├─ feed_view.py           🔥 Новые серии (стартовый экран)
│  │  ├─ library_view.py        список подписок + панель деталей
│  │  ├─ settings_dialog.py
│  │  ├─ add_dialog.py
│  │  ├─ log_view.py
│  │  ├─ tray.py                QSystemTrayIcon + меню
│  │  └─ theme.qss              тёмная тема
│  └─ logging_setup.py          loguru → файл с ротацией + буфер для GUI
├─ tests/
│  ├─ fixtures/astar_7788.html  сохранённый снимок страницы
│  └─ …
├─ requirements.txt
└─ PLAN.md
```

Данные пользователя: `%APPDATA%\ATSM\` — `atsm.db`, `settings.json`, `logs/atsm.log`, `torrents/` (кэш скачанных `.torrent`).

---

## 4. Схема БД

```sql
PRAGMA journal_mode=WAL;

CREATE TABLE anime (
    id            INTEGER PRIMARY KEY,
    title         TEXT NOT NULL,
    source        TEXT NOT NULL,          -- 'astar'
    url           TEXT NOT NULL UNIQUE,   -- канонический URL страницы
    slug          TEXT NOT NULL,          -- '7788-pozhiratel-zvezd-…' — переживает смену домена
    poster_path   TEXT,
    status        TEXT DEFAULT 'ongoing', -- ongoing | completed | paused
    auto_download INTEGER DEFAULT 0,
    is_favorite   INTEGER DEFAULT 0,
    last_check_at TEXT,
    last_check_ok INTEGER,
    last_error    TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE release (
    id            INTEGER PRIMARY KEY,
    anime_id      INTEGER NOT NULL REFERENCES anime(id) ON DELETE CASCADE,
    external_id   TEXT NOT NULL,          -- id из gettorrent.php?id=…  → ключ дедупликации
    episode       INTEGER,                -- NULL, если не распознан
    episode_raw   TEXT NOT NULL,          -- 'Серия 235 (844.05 Mb)'
    title         TEXT,
    quality       TEXT,                   -- зарезервировано под v2
    size_bytes    INTEGER,
    seeders       INTEGER,
    leechers      INTEGER,
    published_at  TEXT,
    torrent_url   TEXT,
    magnet        TEXT,                   -- NULL для astar
    state         TEXT NOT NULL DEFAULT 'new',  -- new|sent|downloaded|error|ignored
    is_seen       INTEGER DEFAULT 0,
    first_seen_at TEXT NOT NULL,
    UNIQUE(anime_id, external_id)
);
CREATE INDEX idx_release_state ON release(state, is_seen);

CREATE TABLE history (
    id         INTEGER PRIMARY KEY,
    release_id INTEGER REFERENCES release(id) ON DELETE CASCADE,
    anime_id   INTEGER REFERENCES anime(id) ON DELETE CASCADE,
    action     TEXT NOT NULL,   -- found|sent|download_error|parse_error
    message    TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE source_status (
    source       TEXT PRIMARY KEY,
    state        TEXT,          -- ok|unreachable|parse_error|layout_changed
    message      TEXT,
    checked_at   TEXT
);

CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE schema_version (version INTEGER NOT NULL);
```

**Ключевое решение:** дедупликация по `(anime_id, external_id)`, где `external_id` — числовой id торрента с сайта. Он стабилен, не зависит от текста заголовка и переживает смену домена. Номер серии — только для отображения и сортировки.

---

## 5. Контракт парсера (Plugin Manager)

```python
class BaseParser(ABC):
    name: str          # 'astar'
    display_name: str  # 'AniStar'
    domains: list[str] # ['astar.bz']  → сопоставление по суффиксу хоста, любой vNN

    @abstractmethod
    def match(self, url: str) -> bool: ...

    @abstractmethod
    def fetch(self, url: str) -> AnimeInfo:
        """Один GET → метаданные аниме + полный список раздач."""
```

`AnimeInfo(title, url, slug, poster_url, status, releases: list[ReleaseInfo])`
`ReleaseInfo(external_id, episode, episode_raw, size_bytes, seeders, leechers, published_at, torrent_url, magnet)`

Регистрация плагина — сканирование пакета `atsm.parsers` и сбор подклассов `BaseParser`. Добавление источника = один новый файл, ядро не трогается (§18 ТЗ).

Ошибки типизированы: `SourceUnreachable`, `ParseError`, `LayoutChanged` (список раздач пуст, хотя HTTP 200) — ровно под §19 «Диагностика».

### Парсер astar (спецификация)

1. Нормализовать URL: вырезать `slug`, хост подставить из `settings.astar_host` (по умолчанию — хост исходной ссылки).
2. GET с `User-Agent` десктопного Chrome, `Accept-Language: ru-RU`, таймаут 15 с, 3 ретрая с backoff.
3. При редиректе/ошибке — перебрать зеркала `v19…v40.astar.bz`, найденный рабочий хост сохранить в настройки.
4. Заголовок — из `<h1>` (обрезать по `/` до первого варианта названия).
5. Раздачи: `soup.select('div.torrent[id^=torrent_]')`, из каждого:
   - `external_id` — из `id="torrent_46219_info"`;
   - `episode` — `re.search(r'Серия\s+(\d+)', text)`;
   - `size_bytes` — из `Размер: 844.05 Mb`;
   - `seeders`/`leechers` — `div.li_distribute` / `div.li_swing`;
   - `published_at` — `Дата: 03-08-2026` → `%d-%m-%Y`;
   - `torrent_url` — `urljoin(base, a['href'])`.
6. Пустой список при HTTP 200 → `LayoutChanged`.

---

## 6. Update Service (ядро)

```
for anime in подписки:
    info = parser.fetch(anime.url)
    known = {r.external_id for r in repo.releases(anime.id)}
    new   = [r for r in info.releases if r.external_id not in known]
    repo.insert_releases(new, state='new')
    history.log('found', …)
    if anime.auto_download and new:
        for r in отфильтровать(new):     # MVP: без правил, всё новое
            torrent_service.send(r)      # → state='sent' | 'error'
    repo.update_check_status(anime, ok/err)
    events.emit(...)
```

**Важно:** при **первом** добавлении подписки все найденные раздачи пишутся с `state='new'`, но `is_seen=1` — иначе 235 серий сразу засыпят ленту и уведомления. Новинками считается только то, что появилось при последующих проверках. Пользователь может «показать все серии» в карточке аниме.

Проверки выполняются в `QThreadPool` (или отдельном worker-потоке), GUI не блокируется. Между запросами к одному источнику — пауза 1–2 с, чтобы не долбить сайт.

---

## 7. Torrent Service — qBittorrent

Web API v2:
- `POST /api/v2/auth/login` (`username`, `password`) → cookie `SID`, кэшируем; при `403` — релогин и один повтор.
- `POST /api/v2/torrents/add` — multipart: файл `.torrent` (скачиваем сами через ту же сессию с UA), плюс `category`, `savepath`, `paused`.
- Проверка соединения в настройках: `GET /api/v2/app/version` → показать версию или ошибку.
- Опционально: `GET /api/v2/torrents/info?hashes=…` для статуса `downloaded` (§11 ТЗ) — берём, если хеш удалось вычислить из `.torrent` (bencode → SHA1 от `info`). Небольшая ручная реализация bencode, без внешних зависимостей.

Фоллбэк на «торрент-клиент Windows по умолчанию» — `os.startfile(путь_к_torrent)`.

Абстракция `BaseTorrentClient.add(torrent_bytes|magnet, category, savepath) -> Result` заранее заготовлена под Transmission/Deluge в v2.

---

## 8. GUI

Стартовый экран — **лента новых серий** (из `Доп.txt`), это главный сценарий:

```
┌───────────────────────────────────────────────┐
│  ATSM        [Новые 3]  Лента │ Библиотека │ ⚙ │
├───────────────────────────────────────────────┤
│ 🔥 Новые серии                  [Скачать всё] │
│ ┌───────────────────────────────────────────┐ │
│ │ [постер] Пожиратель звёзд                 │ │
│ │          Серия 235 · 844 Mb · 03.08.2026  │ │
│ │          сиды 210      [Скачать] [В qB] ⋮ │ │
│ └───────────────────────────────────────────┘ │
│ … One Piece · 1140 серия                      │
├───────────────────────────────────────────────┤
│ Проверка: Solo Leveling…      Обновлено 14:32 │
└───────────────────────────────────────────────┘
```

Экран «Библиотека» — двухпанельный вид из §4 ТЗ: слева список подписок (значок избранного, счётчик новых, дата проверки, индикатор состояния источника), справа детали + таблица раздач с контекстным меню (Скачать `.torrent` / Отправить в qBittorrent / Копировать ссылку / Пометить прочитанным).

Реализация списков — `QListView`/`QTableView` + собственные модели (`QAbstractListModel`) и делегат для карточки. Не `QListWidget`: при сотнях раздач важна виртуализация.

Тёмная тема — единый `theme.qss`, DPI-масштабирование через `Qt::AA_EnableHighDpiScaling` + относительные размеры.

Трей: `QSystemTrayIcon`, меню «Проверить обновления / Скачать всё новое / Открыть / Выход», закрытие окна → сворачивание (с однократной подсказкой). Уведомления — `tray.showMessage()` (нативные тосты Windows), клик по тосту открывает ленту.

---

## 9. Этапы работ

| # | Этап | Результат / критерий готовности |
|---|---|---|
| 1 | Каркас: структура пакетов, конфиг, логирование, БД + миграции | `python -m atsm` создаёт `%APPDATA%\ATSM\atsm.db` со всеми таблицами; лог пишется |
| 2 | Парсер astar + registry | На сохранённом фикстуре `astar_7788.html` тест извлекает 235 раздач, серия 235 = 844.05 Mb, дата 03-08-2026; живой запрос отрабатывает без 403 |
| 3 | Репозитории + `subscription_service` | CLI-скрипт: добавить ссылку → в БД аниме и 235 releases с `is_seen=1` |
| 4 | Update Service + диффинг | Подмена фикстура с +1 серией даёт ровно одну запись `state='new', is_seen=0` и запись в `history` |
| 5 | qBittorrent-клиент | Кнопка «тест соединения» показывает версию; ручная отправка раздачи появляется в клиенте с нужной категорией |
| 6 | GUI: оболочка, тёмная тема, диалог добавления, библиотека | Подписка добавляется из UI, список раздач виден, кнопки скачивания работают |
| 7 | GUI: лента новинок + «Скачать всё» | Экран ленты собирается из `state='new'`, массовая отправка с прогрессом |
| 8 | Планировщик + фон + трей + уведомления | Приложение сворачивается в трей, проверка по интервалу и при старте, тост при новой серии |
| 9 | Настройки, экран логов, диагностика источников, автозапуск | Все пункты §21 ТЗ применяются без перезапуска; в UI виден статус источника |
| 10 | Полировка + сборка | PyInstaller `--onedir --noconsole`, иконка, запуск на чистой машине |

Этапы 1–5 — «мозг» без UI, каждый покрыт тестами. Этапы 6–8 — то, что пользователь увидит. Оценка при полной занятости: 1–5 ≈ 3–4 дня, 6–8 ≈ 4–5 дней, 9–10 ≈ 2 дня.

---

## 10. Тестирование

- **Парсер** — на сохранённом HTML-фикстуре (без сети); отдельный тест-«канарейка» с живым запросом, помечен `@pytest.mark.network`, ловит смену вёрстки.
- **Диффинг** — фикстур «до/после», проверка отсутствия дублей при повторной проверке.
- **qBittorrent** — `responses`/мок HTTP, проверка релогина по 403.
- **GUI** — `pytest-qt`, дымовые тесты моделей и открытия окон.

---

## 11. Риски и как их держим

| Риск | Митигация |
|---|---|
| Смена домена зеркала (v19 → v30 уже произошла) | Хранить `slug`; авто-перебор зеркал; настраиваемый базовый хост |
| Смена вёрстки сайта | Типизированная `LayoutChanged`, статус источника в UI, тест-канарейка |
| 403 / антибот | Общая `requests.Session` с реалистичными заголовками, ретраи с backoff, лимит частоты |
| Нет magnet и качества | Фичи «magnet» и «правила релизов» помечены как v2, модель данных уже готова |
| Много раздач в первой синхронизации | `is_seen=1` при первичном импорте |
| Блокировка UI при проверке | Вся сеть в worker-потоках, связь с GUI только через сигналы |

---

## 12. Открытые вопросы

1. Постеры: тянуть и кэшировать картинку с сайта или обойтись плейсхолдерами в MVP?
2. Хранить пароль qBittorrent в `settings.json` открытым текстом или через Windows Credential Manager (`keyring`)?
3. Нужен ли в MVP просмотр всех старых серий из карточки (кнопка «показать все») или достаточно новых?
