"""
transform.py — преобразование значений из Redis-JSON в значения для MySQL.

Назначение
----------
Объекты в Redis хранятся как JSON и приходят в потоке AMQP в том же виде.
Перед записью в MySQL отдельные поля нужно нормализовать: многоязычные имена
разложить на EN/RU, ISO-даты привести к формату MySQL DATETIME, булевы флаги —
к 0/1. Эти три чистые функции собраны здесь, чтобы и снапшот, и листенер
готовили строки одинаково.

Функции
-------
names(obj, field)  Достаёт (name_en, name_ru) из многоязычного dict или строки.
dt(s[, default])   ISO-8601 → 'YYYY-MM-DD HH:MM:SS'; при пустом/битом значении
                   возвращает default (по умолчанию эпоха 1970-01-01 00:00:00,
                   т.к. колонки MySQL не допускают NULL).
flag(obj, field)   Истинность поля → 1, иначе 0.
"""

from datetime import datetime, timezone

# Значение по умолчанию для отсутствующих/непарсируемых дат.
# Колонки времени в схеме объявлены NOT NULL, поэтому None недопустим.
DT_ZERO = "1970-01-01 00:00:00"

# Истинностные строковые представления флагов (на случай, если фид пришлёт флаг строкой).
_TRUE_STR = {"1", "true", "t", "yes", "y"}


def names(obj: dict, field: str) -> tuple:
    """Вернуть (name_en, name_ru) из многоязычного dict {'EN':…,'RU':…} или строки.

    Если поле — обычная строка, она считается английским именем, RU пустое.
    """
    v = obj.get(field)
    if isinstance(v, dict):
        return (v.get("EN") or ""), (v.get("RU") or "")
    s = str(v) if v else ""
    return s, ""


def dt(s, default: str = DT_ZERO) -> str:
    """ISO-8601 (или epoch) → 'YYYY-MM-DD HH:MM:SS' в UTC для MySQL.

    Возвращает default, если значение пустое или не парсится. Время приводится к
    UTC: если у входа есть смещение часового пояса (включая суффикс 'Z'), оно
    КОНВЕРТИРУЕТСЯ в UTC, а не отбрасывается, — иначе все времена молча уезжали бы
    на величину смещения (в проекте время критично). Числовой epoch трактуется
    как секунды UTC.
    """
    if not s and s != 0:
        return default
    try:
        if isinstance(s, (int, float)):
            parsed = datetime.fromtimestamp(s, tz=timezone.utc)
        else:
            parsed = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return default


def flag(obj: dict, field: str) -> int:
    """Истинность obj[field] → 1, иначе 0 (для TINYINT-флагов в MySQL).

    Корректно обрабатывает строковые флаги: '0'/'false' → 0 (а не 1, как давал бы
    голый truthiness непустой строки), '1'/'true' → 1.
    """
    v = obj.get(field)
    if isinstance(v, str):
        return 1 if v.strip().lower() in _TRUE_STR else 0
    return 1 if v else 0
