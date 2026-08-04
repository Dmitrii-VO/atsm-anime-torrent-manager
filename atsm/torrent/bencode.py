"""Минимальный разбор bencode — нужен только для info_hash.

Хеш позволяет позже спросить у клиента, скачана ли раздача (ТЗ §13).
Отдельная библиотека ради одной функции не оправдана.
"""

from __future__ import annotations

import hashlib


class BencodeError(ValueError):
    pass


def _decode(data: bytes, index: int) -> tuple[object, int]:
    if index >= len(data):
        raise BencodeError("Неожиданный конец данных")

    char = data[index : index + 1]

    if char == b"i":
        end = data.index(b"e", index)
        return int(data[index + 1 : end]), end + 1

    if char == b"l":
        items: list = []
        index += 1
        while data[index : index + 1] != b"e":
            item, index = _decode(data, index)
            items.append(item)
        return items, index + 1

    if char == b"d":
        result: dict = {}
        index += 1
        while data[index : index + 1] != b"e":
            key, index = _decode(data, index)
            value, index = _decode(data, index)
            result[key] = value
        return result, index + 1

    if char.isdigit():
        colon = data.index(b":", index)
        length = int(data[index:colon])
        start = colon + 1
        return data[start : start + length], start + length

    raise BencodeError(f"Недопустимый символ bencode: {char!r}")


def decode(data: bytes) -> object:
    value, _ = _decode(data, 0)
    return value


def info_hash(torrent_data: bytes) -> str:
    """SHA1 от bencode-словаря info — идентификатор раздачи в клиенте."""
    try:
        decoded = decode(torrent_data)
        if not isinstance(decoded, dict) or b"info" not in decoded:
            raise BencodeError("В файле нет секции info")
        # Пересобирать info нельзя: порядок ключей должен остаться исходным,
        # поэтому вырезаем оригинальный срез байтов.
        start = torrent_data.index(b"4:info") + len(b"4:info")
        _, end = _decode(torrent_data, start)
        return hashlib.sha1(torrent_data[start:end]).hexdigest()
    except (ValueError, IndexError, KeyError) as exc:
        raise BencodeError(f"Не удалось вычислить info_hash: {exc}") from exc
