"""
publish.py — класс Publisher: рассылка дельт фида в Centrifugo батчами по HTTP API.

Назначение
----------
Бридж — единственная точка, через которую проходит каждое изменение фида, и он же
знает, к какому событию относится маркет или исход. Поэтому именно он раздаёт
дельты WebSocket-слою: после каждого upsert'а листенер кладёт сообщение в буфер,
а Publisher отправляет буфер в Centrifugo одним запросом `POST /api/batch`
(пачка команд `publish`) — по заполнению `batch_max` команд или по таймеру
`batch_ms`. Без Centrifugo (секции `centrifugo` в config.json нет или
`enabled: false`) класс ничего не делает.

Каналы и формат
---------------
    sport:{sportId}   — дельты СОБЫТИЙ вида спорта (список/страница спорта)
    event:{eventId}   — дельты события, его маркетов и исходов (страница матча)

Сообщение = запись `data` AMQP-потока без изменений (см. PARTNER_INTEGRATION_GUIDE
§3.3), завёрнутая в конверт:
    {"ch": "events|markets|outcomes", "k": "<redis-ключ>", "d": {<объект как в Redis>}}
Клиент, умеющий читать снапшот Redis / поток AMQP, читает и WS-дельты той же
моделью. Версии для применения: события `d.dv`, маркеты `d.ver`, исходы —
применять всегда (у них версии нет).

Отказоустойчивость
------------------
Публикация не должна ни блокировать разбор очереди, ни терять дельты из-за
случайной просрочки. Поэтому:

* не отправленная пачка ВОЗВРАЩАЕТСЯ в начало буфера и уходит со следующим
  сбросом (через `batch_ms`), а не выбрасывается. Повтор не делается на месте:
  Publisher работает в том же потоке, что и разбор AMQP, и второй синхронный
  запрос с таймаутом просто удвоил бы простой;
* буфер ограничен `max_buffer` командами. Переполнение означает, что Centrifugo
  не принимает данные дольше, чем мы можем копить: самые старые команды
  отбрасываются (счётчик `dropped`);
* пауза `retry_s`, в течение которой дельты отбрасываются, включается только
  после `fail_streak` неудач подряд — то есть когда раздача действительно лежит,
  а не притормозила на одном батче.

Каждое отбрасывание — это дельта, не дошедшая до браузеров: цена на экране может
остаться неверной. Поэтому о потерях сообщается наружу колбэком `on_drop(n)`;
мост подключает к нему гейт приёма ставок (см. core.py и gate.py), и на время
после потери приём ставок закрывается. Клиент, кроме того, восстанавливает
пропуски через history/recovery Centrifugo или перечитывает снапшот.

Класс Publisher(cfg)
--------------------
enabled          Включена ли публикация.
add(channel, d)  Положить команду publish в буфер (сбрасывает при batch_max).
on_drop(n)       Колбэк о потерянных дельтах; мост подключает к нему гейт.
flush_if_due()   Сбросить буфер, если прошло batch_ms с прошлого сброса.
flush()          Сбросить буфер немедленно.
stat_str()       Строка счётчиков для строки статистики листенера.
"""

import collections
import json
import time
import urllib.request

from .console import C


