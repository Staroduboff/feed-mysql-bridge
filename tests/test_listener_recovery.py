#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_listener_recovery.py — поведение листенера при сбое записи пакета (feedbridge/listener.py).

Повод — инцидент 24.09.2026. Сборщик удалил событие по ретенции, в кэше листенера
остался его id, следующий маркет по этому событию получил отказ внешнего ключа
(1452), и пакет уходил в requeue по кругу четыре часа, пока сообщения не истекли
по TTL очереди. Проверяется:

  1. устаревший id события → кэши сбрасываются, пакет переигрывается, пакет
     подтверждается брокеру (ack), а не возвращается в очередь;
  2. событие ещё есть в Redis → после сброса кэшей оно дотягивается и маркет пишется;
  3. нетранзиентная ошибка пакета → запись по одному объекту: битый объект
     откатывается к своей точке сохранения, остальные сохраняются, ack;
  4. транзиентная ошибка → requeue (nack), как и раньше;
  5. плановый сброс кэшей id по таймеру.

Нужен живой MySQL: тест создаёт свою базу по schema.sql и удаляет её в конце.
Параметры — переменные окружения FB_TEST_HOST, FB_TEST_PORT, FB_TEST_USER,
FB_TEST_PASSWORD, FB_TEST_DB (по умолчанию feed_bridge_test). Без FB_TEST_USER
тест пропускается. Запуск из корня проекта:

    FB_TEST_USER=… FB_TEST_PASSWORD=… python3 tests/test_listener_recovery.py
