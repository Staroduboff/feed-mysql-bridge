# -*- coding: utf-8 -*-
"""Сводка по ступеням лестницы: метрики цели (*10) привязываются к ступеням по числу клиентов.

    python3 ladder_report.py /tmp/wsload/target-narrow.jsonl
"""
import json
import sys
from datetime import datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))
rows = [json.loads(l) for l in open(sys.argv[1], encoding="utf-8") if l.strip()]
rows = [r for r in rows if r.get("cf_clients") is not None]
if not rows:
    print("нет данных"); sys.exit(1)

# ступень = полка по числу клиентов Centrifugo; берём устойчивые участки
steps = []
cur = None
for r in rows:
    n = int(r["cf_clients"])
    band = 0 if n < 50 else 10 ** len(str(n // 100)) * 100 if False else n
    if cur and abs(n - cur["peak"]) / max(1, cur["peak"]) < 0.15:
        cur["rows"].append(r); cur["peak"] = max(cur["peak"], n)
    else:
        if cur and cur["peak"] >= 50 and len(cur["rows"]) >= 3:
            steps.append(cur)
        cur = {"peak": n, "rows": [r]}
if cur and cur["peak"] >= 50 and len(cur["rows"]) >= 3:
    steps.append(cur)


def med(a):
    a = sorted(x for x in a if x is not None)
    return a[len(a) // 2] if a else None


def mx(a):
    a = [x for x in a if x is not None]
    return max(a) if a else None


print("%-9s %-13s %-7s %-7s %-8s %-9s %-9s %-8s %-7s %-6s" % (
    "клиентов", "время МСК", "CPU %", "CPU пик", "RAM МБ", "исход Мбит", "сообщ/с", "подписок", "очередь", "гейт"))
for s in steps:
    R = s["rows"]
    t0 = datetime.fromtimestamp(R[0]["t"], MSK).strftime("%H:%M")
    t1 = datetime.fromtimestamp(R[-1]["t"], MSK).strftime("%H:%M")
    print("%-9d %-13s %-7.1f %-7.1f %-8d %-9.1f %-9.0f %-8d %-7s %-6s" % (
        s["peak"], t0 + "–" + t1,
        med([r["cpu"] for r in R]), mx([r["cpu"] for r in R]),
        mx([r["mem_mb"] for r in R]),
        mx([r["tx_mbit"] for r in R]),
        mx([r["cf_msg_per_s"] for r in R]),
        mx([r["cf_subs"] or 0 for r in R]),
        mx([r["queue"] for r in R]),
        ",".join(sorted({str(r["gate"]) for r in R}))))

print()
print("RSS Centrifugo: макс %s МБ, моста: макс %s МБ" % (
    mx([r.get("cf_rss_mb") for r in rows]), mx([r.get("bridge_rss_mb") for r in rows])))
print("ошибок ответов Centrifugo за прогон:", mx([r.get("cf_reply_errors") for r in rows]))
print("отставание моста, встреченные значения:", sorted({str(r.get("bridge_lag")) for r in rows}))
print("состояния гейта:", sorted({str(r.get("gate")) for r in rows}))
