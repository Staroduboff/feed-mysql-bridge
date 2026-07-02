"""
listener.py — класс Listener: инкрементальные обновления AMQP → MySQL.

Назначение
----------
После снапшота данные поддерживаются в актуальном состоянии за счёт потока
обновлений из RabbitMQ. Listener подключается к очереди, на каждое сообщение
разбирает его по каналам (events/markets/outcomes/…) и применяет каждый объект
точечным upsert'ом с защитой версией. Соединение автоматически
переподключается при обрыве с нарастающей паузой.

Гарантия «без молчаливых дропов»
--------------------------------
Если у объекта не разрешён родитель (например, маркет пришёл раньше своего
события, или событие новое и его сообщение ещё не дошло), объект НЕ
отбрасывается. Недостающий родитель дорезолвливается прямо из Redis (он там
всегда есть — ключ m:{ev_fid}:… гарантирует существование события e:*:{ev_fid})
по цепочке sport → category → tournament → event → market, после чего объект
вставляется. Любой реально неразрешимый случай (родителя нет даже в Redis)
считается в счётчике self._res и печатается в статистике — он виден, а не молчит.

Формат сообщения
----------------
JSON с полями:
    pc    — счётчик публикаций сервера (глобальный, растёт на каждое сообщение);
    ch    — канал (тип объектов в data: events, markets, outcomes, …);
    data  — словарь {ключ_redis: объект | null}; null означает «удалён».

Детект рестарта сервера: если pc приходит 0, а раньше был > 0 — сервер
перезапустился, и для гарантированной согласованности нужен новый снапшот.

Класс Listener(bridge)
----------------------
Работает через переданный Bridge (соединения, кэши, разрешение ID).
"""

import collections
import json
import time

try:
    import pymysql
except ImportError:
    pymysql = None

from . import amqp, sql
from .config import AMQP_PREFETCH, AMQP_RETRY_DELAY
from .console import C, fmt_lag
from .transform import dt, flag, names

# Каждые столько сообщений сбрасываем «отрицательные» кэши (родитель не найден),
# чтобы недавно появившийся в Redis родитель получил повторную попытку резолва.
_NEG_RESET_EVERY = 2000

# Коды ошибок MySQL, которые считаем ТРАНЗИЕНТНЫМИ (обрыв/failover/дедлок): сообщение
# не теряем — откатываем, переподключаемся и requeue. Постоянные (данные) — НЕ здесь.
_TRANSIENT_DB_CODES = {1180, 1205, 1213, 2002, 2003, 2006, 2013, 2055, 1047, 1053}

# Ограничение роста позитивных id-кэшей (иначе утечка памяти за дни работы listen).
_MKT_CACHE_CAP = 300_000
_EV_CACHE_CAP  = 80_000

# Максимум ключей, просматриваемых SCAN'ом при дорезолве события, чтобы один холодный
# промах не блокировал поток полным обходом всего keyspace Redis.
_EVENT_SCAN_LIMIT = 400_000