class Publisher:
    """Батч-публикация дельт фида в Centrifugo через HTTP API (/api/batch)."""

    def __init__(self, cfg: dict) -> None:
        c = cfg.get("centrifugo") or {}
        self.enabled = bool(c.get("enabled")) and bool(c.get("api_url"))
        self.url = (c.get("api_url") or "").rstrip("/") + "/batch"
        self.key = c.get("api_key") or ""
        self.batch_max = int(c.get("batch_max", 500))
        self.batch_ms = int(c.get("batch_ms", 200))
        # Таймаут запроса. Под fan-out'ом (тысячи подписчиков) ответ на пачку из
        # batch_max команд закономерно дольше: при 2 с замер 14.09.2026 дал восемь
        # просрочек и 6311 потерянных дельт на нагрузке в 8-16 тысяч клиентов.
        self.timeout = float(c.get("timeout", 5.0))
        self.retry_s = float(c.get("retry_s", 5.0))
        self.fail_streak = int(c.get("fail_streak", 3))
        self.max_buffer = int(c.get("max_buffer", self.batch_max * 20))
        self.buf: list = []
        self.last_flush = time.monotonic()
        self.stats = collections.Counter()
        self._fail_until = 0.0
        self._streak = 0
        self.last_drop_at = None      # monotonic последнего отбрасывания
        self.on_drop = None           # колбэк (n) — мост подключает к нему гейт
        self.last_error = ""

    # ── буфер ────────────────────────────────────────────────────────────────

    def add(self, channel: str, data: dict) -> None:
        """Положить publish-команду в буфер; при достижении batch_max — отправить."""
        if not self.enabled:
            return
        self.buf.append({"publish": {"channel": channel, "data": data}})
        if len(self.buf) >= self.batch_max:
            self.flush()

    def flush_if_due(self) -> None:
        if self.buf and (time.monotonic() - self.last_flush) * 1000 >= self.batch_ms:
            self.flush()

    def _drop(self, n: int, why: str) -> None:
        """Отметить потерю n дельт: счётчик, отметка времени и сигнал наружу."""
        if n <= 0:
            return
        self.stats["dropped"] += n
        self.last_drop_at = time.monotonic()
        self.last_error = why or self.last_error
        if self.on_drop:
            try:
                self.on_drop(n)
            except Exception:
                pass      # сигнализация не должна ронять публикацию

    def flush(self) -> None:
        """Отправить накопленные команды одним запросом /api/batch."""
        if not self.buf:
            return
        cmds, self.buf = self.buf, []
        self.last_flush = time.monotonic()
        if time.monotonic() < self._fail_until:
            self._drop(len(cmds), "пауза после серии сбоев")
            return
        body = json.dumps({"commands": cmds}, ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8")
        req = urllib.request.Request(self.url, data=body, method="POST", headers={
            "Content-Type": "application/json",
            "X-API-Key": self.key,
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                reply = json.loads(resp.read() or b"{}")
            errs = 0
            for r in reply.get("replies") or []:
                if isinstance(r, dict) and r.get("error"):
                    errs += 1
                    if not self.last_error:
                        self.last_error = json.dumps(r["error"], ensure_ascii=False)[:120]
            if reply.get("error"):
                raise RuntimeError(json.dumps(reply["error"], ensure_ascii=False)[:120])
            self.stats["batches"] += 1
            self.stats["published"] += len(cmds) - errs
            self.stats["cmd_errors"] += errs
            self._streak = 0
        except Exception as exc:
            self.stats["errors"] += 1
            self._streak += 1
            self.last_error = str(exc)[:120]
            if self._streak >= self.fail_streak:
                # раздача не принимает данные подряд — копить бессмысленно
                self._fail_until = time.monotonic() + self.retry_s
                self._drop(len(cmds) + len(self.buf), self.last_error)
                self.buf = []
                print(f"\n  {C.RED}centrifugo: {self._streak} сбоя подряд "
                      f"({self.last_error}); пауза {self.retry_s:.0f} с, дельты отбрасываются{C.RESET}")
            else:
                # одна просрочка — не потеря: пачка уходит следующим сбросом
                self.buf = cmds + self.buf
                self.stats["requeued"] += len(cmds)
                over = len(self.buf) - self.max_buffer
                if over > 0:
                    self.buf = self.buf[over:]
                    self._drop(over, "переполнение буфера публикации")
                if self.stats["errors"] <= 5 or self.stats["errors"] % 100 == 0:
                    print(f"\n  {C.YELLOW}centrifugo: батч не отправлен ({self.last_error}); "
                          f"{len(cmds)} команд вернулись в буфер{C.RESET}")

    # ── статистика ───────────────────────────────────────────────────────────

    def stat_str(self) -> str:
        if not self.enabled:
            return ""
        s = self.stats
        out = (f"  {C.GRAY}centrifugo: {C.WHITE}{s['published']:,}{C.GRAY} опубл. "
               f"в {C.WHITE}{s['batches']:,}{C.GRAY} батчах")
        if s["requeued"]:
            out += f"  {C.YELLOW}повторно {s['requeued']:,}{C.RESET}"
        if s["dropped"] or s["errors"] or s["cmd_errors"]:
            out += (f"  {C.RED}сбоев {s['errors']} отброшено {s['dropped']:,}"
                    f"{' cmd-ошибок ' + str(s['cmd_errors']) if s['cmd_errors'] else ''}{C.RESET}")
        return out