"""
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if not os.environ.get("FB_TEST_USER"):
    print("  пропуск: не задан FB_TEST_USER (нужен MySQL)")
    sys.exit(0)

import pymysql                                     # noqa: E402

from feedbridge import listener as L               # noqa: E402
from feedbridge.core import Bridge                 # noqa: E402

DB = os.environ.get("FB_TEST_DB", "feed_bridge_test")
MC = {"host": os.environ.get("FB_TEST_HOST", "127.0.0.1"),
      "port": int(os.environ.get("FB_TEST_PORT", "3306")),
      "user": os.environ["FB_TEST_USER"], "password": os.environ.get("FB_TEST_PASSWORD", ""),
      "database": DB}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

fail = 0


def check(name, got, want):
    global fail
    ok = got == want
    if not ok:
        fail += 1
    print("  %-6s %s%s" % ("ok" if ok else "ПРОВАЛ", name,
                           "" if ok else "  (получено %r, ожидалось %r)" % (got, want)))


class FakeRedis:
    """Минимум Redis для дорезолва родителей: get и scan по словарю."""

    def __init__(self):
        self.kv = {}

    def get(self, key):
        return self.kv.get(key)

    def scan(self, cursor, match="*", count=10):
        rx = re.compile("^" + re.escape(match).replace(r"\*", ".*") + "$")
        return 0, [k for k in self.kv if rx.match(k)]


class FakeChannel:
    """Записывает подтверждения брокеру."""

    def __init__(self):
        self.acks, self.nacks = [], []

    def basic_ack(self, delivery_tag, multiple=False):
        self.acks.append(delivery_tag)

    def basic_nack(self, delivery_tag, multiple=False, requeue=True):
        self.nacks.append(delivery_tag)


def admin():
    c = dict(MC)
    c.pop("database")
    return pymysql.connect(autocommit=True, **c)


def create_db():
    ddl = open(os.path.join(ROOT, "schema.sql"), encoding="utf-8").read()
    ddl = re.sub(r"--[^\n]*", "", ddl).replace("feed_bridge", DB)
    con = admin()
    with con.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {DB}")
        for stmt in ddl.split(";"):
            if stmt.strip():
                cur.execute(stmt)
    con.close()


def drop_db():
    con = admin()
    with con.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {DB}")
    con.close()


def sql1(q, args=()):
    con = pymysql.connect(autocommit=True, **MC)
    with con.cursor() as cur:
        cur.execute(q, args)
        rows = cur.fetchall()
    con.close()
    return rows


SPORT, CAT, TRN = "1", "c1", "t1"
EKEY = f"e:{SPORT}:{CAT}:{TRN}:"


def ev(fid, dv=1):
    return {"name": {"en": f"A - B {fid}"}, "start": "2026-09-24T18:00:00Z",
            "status": 1, "dv": dv, "sdv": 1, "uts": "2026-09-24T18:00:00Z"}


def mkt(ver=1, mtype=1):
    return {"type": mtype, "name": {"en": "1x2"}, "open": True, "ver": ver,
            "uts": "2026-09-24T18:00:00Z"}


def oc(ver=1):
    return {"type": 1, "name": {"en": "1"}, "price": 1.5, "status": 1, "ver": ver}


def mk():
    tmp = tempfile.mkdtemp()
    b = Bridge({"mysql": MC, "gate": {"enabled": False,
                                      "state_file": os.path.join(tmp, "gate.json")}})
    b.connect_mysql()
    b.r = FakeRedis()
    b.r.kv[f"s:{SPORT}"] = json.dumps({"name": {"en": "Soccer"}, "dv": 1})
    b.r.kv[f"c:{SPORT}:{CAT}"] = json.dumps({"name": {"en": "Cat"}, "dv": 1})
    b.r.kv[f"t:{SPORT}:{CAT}:{TRN}"] = json.dumps({"name": {"en": "Trn"}, "dv": 1})
    lst = L.Listener(b)
    lst._stats = {"ok": 0, "err": 0, "obj": 0, "coal": 0,      # как в Listener.run()
                  "ev": 0, "mkt": 0, "oc": 0, "t0": L.time.monotonic()}
    lst._print_at = float("inf")                              # строку статистики не печатать
    return b, lst


def feed(lst, ch, tag, items):
    """Одно сообщение AMQP: объекты канала ch в накопитель пакета."""
    lst.batch.note(tag)
    for key, obj in items:
        lst._buffer(ch, key, obj)


def main():
    create_db()
    try:
        L.time.sleep = lambda s: None   # паузы requeue-петли тесту не нужны

        # ── 1. ночной сценарий: событие удалено сборщиком, в Redis его тоже нет ──
        print("\n1. устаревший id события, события нет в Redis")
        b, lst = mk()
        ch = FakeChannel()
        feed(lst, "events", 1, [(EKEY + "100", ev(100))])
        feed(lst, "markets", 2, [("m:100:h1", mkt())])
        lst._flush_batch(ch)
        check("первый пакет записан", sql1("SELECT COUNT(*) FROM markets")[0][0], 1)
        check("id события в кэше", 100 in b.ev_cache, True)
        sql1("DELETE FROM events WHERE feed_id=100")          # это делает сборщик
        # Маркет есть в Redis, события нет: исход дорезолвит маркет по устаревшему id
        # события в обход построчной записи — ровно путь ночи 24.09 (resolved_market).
        b.r.kv["m:100:h2"] = json.dumps(mkt())
        feed(lst, "markets", 3, [("m:100:h2", mkt())])
        feed(lst, "outcomes", 4, [("o:100:h2:1", oc())])
        lst._flush_batch(ch)
        check("пакет подтверждён", ch.acks[-1], 4)
        check("requeue не было", ch.nacks, [])
        check("повтор со сбросом кэшей", lst._res["stale_id_retry"], 1)
        check("маркет без события учтён", lst._res["drop_market_no_event"] >= 1, True)
        check("строк-сирот нет", sql1("SELECT COUNT(*) FROM markets")[0][0], 0)
        feed(lst, "markets", 5, [("m:100:h3", mkt())])        # следующий пакет — без повтора
        lst._flush_batch(ch)
        check("следующий пакет без повтора", lst._res["stale_id_retry"], 1)
        check("и подтверждён", ch.acks[-1], 5)

        # ── 2. событие удалено в базе, но живо в Redis → дотягивается заново ──
        print("\n2. устаревший id события, событие есть в Redis")
        b.r.kv[EKEY + "200"] = json.dumps(ev(200))
        feed(lst, "events", 6, [(EKEY + "200", ev(200))])
        lst._flush_batch(ch)
        sql1("DELETE FROM events WHERE feed_id=200")
        feed(lst, "markets", 7, [("m:200:h1", mkt(ver=2))])
        lst._flush_batch(ch)
        check("пакет подтверждён", ch.acks[-1], 7)
        check("событие восстановлено", sql1("SELECT COUNT(*) FROM events WHERE feed_id=200")[0][0], 1)
        check("маркет записан к новому id", sql1(
            "SELECT COUNT(*) FROM markets m JOIN events e ON e.id=m.event_id "
            "WHERE e.feed_id=200")[0][0], 1)

        # ── 3. ошибка в данных вне построчной записи → запись по одному объекту ──
        print("\n3. нетранзиентная ошибка пакета → поштучно")
        b, lst = mk()
        ch = FakeChannel()
        real = lst._write_batch

        def broken():
            raise pymysql.err.IntegrityError(1062, "Duplicate entry (имитация)")
        lst._write_batch = broken
        feed(lst, "events", 11, [(EKEY + "300", ev(300))])
        feed(lst, "markets", 12, [("m:300:h1", mkt()), ("m:300:bad", mkt(mtype="abc"))])
        lst._flush_batch(ch)
        lst._write_batch = real
        check("пакет подтверждён", ch.acks, [12])
        check("requeue не было", ch.nacks, [])
        check("поштучная запись", lst._res["slow_batch"], 1)
        check("битый объект учтён", lst._res["apply_error"], 1)
        check("годный маркет записан", sql1(
            "SELECT feed_hash FROM markets m JOIN events e ON e.id=m.event_id "
            "WHERE e.feed_id=300"), (("h1",),))
        check("id события в кэше верный", b.ev_cache.get(300),
              sql1("SELECT id FROM events WHERE feed_id=300")[0][0])

        # ── 4. транзиентная ошибка → requeue ──
        print("\n4. транзиентная ошибка → requeue")
        ch = FakeChannel()

        def lost():
            raise pymysql.err.OperationalError(2013, "Lost connection (имитация)")
        lst._write_batch = lost
        feed(lst, "markets", 21, [("m:300:h9", mkt())])
        lst._flush_batch(ch)
        lst._write_batch = real
        check("возвращён в очередь", ch.nacks, [21])
        check("не подтверждён", ch.acks, [])

        # ── 5. плановый сброс кэшей id ──
        print("\n5. плановый сброс кэшей id")
        check("кэш событий заполнен", len(b.ev_cache) > 0, True)
        lst._id_reset_at -= L._ID_CACHE_RESET_S + 1
        feed(lst, "markets", 31, [("m:300:h1", mkt(ver=5))])
        lst._flush_batch(ch)
        check("пакет подтверждён", ch.acks[-1], 31)
        check("таймер перезапущен", L.time.monotonic() - lst._id_reset_at < 5, True)
    finally:
        drop_db()

    print("\nИтог: %s" % ("все проверки пройдены" if not fail else f"провалов: {fail}"))
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
