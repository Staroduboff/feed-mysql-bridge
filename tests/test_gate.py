#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_gate.py — проверки гейта готовности приёма ставок (feedbridge/gate.py).

Зависимостей нет, запускается из корня проекта:

    python3 tests/test_gate.py

Главная проверка — сценарий инцидента 07.09.2026: короткий всплеск сообщений на
умирающем канале НЕ должен открывать приём ставок. Именно на этом сломалась
защита на стороне портала: её флаг возвращался в «здоров» по одному удачному
замеру, и приём открылся на четыре минуты при стоящей линии.
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from feedbridge.gate import (Gate, _iso_to_epoch,          # noqa: E402
                             STATE_OPEN, STATE_HOLD, STATE_SUSPEND)

UTC = datetime.timezone.utc
fail = 0


def uts(t):
    """epoch → строка uts в формате фида."""
    return datetime.datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:23]


def check(name, got, exp):
    global fail
    ok = got == exp
    if not ok:
        fail += 1
    print(("  ok    " if ok else "  ПРОВАЛ ") + name + ("" if ok else f"  (получено {got}, ожидалось {exp})"))


def fresh_gate(**over):
    """Гейт без внешних источников: медленные сигналы недоступны и в решение не идут."""
    cfg = {"gate": dict({"reopen_hold": 30, "lag_crit": 30, "silence_crit": 15,
                         "state_file": ""}, **over)}
    return Gate(cfg)


def warm(g, t0, n=40, step=1.0):
    """Прогнать n секунд живого потока; вернуть время после прогона."""
    t = t0
    for _ in range(n):
        g._uts_cur = uts(t - 0.2)
        g._last_msg = t
        g.tick(now=t)
        t += step
    return t


T = 1_000_000.0