class Listener:
    """Потребление AMQP-потока и применение инкрементальных обновлений."""

    def __init__(self, bridge) -> None:
        self.b = bridge
        # счётчики результата резолва (видимость вместо молчаливых дропов)
        self._res = collections.Counter()
        # отрицательные кэши: родитель не найден даже в Redis (чтобы не сканировать каждый раз)
        self._neg_ev: set = set()    # ev_fid события, отсутствующего в Redis
        self._neg_mkt: set = set()   # feed_hash маркета, отсутствующего в Redis

    # ── низкоуровневый доступ к Redis для дорезолва ───────────────────────────

    def _rget(self, key: str):
        """Прочитать и распарсить JSON-значение ключа Redis (или None)."""
        try:
            v = self.b.r.get(key)
        except Exception:
            return None
        if not v:
            return None
        try:
            return json.loads(v)
        except Exception:
            return None

    def _find_event_key(self, ev_fid: int):
        """Найти полный ключ события e:{sport}:{cat}:{trn}:{ev_fid} в Redis по ev_fid.

        Скан ограничен _EVENT_SCAN_LIMIT ключами: при всплеске (рестарт фида, маркеты
        раньше событий) один холодный промах не должен блокировать поток полным
        обходом всего keyspace. Если не нашли в пределах лимита — None (родитель,
        вероятнее всего, придёт отдельным AMQP-сообщением события, или это сирота)."""
        cursor  = 0
        scanned = 0
        try:
            while True:
                cursor, batch = self.b.r.scan(cursor, match=f"e:*:{ev_fid}", count=10000)
                if batch:
                    return batch[0]
                scanned += 10000
                if cursor == 0 or scanned >= _EVENT_SCAN_LIMIT:
                    return None
        except Exception:
            return None

    def _is_transient_db(self, exc) -> bool:
        """Транзиентная ошибка БД (обрыв/failover/дедлок) — requeue + reconnect, не дроп."""
        if pymysql is not None and isinstance(exc, pymysql.err.InterfaceError):
            return True
        code = exc.args[0] if getattr(exc, "args", None) else None
        return code in _TRANSIENT_DB_CODES

    def _reconnect_db(self) -> bool:
        """Пересоздать соединение с MySQL (после обрыва/failover). True при успехе."""
        try:
            try:
                self.b.db.close()
            except Exception:
                pass
            self.b.connect_mysql()
            self._res["db_reconnect"] += 1
            return True
        except Exception as exc:
            print(f"\n  {C.RED}не удалось переподключиться к MySQL: {exc}{C.RESET}")
            return False

    # ── дорезолв недостающих родителей из Redis (никаких молчаливых дропов) ────

    def _ensure_sport(self, sport_fid_str: str):
        """Гарантировать наличие вида спорта в MySQL (дотянуть из Redis при отсутствии)."""
        b = self.b
        if not sport_fid_str.isdigit():
            return None
        fid = int(sport_fid_str)
        sid = b.sport_id(fid)
        if sid:
            return sid
        obj = self._rget(f"s:{sport_fid_str}")
        if obj is None:
            return None
        en, ru = names(obj, "name")
        with b.db.cursor() as cur:
            cur.execute(sql.SQL_SPORT, (fid, en, ru, obj.get("dv", 0)))
        b.sport_cache.pop(fid, None)
        self._res["resolved_sport"] += 1
        return b.sport_id(fid)

    def _ensure_category(self, sport_fid_str: str, cat_hash: str):
        """Гарантировать наличие категории (дотянуть из Redis при отсутствии)."""
        b = self.b
        if b.cat_id(cat_hash):
            return b.cat_id(cat_hash)
        self._ensure_sport(sport_fid_str)
        obj = self._rget(f"c:{sport_fid_str}:{cat_hash}")
        if obj is not None:
            self._upsert_category(f"c:{sport_fid_str}:{cat_hash}", obj)
            self._res["resolved_category"] += 1
        return b.cat_id(cat_hash)

    def _ensure_tournament(self, sport_fid_str: str, cat_hash: str, trn_hash: str):
        """Гарантировать наличие турнира (дотянуть из Redis при отсутствии)."""
        b = self.b
        if b.trn_id(trn_hash):
            return b.trn_id(trn_hash)
        self._ensure_category(sport_fid_str, cat_hash)
        obj = self._rget(f"t:{sport_fid_str}:{cat_hash}:{trn_hash}")
        if obj is not None:
            self._upsert_tournament(f"t:{sport_fid_str}:{cat_hash}:{trn_hash}", obj)
            self._res["resolved_tournament"] += 1
        return b.trn_id(trn_hash)

    def _ensure_event(self, ev_fid: int):
        """Вернуть surrogate id события; при отсутствии — дотянуть его (и родителей) из Redis."""
        b = self.b
        eid = b.ev_id(ev_fid)
        if eid:
            return eid
        if ev_fid in self._neg_ev:
            return None
        key = self._find_event_key(ev_fid)
        if not key:
            self._neg_ev.add(ev_fid)
            self._res["miss_event_not_in_redis"] += 1
            return None
        obj = self._rget(key)
        if obj is not None:
            self._upsert_event(key, obj)   # _upsert_event сам дотягивает sport/category/tournament
            self._res["resolved_event"] += 1
        return b.ev_id(ev_fid)

    def _ensure_market(self, ev_fid: int, mkt_hash: str):
        """Вернуть surrogate id маркета по (event, hash); при отсутствии — дотянуть из Redis."""
        b = self.b
        eid = self._ensure_event(ev_fid)
        if not eid:
            return None
        mid = b.mkt_id(eid, mkt_hash)
        if mid:
            return mid
        nk = (ev_fid, mkt_hash)
        if nk in self._neg_mkt:
            return None
        obj = self._rget(f"m:{ev_fid}:{mkt_hash}")
        if obj is None:
            self._neg_mkt.add(nk)
            self._res["miss_market_not_in_redis"] += 1
            return None
        self._upsert_market(f"m:{ev_fid}:{mkt_hash}", obj)   # сам дотянет событие
        self._res["resolved_market"] += 1
        return b.mkt_id(eid, mkt_hash)

    def _stat(self, k: str) -> None:
        self._res[k] += 1

    def run(self) -> None:
        """Подключаться к RabbitMQ и применять обновления; переподключаться при обрыве."""
        b  = self.b
        rc = b.cfg["rabbitmq"]
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n{C.BOLD}{'─' * 64}")
        print(f"  LISTEN  ·  {ts}  ·  очередь '{rc['queue']}'")
        print(f"{'─' * 64}{C.RESET}")

        stats = {"ok": 0, "err": 0, "obj": 0, "t0": time.monotonic()}

        def on_message(ch, method, props, body):
            # 1) Парсинг. Битый JSON невосстановим → ack + лог (requeue зациклил бы «ядовитое»).
            try:
                msg = json.loads(body)
            except Exception as exc:
                stats["err"] += 1
                self._res["bad_json"] += 1
                print(f"\n  {C.RED}битый JSON сообщения: {exc}{C.RESET}")
                ch.basic_ack(delivery_tag=method.delivery_tag)
                return

            pc      = msg.get("pc", 0)
            feed_ch = msg.get("ch", "")
            data    = msg.get("data") or {}

            # Детект рестарта сервера — для согласованности нужен снапшот
            if pc == 0 and b.amqp_pc > 0:
                print(f"\n  {C.YELLOW}⚠  pc=0: сервер перезапустился. "
                      f"Для полной синхронизации перезапустите с --snapshot.{C.RESET}")
            b.amqp_pc = pc

            # 2) Применяем объекты. ТРАНЗИЕНТНАЯ ошибка БД (обрыв/failover/дедлок)
            #    пробрасывается на уровень сообщения → откат + reconnect + requeue
            #    (сообщение НЕ теряется). Ошибка ДАННЫХ по одному объекту — считается и
            #    пропускается, не топя весь батч.
            try:
                for key, obj in data.items():
                    if obj is None:
                        continue
                    stats["obj"] += 1
                    try:
                        self._apply_update(feed_ch, key, obj)
                    except Exception as exc:
                        if self._is_transient_db(exc):
                            raise
                        self._res["apply_error"] += 1
                        if self._res["apply_error"] <= 20:
                            code = exc.args[0] if getattr(exc, "args", None) else ""
                            print(f"\n  {C.RED}apply_error [{feed_ch}] {key} (код {code}): {exc}{C.RESET}")
                b.db.commit()
            except Exception as exc:
                # Транзиентный сбой БД или провал commit — сообщение НЕ теряем.
                self._res["db_error"] += 1
                stats["err"] += 1
                try:
                    b.db.rollback()
                except Exception:
                    pass
                print(f"\n  {C.RED}сбой БД на сообщении: {exc} → requeue{C.RESET}")
                if self._is_transient_db(exc):
                    self._reconnect_db()
                try:
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
                except Exception:
                    pass
                time.sleep(1)   # не крутить requeue-петлю вхолостую, пока БД восстанавливается
                return

            ch.basic_ack(delivery_tag=method.delivery_tag)
            stats["ok"] += 1

            n = stats["ok"] + stats["err"]
            if n % _NEG_RESET_EVERY == 0:
                # даём шанс повторно резолвить родителей, появившихся в Redis позже
                self._neg_ev.clear()
                self._neg_mkt.clear()
                # ограничиваем рост позитивных кэшей (репополнятся из MySQL по требованию)
                if len(b.mkt_cache) > _MKT_CACHE_CAP:
                    b.mkt_cache.clear()
                if len(b.ev_cache) > _EV_CACHE_CAP:
                    b.ev_cache.clear()
            if n % 500 == 0:
                self._print_stats(stats, rc)

        attempt = 0
        while True:
            try:
                attempt += 1
                print(f"\n  {C.GRAY}Подключение к RabbitMQ (попытка {attempt})…{C.RESET}")
                # heartbeat щедрый: callback синхронно пишет в MySQL и может дёргать Redis;
                # при кратком тормозе БД 60с-heartbeat мог не успеть уйти и рвал AMQP.
                conn    = amqp.connect(rc, heartbeat=120, connection_attempts=1)
                channel = conn.channel()
                channel.basic_qos(prefetch_count=AMQP_PREFETCH)
                channel.basic_consume(queue=rc["queue"], on_message_callback=on_message)
                print(f"  {C.GREEN}✓ Подключён. Слушаю поток…{C.RESET}")
                attempt = 0
                channel.start_consuming()

            except KeyboardInterrupt:
                print(f"\n\n  {C.YELLOW}Остановлено.{C.RESET}")
                try:
                    channel.stop_consuming()
                    conn.close()
                except Exception:
                    pass
                elapsed = time.monotonic() - stats["t0"]
                rate    = stats["ok"] / elapsed if elapsed > 0 else 0
                print(f"  Итого: {stats['ok']:,} сообщений  "
                      f"{rate:.0f} msg/s  ошибок: {stats['err']}")
                self._print_resolve(force=True)
                break

            except Exception as exc:
                print(f"  {C.RED}Соединение прервано: {exc}{C.RESET}")
                delay = min(AMQP_RETRY_DELAY * attempt, 60)
                print(f"  Повтор через {delay} с…")
                time.sleep(delay)

    def _print_stats(self, stats: dict, rc: dict) -> None:
        """Напечатать строку статистики: обработано, скорость, ошибки, очередь, отставание."""
        elapsed  = time.monotonic() - stats["t0"]
        rate     = stats["ok"] / elapsed if elapsed > 0 else 0
        lag_part = ""
        try:
            left = amqp.queue_depth(rc)
            lag_part = f"  {C.GRAY}в очереди: {C.WHITE}{left:,}"
            if left == 0:
                lag_part += f"  {C.GRAY}отставание: {C.WHITE}0с"
            elif rate > 0:
                lag_part += f"  {C.GRAY}отставание: {C.WHITE}{fmt_lag(left / rate)}"
        except Exception:
            pass
        print(f"\r  {C.GRAY}AMQP  сообщ: {C.WHITE}{stats['ok']:,}  "
              f"{C.GRAY}объектов: {C.WHITE}{stats['obj']:,}  "
              f"{C.GRAY}скорость: {C.WHITE}{rate:.0f} msg/s  "
              f"{C.GRAY}ошибки: {C.RED}{stats['err']}{C.RESET}"
              f"{lag_part}{C.RESET}\033[K",
              end="", flush=True)
        self._print_resolve()

    def _print_resolve(self, force: bool = False) -> None:
        """Печать счётчиков дорезолва/дропов — чтобы потеря данных была ВИДНА, а не молчала."""
        if not self._res:
            return
        parts = []
        for k in ("resolved_event", "resolved_market", "resolved_category",
                  "resolved_tournament", "resolved_sport",
                  "miss_event_not_in_redis", "miss_market_not_in_redis",
                  "drop_event_no_parent", "drop_market_no_event", "drop_outcome_no_parent",
                  "drop_competitor_no_sport", "drop_category_no_sport", "drop_tournament_no_parent",
                  "apply_error", "bad_json", "db_error", "db_reconnect"):
            if self._res.get(k):
                parts.append(f"{k}={self._res[k]}")
        if parts:
            print(f"\n  {C.GRAY}резолв: {C.WHITE}{'  '.join(parts)}{C.RESET}",
                  end=("\n" if force else ""), flush=True)

    def _apply_update(self, channel: str, key: str, obj: dict) -> None:
        """Направить один объект из data в нужный upsert по имени канала."""
        dispatch = {
            "events":      self._upsert_event,
            "markets":     self._upsert_market,
            "outcomes":    self._upsert_outcome,
            "categories":  self._upsert_category,
            "tournaments": self._upsert_tournament,
            "competitors": self._upsert_competitor,
        }
        fn = dispatch.get(channel)
        if fn:
            fn(key, obj)

    # ── точечные upsert'ы (путь AMQP) ─────────────────────────────────────────

    def _upsert_category(self, key: str, obj: dict) -> None:
        """upsert одной категории (c:*) с разрешением sport_id (дотягивает sport из Redis)."""
        b = self.b
        parts = key.split(":")
        if len(parts) < 3:
            return
        sid = self._ensure_sport(parts[1]) if parts[1].isdigit() else None
        if not sid:
            self._res["drop_category_no_sport"] += 1
            return
        en, ru = names(obj, "name")
        with b.db.cursor() as cur:
            cur.execute(sql.SQL_CAT, (parts[2], sid, en, ru, obj.get("dv", 0),
                                      dt(obj.get("ts")), flag(obj, "removed")))
        b.cat_cache.pop(parts[2], None)

    def _upsert_tournament(self, key: str, obj: dict) -> None:
        """upsert одного турнира (t:*) с разрешением sport_id и category_id (дотягивает из Redis)."""
        b = self.b
        parts = key.split(":")
        if len(parts) < 4:
            return
        sid = self._ensure_sport(parts[1]) if parts[1].isdigit() else None
        cid = self._ensure_category(parts[1], parts[2]) if parts[1].isdigit() else None
        if not sid or not cid:
            self._res["drop_tournament_no_parent"] += 1
            return
        en, ru = names(obj, "name")
        with b.db.cursor() as cur:
            cur.execute(sql.SQL_TRN, (parts[3], sid, cid, en, ru, obj.get("dv", 0),
                                      dt(obj.get("ts")), dt(obj.get("cts")),
                                      flag(obj, "removed")))
        b.trn_cache.pop(parts[3], None)

    def _upsert_competitor(self, key: str, obj: dict) -> None:
        """upsert одного участника (v:*) с разрешением sport_id."""
        b = self.b
        parts = key.split(":")
        if len(parts) < 3:
            return
        sid = self._ensure_sport(parts[1]) if parts[1].isdigit() else None
        if not sid:
            self._res["drop_competitor_no_sport"] += 1
            return
        en, ru = names(obj, "name")
        with b.db.cursor() as cur:
            cur.execute(sql.SQL_COMP, (parts[2], sid, en, ru, obj.get("dv", 0),
                                       dt(obj.get("ts")), flag(obj, "removed")))

    def _upsert_event(self, key: str, obj: dict) -> None:
        """upsert одного события (e:*); дотягивает sport/category/tournament из Redis при отсутствии."""
        b = self.b
        parts = key.split(":")
        if len(parts) < 5:
            return
        ev_fid = int(parts[4]) if parts[4].isdigit() else None
        if not ev_fid:
            return
        sid = self._ensure_sport(parts[1]) if parts[1].isdigit() else None
        cid = self._ensure_category(parts[1], parts[2]) if parts[1].isdigit() else None
        tid = self._ensure_tournament(parts[1], parts[2], parts[3]) if parts[1].isdigit() else None
        if not (sid and cid and tid):
            self._res["drop_event_no_parent"] += 1
            return
        en, ru   = names(obj, "name")
        sen, sru = names(obj, "sname")
        score    = obj.get("score")
        with b.db.cursor() as cur:
            cur.execute(sql.SQL_EVENT, (
                ev_fid, sid, cid, tid,
                en, ru, sen, sru,
                dt(obj.get("start")), obj.get("stage", 0), obj.get("stagev2", ""),
                obj.get("status", 0), obj.get("statusv2", ""),
                json.dumps(score) if isinstance(score, dict) else None,
                obj.get("dv", 0), obj.get("sdv", 0),
                dt(obj.get("uts")), flag(obj, "removed"),
            ))
        b.ev_cache.pop(ev_fid, None)   # форсируем пере-поиск, если id только что создан
        self._neg_ev.discard(ev_fid)

    def _upsert_market(self, key: str, obj: dict) -> None:
        """upsert одного маркета (m:*); событие при отсутствии дотягивается из Redis (без дропа)."""
        b = self.b
        parts = key.split(":")
        if len(parts) < 3:
            return
        ev_fid = int(parts[1]) if parts[1].isdigit() else None
        if not ev_fid:
            return
        eid = self._ensure_event(ev_fid)
        if not eid:
            self._res["drop_market_no_event"] += 1   # событие отсутствует даже в Redis — видно в статистике
            return
        en, ru   = names(obj, "name")
        pen, pru = names(obj, "period_name")
        with b.db.cursor() as cur:
            cur.execute(sql.SQL_MKT, (
                parts[2], eid,
                obj.get("type", 0), obj.get("period", 0),
                en, ru, pen, pru,
                obj.get("value", ""),
                1 if obj.get("open") else 0,
                flag(obj, "removed"),
                obj.get("ver", 0), obj.get("rver", 0),
                dt(obj.get("uts")),
            ))
        b.mkt_cache.pop((eid, parts[2]), None)
        self._neg_mkt.discard((ev_fid, parts[2]))

    def _upsert_outcome(self, key: str, obj: dict) -> None:
        """upsert одного исхода (o:*); событие и маркет при отсутствии дотягиваются из Redis (без дропа)."""
        b = self.b
        parts = key.split(":", 3)   # сохраняем полный ocId (может содержать двоеточия)
        if len(parts) < 4:
            return
        ev_fid = int(parts[1]) if parts[1].isdigit() else None
        if not ev_fid:
            return
        eid = self._ensure_event(ev_fid)
        mid = self._ensure_market(ev_fid, parts[2])
        if not eid or not mid:
            self._res["drop_outcome_no_parent"] += 1   # событие/маркет отсутствуют даже в Redis — видно
            return
        en, ru = names(obj, "name")
        price  = obj.get("price")
        with b.db.cursor() as cur:
            cur.execute(sql.SQL_OC, (
                parts[3], mid, eid,
                obj.get("type", 0),
                en, ru,
                obj.get("value", ""),
                float(price) if isinstance(price, (int, float)) else None,
                obj.get("status", 0), obj.get("result", 0),
                flag(obj, "cancelled"), flag(obj, "removed"),
                obj.get("ver", 0), obj.get("rver", 0),
                dt(obj.get("uts")),
            ))
