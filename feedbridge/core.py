"""
core.py — класс Bridge: общее состояние, соединения и разрешение ID.

Назначение
----------
Bridge хранит то, что нужно обоим режимам работы (снапшоту и листенеру):
соединения с Redis и MySQL, кэши соответствия «идентификатор фида → id строки
в MySQL» и низкоуровневые помощники (SCAN по Redis, пакетная запись, очистка
таблиц). Snapshotter и Listener получают экземпляр Bridge и работают через него,
поэтому кэши и соединения у них общие.

Зачем кэши ID
-------------
В фиде объекты ссылаются друг на друга по «хэшам»/feed_id (категория события,
турнир и т.д.), а в MySQL связи идут по суррогатным AUTO_INCREMENT id. Кэши
переводят одно в другое без лишних SELECT на каждую строку.

Атрибуты
--------
cfg              Загруженная конфигурация.
r                Соединение с Redis (decode_responses=True).
db               Соединение с MySQL (autocommit=False).
sport_cache      feed_id вида спорта (int)        → id
cat_cache        хэш категории                    → id
trn_cache        хэш турнира                      → id
ev_cache         feed_id события (int)            → id
mkt_cache        хэш маркета                      → id (заполняется лениво)
amqp_pc          Последний виденный счётчик публикаций pc (для детекта рестарта).
pub              Publisher — батч-рассылка дельт в Centrifugo (см. publish.py).
gate             Gate — готовность приёма ставок по свежести фида (см. gate.py).

Методы соединений
-----------------
connect_redis()  Подключиться к Redis и проверить ping.
connect_mysql()  Подключиться к MySQL (через db.mysql_connect).

Разрешение ID (сначала кэш, затем SELECT в MySQL)
-------------------------------------------------
sport_id(feed_id) / cat_id(h) / trn_id(h) / ev_id(feed_id) / mkt_id(h)
    Возвращают суррогатный id или None, если строки ещё нет.

Кэши и откат транзакции
-----------------------
Родители создаются внутри той же транзакции, в которой пишутся потомки, поэтому
попавший в кэш id может принадлежать ещё не зафиксированной строке. Если
транзакция откатится, такой id станет «фантомным»: строки в базе нет, а
последующие вставки потомков падали бы по внешнему ключу до перезапуска моста.
Все заполнения кэшей идут через cache_put() и помечаются; tx_ok() снимает
пометки после commit, tx_undo() убирает такие записи после rollback.

Низкоуровневые помощники
------------------------
redis_scan(pattern, label="")   Полный SCAN ключей по шаблону (с прогрессом).
cache_put(cache, key, value)    Заполнить кэш id с пометкой «в текущей транзакции».
tx_ok() / tx_undo()             Подтвердить/снять пометки кэшей после commit/rollback.
flush(sql, rows)                Пакетная запись строк (многострочный INSERT) + commit.
warm_caches()                   Загрузить все ID-кэши из MySQL (режим --listen).
truncate_db()                   Очистить все таблицы фида и сбросить кэши.
"""

import sys

from . import sql
from .config import REDIS_SCAN_COUNT, SQL_BATCH
from .console import C
from .db import mysql_connect
from .gate import Gate
from .publish import Publisher

try:
    import redis as redis_lib
except ImportError:
    print("redis не установлен: pip install redis")
    sys.exit(1)