print("1. Разбор uts фида")
import calendar  # noqa: E402
for s in ("2026-09-07T18:31:34.847", "2026-09-08T12:57:00.595", "2024-02-29T23:59:59.000",
          "2026-01-01T00:00:00.000", "2025-12-31T23:59:59.999"):
    ref = calendar.timegm(datetime.datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").timetuple()) + float(s[19:])
    check(s, round(_iso_to_epoch(s), 3), round(ref, 3))
check("мусор вместо даты", _iso_to_epoch("не дата"), None)
check("пустая строка", _iso_to_epoch(""), None)

print("\n2. Живой поток: старт закрыт → HOLD → OPEN ровно через reopen_hold")
g = fresh_gate()
check("на старте приём закрыт", g.state, STATE_SUSPEND)
t = T
for i in range(31):
    g._uts_cur = uts(t - 0.2)
    g._last_msg = t
    g.tick(now=t)
    if i == 0:
        check("первый тик — HOLD, не OPEN", g.state, STATE_HOLD)
    if i == 29:
        check("на 29-й секунде ещё HOLD", g.state, STATE_HOLD)
    if i == 30:
        check("на 30-й секунде OPEN", g.state, STATE_OPEN)
    t += 1

print("\n3. Тишина в потоке закрывает приём по достижении порога")
g = fresh_gate()
t = warm(g, T)
check("до обрыва открыт", g.state, STATE_OPEN)
last = t - 1                   # момент последнего применённого сообщения
for i in range(1, 17):
    g.tick(now=last + i)       # сообщений нет, _last_msg не двигается
    if i == 14:
        check("на 14-й секунде тишины ещё открыт", g.state, STATE_OPEN)
    if i == 15:
        check("на 15-й секунде тишины закрыт", g.state, STATE_SUSPEND)
        check("названа верная причина", g.reasons, ["silence"])

print("\n4. Сценарий 07.09: всплеск на умирающем канале НЕ открывает приём")
g = fresh_gate()
t = warm(g, T)
check("перед обрывом открыт", g.state, STATE_OPEN)
for _ in range(20):                       # обрыв
    g.tick(now=t)
    t += 1
check("после обрыва закрыт", g.state, STATE_SUSPEND)
for _ in range(5):                        # короткий всплеск сообщений
    g._uts_cur = uts(t - 0.2)
    g._last_msg = t
    g.tick(now=t)
    t += 1
check("во время всплеска приём НЕ открыт", g.state, STATE_HOLD)
for _ in range(20):                       # всплеск кончился
    g.tick(now=t)
    t += 1
check("после всплеска снова закрыт", g.state, STATE_SUSPEND)

print("\n5. Замороженная линия: сообщения идут, но max(uts) не растёт")
g = fresh_gate()
t = warm(g, T)
check("разогрев открыт", g.state, STATE_OPEN)
frozen = uts(t)
for _ in range(45):
    g._uts_cur = frozen                   # данные те же, поток есть
    g._last_msg = t
    g.tick(now=t)
    if g.state == STATE_SUSPEND:
        break
    t += 1
check("замороженные данные закрывают приём", g.state, STATE_SUSPEND)
check("названа верная причина", g.reasons, ["lag"])

print("\n6. Битая метка времени из будущего игнорируется")
g = fresh_gate()
g._uts_cur = uts(T + 9999)
g._last_msg = T
g.tick(now=T)
check("lag по будущей метке не считается", g.metrics["lag"], None)

print("\n7. Протухшие медленные сигналы не влияют на решение")
g = fresh_gate()
t = warm(g, T)
g._ready, g._ready_at = 10 ** 6, t - 10_000      # огромная очередь, но замер древний
g._srv, g._srv_at = {"active": False}, t - 10_000
g.tick(now=t)
check("древние значения не закрывают приём", g.state, STATE_OPEN)
g._ready_at = g._srv_at = t                      # те же значения, но свежие
g.tick(now=t)
check("свежие значения закрывают приём", g.state, STATE_SUSPEND)

print("\n8. Отстающий поток: uts растёт, но всё время на 40 минут позади")
# Инцидент 12.09.2026: мост разбирал очередь с отставанием 40-50 минут, то есть
# применял старые объекты. Максимум uts за всё время при этом продолжал расти,
# lag держался около нуля, и приём закрыл лишь запасной сигнал qlag. Со
# скользящим окном lag показывает реальный возраст потока и закрывает приём сам.
g = fresh_gate()
t = warm(g, T)
check("разогрев открыт", g.state, STATE_OPEN)
BEHIND = 40 * 60
for _ in range(200):
    g._uts_cur = uts(t - BEHIND)          # объекты применяются, но сорокаминутные
    g._last_msg = t                       # тишины нет: сообщения идут
    g.tick(now=t)
    if g.state == STATE_SUSPEND:
        break
    t += 1
check("отставание потока закрывает приём", g.state, STATE_SUSPEND)
check("названа верная причина", g.reasons, ["lag"])
# Приём закрывается раньше, чем окно сменится целиком: сначала стареет свежее
# значение, оставшееся в окне с разогрева. Когда окно сменилось полностью, lag
# показывает уже настоящий возраст потока.
for _ in range(int(g.thr["lag_window"]) + 5):
    g._uts_cur = uts(t - BEHIND)
    g._last_msg = t
    g.tick(now=t)
    t += 1
check("lag сходится к реальному возрасту", g.metrics["lag"] >= BEHIND, True)

print("\n9. Поток без uts не ослепляет гейт")
# У исходов перепродаваемого фида uts не заполняется. Если за окно не пришло ни
# одного объекта с uts, сигнал должен опираться на последнее известное значение,
# а не пропадать: иначе застывшая линия перестала бы детектироваться вовсе.
g = fresh_gate()
t = warm(g, T)
check("разогрев открыт", g.state, STATE_OPEN)
for _ in range(300):
    g._last_msg = t                       # сообщения идут, но observe_uts не зовут
    g.tick(now=t)
    if g.state == STATE_SUSPEND:
        break
    t += 1
check("застывшая линия закрывает приём", g.state, STATE_SUSPEND)
check("названа верная причина", g.reasons, ["lag"])

print("\n10. Тик идёт из фонового потока, без внешних вызовов")
# Регрессия 09.09.2026: тик висел на таймере ioloop pika и во время залпа не исполнялся —
# гейт замирал на 11.5 минут и показывал OPEN по устаревшим данным. Проверяем, что состояние
# пересчитывается само, без единого вызова tick() снаружи. Конфигурация источников пустая:
# опрос очереди и Redis упадёт и будет проглочен, тик от этого зависеть не должен.
import time as _t  # noqa: E402
g = fresh_gate()
check("до старта потока закрыт", g.state, STATE_SUSPEND)
g.start_background({})
_t.sleep(2.5)
check("фоновый поток пересчитал состояние", g.state, STATE_HOLD)
check("отметка последнего тика проставлена", g._tick_at is not None, True)
t1 = g._tick_at
_t.sleep(1.5)
check("тики продолжаются", g._tick_at > t1, True)

print("\nИТОГ: " + ("все проверки пройдены" if not fail else f"ПРОВАЛОВ: {fail}"))
sys.exit(1 if fail else 0)
