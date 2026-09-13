"""
sweep.py — класс Sweeper: синхронизация базы с исчезновением объектов из фида.

Зачем
-----
Фид удаляет объекты из Redis по TTL, не присылая сообщения: ключ события живёт
бессрочно, пока событие активно, и ровно 24 часа после `status: 3`. Листенер
получает переход в status 3, но факт исчезновения ключа ему не виден, поэтому
без сборщика база копит завершённые события и их исходы навечно, а «зомби»
(события, которым фид так и не прислал завершение и чьи ключи пропали) остаются
в базе как live.

Три правила (запуск: `bridge.py --sweep`, по крону раз в 5 минут)
---------------------------------------------------------------
1. Правило 24 часов. Событие со `status=3` и `uts` старше `expire_hours` →
   `removed=1`, `gone_at = uts + expire_hours`, каскадом `removed=1` его
   маркетам и исходам. WS-дельта не публикуется: клиенты видели status 3.
2. Правило аномалий. Для активных событий базы (`status<3`, `removed=0`) —
   пакетный EXISTS ключей в Redis (ключ собирается из базы, скана нет). Ключа
   нет → событие с детьми помечается `removed=1, gone_at=NOW()`, в Centrifugo
   уходит дельта удаления `{"ch":"events","k":"e:…","d":null}` в `sport:{id}`
   и `event:{id}`. Предохранитель: если Redis недоступен или пропало больше
   `anomaly_max_share` активных событий (фид перестраивается), ничего не
   помечаем и пишем предупреждение.
3. Ретенция. Строки событий с `removed=1` и `gone_at` старше `retention_days`
   удаляются чанками; маркеты и исходы уходят каскадом по FK (ON DELETE CASCADE).

Все шаги идут короткими транзакциями по чанкам с паузами, чтобы не мешать
листенеру. Итог каждого прогона печатается и (если каталог есть) пишется в
`state_file` для витрины здоровья.

Настройки — секция `sweep` в config.json (все необязательны):
    expire_hours        24     через сколько часов после status=3 ключ исчезает
    retention_days      7      сколько суток хранить удалённые события
    chunk               1000   событий в одном UPDATE
    purge_chunk         200    событий в одном DELETE (с каскадом маркетов/исходов)
    pause_ms            100    пауза между чанками
    anomaly_max_share   0.2    предохранитель правила аномалий
    state_file          /var/lib/feed-health/sweep.json
"""

import json
import os
import time

from .console import C


