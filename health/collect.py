#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
collect.py — сборщик health-check витрины feed-mysql-bridge.

Назначение
----------
Раз в минуту (cron, см. feed-health.cron) снимает состояние всего тракта
«фид → RabbitMQ → бридж → MySQL» и рендерит статическую страницу
(template.html + встроенный JSON) в каталог, который отдаёт nginx.
Ни одной записи в БД фида не делает: только чтение.

Что измеряется (один сэмпл = одна строка samples.jsonl)
-----------------------------------------------------
service   systemd-юнит бриджа: active/sub-state, PID, старт, RSS, CPU %
          (дельта CPUUsageNSec между сэмплами).
mysql     доступность и латентность, множества live/prematch событий в БД,
          MAX(id) events/markets/outcomes (скорость вставки), MAX(uts) событий,
          «возраст последней записи» (information_schema.update_time),
          размер базы.
redis     доступность и латентность, ключ `server` (self-report фида:
          active/good, events.live/prematch, pub.mps, ts → возраст heartbeat),
          скан e:* → множества live/prematch для сверки с БД (sync_metrics:
          совпало / не дошло до БД / «зомби» в БД без пары в Redis).
rabbitmq  Management API: очередь бриджа (ready/unacked/consumers, publish/
          deliver/ack rate) + партнёрские очереди того же префикса.
          Оценка отставания = ready / ack_rate.
journal   строки журнала юнита за интервал с прошлого сэмпла: ошибки
          (сбой БД, apply_error, битый JSON, обрыв AMQP), pc=0, рестарты.
gate      состояние гейта приёма ставок из gate.json — его пишет сам мост раз в
          секунду (feedbridge/gate.py). Сборщик только читает: OPEN/HOLD/SUSPEND,
          причины, сквозная свежесть (now − max uts), тишина в потоке, возраст
          heartbeat BetGate. Секундные сигналы гейта точнее всего, что можно снять
          раз в минуту, поэтому считает их мост, а не сборщик.
host      сервер целиком, как на витрине ch-mem: CPU user/system/iowait по
          дельтам /proc/stat, занятая память (MemTotal − MemAvailable),
          loadavg, диск с данными MariaDB, размер каталога MariaDB
          (du, feed_bridge + остальное), RSS MariaDB.

Вердикт (OK / WARN / CRIT) считается по порогам THR и пишется в сэмпл вместе
со списком причин; смена вердикта, рестарты и ошибки попадают в events.jsonl.

Файлы
-----
DATA_DIR/samples.jsonl   история сэмплов (обрезается до KEEP_DAYS суток)
DATA_DIR/events.jsonl    журнал событий (рестарты, ошибки, смена вердикта)
DATA_DIR/state.json      счётчики для дельт (CPU, время прошлого сэмпла)
WWW_DIR/index.html       готовая страница (полный HTML-документ)
WWW_DIR/data.json        текущий статус без исторических рядов (для машин)

Пути переопределяются переменными окружения FEED_HEALTH_DATA, FEED_HEALTH_WWW,
FEED_HEALTH_UNIT. Доступы к Redis/RabbitMQ/MySQL — из config.json бриджа
(каталог на уровень выше).

