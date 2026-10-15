#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_publish.py — проверки публикатора дельт (feedbridge/publish.py).

Зависимостей нет, запускается из корня проекта:

    python3 tests/test_publish.py

Главная проверка — поведение при сбое раздачи. Замер 14.09.2026 показал, что одна
просрочка /api/batch выбрасывала пачку и включала пятисекундную паузу, в которой
терялось всё: 8 просрочек стоили 6311 не доставленных в браузеры дельт. Теперь
одиночная неудача возвращает пачку в буфер, а отбрасывание начинается только когда
раздача не принимает данные подряд — и о каждой потере узнаёт гейт приёма ставок.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from feedbridge.publish import Publisher          # noqa: E402

fail = 0


def check(name, got, want):
    global fail
    ok = got == want
    if not ok:
        fail += 1
    print("  %-6s %s%s" % ("ok" if ok else "ПРОВАЛ", name,
                           "" if ok else "  (получено %r, ожидалось %r)" % (got, want)))


def mk(**over):
    cfg = {"centrifugo": dict({"enabled": True, "api_url": "http://127.0.0.1:1/api",
                               "api_key": "x", "batch_max": 5, "batch_ms": 200,
                               "timeout": 0.1, "retry_s": 5, "fail_streak": 3,
                               "max_buffer": 20}, **over)}
    p = Publisher(cfg)
    p.drops = []
    p.on_drop = lambda n: p.drops.append(n)
    return p


def send_fail(p, n=1):
    """n сбросов буфера при недоступной раздаче (api_url заведомо мёртвый)."""
    for _ in range(n):
        p.add("sport:1", {"x": 1})
        p.flush()


print("1. Одиночная неудача не теряет дельты")
p = mk()
p.add("sport:1", {"x": 1})
p.flush()
check("пачка вернулась в буфер", len(p.buf), 1)
check("ничего не отброшено", p.stats["dropped"], 0)
check("гейт не потревожен", p.drops, [])
check("сбой посчитан", p.stats["errors"], 1)
check("повтор посчитан", p.stats["requeued"], 1)

print("\n2. Серия сбоев подряд включает паузу и отбрасывание")
p = mk()
send_fail(p, 3)
check("после трёх сбоев буфер очищен", len(p.buf), 0)
check("потери посчитаны", p.stats["dropped"] > 0, True)
check("гейт получил сигнал", len(p.drops) > 0, True)
check("пауза включена", p._fail_until > 0, True)

print("\n3. В паузе дельты отбрасываются и об этом узнаёт гейт")
before = p.stats["dropped"]
p.add("sport:1", {"y": 2})
p.flush()
check("отброшено в паузе", p.stats["dropped"] > before, True)
check("сигнал о потере повторился", len(p.drops) >= 2, True)

print("\n4. Буфер не растёт бесконечно")
p = mk(fail_streak=1000, max_buffer=10)
send_fail(p, 25)
check("буфер ограничен max_buffer", len(p.buf) <= 10, True)
check("лишнее отброшено", p.stats["dropped"] > 0, True)
check("гейт узнал о переполнении", len(p.drops) > 0, True)

print("\n5. Отметка времени потери проставляется")
p = mk()
check("до потери отметки нет", p.last_drop_at, None)
send_fail(p, 3)
check("после потери отметка есть", p.last_drop_at is not None, True)

print("\n6. Выключенный публикатор ничего не делает")
p = Publisher({})
p.add("sport:1", {"x": 1})
p.flush()
check("буфер пуст", len(p.buf), 0)
check("статистика пуста", sum(p.stats.values()), 0)
check("строка статистики пуста", p.stat_str(), "")

print("\nИТОГ: " + ("все проверки пройдены" if not fail else "ПРОВАЛОВ: %d" % fail))
sys.exit(1 if fail else 0)
