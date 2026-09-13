"""
gate.py — гейт готовности приёма ставок по свежести фида.

Назначение
----------
Отвечает на один вопрос: можно ли прямо сейчас принимать ставки по линии,
которую отдаёт этот мост. Ответ — состояние OPEN / HOLD / SUSPEND, причины и
набор измеренных сигналов.

Зачем отдельный гейт, а не глубина очереди
------------------------------------------
Глубина очереди RabbitMQ меряет ТОЛЬКО последний участок тракта: успевает ли
мост вычитывать то, что ему уже положили. Тракт же состоит из пяти участков:

    BetGate (очередь gt.markets)
      → ядро betsportserver
      → RabbitMQ ядра (очередь bridge_to_second_server)
      → shovel
      → RabbitMQ партнёрской раздачи (очередь gt.external.*)
      → этот мост
      → MySQL

Любой затык выше по течению выглядит на последней очереди как «очередь пуста,
отставание ноль, всё отлично». Ровно так и было в инциденте 07.09.2026: с 23:11
до 23:19 МСК очередь была пуста, а данные не двигались — 294 тыс. сообщений
копились на брокере BetGate.

Поэтому основной сигнал гейта — не длина очереди, а СКВОЗНАЯ СВЕЖЕСТЬ:
насколько отстало от текущего времени поле `uts` (время обновления объекта в
фиде) у самых свежих применённых объектов. Этот показатель не зависит от того,
на каком участке затык, и ловит даже случай, когда сам BetGate перестал
генерировать обновления.

Не путать с проверкой свежести ОТДЕЛЬНОГО рынка: там `uts` бесполезен, у
открытых лайв-рынков медиана возраста около 6 минут (рынок просто не менялся).
Здесь берётся МАКСИМУМ по потоку — в живом потоке всегда есть объекты,
обновлённые секунду назад. На боевом потоке этот максимум отстаёт от текущего
времени на 0–1 секунду.

Почему максимум СКОЛЬЗЯЩИЙ, а не за всё время
----------------------------------------------
Максимум за всё время монотонно растёт и отвечает на вопрос «видели ли мы
когда-нибудь свежий объект», а нужен ответ «свежее ли то, что мы применяем
СЕЙЧАС». Разница проявилась 12.09.2026: мост отставал от очереди на 40–50 минут,
то есть применял сорокаминутной давности объекты, а lag показывал 0–30 с и порог
не переходил — приём закрыл запасной сигнал qlag. Для отстающего (но живого)
потока накопленный максимум слеп, потому что однажды увиденное свежее значение
из него уже не уходит. Поэтому максимум считается по окну `lag_window`: значения
старше окна из расчёта выпадают, и lag становится реальным возрастом потока.

Окно набирается посекундными корзинами: горячий путь пишет только в текущую
корзину (одно сравнение строк, без обращения к часам), тик перекладывает её в
кольцо. Гонка на перекладывании безопасна: потерянная выборка может сделать
максимум только СТАРЕЕ, то есть оценку — консервативнее.

Сигналы
-------
lag    now − max(uts) по событиям и маркетам, применённым за последние
       `lag_window` секунд. Основной сигнал, считается локально и доступен всегда.
sil    Секунд с момента применения последнего сообщения (тишина в потоке).
       Срабатывает раньше, чем успевает вырасти lag.
qlag   Оценка отставания по своей очереди: ready / скорость обработки.
       Единственный сигнал, который виден и на голой очереди.
hb     Возраст heartbeat BetGate из ключа Redis `server` (поле amqp.hb_time).
       Ключ реплицируется с ядра, то есть это взгляд ядра на канал с BetGate.
srv    Самоотчёт ядра из того же ключа: amqp.good, pub.good, redis.good.

Почему `outcomes` не участвует
------------------------------
В перепродаваемом фиде у исходов `uts` не заполняется — там лежит epoch-ноль
(1970-01-01). Метрика строится только на `events` и `markets`. Если строить её
на исходах, гейт будет вечно закрыт.

Состояния и выдержка
--------------------
SUSPEND  хотя бы один сигнал за порогом. Ставится немедленно.
HOLD     все сигналы в норме, но выдержка ещё не выдержана.
OPEN     все сигналы в норме непрерывно `reopen_hold` секунд.

Асимметрия намеренная: закрываемся быстро, открываемся медленно. Это прямой
урок инцидента 07.09.2026 — там защита на стороне портала возвращалась в
«здоров» по одному удачному замеру, и одна пачка сообщений на умирающем канале
открыла приём ставок на четыре минуты.

Публикация состояния
--------------------
Состояние пишется в JSON-файл (`gate.state_file`), откуда его забирает витрина
здоровья. Наружу партнёру гейт пока ничего не отдаёт: как доставлять сигнал в
партнёрскую платформу — отдельное решение.

Использование
-------------
    gate = Gate(cfg)                 # создаётся в Bridge
    gate.start_background(cfg)       # один раз: фоновый поток — секундный тик + опрос
    gate.observe_uts(obj.get("uts")) # в горячем пути, на каждый объект
    gate.mark_message()              # в горячем пути, на каждое сообщение

Тик НЕ вызывается из кода листенера: он живёт в фоновом потоке, иначе во время залпа
(ioloop pika занят подряд идущими колбэками) гейт перестаёт пересчитываться.

`observe_uts` сравнивает строки лексикографически (формат ISO с фиксированной
шириной сортируется как время), поэтому в горячем пути нет разбора дат.
"""

