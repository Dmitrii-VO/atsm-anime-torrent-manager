# RuTracker — устройство источника

Разведка 07.09.2026 на живом сайте: страницы смотрел в браузере, запросы проверял
из Python. Всё, что ниже, проверено, а не взято из памяти.

## Главное: сайт закрыт Cloudflare

`requests` с браузерным User-Agent получает **403** и страницу «Just a moment…
Enable JavaScript and cookies to continue», заголовок `Server: cloudflare`.
Так отвечают и `viewforum.php`, и `viewtopic.php`, и `tracker.php`.

Проверку решает JavaScript в браузере. Обход в проект не закладывается, поэтому
единственный путь — **работать сессией, которую пользователь открыл сам**:
скопировать куки из браузера в настройки.

Куки `bb_session` и `cf_clearance` помечены **HttpOnly**, из `document.cookie` их
не видно (JS отдаёт только `bb_guid`, `bb_ssl`, `bb_t`). Значит копировать —
из DevTools → Application → Cookies, либо «Copy as cURL» из вкладки Network.

Автоматически вытащить куки из Chrome **нельзя**: `browser_cookie3` падает с
`Unable to get key for cookie decryption` — с Chrome 127 включено App-Bound
Encryption, на машине стоит Chrome 152.

`cf_clearance` привязан к IP и User-Agent, поэтому запросы обязаны идти с тем же
UA, что в браузере. На этой машине:
`Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36`

RSS нет: `rss.php?f=252` отдаёт 404.

## Проверено: с куками из браузера `requests` работает

Спайк 07.09.2026, куки скопированы из DevTools («Copy as cURL») вместе с
User-Agent. Все три запроса прошли **без заглушки Cloudflare**:

| Запрос | Ответ |
|---|---|
| `tracker.php?nm=Дьявол носит Prada` | 200, 243 КБ, «Результатов поиска: 48», сессия залогинена |
| `dl.php?t=<id>` | 200, `application/x-bittorrent`, 193 КБ, начинается с `d` |
| `viewtopic.php?t=<id>` | 200, magnet с `info_hash` на месте |

Хватило пяти кук (`bb_guid`, `bb_ssl`, `bb_session`, `bb_t`, `cf_clearance`)
и совпадающего UA. Ни `sec-ch-ua`, ни прочих заголовков Chrome не понадобилось.

`dl.php` отдаёт файл обычным GET, POST не нужен.

## Поиск — `tracker.php`

`GET https://rutracker.org/forum/tracker.php?nm=<запрос>` работает (форма на сайте
POST, но GET принимается). Ответ — таблица результатов, до 500 совпадений:
«Результатов поиска: 48 (max: 500)». Раздел можно ограничить: `&f=252` или
`&f=252,1950,313`.

Строка результата — `tr.tCenter.hl-tr`, `id="trs-tr-<topic_id>"`,
`data-topic_id="<topic_id>"`. Осторожно: `data-topic_id` встречается в разметке
**дважды на строку** (48 результатов дали 96 совпадений регуляркой) — при разборе
идти по строкам таблицы, а не по атрибутам. Колонки по порядку:

| Ячейка | Что внутри |
|---|---|
| `td.t-ico` ×2 | иконки, в т.ч. `span.tor-icon.tor-approved` — статус проверки |
| `td.f-name-col` | раздел, `a.gen.f.ts-text` («Зарубежное кино (UHD Video)») |
| `td.t-title-col` | тема, `a.med.tLink.tt-text` — всё название целиком |
| `td.u-name-col` | автор раздачи |
| `td.tor-size` | размер («18.8 GB») и `a.tr-dl` — ссылка на `dl.php?t=<id>` |
| далее | S (сиды), L (личи), C (скачиваний), «ДОБАВЛЕН» — дата вида `26-Июл-26` |

Дата с русским сокращением месяца — свой разбор, `strptime` тут не поможет.

## Раздел форума — `viewforum.php?f=<id>`

Строка темы — `tr.hl-tr`, `id="tr-<topic_id>"`, `data-topic_id`. Внутри
`a.torTopic` (название), `span.seedmed` / `span.leechmed`, размер и `a.f-dl` в
ячейке `td.vf-col-tor`, последнее сообщение в `td.vf-col-last-post`.
Пагинация — по 50 тем, ссылки `a.pg`.

Для ленты новинок `tracker.php` удобнее: там есть колонка «Добавлен», а в
`viewforum` — только дата последнего сообщения.

## Страница темы — `viewtopic.php?t=<id>`

- `h1.maintitle` — полное название со всей разметкой качества;
- `a.magnet-link` — `magnet:?xt=urn:btih:<40 hex>`, то есть **info_hash достаётся
  без скачивания файла**;
- `a.dl-stub` «Скачать .torrent» — ссылка `dl.php?t=<id>`;
- `.seed` / `.leech` — «Сиды: 925», «Личи: 115»; размер отдельной строкой.

## Название темы

Весь смысл упакован в заголовок:

```
Поймать Эль Чапо / La Captura / Facing El Chapo (Чава Картас / Chava Cartas)
[2026, Мексика, боевик, криминал, триллер, WEB-DLRip-AVC] MVO (MUZOBOZ) + Sub Rus…
```

Схема: `Русское / Оригинальное / Ещё варианты (Режиссёр) [год, страна, жанры,
качество] озвучка + субтитры`. Отдельных полей качества и года в разметке нет —
только эта строка.

## Скачивание торрента

`GET dl.php?t=<topic_id>` с рабочими куками отдаёт `application/x-bittorrent`.
Без кук — та же заглушка Cloudflare. Проверять ответ по `content-type` и первому
байту `d`, как уже сделано для astar.

## Блокировки провайдеров

Домен блокируется в РФ, на этой машине сайт открывается (системный обход).
Для чужих машин пригодится поле прокси в настройках источника
(`session.proxies`, HTTP/SOCKS5) и запасной домен `rutracker.net`.
