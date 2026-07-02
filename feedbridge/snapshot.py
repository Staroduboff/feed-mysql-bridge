"""
snapshot.py — класс Snapshotter: полная загрузка Redis → MySQL.

Назначение
----------
Снапшот приводит MySQL в полное соответствие с текущим состоянием Redis.
Последовательность: очистить очередь RabbitMQ → очистить таблицы → загрузить
объекты по типам в порядке зависимостей (виды спорта → … → исходы), чтобы
внешние ключи разрешались по уже загруженным родителям.

Очистка очереди перед снапшотом нужна, чтобы после полной загрузки листенер не
применил поверх неё устаревшие сообщения, накопившиеся в очереди.

Порядок загрузки (важен из-за внешних ключей)
---------------------------------------------
sports → categories → tournaments → competitors → market_type_names
       → events → markets → outcomes

Класс Snapshotter(bridge)
-------------------------
Работает через переданный Bridge (его соединения, кэши и помощники).

Методы
------
run()                    Полный снапшот: очистка очереди и БД + все этапы загрузки.
_load_sports()           Загрузить виды спорта (s:*) и заполнить sport_cache.
_load_categories()       Загрузить категории (c:*) и заполнить cat_cache.
_load_tournaments()      Загрузить турниры (t:*) и заполнить trn_cache.
_load_competitors()      Загрузить участников (v:*).
_load_market_type_names()Загрузить имена типов маркетов (sm:*).
_load_events()           Загрузить события (e:*) и заполнить ev_cache.
_load_markets()          Загрузить маркеты (m:*) и заполнить mkt_cache.
_load_outcomes()         Загрузить исходы (o:*).
"""

import json
import time

from . import amqp, sql
from .config import FLUSH_EVERY, REDIS_SCAN_COUNT
from .console import C, phase_done, progress
from .transform import dt, flag, names