class Bridge:
    """Общие соединения, кэши идентификаторов и низкоуровневые операции."""

    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.r   = None   # redis.Redis
        self.db  = None   # pymysql.Connection
        # Кэши суррогатных id: идентификатор фида → AUTO_INCREMENT id в MySQL
        self.sport_cache: dict = {}  # feed_id вида спорта (int) → id
        self.cat_cache:   dict = {}  # хэш категории            → id
        self.trn_cache:   dict = {}  # хэш турнира              → id
        self.ev_cache:    dict = {}  # feed_id события (int)    → id
        self.mkt_cache:   dict = {}  # (event_id, хэш маркета)  → id (лениво)
        # Записи кэшей, сделанные в текущей (ещё не зафиксированной) транзакции:
        # при откате их надо снять, иначе в кэше останется id несуществующей строки.
        self._tx_marks: list = []
        self.amqp_pc: int = -1       # последний виденный счётчик публикаций AMQP
        # Рассылка дельт в Centrifugo батчами (секция centrifugo в config.json; иначе no-op)
        self.pub = Publisher(cfg)
        # Готовность приёма ставок по свежести фида (секция gate в config.json)
        self.gate = Gate(cfg)

    # ── соединения ──────────────────────────────────────────────────────────

    def connect_redis(self) -> None:
        """Подключиться к Redis по cfg['redis'] и проверить доступность (ping)."""
        rc = self.cfg["redis"]
        self.r = redis_lib.Redis(
            host=rc["host"], port=rc["port"],
            password=rc["password"], db=rc.get("db", 0),
            decode_responses=True,
            socket_connect_timeout=10, socket_timeout=30,
        )
        self.r.ping()
        print(f"  {C.GREEN}✓ Redis  {rc['host']}:{rc['port']}{C.RESET}")

    def connect_mysql(self) -> None:
        """Подключиться к MySQL по cfg['mysql'] в режиме транзакций (autocommit=False)."""
        mc = self.cfg["mysql"]
        self.db = mysql_connect(self.cfg, autocommit=False)
        print(f"  {C.GREEN}✓ MySQL  {mc['host']}:{mc.get('port',3306)}/{mc['database']}{C.RESET}")

    # ── кэши id и их согласованность с транзакцией ────────────────────────────

    def cache_put(self, cache: dict, key, value) -> None:
        """Положить соответствие в кэш id, пометив его как сделанное в текущей транзакции."""
        cache[key] = value
        self._tx_marks.append((cache, key))

    def tx_ok(self) -> None:
        """Транзакция зафиксирована: записи кэшей соответствуют строкам в базе."""
        self._tx_marks.clear()

    def tx_undo(self) -> None:
        """Транзакция откачена: снять записи кэшей, сделанные внутри неё."""
        for cache, key in self._tx_marks:
            cache.pop(key, None)
        self._tx_marks.clear()

    # ── разрешение ID (сначала кэш, потом MySQL) ──────────────────────────────

    def sport_id(self, feed_id: int):
        """Суррогатный id вида спорта по его feed_id (или None)."""
        if feed_id in self.sport_cache:
            return self.sport_cache[feed_id]
        with self.db.cursor() as cur:
            cur.execute("SELECT id FROM sports WHERE feed_id=%s", (feed_id,))
            row = cur.fetchone()
        if row:
            self.cache_put(self.sport_cache, feed_id, row["id"])
            return row["id"]
        return None

    def cat_id(self, h: str):
        """Суррогатный id категории по её хэшу (или None)."""
        if h in self.cat_cache:
            return self.cat_cache[h]
        with self.db.cursor() as cur:
            cur.execute("SELECT id FROM categories WHERE feed_hash=%s", (h,))
            row = cur.fetchone()
        if row:
            self.cache_put(self.cat_cache, h, row["id"])
            return row["id"]
        return None

    def trn_id(self, h: str):
        """Суррогатный id турнира по его хэшу (или None)."""
        if h in self.trn_cache:
            return self.trn_cache[h]
        with self.db.cursor() as cur:
            cur.execute("SELECT id FROM tournaments WHERE feed_hash=%s", (h,))
            row = cur.fetchone()
        if row:
            self.cache_put(self.trn_cache, h, row["id"])
            return row["id"]
        return None

    def ev_id(self, feed_id: int):
        """Суррогатный id события по его feed_id (или None)."""
        if feed_id in self.ev_cache:
            return self.ev_cache[feed_id]
        with self.db.cursor() as cur:
            cur.execute("SELECT id FROM events WHERE feed_id=%s", (feed_id,))
            row = cur.fetchone()
        if row:
            self.cache_put(self.ev_cache, feed_id, row["id"])
            return row["id"]
        return None

    def mkt_id(self, event_id, h: str):
        """Суррогатный id маркета по (event_id, feed_hash) (или None).

        ВАЖНО: feed_hash маркета (parts[2] ключа m:{ev_fid}:{hash}) — это хэш ШАБЛОНА
        маркета (тип/период/параметры), он ОБЩИЙ для разных событий. Поэтому маркет
        уникален только парой (event_id, feed_hash); резолвить по одному feed_hash
        нельзя — иначе маркеты разных событий схлопываются в одну строку.
        """
        key = (event_id, h)
        if key in self.mkt_cache:
            return self.mkt_cache[key]
        with self.db.cursor() as cur:
            cur.execute("SELECT id FROM markets WHERE event_id=%s AND feed_hash=%s", (event_id, h))
            row = cur.fetchone()
        if row:
            self.cache_put(self.mkt_cache, key, row["id"])
            return row["id"]
        return None

    # ── низкоуровневые помощники ──────────────────────────────────────────────

    def redis_scan(self, pattern: str, label: str = "") -> list:
        """Полный SCAN всех ключей Redis по шаблону; печатает счётчик при label."""
        keys, cursor = [], 0
        while True:
            cursor, batch = self.r.scan(cursor, match=pattern, count=REDIS_SCAN_COUNT)
            keys.extend(batch)
            if label:
                print(f"  {C.GRAY}{label}… {len(keys):,}{C.RESET}", end="\r", flush=True)
            if cursor == 0:
                break
        return keys

    def flush(self, statement: str, rows: list) -> None:
        """Записать пачку строк одним многострочным INSERT (по SQL_BATCH) и сделать commit.

        Не executemany: его склейка в один INSERT срабатывает только для кортежей
        VALUES из одних плейсхолдеров, а у маркетов и исходов там есть литералы
        (NOW(), NULL) — самые большие таблицы писались бы построчно. sql.many()
        собирает многострочный оператор явно (см. sql.py).

        Если в чанке одна строка нарушает constraint/типизацию, падает весь чанк
        (500 строк). В этом случае откатываемся на построчную вставку: валидные
        строки сохраняются, битые пропускаются с предупреждением — один мусорный
        объект не должен ронять весь снапшот.
        """
        if not rows:
            return
        with self.db.cursor() as cur:
            for i in range(0, len(rows), SQL_BATCH):
                chunk = rows[i:i + SQL_BATCH]
                try:
                    cur.execute(sql.many(statement, len(chunk)),
                                [v for row in chunk for v in row])
                except Exception as exc:
                    bad = 0
                    for row in chunk:
                        try:
                            cur.execute(statement, row)
                        except Exception:
                            bad += 1
                    print(f"  {C.YELLOW}⚠  flush: чанк упал многострочной вставкой "
                          f"({exc}); построчно, пропущено {bad}/{len(chunk)}{C.RESET}")
        self.db.commit()
        self.tx_ok()

    def warm_caches(self) -> None:
        """Загрузить ID-кэши из MySQL — нужно при старте в режиме --listen."""
        print(f"  {C.GRAY}Загрузка ID-кэшей из MySQL…{C.RESET}", end="\r", flush=True)
        with self.db.cursor() as cur:
            cur.execute("SELECT id, feed_id FROM sports")
            self.sport_cache = {r["feed_id"]: r["id"] for r in cur.fetchall()}
            cur.execute("SELECT id, feed_hash FROM categories")
            self.cat_cache = {r["feed_hash"]: r["id"] for r in cur.fetchall()}
            cur.execute("SELECT id, feed_hash FROM tournaments")
            self.trn_cache = {r["feed_hash"]: r["id"] for r in cur.fetchall()}
            cur.execute("SELECT id, feed_id FROM events")
            self.ev_cache = {r["feed_id"]: r["id"] for r in cur.fetchall()}
        # кэши перезаписаны целиком — старые пометки указывают на прежние словари
        self._tx_marks.clear()
        total = (len(self.sport_cache) + len(self.cat_cache) +
                 len(self.trn_cache)  + len(self.ev_cache))
        print(f"  {C.GRAY}ID-кэши: {C.WHITE}{total:,} записей  "
              f"{C.GRAY}(sports {len(self.sport_cache)}, "
              f"cats {len(self.cat_cache)}, "
              f"trns {len(self.trn_cache)}, "
              f"evs {len(self.ev_cache)}){C.RESET}")

    def truncate_db(self) -> None:
        """Очистить все таблицы фида (в обратном FK-порядке) и сбросить кэши."""
        with self.db.cursor() as cur:
            cur.execute("SET FOREIGN_KEY_CHECKS=0")
            try:
                for t in sql.TABLES:
                    cur.execute(f"TRUNCATE TABLE {t}")
            finally:
                # ОБЯЗАТЕЛЬНО восстановить: соединение долгоживущее и уходит в listen;
                # иначе вся listen-сессия осталась бы с выключенной проверкой FK.
                cur.execute("SET FOREIGN_KEY_CHECKS=1")
        self.db.commit()
        self.tx_ok()
        self.sport_cache.clear()
        self.cat_cache.clear()
        self.trn_cache.clear()
        self.ev_cache.clear()
        self.mkt_cache.clear()