Запуск: python3 health/collect.py   (обычно из cron под root — нужен
доступ к journalctl и systemctl show).
"""

import base64
import calendar
import datetime
import fcntl
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BRIDGE_DIR = os.path.dirname(HERE)
sys.path.insert(0, BRIDGE_DIR)

DATA_DIR = os.environ.get("FEED_HEALTH_DATA", "/var/lib/feed-health")
WWW_DIR = os.environ.get("FEED_HEALTH_WWW", "/var/www/feed-health")
UNIT = os.environ.get("FEED_HEALTH_UNIT", "feed-bridge")
MYSQL_DATA_DIR = "/var/lib/mysql"

TPL = os.path.join(HERE, "template.html")
SAMPLES = os.path.join(DATA_DIR, "samples.jsonl")
EVENTS = os.path.join(DATA_DIR, "events.jsonl")
STATE = os.path.join(DATA_DIR, "state.json")
LOCK = os.path.join(DATA_DIR, ".lock")
LOG = os.path.join(DATA_DIR, "collect.log")
# Состояние гейта приёма ставок: пишет сам мост раз в секунду (feedbridge/gate.py),
# сборщик только читает. Путь должен совпадать с gate.state_file в config.json.
GATE = os.path.join(DATA_DIR, "gate.json")

# Логотипы команд для витрины sport-live: репо betting/team_logo (клон на *10) и его резолвер
# team_logo.py; ссылки вида https://gem.x1b.site/team-logo/<d>/<id>.png. Необязательно:
# без клона каталог собирается как раньше (монограммы на странице).
TEAM_LOGO_ROOT = os.environ.get("TEAM_LOGO_ROOT", "/opt/team_logo")
TEAM_LOGO_URL = os.environ.get("TEAM_LOGO_URL", "https://gem.x1b.site/team-logo")
LOGO_CACHE = os.path.join(DATA_DIR, "logo_cache.json")   # имя команды → ссылка ("" = не найдено); удалить для пересчёта
MMAIN_CACHE = os.path.join(DATA_DIR, "main_markets.json")  # последний удачный набор главных маркетов из админки Спорта

KEEP_DAYS = 16        # хранить сэмплы
RAW_DAYS = 3          # окно 1-минутных точек на странице
AGG_DAYS = 15         # окно 10-минутных агрегатов на странице
AGG_STEP = 600        # секунд в агрегате
EVENTS_KEEP_DAYS = 30
EVENTS_SHOW = 15

# Пороги вердикта. Секунды, ГБ, МБ.
THR = dict(
    lag_warn=60, lag_crit=300,        # оценка отставания бриджа от очереди
    wage_warn=120, wage_crit=600,     # секунд с последней записи в events/markets/outcomes
    sage_warn=30,                     # возраст heartbeat фида (ключ server.ts)
    live_miss_warn=5,                 # live-событий Redis, которых нет в БД в том же состоянии
    pre_miss_warn=30,                 # то же для prematch
    disk_warn_gb=15, disk_crit_gb=5,  # свободно на диске с данными MySQL
    mem_warn_mb=600,                  # RSS процесса бриджа
)

# Порядок колонок исторических рядов на странице (компактные массивы).
COLS = ["t", "svc", "mem", "cpu", "pub", "ack", "dlv", "ready", "lag", "wage", "sage",
        "live_db", "live_r", "live_miss", "live_zomb", "live_srv", "live_late",
        "pre_db", "pre_r", "pre_miss", "pre_zomb", "pre_late", "ins_ev", "ins_mk", "ins_oc",
        "err", "rst", "pc0", "db_gb", "dsk_u", "dsk_t", "load", "my_ms", "rd_ms",
        "cons", "cpu_u", "cpu_s", "cpu_w", "cpu_pk", "mem_used", "mem_tot", "mdb_gb", "mdb_rss",
        "vd", "rs",
        # Гейт приёма ставок (пишет мост, см. feedbridge/gate.py): состояние,
        # сквозная свежесть, тишина в потоке, возраст heartbeat BetGate, возраст файла.
        "gs", "glag", "gsil", "ghb", "gage"]
# Правило агрегации колонки в 10-минутные окна.
AGG = dict(svc="min", mem="max", cpu="avg", pub="avg", ack="avg", dlv="avg", ready="max",
           lag="max", wage="max", sage="max", live_db="last", live_r="last", live_miss="max",
           live_zomb="last", live_srv="last", live_late="sum", pre_db="last", pre_r="last",
           pre_miss="max", pre_zomb="last", pre_late="sum", ins_ev="avg", ins_mk="avg",
           ins_oc="avg", err="sum", rst="sum",
           pc0="sum", db_gb="last", dsk_u="last", dsk_t="last", load="max", my_ms="max",
           rd_ms="max", cons="min", cpu_u="avg", cpu_s="avg", cpu_w="avg", cpu_pk="max",
           mem_used="max", mem_tot="last", mdb_gb="last", mdb_rss="max", vd="max", rs="worst",
           gs="max", glag="max", gsil="max", ghb="max", gage="max")

ERR_PAT = ("сбой БД", "apply_error", "битый JSON", "не удалось переподключиться",
           "Соединение прервано", "flush: чанк")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


# ── утилиты ─────────────────────────────────────────────────────────────────

def run(cmd, timeout):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.stdout


def parse_iso_utc(s):
    """'2026-09-07T05:54:01.076[Z]' → epoch (значение считается UTC)."""
    if not s:
        return None
    s = str(s).rstrip("Z").split(".")[0].replace(" ", "T")
    try:
        return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return None


def local_dt_to_epoch(d):
    """datetime без tz в ЛОКАЛЬНОЙ зоне сервера → epoch (update_time MySQL, systemd)."""
    return int(time.mktime(d.timetuple()))


def rnd(v, n=1):
    try:
        return round(float(v), n) if v is not None else None
    except Exception:
        return None


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def atomic_write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def rotate_log():
    try:
        if os.path.exists(LOG) and os.path.getsize(LOG) > 5 * 1024 * 1024:
            with open(LOG, encoding="utf-8", errors="ignore") as f:
                tail = f.readlines()[-2000:]
            atomic_write(LOG, "".join(tail))
    except Exception:
        pass


# ── сбор ────────────────────────────────────────────────────────────────────

def collect_service(prev, now):
    d = dict(svc=0, sub=None, pid=None, start=None, up=None, mem=None, cpu=None, nrst=None)
    try:
        out = run(["systemctl", "show", UNIT, "-p",
                   "ActiveState,SubState,MainPID,ExecMainStartTimestamp,NRestarts,MemoryCurrent,CPUUsageNSec"], 10)
        kv = {}
        for ln in out.splitlines():
            if "=" in ln:
                k, v = ln.split("=", 1)
                kv[k] = v.strip()
        d["svc"] = 1 if kv.get("ActiveState") == "active" else 0
        d["sub"] = kv.get("SubState")
        pid = int(kv.get("MainPID") or 0)
        d["pid"] = pid or None
        m = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", kv.get("ExecMainStartTimestamp", ""))
        if m:
            st = local_dt_to_epoch(datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
            d["start"] = st
            d["up"] = max(0, int(now - st))
        d["nrst"] = int(kv.get("NRestarts") or 0)
        mem = kv.get("MemoryCurrent", "")
        d["mem"] = rnd(int(mem) / 1048576) if mem.isdigit() else None
        cpu = kv.get("CPUUsageNSec", "")
        cpu_ns = int(cpu) if cpu.isdigit() else None
        d["_cpu_ns"] = cpu_ns
        if (cpu_ns is not None and prev.get("cpu_ns") is not None and prev.get("pid") == pid
                and prev.get("t") and now - prev["t"] > 5):
            dc = cpu_ns - prev["cpu_ns"]
            if dc >= 0:
                d["cpu"] = rnd(dc / ((now - prev["t"]) * 1e9) * 100)
    except Exception as e:
        d["svc_err"] = str(e)[:120]
    return d


def collect_mysql(cfg, now):
    d = dict(my=0, my_ms=None, live_db=None, pre_db=None, ev_id=None, mk_id=None, oc_id=None,
             ev_uts=None, wage=None, db_gb=None, my_up=None)
    try:
        import pymysql
        import pymysql.cursors
        mc = cfg["mysql"]
        t0 = time.time()
        conn = pymysql.connect(host=mc["host"], port=mc.get("port", 3306), user=mc["user"],
                               password=mc["password"], database=mc["database"],
                               charset=mc.get("charset", "utf8mb4"),
                               cursorclass=pymysql.cursors.DictCursor, autocommit=True,
                               connect_timeout=8, read_timeout=25, write_timeout=25)
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchall()
        d["my"] = 1
        d["my_ms"] = rnd((time.time() - t0) * 1000)
        # Множества feed_id для сверки с Redis (см. sync_metrics): live = stage 2 и
        # статус open/suspended; prematch = stage 1, open. Десятки и ~2 тыс. строк.
        cur.execute("SELECT feed_id FROM events WHERE stage=2 AND status IN (1,2) AND removed=0")
        d["_live_db_set"] = {x["feed_id"] for x in cur.fetchall()}
        cur.execute("SELECT feed_id FROM events WHERE stage=1 AND status=1 AND removed=0")
        d["_pre_db_set"] = {x["feed_id"] for x in cur.fetchall()}
        cur.execute("SELECT (SELECT MAX(id) FROM events) e, (SELECT MAX(id) FROM markets) m, "
                    "(SELECT MAX(id) FROM outcomes) o")
        r = cur.fetchone()
        d["ev_id"], d["mk_id"], d["oc_id"] = r["e"], r["m"], r["o"]
        cur.execute("SELECT MAX(uts) u FROM events")
        u = cur.fetchone()["u"]
        if u:
            d["ev_uts"] = calendar.timegm(u.timetuple())   # uts хранится в UTC (transform.dt)
        cur.execute("SELECT table_name t, update_time ut, data_length+index_length b "
                    "FROM information_schema.tables WHERE table_schema=DATABASE()")
        tot, last = 0, None
        for r in cur.fetchall():
            tot += int(r["b"] or 0)
            ut = r["ut"]
            if ut and r["t"] in ("events", "markets", "outcomes"):
                ep = local_dt_to_epoch(ut)            # update_time — в зоне сессии (SYSTEM)
                last = ep if last is None else max(last, ep)
        d["wage"] = max(0, int(now - last)) if last else None
        d["db_gb"] = rnd(tot / 1073741824, 2)
        cur.execute("SHOW GLOBAL STATUS LIKE 'Uptime'")
        r = cur.fetchone()
        d["my_up"] = int(r["Value"]) if r else None
        conn.close()
    except Exception as e:
        d["my_err"] = str(e)[:120]
    return d


def collect_redis(cfg, now):
    d = dict(rd=0, rd_ms=None, srv_ok=None, srv_active=None, live_r=None, pre_r=None,
             pub_mps=None, sage=None, dbsize=None)
    try:
        import redis as redis_lib
        rc = cfg["redis"]
        t0 = time.time()
        r = redis_lib.Redis(host=rc["host"], port=rc["port"], password=rc["password"],
                            db=rc.get("db", 0), decode_responses=True,
                            socket_connect_timeout=5, socket_timeout=8)
        r.ping()
        d["rd"] = 1
        d["rd_ms"] = rnd((time.time() - t0) * 1000)
        d["dbsize"] = r.dbsize()
        s = r.get("server")
        if s:
            o = json.loads(s)
            d["srv_active"] = 1 if o.get("active") else 0
            d["srv_ok"] = 1 if (o.get("active") and (o.get("amqp") or {}).get("good")
                                and (o.get("pub") or {}).get("good")) else 0
            ev = o.get("events") or {}
            d["live_srv"], d["pre_srv"] = ev.get("live"), ev.get("prematch")   # self-report фида
            d["pub_mps"] = rnd((o.get("pub") or {}).get("mps"))
            ts = parse_iso_utc(o.get("ts"))
            if ts:
                d["sage"] = max(0, int(now - ts))
        # Скан событий e:* (≈10 тыс. ключей, ~1.5 с): множества live/prematch по тем же
        # критериям, что и в MySQL, — для сверки «что в Redis, то и в базе».
        live, pre = set(), set()
        cursor = 0
        while True:
            cursor, batch = r.scan(cursor, match="e:*", count=20000)
            if batch:
                for k, v in zip(batch, r.mget(batch)):
                    if not v:
                        continue
                    try:
                        o = json.loads(v)
                        fid = int(k.rsplit(":", 1)[1])
                    except Exception:
                        continue
                    if o.get("removed"):
                        continue
                    st, ss = o.get("stage"), o.get("status")
                    if st == 2 and ss in (1, 2):
                        live.add(fid)
                    elif st == 1 and ss == 1:
                        pre.add(fid)
            if cursor == 0:
                break
        d["live_r"], d["pre_r"] = len(live), len(pre)
        d["_live_r_set"], d["_pre_r_set"] = live, pre
    except Exception as e:
        d["rd_err"] = str(e)[:120]
    return d


def sync_metrics(s):
    """Сверка множеств Redis ↔ MySQL: *_db = совпало, *_miss = есть в Redis, нет в БД
    (бридж не донёс), *_zomb = есть в БД, нет в Redis (незакрытые фидом «зомби»)."""
    for name in ("live", "pre"):
        rs, ds = s.pop(f"_{name}_r_set", None), s.pop(f"_{name}_db_set", None)
        if rs is None or ds is None:
            s[f"{name}_db"] = len(ds) if ds is not None else None
            s[f"{name}_miss"] = s[f"{name}_zomb"] = None
            continue
        s[f"{name}_db"] = len(rs & ds)
        s[f"{name}_miss"] = len(rs - ds)
        s[f"{name}_zomb"] = len(ds - rs)
        s[f"_{name}_miss_set"] = rs - ds


def recheck_missing(cfg, s, wait=3):
    """Повторная проверка «не дошедших» через wait секунд.

    Снимок БД и скан Redis разнесены на ~1.5 с, а фид переключает события пачками
    на круглых минутах (Created/Started в hh:mm:00.5); бридж применяет их за 1–2 с.
    Сэмпл крона стартует в hh:mm:01 и систематически попадает в это окно. Поэтому
    события, которых не оказалось в БД, перепроверяются: если они уже там в нужном
    состоянии — это латентность (счётчик *_late), а не потеря; в *_miss остаётся
    только то, что не догнало и через wait секунд."""
    sets = {n: s.pop(f"_{n}_miss_set", None) for n in ("live", "pre")}
    s["live_late"], s["pre_late"] = 0, 0
    if not any(sets.values()):
        return
    # Если в очереди есть сообщения (бридж разгребает всплеск после паузы фида), окно
    # растягивается на оценку отставания: «не дошло» должно означать потерю, а не то,
    # что очередь ещё не пуста — отставание видно на своём графике.
    wait = min(wait + max(int(s.get("lag") or 0), 0), 15)
    s["rc_wait"] = wait
    time.sleep(wait)
    cond = {"live": "stage=2 AND status IN (1,2)", "pre": "stage=1 AND status=1"}
    try:
        import pymysql
        import pymysql.cursors
        mc = cfg["mysql"]
        conn = pymysql.connect(host=mc["host"], port=mc.get("port", 3306), user=mc["user"],
                               password=mc["password"], database=mc["database"],
                               charset=mc.get("charset", "utf8mb4"),
                               cursorclass=pymysql.cursors.DictCursor, autocommit=True,
                               connect_timeout=8, read_timeout=15)
        cur = conn.cursor()
        for n, fids in sets.items():
            if not fids:
                continue
            cur.execute("SELECT feed_id FROM events WHERE removed=0 AND %s AND feed_id IN (%s)"
                        % (cond[n], ",".join(str(int(f)) for f in fids)))
            late = len({x["feed_id"] for x in cur.fetchall()} & fids)
            s[f"{n}_late"] = late
            s[f"{n}_miss"] -= late
            s[f"{n}_db"] += late
        conn.close()
    except Exception as e:
        s["rc_err"] = str(e)[:120]


def collect_rmq(cfg, now):
    d = dict(mq=0, cons=None, ready=None, unack=None, pub=None, ack=None, dlv=None, lag=None,
             partners=[])
    try:
        rc = cfg["rabbitmq"]
        mgmt = rc.get("management_url", "").rstrip("/")
        vh = urllib.parse.quote(rc.get("vhost", "/"), safe="")
        tok = base64.b64encode(f"{rc['username']}:{rc['password']}".encode()).decode()
        req = urllib.request.Request(f"{mgmt}/api/queues/{vh}",
                                     headers={"Authorization": "Basic " + tok})
        qs = json.loads(urllib.request.urlopen(req, timeout=10).read())
        d["mq"] = 1
        prefix = rc["queue"].rsplit(".", 1)[0] + "."
        for q in qs:
            ms = q.get("message_stats") or {}
            rate = lambda k: rnd((ms.get(k) or {}).get("rate"))
            item = dict(name=q["name"], msgs=q.get("messages"), ready=q.get("messages_ready"),
                        unack=q.get("messages_unacknowledged"), cons=q.get("consumers"),
                        pub=rate("publish_details"), ack=rate("ack_details"),
                        dlv=rate("deliver_get_details"),
                        mb=rnd((q.get("message_bytes") or 0) / 1048576))
            if q["name"] == rc["queue"]:
                d.update(cons=item["cons"], ready=item["ready"], unack=item["unack"],
                         pub=item["pub"], ack=item["ack"], dlv=item["dlv"])
            elif q["name"].startswith(prefix):
                d["partners"].append(item)
        d["partners"].sort(key=lambda x: x["name"])
        if d["ready"] is not None:
            if d["ready"] == 0:
                d["lag"] = 0
            elif d["ack"] and d["ack"] > 0:
                d["lag"] = int(round(d["ready"] / d["ack"]))
    except Exception as e:
        d["mq_err"] = str(e)[:120]
    return d


def collect_gate(now):
    """Прочитать состояние гейта приёма ставок, записанное мостом (feedbridge/gate.py).

    Сборщик ничего не считает сам: гейт живёт внутри моста и видит каждое сообщение,
    поэтому его секундные сигналы точнее, чем всё, что можно снять раз в минуту.
    Здесь только чтение файла и раскладка в поля сэмпла для графиков.

    gage — возраст самого файла: если мост встал, гейт перестаёт обновляться, и это
    само по себе повод не доверять его последнему состоянию.
    """
    d = dict(gs=None, glag=None, gsil=None, ghb=None, gage=None, _gate=None)
    g = load_json(GATE, None)
    if not g:
        return d
    d["_gate"] = g
    d["gs"] = g.get("state")
    d["gage"] = max(0, int(now - (g.get("t") or now)))
    m = g.get("metrics") or {}
    for src, dst in (("lag", "glag"), ("sil", "gsil"), ("hb", "ghb")):
        v = m.get(src)
        if v is not None:
            d[dst] = rnd(v)
    return d


def collect_journal(since, now):
    d = dict(err=0, pc0=0, rst=0, lines=[])
    try:
        out = run(["journalctl", "-u", UNIT, f"--since=@{int(since)}", f"--until=@{int(now)}",
                   "-o", "cat", "-a", "--no-pager", "-q"], 25)
        for ln in out.splitlines():
            s = ANSI.sub("", ln).strip()
            if not s or s.startswith("AMQP  сообщ") or s.startswith("резолв:"):
                continue
            if s.startswith("Started ") and UNIT in s:
                d["rst"] += 1
                d["lines"].append("рестарт сервиса")
            elif "pc=0" in s:
                d["pc0"] += 1
                d["lines"].append(s[:160])
            elif any(p in s for p in ERR_PAT):
                d["err"] += 1
                if len(d["lines"]) < 5:
                    d["lines"].append(s[:160])
    except Exception as e:
        d["jr_err"] = str(e)[:120]
    return d


def collect_host(prev):
    """Ресурсы сервера целиком: CPU (дельты /proc/stat между сэмплами), память
    (/proc/meminfo), диск с данными MariaDB, размер каталога MariaDB, RSS MariaDB."""
    d = dict(load=None, ncpu=os.cpu_count(), dsk_u=None, dsk_t=None,
             cpu_u=None, cpu_s=None, cpu_w=None, mem_used=None, mem_tot=None,
             mdb_gb=None, mdb_rss=None)
    try:
        d["load"] = rnd(os.getloadavg()[0], 2)
        path = MYSQL_DATA_DIR if os.path.isdir(MYSQL_DATA_DIR) else "/"
        du = shutil.disk_usage(path)
        d["dsk_u"] = rnd(du.used / 1073741824)
        d["dsk_t"] = rnd(du.total / 1073741824)
    except Exception:
        pass
    try:
        # cpu user nice system idle iowait irq softirq steal …
        with open("/proc/stat") as f:
            parts = f.readline().split()
        cur = [int(x) for x in parts[1:9]]
        d["_cpu_stat"] = cur
        pc = prev.get("cpu_stat")
        # Окно короче 30 с (ручной прогон рядом с кроном) не считаем: в нём сборщик
        # измерил бы сам себя — запуск python, скан Redis, чтение журнала.
        if pc and len(pc) == 8 and prev.get("t") and time.time() - prev["t"] >= 30:
            dl = [a - b for a, b in zip(cur, pc)]
            tot = sum(dl)
            if tot > 0 and min(dl) >= 0:
                d["cpu_u"] = rnd((dl[0] + dl[1]) / tot * 100)
                d["cpu_s"] = rnd((dl[2] + dl[5] + dl[6] + dl[7]) / tot * 100)
                d["cpu_w"] = rnd(dl[4] / tot * 100)
    except Exception:
        pass
    try:
        mi = {}
        with open("/proc/meminfo") as f:
            for ln in f:
                k, v = ln.split(":", 1)
                mi[k] = int(v.strip().split()[0])   # кБ
        if "MemTotal" in mi and "MemAvailable" in mi:
            d["mem_tot"] = rnd(mi["MemTotal"] / 1048576, 2)
            d["mem_used"] = rnd((mi["MemTotal"] - mi["MemAvailable"]) / 1048576, 2)
    except Exception:
        pass
    try:
        if os.path.isdir(MYSQL_DATA_DIR):
            out = run(["du", "-sb", MYSQL_DATA_DIR], 20)
            d["mdb_gb"] = rnd(int(out.split()[0]) / 1073741824, 2)
        out = run(["systemctl", "show", "mariadb", "-p", "MemoryCurrent"], 10)
        v = out.strip().split("=", 1)[-1]
        if v.isdigit():
            d["mdb_rss"] = rnd(int(v) / 1048576)
    except Exception:
        pass
    return d


# ── вердикт ─────────────────────────────────────────────────────────────────

def verdict(s):
    crit, warn = [], []
    if not s.get("svc"):
        crit.append("svc")
    if not s.get("my"):
        crit.append("mysql")
    if not s.get("mq"):
        warn.append("mgmt")
    else:
        if s.get("cons") is not None and s["cons"] == 0:
            crit.append("noconsumer")
        elif (s.get("ready") or 0) > 1000 and (s.get("ack") or 0) < 1 and s.get("svc"):
            crit.append("stuck")
    lag = s.get("lag")
    if lag is not None:
        if lag >= THR["lag_crit"]:
            crit.append("lag")
        elif lag >= THR["lag_warn"]:
            warn.append("lag")
    wage = s.get("wage")
    if wage is not None:
        if wage >= THR["wage_crit"]:
            crit.append("wage")
        elif wage >= THR["wage_warn"]:
            warn.append("wage")
    if not s.get("rd"):
        warn.append("redis")
    elif s.get("srv_ok") == 0:
        warn.append("feed")
    if s.get("sage") is not None and s["sage"] > THR["sage_warn"]:
        warn.append("feedhb")
    # Сверка с Redis: WARN только при ДВУХ сэмплах подряд выше порога. Скан Redis и запрос
    # к БД разнесены на ~1.5 с, и в минуту массового старта матчей несколько событий
    # успевают стать live в Redis, но ещё не в базе — это сдвиг замера, а не потеря.
    if s.get("live_miss") is not None and s["live_miss"] > THR["live_miss_warn"] \
            and (s.get("_prev_live_miss") or 0) > THR["live_miss_warn"]:
        warn.append("live")
    if s.get("pre_miss") is not None and s["pre_miss"] > THR["pre_miss_warn"] \
            and (s.get("_prev_pre_miss") or 0) > THR["pre_miss_warn"]:
        warn.append("prematch")
    if s.get("err"):
        warn.append("errors")
    if s.get("pc0"):
        warn.append("pc0")
    if s.get("rst"):
        warn.append("restart")
    if s.get("dsk_u") is not None and s.get("dsk_t"):
        free = s["dsk_t"] - s["dsk_u"]
        if free < THR["disk_crit_gb"]:
            crit.append("disk")
        elif free < THR["disk_warn_gb"]:
            warn.append("disk")
    if s.get("mem") and s["mem"] > THR["mem_warn_mb"]:
        warn.append("mem")
    vd = 2 if crit else (1 if warn else 0)
    return vd, crit + warn


# ── история ─────────────────────────────────────────────────────────────────

def read_samples(since):
    out = []
    try:
        with open(SAMPLES, encoding="utf-8") as f:
            for ln in f:
                try:
                    s = json.loads(ln)
                except Exception:
                    continue
                if s.get("t", 0) >= since:
                    out.append(s)
    except FileNotFoundError:
        pass
    out.sort(key=lambda s: s["t"])
    return out


def prune_samples(now):
    """Раз в час переписать samples.jsonl, оставив KEEP_DAYS суток."""
    try:
        if os.path.getsize(SAMPLES) < 2_000_000 and int(now) % 3600 > 90:
            return
        keep = read_samples(now - KEEP_DAYS * 86400)
        atomic_write(SAMPLES, "".join(json.dumps(s, ensure_ascii=False, separators=(",", ":")) + "\n"
                                      for s in keep))
    except Exception:
        pass


def append_jsonl(path, obj):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")


def rows_from(samples):
    """Сэмплы → компактные строки COLS; ins_* = строк/мин по дельтам MAX(id)."""
    rows, prev = [], None
    for s in samples:
        ins = dict(ins_ev=None, ins_mk=None, ins_oc=None)
        if prev:
            dm = (s["t"] - prev["t"]) / 60.0
            if 0.5 <= dm <= 30:
                for k, c in (("ins_ev", "ev_id"), ("ins_mk", "mk_id"), ("ins_oc", "oc_id")):
                    a, b = prev.get(c), s.get(c)
                    if a is not None and b is not None and b >= a:
                        ins[k] = rnd((b - a) / dm)
        # пик CPU в 1-мин точке = её же среднее; в 10-мин окне агрегируется как max
        cpu_pk = None
        if all(s.get(k) is not None for k in ("cpu_u", "cpu_s", "cpu_w")):
            cpu_pk = rnd(s["cpu_u"] + s["cpu_s"] + s["cpu_w"])
        r = []
        for c in COLS:
            if c in ins:
                r.append(ins[c])
            elif c == "cpu_pk":
                r.append(cpu_pk)
            elif c == "rs":
                rs = s.get("rs") or []
                r.append(",".join(rs) if rs else None)
            else:
                r.append(s.get(c))
        rows.append(r)
        prev = s
    return rows


def aggregate(rows, step):
    """1-мин строки → окна по step секунд по правилам AGG."""
    buckets = {}
    for r in rows:
        k = r[0] - (r[0] % step)
        buckets.setdefault(k, []).append(r)
    out = []
    for k in sorted(buckets):
        grp = buckets[k]
        agg = [k]
        worst_vd, worst_rs = -1, None
        for i, c in enumerate(COLS[1:], start=1):
            vals = [r[i] for r in grp if r[i] is not None]
            mode = AGG.get(c, "last")
            if c == "rs":
                agg.append(worst_rs)
                continue
            if not vals:
                agg.append(None)
                continue
            if mode == "min":
                v = min(vals)
            elif mode == "max":
                v = max(vals)
            elif mode == "sum":
                v = sum(vals)
            elif mode == "avg":
                v = rnd(sum(vals) / len(vals))
            else:
                v = vals[-1]
            if c == "vd":
                for r in grp:
                    if r[i] is not None and r[i] > worst_vd:
                        worst_vd, worst_rs = r[i], r[CI["rs"]]
            agg.append(v)
        out.append(agg)
    return out


CI = {c: i for i, c in enumerate(COLS)}


WALLET_FILE = "/var/lib/sport-live/wallet.json"

# Оценка времени расчёта ставки (для карточки «Мои ставки»): считаем с запасом, точность —
# минута, страница сама ведёт обратный отсчёт. Замер по feed_bridge 05–08.09.2026 (от старта
# матча до расчёта основных маркетов, минуты, медиана / 90 %): футбол 119/144, хоккей 142/172,
# баскетбол 111/138, гандбол 108/132; фид рассчитывает исходы через ~3–10 мин после конца.
ETA_LAG = 10 * 60                                  # запас на расчёт фида после конца матча
ETA_PREMATCH_MIN = {1: 125, 7: 175, 3: 140, 8: 135}   # prematch: старт + N минут (≈ 90-й процентиль)


def leg_eta(r, now):
    """Момент расчёта ноги (unix, с запасом) или None для видов спорта без модели.

    Футбол — по таймеру фида (секунды матча, идёт/стоит) и номеру тайма; маркеты 1-го тайма —
    до конца тайма. Хоккей / баскетбол / гандбол — по номеру текущего периода, текущий период
    считаем целиком (реальное время периода с остановками), плюс перерывы.
    """
    sp = int(r.get("sp") or 0)
    if sp not in ETA_PREMATCH_MIN:
        return None
    st = r.get("st")
    start = calendar.timegm(st.timetuple()) if st else None
    if r.get("stage") != 2:                                   # prematch
        return (start + ETA_PREMATCH_MIN[sp] * 60) if start else None
    if r.get("es") == 3:                                      # матч завершён — ждём расчёт фида
        return now + ETA_LAG
    sc = r.get("score")
    try:
        sc = json.loads(sc) if isinstance(sc, str) else (sc or {})
    except Exception:
        sc = {}
    if not isinstance(sc, dict):
        sc = {}
    keys = [int(k) for k in (sc.get("list") or {}).keys() if str(k).isdigit() and int(k) > 0]
    per = max(keys) if keys else 1
    pn = r.get("pn") or ""
    if sp == 1:
        secs = float(sc.get("timer_v") or 0)
        if sc.get("timer_d") == 1 and sc.get("timer_t"):
            try:
                t0 = datetime.datetime.fromisoformat(str(sc["timer_t"]).replace("Z", "+00:00")).timestamp()
                secs += max(0.0, now - t0)
            except Exception:
                pass
        m = secs / 60.0
        if pn == "Half1":
            remain = (max(0.0, 45 - m) + 5) if per <= 1 else 0
        elif per <= 1:
            remain = max(0.0, 45 - m) + 5 + 15 + 45 + 5
        else:
            remain = max(0.0, 90 - m) + 5
        return now + remain * 60 + ETA_LAG
    if sp == 7:      # хоккей: период ≈ 35 реальных мин, перерывы 17
        remain = (3 - min(per, 3)) * (35 + 17) + 35
        if pn == "Period1" and per <= 1:
            remain = 35
    elif sp == 3:    # баскетбол: четверть ≈ 28 реальных мин, большой перерыв 15 после 2-й
        remain = (4 - min(per, 4)) * 28 + 28 + (15 if per <= 2 else 0)
        if pn == "Quarter1" and per <= 1:
            remain = 28
        elif pn == "Half1" and per <= 2:
            remain = (2 - min(per, 2)) * 28 + 28
    else:            # гандбол: тайм ≈ 35 реальных мин, перерыв 15
        remain = (2 - min(per, 2)) * 35 + 35 + (15 if per <= 1 else 0)
        if pn == "Half1" and per <= 1:
            remain = 35
    return now + remain * 60 + ETA_LAG


def settle_bets(cfg, now):
    """Расчёт ставок эмулированного кошелька прототипа sport-live (sport-live/api/wallet.php).

    Кошелёк — текстовый JSON (та же простая логика, что у тестового стенда Borlette): PHP
    списывает ставку и записывает её в тот же файл; здесь раз в минуту для открытых ставок
    смотрим результаты исходов в feed_bridge (outcomes.status 4 = рассчитан; result 1 win,
    2 loss, 3 return, 4/5 half win/loss; cancelled = возврат). Купон = экспресс: любая
    проигравшая нога → lose сразу; все ноги рассчитаны → выплата stake × произведение кэфов
    (возвратная нога = кэф 1). Исход, исчезнувший из БД (событие зачищено), спустя 7 суток
    считается возвратом. Файл правим под тем же flock, что и PHP.
    """
    if not os.path.exists(WALLET_FILE):
        return 0
    import fcntl
    try:
        import pymysql
        with open(WALLET_FILE, "r+", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                txt = fh.read()
                w = json.loads(txt) if txt.strip() else {}
                bets = w.get("bets") or {}
                open_bets = [b for b in bets.values() if b.get("status") == "open"]
                if not open_bets:
                    return 0
                mc = cfg["mysql"]
                conn = pymysql.connect(host=mc["host"], port=mc.get("port", 3306), user=mc["user"],
                                       password=mc["password"], database=mc["database"],
                                       cursorclass=pymysql.cursors.DictCursor, connect_timeout=5)
                cur = conn.cursor()
                settled = []
                eta_changed = False
                for b in open_bets:
                    verdict, odds, pending, eta = "win", 1.0, False, None
                    for sel in b.get("sel", []):
                        cur.execute("""SELECT oc.status, oc.result, oc.cancelled, oc.removed, e.removed er,
                                              e.start_time st, e.stage, e.status es, e.score, s.feed_id sp,
                                              m.period_name_en pn
                                       FROM outcomes oc JOIN markets m ON m.id = oc.market_id
                                            JOIN events e ON e.id = m.event_id JOIN sports s ON s.id = e.sport_id
                                       WHERE e.feed_id = %s AND m.feed_hash = %s AND oc.feed_hash = %s LIMIT 1""",
                                    (sel["ev"], sel["mk"], sel["oc"]))
                        r = cur.fetchone()
                        if not r:
                            if now - float(b.get("placed") or now) > 7 * 86400:
                                continue                      # нога пропала из БД → возврат (кэф 1)
                            pending = True
                            continue
                        if r["cancelled"] or r["result"] == 3:
                            continue                          # возврат ноги
                        if r["result"] == 0:
                            if r["er"] or r["removed"]:
                                continue                      # событие снято без расчёта → возврат ноги
                            pending = True
                            t = leg_eta(r, now)               # ждём ногу: оценка времени её расчёта
                            if t is not None:
                                eta = max(eta or 0, t)
                            continue
                        if r["result"] == 2:
                            verdict, pending = "lose", False
                            break                             # экспресс проигран сразу
                        if r["result"] == 1:
                            odds *= float(sel["odds"])
                        elif r["result"] == 4:
                            odds *= (1.0 + float(sel["odds"])) / 2.0
                        elif r["result"] == 5:
                            odds *= 0.5
                    if pending:
                        # ETA экспресса = самая поздняя нога; пишем в файл при сдвиге ≥ 1 мин
                        if eta is not None and abs(int(b.get("eta") or 0) - int(eta)) >= 60:
                            b["eta"] = int(eta)
                            eta_changed = True
                        elif eta is None and b.get("eta"):
                            b.pop("eta", None)
                            eta_changed = True
                        continue
                    b.pop("eta", None)
                    stake = int(b["stake"])
                    if verdict == "lose":
                        payout = 0
                    else:
                        payout = int(round(stake * odds))
                        verdict = "return" if abs(odds - 1.0) < 1e-9 else "win"
                    b["status"], b["payout"], b["settled"] = verdict, payout, int(now)
                    if payout:
                        w.setdefault("balances", {})
                        w["balances"][b["user"]] = int(w["balances"].get(b["user"], 0)) + payout
                    settled.append(b)
                conn.close()
                if settled or eta_changed:
                    fh.seek(0)
                    fh.truncate()
                    fh.write(json.dumps(w, ensure_ascii=False))
                    fh.flush()
                if settled:
                    with open(os.path.join(os.path.dirname(WALLET_FILE), "wallet.log"), "a", encoding="utf-8") as lg:
                        for b in settled:
                            lg.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [settle] {b['id']} user={b['user']} "
                                     f"{b['status']} stake={b['stake']} payout={b['payout']}" + "\n")
                return len(settled)
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
    except Exception as ex:
        print(f"settle: {str(ex)[:160]}")
        return 0


def attach_logos(events):
    """lh/la — ссылки на логотипы хозяев/гостей по имени события «A - B» (резолвер team_logo.py).

    Индекс резолвера строится только если в кэше нет какого-то имени (0,3 с), сами имена
    сохраняются в LOGO_CACHE вместе с промахами, так что обычно тик стоит один json.
    """
    if not os.path.exists(os.path.join(TEAM_LOGO_ROOT, "team_logo.py")):
        return 0
    if TEAM_LOGO_ROOT not in sys.path:
        sys.path.insert(0, TEAM_LOGO_ROOT)
    import team_logo
    cache = load_json(LOGO_CACHE, {})
    if not isinstance(cache, dict):
        cache = {}
    logos, hits, changed = None, 0, False
    for e in events:
        n = e.get("n") or ""
        i = n.find(" - ")
        names = [n[:i], n[i + 3:]] if i > 0 else [n]
        urls = []
        for nm in names:
            nm = nm.strip()
            if not nm:
                urls.append("")
                continue
            if nm in cache:
                u = cache[nm]
            else:
                if logos is None:
                    logos = team_logo.TeamLogos(TEAM_LOGO_ROOT, TEAM_LOGO_URL)
                u = logos.url(nm) or ""
                cache[nm] = u
                changed = True
            urls.append(u)
        if urls[0]:
            e["lh"] = urls[0]
            hits += 1
        if len(urls) > 1 and urls[1]:
            e["la"] = urls[1]
            hits += 1
    if changed:
        if len(cache) > 30000:
            cache = dict(list(cache.items())[-15000:])
        atomic_write(LOGO_CACHE, json.dumps(cache, ensure_ascii=False))
    return hits


def write_catalog(cfg, now):
    """Справочник для демо-страницы WS (centrifugo/ws-demo.html): виды спорта, live и
    ближайшие prematch-события с названиями, имена типов маркетов/исходов. Пишется в
    WWW_DIR/catalog.json раз в минуту; это же — заготовка будущего REST-снимка."""
    try:
        import pymysql
        import pymysql.cursors
        mc = cfg["mysql"]
        conn = pymysql.connect(host=mc["host"], port=mc.get("port", 3306), user=mc["user"],
                               password=mc["password"], database=mc["database"],
                               charset=mc.get("charset", "utf8mb4"),
                               cursorclass=pymysql.cursors.DictCursor, autocommit=True,
                               connect_timeout=8, read_timeout=20)
        cur = conn.cursor()
        cur.execute("SELECT feed_id id, name_en n FROM sports ORDER BY feed_id")
        sports = [dict(id=r["id"], n=r["n"]) for r in cur.fetchall()]
        cur.execute("""SELECT e.feed_id id, s.feed_id sp, e.name_en n, t.name_en tr, c.name_en cat,
                              c.feed_hash ch, t.feed_hash th, e.dv, e.sdv,
                              e.start_time st, e.stage, e.status, e.statusv2 sv, e.score
                       FROM events e JOIN sports s ON s.id=e.sport_id
                            JOIN tournaments t ON t.id=e.tournament_id
                            JOIN categories c ON c.id=e.category_id
                       WHERE e.removed=0 AND (
                             (e.stage=2 AND e.status IN (1,2) AND e.uts>NOW()-INTERVAL 1 DAY)
                          OR (e.stage=1 AND e.status=1
                              AND e.start_time BETWEEN NOW()-INTERVAL 1 HOUR AND NOW()+INTERVAL 12 HOUR))
                       ORDER BY e.stage DESC, e.start_time""")
        events = []
        for r in cur.fetchall():
            sc = None
            try:
                lst = (json.loads(r["score"]) if r["score"] else {}).get("list") or {}
                main = lst.get("0")
                if main and len(main) >= 2:
                    sc = f"{main[0]}:{main[1]}"
            except Exception:
                pass
            score = None
            try:
                score = json.loads(r["score"]) if r["score"] else None   # объект score как в Redis (list, timer_*)
            except Exception:
                pass
            events.append(dict(id=r["id"], sp=r["sp"], n=r["n"], tr=r["tr"], cat=r["cat"],
                               ch=r["ch"], th=r["th"],
                               st=(r["st"].strftime("%Y-%m-%dT%H:%M:%SZ") if r["st"] else None),
                               stage=r["stage"], status=r["status"], sv=r["sv"], sc=sc,
                               dv=r["dv"], sdv=r["sdv"],   # версии события фида (общая и счёта):
                               # страница по ним решает, что свежее — каталог или пришедшая WS-дельта
                               score=(score if r["stage"] == 2 else None)))
        try:
            attach_logos(events)
        except Exception as ex:
            print(f"catalog logos: {str(ex)[:120]}")
        # Сортировка витрины из админки Спорта (ведёт спортаналитик): ключи cfg:* в Redis фида.
        # Берём точечно по хэшам событий каталога (MGET), без SCAN. order 999999 = «не ранжирован»
        # (хвост), excl = скрыть (как betsportwss isExcluded). tl/th — порядок турниров в live и
        # hot-список, m — порядок маркетов, me — исключённые маркеты (пары тип+период).
        mcfg, mexcl = {}, {}
        try:
            import redis as redis_lib
            rc = cfg["redis"]
            r = redis_lib.Redis(host=rc["host"], port=rc["port"], password=rc["password"],
                                db=rc.get("db", 0), decode_responses=True,
                                socket_connect_timeout=5, socket_timeout=10)
            sp_ids = sorted({str(e["sp"]) for e in events} | {str(s["id"]) for s in sports})
            keys = ([f"cfg:s:{s}" for s in sp_ids] + [f"cfg:tl:{s}" for s in sp_ids]
                    + [f"cfg:th:{s}" for s in sp_ids] + [f"cfg:m:{s}" for s in sp_ids]
                    + [f"cfg:me:{s}" for s in sp_ids])
            ck = sorted({f"cfg:c:{e['sp']}:{e['ch']}" for e in events})
            tk = sorted({f"cfg:t:{e['sp']}:{e['ch']}:{e['th']}" for e in events})
            allk = keys + ck + tk
            vals = {}
            for i in range(0, len(allk), 500):
                part = allk[i:i + 500]
                for k, v in zip(part, r.mget(part)):
                    if v:
                        try:
                            vals[k] = json.loads(v)
                        except Exception:
                            pass
            for s in sports:
                sc_ = vals.get(f"cfg:s:{s['id']}") or {}
                s["o"] = int(sc_.get("order", 999999)) if str(sc_.get("order", "")).lstrip("-").isdigit() else 999999
                s["x"] = int(bool(sc_.get("excl")))
            tl = {s: {h: i for i, h in enumerate((vals.get(f"cfg:tl:{s}") or {}).get("order") or [])} for s in sp_ids}
            th = {s: set((vals.get(f"cfg:th:{s}") or {}).get("order") or []) for s in sp_ids}
            for s in sp_ids:
                m = vals.get(f"cfg:m:{s}")
                if isinstance(m, list):
                    mcfg[s] = [[x.get("m"), x.get("p")] for x in m if isinstance(x, dict) and x.get("m")]
                me = vals.get(f"cfg:me:{s}")
                if isinstance(me, list) and me:
                    mexcl[s] = [[x.get("m"), x.get("p")] for x in me if isinstance(x, dict) and x.get("m")]  # m=0 — пустые блоки формы
            for e in events:
                s = str(e["sp"])
                c_ = vals.get(f"cfg:c:{e['sp']}:{e['ch']}") or {}
                t_ = vals.get(f"cfg:t:{e['sp']}:{e['ch']}:{e['th']}") or {}
                e["co"] = int(c_.get("order", 999999)) if str(c_.get("order", "")).lstrip("-").isdigit() else 999999
                e["to"] = int(t_.get("order", 999999)) if str(t_.get("order", "")).lstrip("-").isdigit() else 999999
                e["lp"] = tl.get(s, {}).get(e["th"])
                e["hot"] = int(e["th"] in th.get(s, set()))
                e["x"] = int(bool(c_.get("excl")) or bool(t_.get("excl")))
                sx = next((sp for sp in sports if str(sp["id"]) == s), None)
                if sx and sx.get("x"):
                    e["x"] = 1
        except Exception as ex:
            print(f"catalog cfg: {str(ex)[:120]}")
        # Словарь типов маркетов вида спорта: имя типа и коды исходов по порядку фида.
        cur.execute("""SELECT s.feed_id sp, m.market_type mt, m.name_en n, m.outcomes o, m.outcome_names onm
                       FROM market_type_names m JOIN sports s ON s.id=m.sport_id""")
        mtypes, mtdef = {}, {}
        for r in cur.fetchall():
            try:
                codes = json.loads(r["o"]) if isinstance(r["o"], str) else (r["o"] or [])
                names = json.loads(r["onm"]) if isinstance(r["onm"], str) else (r["onm"] or [])
            except Exception:
                codes, names = [], []
            mtypes.setdefault(str(r["sp"]), {})[str(r["mt"])] = dict(
                n=r["n"], o={str(c): nm for c, nm in zip(codes, names)})
            mtdef[(r["sp"], r["mt"])] = dict(n=r["n"], oc=names)
        # Наборы главных маркетов строк таблицы — те же, что в основном Спорте: первые MAIN_SLOTS
        # пар «тип + период» из настройки админки «Сортировка / Маркеты» (ключ Redis cfg:m:{sport},
        # минус исключённые cfg:me). betsportwss отдаёт фронту ровно это (model.js mainMarkets по
        # первым mcount записям порядка; client.js для live-списка запрашивает mcount = 3), поэтому
        # у каждого вида спорта свой набор колонок: футбол — победитель, двойной шанс, фора;
        # теннис — победитель, фора по геймам, тотал геймов, и т.д. В колонку таблицы помещается
        # маркет с 2–3 исходами, пары с другим числом исходов (точный счёт) пропускаем.
        MAIN_SLOTS = 3
        mmain = {}
        for s, pairs_cfg in mcfg.items():
            exc = {(x[0], x[1]) for x in mexcl.get(s, [])}
            slots = []
            for t, p in pairs_cfg:
                if t is None or p is None or (t, p) in exc:
                    continue
                d = mtdef.get((int(s), t))
                if not d or not (2 <= len(d["oc"]) <= 3):
                    continue
                slots.append(dict(t=t, p=p, n=d["n"], oc=d["oc"]))
                if len(slots) >= MAIN_SLOTS:
                    break
            if slots:
                mmain[s] = slots
        # Настройка живёт в Redis фида; если он не ответил — берём последнюю удачную, иначе
        # таблица осталась бы вовсе без колонок с кэфами.
        if mmain:
            atomic_write(MMAIN_CACHE, json.dumps(mmain, ensure_ascii=False))
        else:
            try:
                with open(MMAIN_CACHE, encoding="utf-8") as fh:
                    mmain = json.load(fh)
            except Exception:
                mmain = {}
        # Главные маркеты каждого события каталога (строки таблицы и hot-карточки — чтобы список
        # не тянул снимок каждого события): по одному маркету на слот вида спорта, в порядке слотов.
        # Если пара «тип+период» представлена несколькими линиями (тотал 2.5 и 3.5, фора ±1), берём
        # самую сбалансированную открытую — это и есть основная линия.
        ids = [e["id"] for e in events]
        main, nm_all = {}, {}
        if ids:
            ph = ",".join(str(int(i)) for i in ids)
            cur.execute(f"""SELECT e.feed_id ev, COUNT(*) c FROM markets m JOIN events e ON e.id=m.event_id
                            WHERE e.feed_id IN ({ph}) AND m.removed=0 GROUP BY e.feed_id""")
            nm_all = {r["ev"]: r["c"] for r in cur.fetchall()}
        tp_pairs = sorted({(int(d["t"]), int(d["p"])) for sl in mmain.values() for d in sl})
        if ids and tp_pairs:
            cond = " OR ".join(f"(m.market_type={t} AND m.period={p})" for t, p in tp_pairs)
            cur.execute(f"""SELECT e.feed_id ev, m.id mid, m.feed_hash h, m.name_en n, m.period_name_en p,
                                   m.market_type mt, m.period pr, m.value v, m.open o,
                                   oc.feed_hash oh, oc.name_en onm, oc.value ov,
                                   oc.outcome_type ot, oc.price, oc.status os
                            FROM markets m JOIN events e ON e.id=m.event_id
                            LEFT JOIN outcomes oc ON oc.market_id=m.id AND oc.removed=0
                            WHERE e.feed_id IN ({ph}) AND m.removed=0 AND ({cond})""")
            per = {}
            for r in cur.fetchall():
                mk = per.setdefault(r["ev"], {}).setdefault(r["mid"], dict(
                    h=r["h"], n=r["n"], p=r["p"], type=r["mt"], period=r["pr"],
                    v=r["v"] or "", open=int(r["o"] or 0), oc=[]))
                if r["oh"] is not None:
                    mk["oc"].append(dict(h=r["oh"], n=r["onm"], v=r["ov"] or "", t=r["ot"],
                                         price=(float(r["price"]) if r["price"] is not None else None),
                                         status=r["os"]))

            def pick(cands):
                """одна линия из нескольких: открытая и самая сбалансированная = основная"""
                if not cands:
                    return None
                best, bd = None, None
                for m in cands:
                    pr = [o["price"] for o in m["oc"] if o["price"] is not None]
                    if len(pr) < 2:
                        continue
                    d = abs(pr[0] - pr[1]) + (0 if m["open"] else 100)
                    if bd is None or d < bd:
                        best, bd = m, d
                return best or cands[0]

            sp_of = {e["id"]: str(e["sp"]) for e in events}
            for ev_id, mks in per.items():
                slots = mmain.get(sp_of.get(ev_id, ""), [])
                if not slots:
                    continue
                by_tp = {}
                for m in mks.values():
                    by_tp.setdefault((m["type"], m["period"]), []).append(m)
                main[ev_id] = [pick(by_tp.get((d["t"], d["p"]), [])) for d in slots]
        for e in events:
            e["main"] = main.get(e["id"])
            e["nm"] = nm_all.get(e["id"], 0)
        # Снимки маркетов/исходов для live и стартующих в ближайший час событий —
        # статический прототип REST-снимка (snap/{eventId}.json): демо подгружает его при
        # подписке, чтобы сразу знать имена маркетов и исходные кэфы.
        snap_dir = os.path.join(WWW_DIR, "snap")
        os.makedirs(snap_dir, exist_ok=True)
        n_snap = 0
        for ev in events:
            soon = ev["st"] and ev["st"] <= time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 3600))
            if not (ev["stage"] == 2 or soon):
                continue
            cur.execute("""SELECT m.id mid, m.feed_hash h, m.name_en n, m.period_name_en p, m.market_type mt,
                                  m.period pr, m.value v, m.open o
                           FROM markets m JOIN events e ON e.id=m.event_id
                           WHERE e.feed_id=%s AND m.removed=0""", (ev["id"],))
            mk = {}
            for r in cur.fetchall():
                mk[r["mid"]] = dict(h=r["h"], n=r["n"], p=r["p"], type=r["mt"], period=r["pr"],
                                    v=r["v"], open=int(r["o"] or 0), oc=[])
            cur.execute("""SELECT o.market_id mid, o.feed_hash h, o.name_en n, o.value v, o.price, o.status s,
                                  o.outcome_type t
                           FROM outcomes o JOIN events e ON e.id=o.event_id
                           WHERE e.feed_id=%s AND o.removed=0""", (ev["id"],))
            for r in cur.fetchall():
                m = mk.get(r["mid"])
                if m is not None:
                    m["oc"].append(dict(h=r["h"], n=r["n"], v=r["v"], t=r["t"],
                                        price=(float(r["price"]) if r["price"] is not None else None),
                                        status=r["s"]))
            atomic_write(os.path.join(snap_dir, f"{ev['id']}.json"),
                         json.dumps(dict(t=int(now), ev=ev, markets=list(mk.values())),
                                    ensure_ascii=False, separators=(",", ":")))
            ev["snap"] = True   # клиенты запрашивают snap/{id}.json только при этом флаге
            n_snap += 1
        conn.close()
        # снимки событий, ушедших из каталога, живут ещё 2 часа
        try:
            for f in os.scandir(snap_dir):
                if f.is_file() and f.stat().st_mtime < now - 7200:
                    os.remove(f.path)
        except Exception:
            pass
        atomic_write(os.path.join(WWW_DIR, "catalog.json"),
                     json.dumps(dict(t=int(now), sports=sports, events=events, mtypes=mtypes, snaps=n_snap,
                                     mcfg=mcfg, mexcl=mexcl, mmain=mmain),
                                ensure_ascii=False, separators=(",", ":")))
        return len(events)
    except Exception as e:
        print(f"catalog: {str(e)[:120]}")
        return None


# ── main ────────────────────────────────────────────────────────────────────

def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(WWW_DIR, exist_ok=True)
    rotate_log()
    lock = open(LOCK, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("skip: предыдущий сбор ещё идёт")
        return

    from feedbridge.config import load_config
    cfg = load_config()

    now = time.time()
    state = load_json(STATE, {})
    prev_t = state.get("t") or (now - 70)

    s = dict(t=int(now))
    s.update(collect_service(state, now))
    s.update(collect_mysql(cfg, now))
    s.update(collect_redis(cfg, now))
    s.update(collect_rmq(cfg, now))
    s.update(collect_journal(prev_t + 1, now))
    s.update(collect_host(state))
    s.update(collect_gate(now))
    sync_metrics(s)
    recheck_missing(cfg, s)
    s["_prev_live_miss"], s["_prev_pre_miss"] = state.get("live_miss"), state.get("pre_miss")
    vd, rs = verdict(s)
    s.pop("_prev_live_miss", None)
    s.pop("_prev_pre_miss", None)
    s["vd"], s["rs"] = vd, rs

    # события: рестарты / ошибки / смена вердикта
    prev_vd = state.get("vd")
    if s["rst"]:
        append_jsonl(EVENTS, dict(t=s["t"], kind="restart", text="рестарт сервиса", vd=vd))
    if s["err"] or s["pc0"]:
        txt = "; ".join(l for l in s["lines"] if l != "рестарт сервиса")[:300] or "ошибки в журнале"
        append_jsonl(EVENTS, dict(t=s["t"], kind="error", text=f"{s['err'] + s['pc0']}× {txt}", vd=vd))
    if prev_vd is not None and prev_vd != vd:
        names = ["OK", "WARN", "CRIT"]
        append_jsonl(EVENTS, dict(t=s["t"], kind="verdict",
                                  text=f"{names[prev_vd]} → {names[vd]}" + (": " + ", ".join(rs) if rs else ""),
                                  vd=vd))

    cpu_ns = s.pop("_cpu_ns", None)
    cpu_stat = s.pop("_cpu_stat", None)
    gate_snap = s.pop("_gate", None)      # полный снимок гейта — на страницу, не в историю
    lines = s.pop("lines", [])
    append_jsonl(SAMPLES, s)
    atomic_write(STATE, json.dumps(dict(t=s["t"], cpu_ns=cpu_ns, cpu_stat=cpu_stat, pid=s.get("pid"), vd=vd,
                                        live_miss=s.get("live_miss"), pre_miss=s.get("pre_miss"))))
    prune_samples(now)

    # ── страница ──
    samples = read_samples(now - AGG_DAYS * 86400)
    rows = rows_from(samples)
    h1 = [r for r in rows if r[0] >= now - RAW_DAYS * 86400]
    h10 = aggregate(rows, AGG_STEP)

    day = [x for x in samples if x["t"] >= now - 86400]
    d24 = dict(err=sum(x.get("err") or 0 for x in day),
               pc0=sum(x.get("pc0") or 0 for x in day),
               rst=sum(x.get("rst") or 0 for x in day),
               n=len(day),
               ok_pct=rnd(100.0 * sum(1 for x in day if (x.get("vd") or 0) == 0) / len(day)) if day else None,
               crit_pct=rnd(100.0 * sum(1 for x in day if (x.get("vd") or 0) == 2) / len(day)) if day else None,
               growth_gb=None, ins_mk=None, ins_oc=None, ack_avg=None, lag_max=None, wage_max=None)
    if len(day) >= 2 and day[0].get("db_gb") is not None and day[-1].get("db_gb") is not None:
        span = (day[-1]["t"] - day[0]["t"]) / 86400.0
        if span > 0.2:
            d24["growth_gb"] = rnd((day[-1]["db_gb"] - day[0]["db_gb"]) / span, 2)
    dr = [r for r in rows if r[0] >= now - 86400]
    for k in ("ins_mk", "ins_oc"):
        vals = [r[CI[k]] for r in dr if r[CI[k]] is not None]
        d24[k] = rnd(sum(vals) / len(vals)) if vals else None
    vals = [r[CI["ack"]] for r in dr if r[CI["ack"]] is not None]
    d24["ack_avg"] = rnd(sum(vals) / len(vals)) if vals else None
    for k in ("lag", "wage"):
        vals = [r[CI[k]] for r in dr if r[CI[k]] is not None]
        d24[k + "_max"] = max(vals) if vals else None

    events = []
    try:
        with open(EVENTS, encoding="utf-8") as f:
            for ln in f:
                try:
                    e = json.loads(ln)
                    if e.get("t", 0) >= now - EVENTS_KEEP_DAYS * 86400:
                        events.append(e)
                except Exception:
                    pass
    except FileNotFoundError:
        pass
    if len(events) > 2000:   # обрезка журнала событий
        atomic_write(EVENTS, "".join(json.dumps(e, ensure_ascii=False, separators=(",", ":")) + "\n"
                                     for e in events[-1500:]))
    events = events[-EVENTS_SHOW:]

    msk = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=3)
    cur = {k: v for k, v in s.items() if k not in ("partners",)}
    # последний прогон сборщика bridge.py --sweep (пишет sweep.json в DATA_DIR)
    sweep = load_json(os.path.join(DATA_DIR, "sweep.json"), None)
    try:
        running = bool(run(["pgrep", "-f", "bridge.py --sweep"], 5).strip())
    except Exception:
        running = None
    if sweep:
        sweep["running"] = running
    elif running:
        sweep = dict(running=True, first=True)   # первый прогон ещё идёт, итога пока нет
    cur["sweep"] = sweep
    cur["gate"] = gate_snap   # состояние гейта приёма ставок целиком: причины и пороги
    page = dict(updated=msk.strftime("%d.%m %H:%M") + " МСК", now=int(now), cols=COLS,
                cur=cur, d24=d24, partners=s.get("partners") or [], events=events, thr=THR,
                unit=UNIT, queue=cfg["rabbitmq"]["queue"], host=socket.gethostname(),
                db=cfg["mysql"]["database"], kept=dict(raw_days=RAW_DAYS, agg_days=AGG_DAYS, agg_step=AGG_STEP))
    atomic_write(os.path.join(WWW_DIR, "data.json"),
                 json.dumps(page, ensure_ascii=False, separators=(",", ":")))
    page["h1"], page["h10"] = h1, h10
    frag = open(TPL, encoding="utf-8").read().replace("/*__DATA__*/",
                                                      json.dumps(page, ensure_ascii=False, separators=(",", ":")))
    shell = ('<!doctype html><html lang="ru"><head><meta charset="utf-8">'
             '<meta name="viewport" content="width=device-width,initial-scale=1">'
             '<title>Здоровье feed-mysql-bridge — live</title>'
             '<style>*,*::before,*::after{box-sizing:border-box}html,body{margin:0}</style>'
             '</head><body>' + frag + '</body></html>')
    atomic_write(os.path.join(WWW_DIR, "index.html"), shell)
    n_cat = write_catalog(cfg, now)
    n_settle = settle_bets(cfg, now)
    if n_settle:
        print(f"settle: {n_settle} bets")

    gname = ["OPEN", "HOLD", "SUSPEND"][s["gs"]] if s.get("gs") is not None else "n/a"
    greas = ",".join((gate_snap or {}).get("reasons") or [])
    print(f"{page['updated']} vd={['OK', 'WARN', 'CRIT'][vd]}{'(' + ','.join(rs) + ')' if rs else ''} "
          f"gate={gname}{'(' + greas + ')' if greas else ''} glag={s.get('glag')} gsil={s.get('gsil')} "
          f"svc={s['svc']} ack={s.get('ack')} pub={s.get('pub')} ready={s.get('ready')} lag={s.get('lag')} "
          f"wage={s.get('wage')} live={s.get('live_db')}/{s.get('live_r')}(miss {s.get('live_miss')}, late {s.get('live_late')}, zomb {s.get('live_zomb')}) "
          f"pre={s.get('pre_db')}/{s.get('pre_r')}(miss {s.get('pre_miss')}) "
          f"mem={s.get('mem')} cpu={s.get('cpu')} host cpu={s.get('cpu_u')}/{s.get('cpu_s')}/{s.get('cpu_w')} "
          f"ram={s.get('mem_used')}/{s.get('mem_tot')} mdb={s.get('mdb_gb')}GiB err={s['err']} rst={s['rst']} rows={len(rows)} "
          f"catalog={n_cat} errs={[k for k in ('svc_err', 'my_err', 'rd_err', 'mq_err', 'jr_err') if s.get(k)]} "
          f"{'; '.join(lines[:2])}")


if __name__ == "__main__":
    main()
