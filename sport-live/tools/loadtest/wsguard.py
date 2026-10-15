# -*- coding: utf-8 -*-
"""Сторож на машине-генераторе (mixdev). Останавливает нагрузку, если она начинает мешать
тому, ради чего этот сервер существует.

    python3 wsguard.py [секунд]

mixdev — главный MySQL-хост дев-окружения: через него работает дев Пижамы (сотни соединений
с iodum) и дев Спорта. Канал 1 Гбит/с общий, поэтому генератор не должен ни насыщать сеть,
ни отбирать процессор у mysqld. Пороги ниже консервативные; при срабатывании процесс
генератора получает SIGTERM (он корректно закрывает соединения и печатает итог).
"""
import json
import os
import socket
import subprocess
import sys
import time

DUR = int(sys.argv[1]) if len(sys.argv) > 1 else 3600
OUT = "/tmp/wsload/guard.jsonl"
os.makedirs(os.path.dirname(OUT), exist_ok=True)

MAX_MBIT = 500        # половина канала: выше — рискуем клиентами MySQL
MAX_SQL_MS = 500      # отклик MySQL на SELECT 1
MAX_LOAD = 10.0       # при 12 ядрах
MIN_FREE_MB = 4096

NIC = "enp41s0"


def nic_bytes():
    b = "/sys/class/net/%s/statistics/" % NIC
    return int(open(b + "rx_bytes").read()), int(open(b + "tx_bytes").read())


def sql_ms():
    """Отклик mysqld: время до приветственного пакета на 3306.

    Учётная запись не нужна и не хранится на диске: сервер шлёт greeting сразу после
    установления соединения, и его задержка так же показывает, что mysqld голодает по
    процессору, как и время выполнения запроса.
    """
    t = time.time()
    try:
        c = socket.create_connection(("127.0.0.1", 3306), timeout=5)
        c.settimeout(5)
        data = c.recv(16)
        c.close()
        if not data:
            return 9999
    except Exception:
        return 9999
    return round((time.time() - t) * 1000, 1)


def mem_free_mb():
    for line in open("/proc/meminfo"):
        if line.startswith("MemAvailable"):
            return int(line.split()[1]) // 1024
    return 0


def stop(reason):
    print("СТОП: " + reason)
    subprocess.call(["pkill", "-TERM", "-f", "wsload.js"])
    with open(OUT, "a") as f:
        f.write(json.dumps({"t": int(time.time()), "stop": reason}, ensure_ascii=False) + "\n")


print("сторож генератора: %d с (сеть <%d Мбит/с, MySQL <%d мс, load <%.0f)" % (DUR, MAX_MBIT, MAX_SQL_MS, MAX_LOAD))
r0, t0 = nic_bytes()
tprev = time.time()
end = time.time() + DUR
f = open(OUT, "a")
while time.time() < end:
    time.sleep(5)
    now = time.time()
    dt = now - tprev
    r1, t1 = nic_bytes()
    rx = (r1 - r0) * 8 / dt / 1e6
    tx = (t1 - t0) * 8 / dt / 1e6
    ms = sql_ms()
    load = float(open("/proc/loadavg").read().split()[0])
    free = mem_free_mb()
    rec = {"t": int(now), "rx_mbit": round(rx, 1), "tx_mbit": round(tx, 1), "sql_ms": ms,
           "load1": load, "free_mb": free}
    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    f.flush()
    print("вход %6.1f / исход %5.1f Мбит/с | MySQL %6.1f мс | load %4.2f | свободно %d МБ" % (rx, tx, ms, load, free))
    if rx > MAX_MBIT or tx > MAX_MBIT:
        stop("сеть %.0f/%.0f Мбит/с выше порога %d" % (rx, tx, MAX_MBIT)); break
    if ms > MAX_SQL_MS:
        stop("MySQL отвечает %.0f мс (порог %d)" % (ms, MAX_SQL_MS)); break
    if load > MAX_LOAD:
        stop("load %.1f выше порога %.0f" % (load, MAX_LOAD)); break
    if free < MIN_FREE_MB:
        stop("свободной памяти %d МБ (порог %d)" % (free, MIN_FREE_MB)); break
    r0, t0, tprev = r1, t1, now
f.close()
print("сторож завершён")
