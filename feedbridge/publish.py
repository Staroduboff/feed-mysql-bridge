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
Публикация никогда не блокирует запись в MySQL: таймаут запроса короткий,
после ошибки — пауза `retry_s` секунд, в течение которой дельты отбрасываются
(счётчик `dropped`). Клиент восстанавливает пропуски через history/recovery
Centrifugo или перечитывает снапшот.

Класс Publisher(cfg)
--------------------
enabled          Включена ли публикация.
add(channel, d)  Положить команду publish в буфер (сбрасывает при batch_max).
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
        self.timeout = float(c.get("timeout", 2.0))
        self.retry_s = float(c.get("retry_s", 5.0))
        self.buf: list = []
        self.last_flush = time.monotonic()
        self.stats = collections.Counter()
        self._fail_until = 0.0
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

    def flush(self) -> None:
        """Отправить накопленные команды одним запросом /api/batch."""
        if not self.buf:
            return
        cmds, self.buf = self.buf, []
        self.last_flush = time.monotonic()
        if time.monotonic() < self._fail_until:
            self.stats["dropped"] += len(cmds)   # пауза после сбоя: не тормозим поток
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
        except Exception as exc:
            self.stats["errors"] += 1
            self.stats["dropped"] += len(cmds)
            self.last_error = str(exc)[:120]
            self._fail_until = time.monotonic() + self.retry_s
            if self.stats["errors"] <= 5 or self.stats["errors"] % 100 == 0:
                print(f"\n  {C.RED}centrifugo: батч не отправлен ({self.last_error}); "
                      f"пауза {self.retry_s:.0f} с{C.RESET}")

    # ── статистика ───────────────────────────────────────────────────────────

    def stat_str(self) -> str:
        if not self.enabled:
            return ""
        s = self.stats
        out = (f"  {C.GRAY}centrifugo: {C.WHITE}{s['published']:,}{C.GRAY} опубл. "
               f"в {C.WHITE}{s['batches']:,}{C.GRAY} батчах")
        if s["dropped"] or s["errors"] or s["cmd_errors"]:
            out += (f"  {C.RED}сбоев {s['errors']} отброшено {s['dropped']:,}"
                    f"{' cmd-ошибок ' + str(s['cmd_errors']) if s['cmd_errors'] else ''}{C.RESET}")
        return out