class Sweeper:
    def __init__(self, bridge) -> None:
        self.b = bridge
        c = bridge.cfg.get("sweep") or {}
        self.expire_hours = int(c.get("expire_hours", 24))
        self.retention_days = int(c.get("retention_days", 7))
        self.chunk = int(c.get("chunk", 1000))
        self.purge_chunk = int(c.get("purge_chunk", 200))
        self.pause = float(c.get("pause_ms", 100)) / 1000.0
        self.anomaly_max_share = float(c.get("anomaly_max_share", 0.2))
        self.state_file = c.get("state_file", "/var/lib/feed-health/sweep.json")
        self.stats = dict(expired_events=0, expired_markets=0, expired_outcomes=0,
                          anomaly_checked=0, anomaly_gone=0, anomaly_skipped=None,
                          purged_events=0, purge_pending=0, errors=[])

    # ── helpers ──────────────────────────────────────────────────────────────

    def _cascade(self, cur, ids: list) -> None:
        """removed=1 маркетам и исходам перечисленных событий (по суррогатным id)."""
        ph = ",".join(["%s"] * len(ids))
        cur.execute(f"UPDATE markets SET removed=1 WHERE removed=0 AND event_id IN ({ph})", ids)
        self.stats["expired_markets"] += cur.rowcount
        cur.execute(f"UPDATE outcomes SET removed=1 WHERE removed=0 AND event_id IN ({ph})", ids)
        self.stats["expired_outcomes"] += cur.rowcount

    # ── правило 24 часов ────────────────────────────────────────────────────

    def mark_expired(self) -> None:
        db = self.b.db
        with db.cursor() as cur:
            # события, которые фид пометил removed раньше, чем появилась gone_at
            cur.execute("UPDATE events SET gone_at=DATE_ADD(uts, INTERVAL %s HOUR) "
                        "WHERE removed=1 AND gone_at IS NULL", (self.expire_hours,))
            db.commit()
            while True:
                cur.execute("SELECT id FROM events WHERE status=3 AND removed=0 "
                            "AND uts < NOW()-INTERVAL %s HOUR ORDER BY id LIMIT %s",
                            (self.expire_hours, self.chunk))
                ids = [r["id"] for r in cur.fetchall()]
                if not ids:
                    break
                ph = ",".join(["%s"] * len(ids))
                cur.execute(f"UPDATE events SET removed=1, gone_at=DATE_ADD(uts, INTERVAL %s HOUR) "
                            f"WHERE id IN ({ph})", [self.expire_hours] + ids)
                self.stats["expired_events"] += cur.rowcount
                self._cascade(cur, ids)
                db.commit()
                time.sleep(self.pause)

    # ── правило аномалий ────────────────────────────────────────────────────

    def check_anomalies(self) -> None:
        b, db = self.b, self.b.db
        try:
            b.r.ping()
        except Exception as e:
            self.stats["anomaly_skipped"] = f"redis недоступен: {str(e)[:80]}"
            return
        with db.cursor() as cur:
            cur.execute("SELECT e.id, e.feed_id, s.feed_id sp, c.feed_hash ch, t.feed_hash th "
                        "FROM events e JOIN sports s ON s.id=e.sport_id "
                        "JOIN categories c ON c.id=e.category_id "
                        "JOIN tournaments t ON t.id=e.tournament_id "
                        "WHERE e.status<3 AND e.removed=0")
            rows = cur.fetchall()
        self.stats["anomaly_checked"] = len(rows)
        if not rows:
            return
        missing = []
        for i in range(0, len(rows), 500):
            part = rows[i:i + 500]
            p = b.r.pipeline()
            for r in part:
                p.exists(f"e:{r['sp']}:{r['ch']}:{r['th']}:{r['feed_id']}")
            try:
                res = p.execute()
            except Exception as e:
                self.stats["anomaly_skipped"] = f"redis EXISTS: {str(e)[:80]}"
                return
            missing.extend(r for r, ex in zip(part, res) if not ex)
        if not missing:
            return
        share = len(missing) / len(rows)
        if share > self.anomaly_max_share:
            self.stats["anomaly_skipped"] = (f"пропало {len(missing)} из {len(rows)} активных "
                                             f"({share:.0%}) — похоже на перестроение фида, не помечаем")
            return
        with db.cursor() as cur:
            for i in range(0, len(missing), self.chunk):
                part = missing[i:i + self.chunk]
                ids = [r["id"] for r in part]
                ph = ",".join(["%s"] * len(ids))
                cur.execute(f"UPDATE events SET removed=1, gone_at=NOW() WHERE id IN ({ph})", ids)
                self.stats["anomaly_gone"] += cur.rowcount
                self._cascade(cur, ids)
                db.commit()
                for r in part:
                    key = f"e:{r['sp']}:{r['ch']}:{r['th']}:{r['feed_id']}"
                    msg = {"ch": "events", "k": key, "d": None}
                    b.pub.add(f"sport:{r['sp']}", msg)
                    b.pub.add(f"event:{r['feed_id']}", msg)
                time.sleep(self.pause)
        b.pub.flush()

    # ── ретенция ────────────────────────────────────────────────────────────

    def purge(self) -> None:
        db = self.b.db
        with db.cursor() as cur:
            while True:
                cur.execute("SELECT id FROM events WHERE removed=1 AND gone_at IS NOT NULL "
                            "AND gone_at < NOW()-INTERVAL %s DAY ORDER BY id LIMIT %s",
                            (self.retention_days, self.purge_chunk))
                ids = [r["id"] for r in cur.fetchall()]
                if not ids:
                    break
                ph = ",".join(["%s"] * len(ids))
                # маркеты и исходы уходят каскадом по FK ON DELETE CASCADE
                cur.execute(f"DELETE FROM events WHERE id IN ({ph})", ids)
                self.stats["purged_events"] += cur.rowcount
                db.commit()
                time.sleep(self.pause)
            cur.execute("SELECT COUNT(*) n FROM events WHERE removed=1 AND gone_at IS NOT NULL "
                        "AND gone_at >= NOW()-INTERVAL %s DAY", (self.retention_days,))
            self.stats["purge_pending"] = cur.fetchone()["n"]

    # ── run ─────────────────────────────────────────────────────────────────

    def run(self) -> None:
        t0 = time.time()
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n{C.BOLD}{'─' * 64}\n  SWEEP  ·  {ts}  ·  24ч={self.expire_hours}  ретенция={self.retention_days}д\n{'─' * 64}{C.RESET}")
        for name, fn in (("expired", self.mark_expired), ("anomalies", self.check_anomalies), ("purge", self.purge)):
            t1 = time.time()
            try:
                fn()
            except Exception as e:
                self.stats["errors"].append(f"{name}: {str(e)[:120]}")
                try:
                    self.b.db.rollback()
                except Exception:
                    pass
            print(f"  {C.GRAY}{name}: {time.time() - t1:.1f} с{C.RESET}", flush=True)
        s = self.stats
        s["duration_s"] = round(time.time() - t0, 1)
        s["t"] = int(time.time())
        print(f"  {C.GREEN}✓{C.RESET} истекло: событий {s['expired_events']:,}, маркетов {s['expired_markets']:,}, "
              f"исходов {s['expired_outcomes']:,} · аномалии: проверено {s['anomaly_checked']:,}, "
              f"пропало {s['anomaly_gone']}" + (f" ({s['anomaly_skipped']})" if s["anomaly_skipped"] else "") +
              f" · удалено событий {s['purged_events']:,}, ждут ретенции {s['purge_pending']:,} · "
              f"{s['duration_s']} с" + (f" · {C.RED}ошибки: {s['errors']}{C.RESET}" if s["errors"] else ""))
        try:
            if os.path.isdir(os.path.dirname(self.state_file)):
                tmp = self.state_file + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(s, f, ensure_ascii=False)
                os.replace(tmp, self.state_file)
        except Exception:
            pass
