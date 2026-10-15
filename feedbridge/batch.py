"""
batch.py — накопитель объектов для пакетной записи в MySQL.

Назначение
----------
Поток AMQP приходит по одному объекту в сообщении. Записывать каждый объект
отдельным `INSERT … ON DUPLICATE KEY UPDATE` с отдельным `COMMIT` — это два
обращения к MySQL на сообщение, и на этом упирается пропускная способность
моста. Batch накапливает объекты нескольких сотен сообщений, чтобы листенер
записал их несколькими многострочными операторами и одним commit.

Склейка по версии (coalescing)
------------------------------
В одном пакете один и тот же объект (например, цена исхода) часто приходит
несколько раз. В MySQL всё равно «выживет» только версия с максимальным
`dv`/`ver` — это гарантирует защита версией в sql.py. Поэтому накопитель хранит
по ключу ровно одну запись — с наибольшей версией (при равных версиях —
пришедшую последней, как и при последовательном применении). Отброшенные
дубликаты считаются в `coalesced`: это чистая экономия строк и публикаций в
Centrifugo, а не потеря данных.

Порядок уровней
---------------
Объекты раскладываются по уровням зависимости, чтобы листенер записал их в
порядке «справочники → события → маркеты → исходы» и родитель всегда
существовал к моменту вставки потомка.

Класс Batch
-----------
note(tag)               Отметить принятое сообщение (счётчик, delivery_tag, старт таймера).
put(channel, key, obj)  Положить объект в нужный уровень (со склейкой по версии).
due(max_msgs, max_ms)   Пора ли сбрасывать пакет (по числу сообщений или по времени).
clear()                 Сбросить накопленное (после записи или при обрыве соединения).
"""

import time

# Канал AMQP → (имя буфера, поле версии для склейки)
CHANNELS = {
    "categories":  ("cats",      "dv"),
    "tournaments": ("trns",      "dv"),
    "competitors": ("comps",     "dv"),
    "events":      ("events",    "dv"),
    "markets":     ("markets",   "ver"),
    "outcomes":    ("outcomes",  "ver"),
}


def _ver(obj: dict, field: str) -> int:
    """Числовая версия объекта (`dv` или `ver`); отсутствует/битая — считаем нулём."""
    try:
        return int(obj.get(field) or 0)
    except (TypeError, ValueError):
        return 0


class Batch:
    """Накопитель одного пакета записи: объекты по уровням + метаданные пакета."""

    def __init__(self) -> None:
        self.clear()

    # ── жизненный цикл ────────────────────────────────────────────────────────

    def clear(self) -> None:
        """Опустошить накопитель (после успешной записи или при обрыве AMQP)."""
        self.cats:     dict = {}   # хэш категории                    → (ключ, объект)
        self.trns:     dict = {}   # хэш турнира                      → (ключ, объект)
        self.comps:    dict = {}   # хэш участника                    → (ключ, объект)
        self.events:   dict = {}   # feed_id события                  → (ключ, объект)
        self.markets:  dict = {}   # (feed_id события, хэш маркета)   → (ключ, объект)
        self.outcomes: dict = {}   # (feed_id, хэш маркета, id исхода)→ (ключ, объект)
        self.msgs      = 0         # сообщений в пакете
        self.objs      = 0         # объектов принято (до склейки)
        self.coalesced = 0         # объектов склеено (перезаписано более свежей версией)
        self.tag       = None      # delivery_tag последнего сообщения пакета
        self.t0        = 0.0       # monotonic-время первого сообщения пакета

    @property
    def empty(self) -> bool:
        """В пакете нет ни одного принятого сообщения."""
        return self.msgs == 0

    def note(self, delivery_tag) -> None:
        """Отметить принятое сообщение: счётчик, тег для группового ack, старт таймера."""
        if self.msgs == 0:
            self.t0 = time.monotonic()
        self.msgs += 1
        self.tag = delivery_tag

    def due(self, max_msgs: int, max_ms: int) -> bool:
        """Пора сбрасывать: набралось max_msgs сообщений или прошло max_ms с первого."""
        if self.msgs == 0:
            return False
        return self.msgs >= max_msgs or (time.monotonic() - self.t0) * 1000 >= max_ms

    # ── приём объектов ────────────────────────────────────────────────────────

    def put(self, channel: str, key: str, obj: dict) -> bool:
        """Положить объект канала в свой уровень. False — канал моста не интересует."""
        spec = CHANNELS.get(channel)
        if spec is None:
            return False
        name, vfield = spec
        store = getattr(self, name)
        k = self._key(channel, key)
        if k is None:
            return False
        self.objs += 1
        old = store.get(k)
        if old is not None:
            self.coalesced += 1
            # Пришедшее старее уже накопленного — защита версией в MySQL всё равно
            # отбросила бы его, поэтому не подменяем более свежую запись.
            if _ver(obj, vfield) < _ver(old[1], vfield):
                return True
        store[k] = (key, obj)
        return True

    @staticmethod
    def _key(channel: str, key: str):
        """Ключ склейки = то, что уникально идентифицирует строку в MySQL."""
        parts = key.split(":")
        if channel == "categories" or channel == "competitors":
            return parts[2] if len(parts) >= 3 else None
        if channel == "tournaments":
            return parts[3] if len(parts) >= 4 else None
        if channel == "events":
            if len(parts) < 5 or not parts[4].isdigit():
                return None
            return int(parts[4])
        if channel == "markets":
            if len(parts) < 3 or not parts[1].isdigit():
                return None
            return (int(parts[1]), parts[2])
        if channel == "outcomes":
            p = key.split(":", 3)   # id исхода может содержать двоеточия
            if len(p) < 4 or not p[1].isdigit():
                return None
            return (int(p[1]), p[2], p[3])
        return None
