# -*- coding: utf-8 -*-
"""Сбор метрик цели во время нагрузочного теста. Запускается на *10.

    python3 wsmetrics.py [секунд] [файл.jsonl]

Каждые 5 секунд пишет строку JSON: клиенты и подписки Centrifugo, отправленные им
сообщения и байты, соединения nginx, загрузка процессора и память сервера, исходящий
трафик, состояние моста (очередь, отставание, гейт) — чтобы после теста было видно,
что упёрлось первым и не пострадал ли разбор очереди фида.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

DUR = int(sys.argv[1]) if len(sys.argv) > 1 else 3600
OUT = sys.argv[2] if len(sys.argv) > 2 else "/tmp/wsload/target.jsonl"
os.makedirs(os.path.dirname(OUT), exist_ok=True)

NIC = next((n for n in os.listdir("/sys/class/net") if n != "lo" and not n.startswith(("docker", "br-", "veth"))), "eth0")
GATE = "/var/lib/feed-health/gate.json"
ANSI = re.compile(r"\[[0-9;]*[A-Za-z]")


def metrics():
    """Счётчики Centrifugo из Prometheus-эндпоинта."""
    try:
        raw = urllib.request.urlopen("http://127.0.0.1:8000/metrics", timeout=3).read().decode()
    except Exception:
        return {}
    out = {}
    want = {
        "centrifugo_node_num_clients": "clients",
        "centrifugo_node_num_subscriptions": "subs",
        "centrifugo_node_num_channels": "channels",
    }
    sums = {"centrifugo_transport_messages_sent": "msg_sent",
            "centrifugo_transport_messages_sent_size": "bytes_sent",
            "centrifugo_client_num_reply_errors": "reply_errors"}
    for line in raw.split("\n"):
        if not line or line[0] == "#":
            continue
        name = line.split("{")[0].split(" ")[0]
        try:
            val = float(line.rsplit(" ", 1)[1])
        except Exception:
            continue
        if name in want:
            out[want[name]] = val
        for pref, key in sums.items():
            if name == pref or name.startswith(pref + "_total"):
                out[key] = out.get(key, 0) + val
    return out


def cpu_sample():
    with open("/proc/stat") as f:
        p = f.readline().split()[1:]
    v = [int(x) for x in p]
    return sum(v), v[3] + v[4]      # total, idle+iowait


def nic():
    b = "/sys/class/net/%s/statistics/" % NIC
    return int(open(b + "rx_bytes").read()), int(open(b + "tx_bytes").read())


def mem():
    d = {}
    for line in open("/proc/meminfo"):
        k, v = line.split(":", 1)
        d[k] = int(v.strip().split()[0])
    return (d["MemTotal"] - d["MemAvailable"]) // 1024, d["MemTotal"] // 1024


def proc_rss(pattern):
    try:
        pid = subprocess.check_output(["pgrep", "-f", pattern], text=True).split()[0]
        return int(open("/proc/%s/statm" % pid).read().split()[1]) * 4096 // 1048576
    except Exception:
        return None


def nginx_conns():
    try:
        out = subprocess.check_output(["ss", "-tnH", "state", "established"], text=True)
        return sum(1 for l in out.split("\n") if ":443" in l.split()[-2] if l.strip()) if out else 0
    except Exception:
        return None


def gate():
    """Состояние гейта и потерянные дельты: pdrops — накопленный счётчик недоставленных
    в браузеры изменений, главный показатель для проверки правки публикатора."""
    try:
        g = json.load(open(GATE))
        m = g.get("metrics") or {}
        return g.get("state_name"), m.get("lag"), m.get("pdrops"), m.get("pdrop")
    except Exception:
        return None, None, None, None


def bridge_queue():
    """Глубина очереди и отставание — из последней строки журнала моста."""
    try:
        out = subprocess.check_output(
            ["journalctl", "-u", "feed-bridge", "-n", "1", "--no-pager", "-o", "cat"], text=True)
        out = ANSI.sub("", out)      # мост печатает строку статистики в цвете
        q = re.search(r"в очереди:\s*([\d,]+)", out)
        lag = re.search(r"отставание:\s*(\S+)", out)
        rate = re.search(r"скорость:\s*(\d+)", out)
        return (int(q.group(1).replace(",", "")) if q else None,
                lag.group(1) if lag else None,
                int(rate.group(1)) if rate else None)
    except Exception:
        return None, None, None


print("сбор метрик цели: %d с, интерфейс %s → %s" % (DUR, NIC, OUT))
c0, i0 = cpu_sample()
r0, t0 = nic()
m0 = metrics()
tprev = time.time()
end = time.time() + DUR
f = open(OUT, "a")
while time.time() < end:
    time.sleep(5)
    now = time.time()
    dt = now - tprev
    c1, i1 = cpu_sample()
    r1, t1 = nic()
    m1 = metrics()
    cpu = 100.0 * (1 - (i1 - i0) / max(1, c1 - c0))
    used, total = mem()
    q, lag, rate = bridge_queue()
    gs, glag, gdrops, gdrop = gate()
    rec = {
        "t": int(now),
        "cpu": round(cpu, 1),
        "mem_mb": used, "mem_total_mb": total,
        "rx_mbit": round((r1 - r0) * 8 / dt / 1e6, 1),
        "tx_mbit": round((t1 - t0) * 8 / dt / 1e6, 1),
        "cf_clients": m1.get("clients"), "cf_subs": m1.get("subs"), "cf_channels": m1.get("channels"),
        "cf_msg_per_s": round((m1.get("msg_sent", 0) - m0.get("msg_sent", 0)) / dt, 1),
        "cf_mbit_out": round((m1.get("bytes_sent", 0) - m0.get("bytes_sent", 0)) * 8 / dt / 1e6, 1),
        "cf_reply_errors": m1.get("reply_errors"),
        "cf_rss_mb": proc_rss("centrifugo"), "bridge_rss_mb": proc_rss("bridge.py"),
        "nginx_conns": nginx_conns(),
        "queue": q, "bridge_lag": lag, "bridge_rate": rate,
        "gate": gs, "gate_lag": glag, "pdrops": gdrops, "pdrop": gdrop,
        "load1": float(open("/proc/loadavg").read().split()[0]),
    }
    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    f.flush()
    print("cpu %5.1f%% | RAM %5d МБ | исход %6.1f Мбит/с | клиентов %s подписок %s | %s сообщ/с %s Мбит/с | очередь %s отставание %s | гейт %s | потеряно дельт %s" % (
        rec["cpu"], rec["mem_mb"], rec["tx_mbit"], rec["cf_clients"], rec["cf_subs"],
        rec["cf_msg_per_s"], rec["cf_mbit_out"], rec["queue"], rec["bridge_lag"], rec["gate"], rec["pdrops"]))
    c0, i0, r0, t0, m0, tprev = c1, i1, r1, t1, m1, now
f.close()
print("готово")