class Snapshotter:
    """Полная загрузка снимка из Redis в MySQL."""

    def __init__(self, bridge) -> None:
        self.b = bridge

    def run(self) -> None:
        """Очистить очередь RabbitMQ и таблицы, затем загрузить все типы объектов."""
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n{C.BOLD}{'─' * 64}")
        print(f"  SNAPSHOT  ·  {ts}")
        print(f"{'─' * 64}{C.RESET}\n")

        print(f"  {C.GRAY}Очистка очереди RabbitMQ…{C.RESET}", end="\r", flush=True)
        try:
            purged = amqp.purge_queue(self.b.cfg["rabbitmq"])
            print(f"  {C.GRAY}Очередь очищена: {C.WHITE}{purged:,} сообщений удалено{C.RESET}")
        except Exception as e:
            print(f"  {C.YELLOW}⚠  Не удалось очистить очередь: {e}{C.RESET}")

        print(f"  {C.GRAY}Очистка базы данных…{C.RESET}", end="\r", flush=True)
        self.b.truncate_db()
        print(f"  {C.GRAY}База данных очищена{C.RESET}")

        t_total = time.monotonic()
        self._load_sports()
        self._load_categories()
        self._load_tournaments()
        self._load_competitors()
        self._load_market_type_names()
        self._load_events()
        self._load_markets()
        self._load_outcomes()
        elapsed = time.monotonic() - t_total
        print(f"\n  {C.GREEN}{C.BOLD}✓ Снапшот завершён за "
              f"{int(elapsed)//60}м {int(elapsed)%60} с{C.RESET}\n")

    def _load_sports(self) -> None:
        """Загрузить виды спорта (s:*); после записи заполнить sport_cache из MySQL."""
        b    = self.b
        t0   = time.monotonic()
        keys = b.redis_scan("s:*", "Сканирование sports")
        vals = b.r.mget(keys) if keys else []
        rows = []
        for k, v in zip(keys, vals):
            if not v:
                continue
            try:
                obj = json.loads(v)
            except Exception:
                continue
            parts = k.split(":")
            if len(parts) < 2 or not parts[1].isdigit():
                continue
            en, ru = names(obj, "name")
            rows.append((int(parts[1]), en, ru, obj.get("dv", 0)))
        b.flush(sql.SQL_SPORT, rows)
        with b.db.cursor() as cur:
            cur.execute("SELECT id, feed_id FROM sports")
            b.sport_cache = {r["feed_id"]: r["id"] for r in cur.fetchall()}
        phase_done("sports", f"{len(b.sport_cache):,} строк", time.monotonic() - t0)

    def _load_categories(self) -> None:
        """Загрузить категории (c:*); после записи заполнить cat_cache из MySQL."""
        b    = self.b
        t0   = time.monotonic()
        keys = b.redis_scan("c:*", "Сканирование categories")
        rows = []
        for i in range(0, len(keys), REDIS_SCAN_COUNT):
            chunk = keys[i:i + REDIS_SCAN_COUNT]
            for k, v in zip(chunk, b.r.mget(chunk)):
                if not v:
                    continue
                try:
                    obj = json.loads(v)
                except Exception:
                    continue
                parts = k.split(":")
                if len(parts) < 3:
                    continue
                sid = b.sport_id(int(parts[1])) if parts[1].isdigit() else None
                if not sid:
                    continue
                en, ru = names(obj, "name")
                rows.append((parts[2], sid, en, ru, obj.get("dv", 0),
                             dt(obj.get("ts")), flag(obj, "removed")))
            progress("Загрузка categories", min(i + REDIS_SCAN_COUNT, len(keys)), len(keys))
        b.flush(sql.SQL_CAT, rows)
        with b.db.cursor() as cur:
            cur.execute("SELECT id, feed_hash FROM categories")
            b.cat_cache = {r["feed_hash"]: r["id"] for r in cur.fetchall()}
        phase_done("categories", f"{len(b.cat_cache):,} строк", time.monotonic() - t0)

    def _load_tournaments(self) -> None:
        """Загрузить турниры (t:*); после записи заполнить trn_cache из MySQL."""
        b    = self.b
        t0   = time.monotonic()
        keys = b.redis_scan("t:*", "Сканирование tournaments")
        rows = []
        for i in range(0, len(keys), REDIS_SCAN_COUNT):
            chunk = keys[i:i + REDIS_SCAN_COUNT]
            for k, v in zip(chunk, b.r.mget(chunk)):
                if not v:
                    continue
                try:
                    obj = json.loads(v)
                except Exception:
                    continue
                parts = k.split(":")
                if len(parts) < 4:
                    continue
                sid = b.sport_id(int(parts[1])) if parts[1].isdigit() else None
                cid = b.cat_id(parts[2])
                if not sid or not cid:
                    continue
                en, ru = names(obj, "name")
                rows.append((parts[3], sid, cid, en, ru, obj.get("dv", 0),
                             dt(obj.get("ts")), dt(obj.get("cts")), flag(obj, "removed")))
            progress("Загрузка tournaments", min(i + REDIS_SCAN_COUNT, len(keys)), len(keys))
        b.flush(sql.SQL_TRN, rows)
        with b.db.cursor() as cur:
            cur.execute("SELECT id, feed_hash FROM tournaments")
            b.trn_cache = {r["feed_hash"]: r["id"] for r in cur.fetchall()}
        phase_done("tournaments", f"{len(b.trn_cache):,} строк", time.monotonic() - t0)

    def _load_competitors(self) -> None:
        """Загрузить участников (v:*)."""
        b    = self.b
        t0   = time.monotonic()
        keys = b.redis_scan("v:*", "Сканирование competitors")
        rows = []
        for i in range(0, len(keys), REDIS_SCAN_COUNT):
            chunk = keys[i:i + REDIS_SCAN_COUNT]
            for k, v in zip(chunk, b.r.mget(chunk)):
                if not v:
                    continue
                try:
                    obj = json.loads(v)
                except Exception:
                    continue
                parts = k.split(":")
                if len(parts) < 3:
                    continue
                sid = b.sport_id(int(parts[1])) if parts[1].isdigit() else None
                if not sid:
                    continue
                en, ru = names(obj, "name")
                rows.append((parts[2], sid, en, ru, obj.get("dv", 0),
                             dt(obj.get("ts")), flag(obj, "removed")))
            progress("Загрузка competitors", min(i + REDIS_SCAN_COUNT, len(keys)), len(keys))
        b.flush(sql.SQL_COMP, rows)
        phase_done("competitors", f"{len(rows):,} строк", time.monotonic() - t0)

    def _load_market_type_names(self) -> None:
        """Загрузить справочник имён типов маркетов (sm:*)."""
        b    = self.b
        t0   = time.monotonic()
        keys = b.redis_scan("sm:*", "Сканирование market_type_names")
        vals = b.r.mget(keys) if keys else []
        rows = []
        for k, v in zip(keys, vals):
            if not v:
                continue
            try:
                obj = json.loads(v)
            except Exception:
                continue
            parts = k.split(":")
            if len(parts) < 3 or not parts[1].isdigit() or not parts[2].isdigit():
                continue
            sid = b.sport_id(int(parts[1]))
            if not sid:
                continue
            en, ru = names(obj, "name")
            rows.append((sid, int(parts[2]), en, ru,
                         json.dumps(obj.get("outcomes", [])),
                         json.dumps(obj.get("outcome_names", []))))
        b.flush(sql.SQL_MTN, rows)
        phase_done("market_type_names", f"{len(rows):,} строк", time.monotonic() - t0)

    def _load_events(self) -> None:
        """Загрузить события (e:*); после записи заполнить ev_cache из MySQL."""
        b    = self.b
        t0   = time.monotonic()
        keys = b.redis_scan("e:*", "Сканирование events")
        rows = []
        for i in range(0, len(keys), REDIS_SCAN_COUNT):
            chunk = keys[i:i + REDIS_SCAN_COUNT]
            for k, v in zip(chunk, b.r.mget(chunk)):
                if not v:
                    continue
                try:
                    obj = json.loads(v)
                except Exception:
                    continue
                parts = k.split(":")
                if len(parts) < 5:
                    continue
                sid    = b.sport_id(int(parts[1])) if parts[1].isdigit() else None
                cid    = b.cat_id(parts[2])
                tid    = b.trn_id(parts[3])
                ev_fid = int(parts[4]) if parts[4].isdigit() else None
                if not (sid and cid and tid and ev_fid):
                    continue
                en, ru   = names(obj, "name")
                sen, sru = names(obj, "sname")
                score    = obj.get("score")
                rows.append((
                    ev_fid, sid, cid, tid,
                    en, ru, sen, sru,
                    dt(obj.get("start")), obj.get("stage", 0), obj.get("stagev2", ""),
                    obj.get("status", 0), obj.get("statusv2", ""),
                    json.dumps(score) if isinstance(score, dict) else None,
                    obj.get("dv", 0), obj.get("sdv", 0),
                    dt(obj.get("uts")), flag(obj, "removed"),
                ))
            progress("Загрузка events", min(i + REDIS_SCAN_COUNT, len(keys)), len(keys))
        b.flush(sql.SQL_EVENT, rows)
        with b.db.cursor() as cur:
            cur.execute("SELECT id, feed_id FROM events")
            b.ev_cache = {r["feed_id"]: r["id"] for r in cur.fetchall()}
        phase_done("events", f"{len(b.ev_cache):,} строк", time.monotonic() - t0)

    def _scan_mget_load(self, pattern: str, label: str, build, sql_stmt: str) -> int:
        """SCAN + НЕМЕДЛЕННЫЙ MGET каждого батча, с потоковым flush.

        Старый путь собирал ВЕСЬ список ключей (полный обход m:*/o:* — минуты), и
        только потом читал значения через mget. Для активно «тикающих» ключей
        (партнёр на каждый тик коэффициента делает DEL+SET) к моменту позднего mget
        ключ часто оказывается в фазе «после DEL, до SET» → mget отдаёт None → ключ
        молча терялся. Поэтому свежие прематч/лайв-маркеты в снапшот не попадали, а
        статичные (finished) — попадали. Здесь значение читается СРАЗУ после scan
        батча, пока ключ «горячий»: окно DEL+SET сжимается с минут до миллисекунд.

        build(key, obj) -> tuple | None (None = пропуск). Возвращает число строк.
        """
        b      = self.b
        cursor = 0
        rows   = []
        loaded = 0
        seen   = 0
        while True:
            cursor, batch = b.r.scan(cursor, match=pattern, count=REDIS_SCAN_COUNT)
            if batch:
                for k, v in zip(batch, b.r.mget(batch)):
                    if not v:
                        continue
                    try:
                        obj = json.loads(v)
                    except Exception:
                        continue
                    row = build(k, obj)
                    if row is not None:
                        rows.append(row)
                seen += len(batch)
                print(f"  {C.GRAY}{label}… {seen:,}{C.RESET}", end="\r", flush=True)
            if len(rows) >= FLUSH_EVERY:
                b.flush(sql_stmt, rows)
                loaded += len(rows)
                rows.clear()
            if cursor == 0:
                break
        b.flush(sql_stmt, rows)
        loaded += len(rows)
        return loaded

    def _load_markets(self) -> None:
        """Загрузить маркеты (m:*); читает значения per-batch (см. _scan_mget_load)."""
        b  = self.b
        t0 = time.monotonic()

        def build(k, obj):
            parts = k.split(":")
            if len(parts) < 3:
                return None
            ev_fid = int(parts[1]) if parts[1].isdigit() else None
            if not ev_fid:
                return None
            eid = b.ev_id(ev_fid)
            if not eid:
                return None   # маркет неизвестного/истёкшего события — добьёт листенер из потока
            en, ru   = names(obj, "name")
            pen, pru = names(obj, "period_name")
            return (
                parts[2], eid,
                obj.get("type", 0), obj.get("period", 0),
                en, ru, pen, pru,
                obj.get("value", ""),
                1 if obj.get("open") else 0,
                flag(obj, "removed"),
                obj.get("ver", 0), obj.get("rver", 0),
                dt(obj.get("uts")),
            )

        self._scan_mget_load("m:*", "Загрузка markets", build, sql.SQL_MKT)
        # Кэш маркетов нужен следующему шагу — загрузке исходов; ключ (event_id, feed_hash),
        # т.к. feed_hash маркета общий для разных событий.
        with b.db.cursor() as cur:
            cur.execute("SELECT id, event_id, feed_hash FROM markets")
            b.mkt_cache = {(r["event_id"], r["feed_hash"]): r["id"] for r in cur.fetchall()}
        phase_done("markets", f"{len(b.mkt_cache):,} строк", time.monotonic() - t0)

    def _load_outcomes(self) -> None:
        """Загрузить исходы (o:*); читает значения per-batch (см. _scan_mget_load)."""
        b  = self.b
        t0 = time.monotonic()

        def build(k, obj):
            # split(":", 3) сохраняет полный ocId, даже если он содержит двоеточия
            parts = k.split(":", 3)
            if len(parts) < 4:
                return None
            ev_fid = int(parts[1]) if parts[1].isdigit() else None
            if not ev_fid:
                return None
            eid = b.ev_id(ev_fid)
            mid = b.mkt_id(eid, parts[2]) if eid else None
            if not eid or not mid:
                return None
            en, ru = names(obj, "name")
            price  = obj.get("price")
            return (
                parts[3], mid, eid,
                obj.get("type", 0),
                en, ru,
                obj.get("value", ""),
                float(price) if isinstance(price, (int, float)) else None,
                obj.get("status", 0), obj.get("result", 0),
                flag(obj, "cancelled"), flag(obj, "removed"),
                obj.get("ver", 0), obj.get("rver", 0),
                dt(obj.get("uts")),
            )

        loaded = self._scan_mget_load("o:*", "Загрузка outcomes", build, sql.SQL_OC)
        phase_done("outcomes", f"{loaded:,} строк", time.monotonic() - t0)