import collections
import json
import os
import threading
import time

# Пороги по умолчанию. Переопределяются секцией "gate" в config.json.
# Секунды, если не указано иное.
THR = dict(
    lag_warn=10,        # сквозная свежесть: WARN
    lag_crit=30,        # сквозная свежесть: закрыть приём
    lag_window=60,      # окно, по которому берётся максимум uts
    silence_crit=15,    # тишина в потоке: закрыть приём
    qlag_crit=30,       # отставание по своей очереди: закрыть приём
    hb_crit=30,         # возраст heartbeat BetGate: закрыть приём
    reopen_hold=30,     # выдержка: столько секунд непрерывной нормы до открытия
    poll_slow=10,       # как часто опрашивать медленные источники (очередь, Redis)
    future_skew=120,    # игнорировать uts, опережающий наши часы больше чем на столько
    write_every=5,      # писать файл состояния не реже, чем раз в столько секунд
)

STATE_OPEN, STATE_HOLD, STATE_SUSPEND = 0, 1, 2
STATE_NAME = {STATE_OPEN: "OPEN", STATE_HOLD: "HOLD", STATE_SUSPEND: "SUSPEND"}

# Расшифровка причин для человека — используется и витриной.
REASONS = {
    "lag": "данные отстают от фида",
    "silence": "тишина в потоке",
    "qlag": "мост не успевает за очередью",
    "hb": "heartbeat BetGate устарел",
    "amqp": "ядро: канал с BetGate нездоров",
    "pub": "ядро: публикация нездорова",
    "redis": "ядро: Redis нездоров",
    "inactive": "ядро не активно",
}

_DEFAULT_STATE_FILE = "/var/lib/feed-health/gate.json"


def _iso_to_epoch(s):
    """'2026-09-07T18:31:34.847' (наивный UTC) → epoch-секунды float. None при мусоре."""
    if not s or len(s) < 19:
        return None
    try:
        y = int(s[0:4]); mo = int(s[5:7]); d = int(s[8:10])
        h = int(s[11:13]); mi = int(s[14:16]); sec = int(s[17:19])
        frac = 0.0
        if len(s) > 20 and s[19] == ".":
            frac = float(s[19:23])
        # calendar.timegm без импорта calendar: считаем через days-from-civil
        # (быстрее и без зависимости от локальной зоны).
        yy = y - (mo <= 2)
        era = (yy if yy >= 0 else yy - 399) // 400
        yoe = yy - era * 400
        doy = (153 * (mo + (-3 if mo > 2 else 9)) + 2) // 5 + d - 1
        doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
        days = era * 146097 + doe - 719468
        return days * 86400 + h * 3600 + mi * 60 + sec + frac
    except Exception:
        return None


