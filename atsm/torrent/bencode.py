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
        end = data.find(b"e", index + 1)
        if end == -1:
            raise BencodeError("Не завершено целое число")
        raw = data[index + 1 : end]
        try:
            return int(raw), end + 1
        except ValueError as exc:
            raise BencodeError("Некорректное целое число") from exc

    if char == b"l":
        items: list = []
        index += 1
        while True:
            if index >= len(data):
                raise BencodeError("Не завершён список")
            if data[index : index + 1] == b"e":
                return items, index + 1
            item, index = _decode(data, index)
            items.append(item)

    if char == b"d":
        result: dict = {}
        index += 1
        while True:
            if index >= len(data):
                raise BencodeError("Не завершён словарь")
            if data[index : index + 1] == b"e":
                return result, index + 1
            key, index = _decode(data, index)
            value, index = _decode(data, index)
            result[key] = value

    if char.isdigit():
        colon = data.find(b":", index)
        if colon == -1:
            raise BencodeError("Не задана длина строки")
        try:
            length = int(data[index:colon])
        except ValueError as exc:
            raise BencodeError("Некорректная длина строки") from exc
        start = colon + 1
        end = start + length
        if end > len(data):
            raise BencodeError("Строка короче заявленной длины")
        return data[start:end], end

    raise BencodeError(f"Недопустимый символ bencode: {char!r}")


def decode(data: bytes) -> object:
    value, end = _decode(data, 0)
    if end != len(data):
        raise BencodeError("Лишние данные после bencode-значения")
    return value


def info_hash(torrent_data: bytes) -> str:
    """SHA1 от bencode-словаря info — идентификатор раздачи в клиенте."""
    try:
        if not torrent_data.startswith(b"d"):
            raise BencodeError("Корневое значение торрента не является словарём")

        index = 1
        info_range: tuple[int, int] | None = None
        while True:
            if index >= len(torrent_data):
                raise BencodeError("Не завершён корневой словарь")
            if torrent_data[index : index + 1] == b"e":
                index += 1
                break

            key, index = _decode(torrent_data, index)
            if not isinstance(key, bytes):
                raise BencodeError("Ключ словаря должен быть строкой")
            value_start = index
            _, index = _decode(torrent_data, index)
            if key == b"info":
                if info_range is not None:
                    raise BencodeError("Секция info указана несколько раз")
                info_range = (value_start, index)

        if index != len(torrent_data):
            raise BencodeError("Лишние данные после корневого словаря")
        if info_range is None:
            raise BencodeError("В файле нет секции info")

        start, end = info_range
        return hashlib.sha1(torrent_data[start:end]).hexdigest()
    except BencodeError:
        raise
    except (ValueError, IndexError, KeyError) as exc:
        raise BencodeError(f"Не удалось вычислить info_hash: {exc}") from exc