class Gate:
    """Состояние готовности приёма ставок. Дёшев в горячем пути."""

    def __init__(self, cfg):
        g = (cfg or {}).get("gate") or {}
        self.thr = dict(THR)
        for k in self.thr:
            if k in g:
                self.thr[k] = g[k]
        self.enabled = g.get("enabled", True)
        self.state_file = g.get("state_file", _DEFAULT_STATE_FILE)

        # горячий путь
        self._uts_cur = ""          # максимум uts в текущей корзине (ISO сортируется как время)
        # кольцо закрытых корзин [(время тика, максимум uts)]; глубина с запасом на
        # случай, если фоновый поток тикает чаще раза в секунду
        self._uts_ring = collections.deque(maxlen=max(8, int(self.thr["lag_window"]) * 4))
        self._uts_ever = ""         # максимум за всё время — запасной, когда окно пусто
        self._last_msg = None       # время применения последнего сообщения
        self._msgs = 0              # монотонный счётчик сообщений (горячий путь только +1)
        self._msgs_prev = 0         # его значение на прошлом тике — для оценки скорости

        # медленные источники: обновляются фоновым потоком, tick только читает
        self._ready = None          # ready в своей очереди
        self._ready_at = None       # когда это значение получено
        self._srv = None            # разобранный ключ Redis `server`
        self._srv_at = None         # когда этот ключ прочитан
        self._slow_at = None        # когда фоновый круг вообще отработал
        self._bg = None

        # состояние
        self.state = STATE_SUSPEND  # до первого удачного замера считаем закрытым
        self.reasons = ["startup"]
        self._ok_since = None
        self._rate = None
        self._tick_at = None
        self._wrote_at = 0.0
        self._changed_at = time.time()
        self.metrics = {}

    # ── горячий путь ────────────────────────────────────────────────────────

    def observe_uts(self, uts):
        """Учесть uts применённого объекта. Только строковое сравнение."""
        if uts and uts > self._uts_cur:
            self._uts_cur = uts

    def mark_message(self):
        """Отметить факт применения сообщения (для сигнала тишины и скорости)."""
        self._last_msg = time.time()
        self._msgs += 1

    # ── периодическая оценка ────────────────────────────────────────────────

    def tick(self, now=None):
        """Пересчитать состояние. Вызывается раз в секунду из фонового потока (см. _bg_loop)."""
        if not self.enabled:
            return self.state
        now = now or time.time()

        # Скорость обработки — по дельте монотонного счётчика между тиками. Счётчик
        # НЕ обнуляется: горячий путь только инкрементит, тик только читает. Обнуление
        # из другого потока теряло бы инкременты, попавшие между чтением и сбросом.
        seen = self._msgs
        if self._tick_at:
            dt = now - self._tick_at
            if dt > 0:
                r = max(0, seen - self._msgs_prev) / dt
                self._rate = r if self._rate is None else (self._rate * 0.7 + r * 0.3)
        self._tick_at = now
        self._msgs_prev = seen

        # Закрыть текущую корзину uts и начать новую. Обмен одним оператором, чтобы
        # окно гонки с горячим путём было минимальным; потеря выборки в этой гонке
        # делает максимум только старее — оценка остаётся консервативной.
        cur, self._uts_cur = self._uts_cur, ""
        self._uts_ring.append((now, cur))
        if cur > self._uts_ever:
            self._uts_ever = cur

        m = self._measure(now)
        self.metrics = m
        bad = self._bad(m)

        prev = self.state
        if bad:
            self.state = STATE_SUSPEND
            self._ok_since = None
        else:
            if self._ok_since is None:
                self._ok_since = now
            held = now - self._ok_since
            self.state = STATE_OPEN if held >= self.thr["reopen_hold"] else STATE_HOLD
        self.reasons = bad

        if self.state != prev:
            self._changed_at = now
            self._log_transition(prev, m)
        if self.state != prev or now - self._wrote_at >= self.thr["write_every"]:
            self._write(now)
        return self.state

    def _measure(self, now):
        """Собрать все сигналы в один словарь.

        Медленные сигналы (очередь, самоотчёт ядра) считаются протухшими, если
        фоновый поток давно не обновлял их: лучше «неизвестно», чем решение по
        значению десятиминутной давности.
        """
        limit = 3 * self.thr["poll_slow"]
        fresh = lambda at: at is not None and (now - at) <= limit   # noqa: E731
        ready = self._ready if fresh(self._ready_at) else None
        srv = self._srv if fresh(self._srv_at) else None
        stale = not fresh(self._slow_at)
        uts_win = self._uts_window(now)
        m = dict(lag=None, sil=None, qlag=None, hb=None, ready=ready,
                 rate=round(self._rate, 1) if self._rate is not None else None,
                 uts_max=uts_win or None, srv_amqp=None, srv_pub=None,
                 srv_redis=None, srv_active=None, sage=None, slow_stale=1 if stale else 0)

        ep = _iso_to_epoch(uts_win)
        if ep is not None and ep <= now + self.thr["future_skew"]:
            m["lag"] = max(0.0, round(now - ep, 1))

        if self._last_msg:
            m["sil"] = max(0.0, round(now - self._last_msg, 1))

        if ready is not None and self._rate:
            m["qlag"] = round(ready / self._rate, 1) if self._rate > 0 else None
        elif ready == 0:
            m["qlag"] = 0.0

        s = srv
        if s:
            amqp = s.get("amqp") or {}
            m["srv_amqp"] = 1 if amqp.get("good") else 0
            m["srv_pub"] = 1 if (s.get("pub") or {}).get("good") else 0
            m["srv_redis"] = 1 if (s.get("redis") or {}).get("good") else 0
            m["srv_active"] = 1 if s.get("active") else 0
            hb = _iso_to_epoch((amqp.get("hb_time") or "").rstrip("Z"))
            if hb is not None:
                m["hb"] = max(0.0, round(now - hb, 1))
            ts = _iso_to_epoch(s.get("ts") or "")
            if ts is not None:
                m["sage"] = max(0.0, round(now - ts, 1))
        return m

    def _uts_window(self, now):
        """Максимум uts за последние lag_window секунд ('' — за окно ничего не применяли).

        Текущая (ещё не закрытая) корзина участвует всегда: в ней самые свежие
        наблюдения.

        Если за окно не применили НИ ОДНОГО объекта с uts, берётся максимум за всё
        время. Пустое окно не должно означать «сигнала нет»: в потоке есть каналы
        без uts (у исходов перепродаваемого фида там epoch-ноль), и поток из одних
        исходов оставил бы окно пустым при живом счётчике сообщений — то есть
        застывшая линия перестала бы детектироваться вовсе. Запасное значение
        только растёт по возрасту, поэтому ошибается в сторону закрытия.
        """
        edge = now - self.thr["lag_window"]
        best = self._uts_cur
        for at, uts in reversed(self._uts_ring):
            if at < edge:
                break
            if uts > best:
                best = uts
        return best or self._uts_ever

    def _bad(self, m):
        """Список причин закрытия. Неизвестный сигнал причиной НЕ считается."""
        t, bad = self.thr, []
        if m["lag"] is not None and m["lag"] >= t["lag_crit"]:
            bad.append("lag")
        if m["sil"] is not None and m["sil"] >= t["silence_crit"]:
            bad.append("silence")
        if m["qlag"] is not None and m["qlag"] >= t["qlag_crit"]:
            bad.append("qlag")
        if m["hb"] is not None and m["hb"] >= t["hb_crit"]:
            bad.append("hb")
        if m["srv_active"] == 0:
            bad.append("inactive")
        if m["srv_amqp"] == 0:
            bad.append("amqp")
        if m["srv_pub"] == 0:
            bad.append("pub")
        if m["srv_redis"] == 0:
            bad.append("redis")
        return bad

    # ── медленные источники ─────────────────────────────────────────────────

    def start_background(self, cfg):
        """Запустить фоновый опрос очереди и ключа Redis `server`.

        ВАЖНО: оба источника — сетевые, и обращаться к ним из тика нельзя. Тик
        вызывается из ioloop pika, в том же потоке, что и доставка сообщений:
        секунда ожидания HTTP или Redis — это секунда, когда мост не разбирает
        поток. Поэтому опрос живёт в отдельном демон-потоке со своими короткими
        таймаутами и собственным клиентом Redis (клиент моста не потокобезопасен
        и настроен на длинные таймауты для снапшота).
        """
        if self._bg or not self.enabled:
            return
        self._bg = threading.Thread(target=self._bg_loop, args=(cfg,),
                                    name="gate-slow", daemon=True)
        self._bg.start()

    def _bg_loop(self, cfg):
        """Секундный тик гейта + опрос медленных источников раз в poll_slow секунд.

        Тик живёт ЗДЕСЬ, а не в таймере листенера, и это принципиально. Таймер
        (`conn.call_later`) исполняется в ioloop pika — том же потоке, что и доставка
        сообщений. Пока идёт залп (после рестарта ядра фида в очереди накапливаются
        десятки тысяч сообщений, и колбэки `on_message` идут подряд без пауз), ioloop
        до таймера не доходит, и гейт перестаёт пересчитываться ровно тогда, когда он
        нужнее всего. Замер 09.09.2026: во время двух залпов возраст файла состояния
        рос до 689 секунд, гейт 11.5 минут не обновлялся и показывал OPEN по данным
        трёхминутной давности при растущей очереди. Отдельный поток от загрузки
        ioloop не зависит.
        """
        rc = (cfg or {}).get("rabbitmq") or {}
        rd = (cfg or {}).get("redis") or {}
        client = None
        next_slow = 0.0
        while True:
            now = time.time()
            if now >= next_slow:
                next_slow = now + max(1, int(self.thr["poll_slow"]))
                try:
                    from . import amqp as amqp_mod
                    try:
                        self._ready = amqp_mod.queue_depth(rc)
                        self._ready_at = time.time()
                    except Exception:
                        self._ready = None
                    try:
                        if client is None:
                            import redis as redis_lib
                            client = redis_lib.Redis(
                                host=rd["host"], port=rd["port"], password=rd.get("password"),
                                db=rd.get("db", 0), decode_responses=True,
                                socket_connect_timeout=3, socket_timeout=3)
                        raw = client.get("server")
                        self._srv = json.loads(raw) if raw else None
                        self._srv_at = time.time()
                    except Exception:
                        client = None      # пересоздадим на следующем круге; значение
                                           # оставляем, но оно протухнет по _srv_at
                    self._slow_at = time.time()
                except Exception:
                    pass                    # фон не должен падать никогда
            try:
                self.tick()
            except Exception:
                pass                        # тик тоже не имеет права уронить поток
            time.sleep(1.0)

    # ── вывод ───────────────────────────────────────────────────────────────

    def _log_transition(self, prev, m):
        why = ", ".join(REASONS.get(r, r) for r in self.reasons) or "норма"
        print(f"\n  GATE {STATE_NAME.get(prev, prev)} → {STATE_NAME[self.state]}: {why}"
              f"  [lag={m['lag']} sil={m['sil']} qlag={m['qlag']} hb={m['hb']}]",
              flush=True)

    def snapshot(self, now=None):
        now = now or time.time()
        return {
            "t": round(now, 3),
            "state": self.state,
            "state_name": STATE_NAME[self.state],
            "reasons": self.reasons,
            "since": round(self._changed_at, 3),
            "ok_since": round(self._ok_since, 3) if self._ok_since else None,
            "metrics": self.metrics,
            "thr": self.thr,
        }

    def _write(self, now):
        """Атомарная запись файла состояния (tmp + replace)."""
        if not self.state_file:
            return
        try:
            d = os.path.dirname(self.state_file)
            if d and not os.path.isdir(d):
                os.makedirs(d, exist_ok=True)
            tmp = self.state_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.snapshot(now), f, ensure_ascii=False)
            os.replace(tmp, self.state_file)
            self._wrote_at = now
        except Exception:
            pass   # витрина переживёт отсутствие файла, поток важнее
