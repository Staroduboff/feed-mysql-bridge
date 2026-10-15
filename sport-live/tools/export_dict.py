#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
export_dict.py — словари подписей фида для прототипа sport-live.

Перепродаваемый фид отдаёт названия маркетов, исходов и периодов кодами
(Win1AndDraw, BothToScoreAndWin1, Half1 …) на всех языках одинаково. Переводы
этих кодов ведёт команда Спорта в словарях фронта бэка Спорта
(репо betting/BackEnd, dict/frontend/{ln}.ini, 13 языков); Vue-фронт Спорта
берёт их как dict('event.market.outcome.' + код). Этот скрипт выгружает нужные
секции в JSON, который читает адаптер sport-live (assets/js/live-data.js):

  [frontapi.event.market.name]      → "market"   (493 кода)
  [frontapi.event.market.outcome]   → "outcome"  (125 кодов)
  [frontapi.event.market.subperiod] → "period"   (65 кодов)
  [frontapi.sport.name]             → "sport"    (51 код)

Результат: sport-live/assets/dict/{ln}.json = {"market": {код: подпись}, …}.
Повторять при обновлении ini в репо бэка Спорта.

Запуск (из корня репо бриджа):
  python3 sport-live/tools/export_dict.py [--src ../BackEnd/dict/frontend] [--langs ru,en,fr,ht]
"""
import argparse
import io
import json
import os

SECTIONS = {
    "frontapi.event.market.name": "market",
    "frontapi.event.market.outcome": "outcome",
    "frontapi.event.market.subperiod": "period",
    "frontapi.sport.name": "sport",
}
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SRC = os.path.normpath(os.path.join(HERE, "..", "..", "..", "BackEnd", "dict", "frontend"))
OUT_DIR = os.path.normpath(os.path.join(HERE, "..", "assets", "dict"))


def parse_ini(path):
    """Словарь F3: секции [name], строки key = value; комментарии ; и #."""
    out = {v: {} for v in SECTIONS.values()}
    cur = None
    with io.open(path, encoding="utf-8", errors="replace") as f:
        for ln in f:
            ln = ln.strip()
            if not ln or ln[0] in ";#":
                continue
            if ln.startswith("[") and ln.endswith("]"):
                cur = SECTIONS.get(ln[1:-1].strip())
                continue
            if cur is None or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            k, v = k.strip(), v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            if k and v:
                out[cur][k] = v
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=DEFAULT_SRC, help="каталог dict/frontend репо betting/BackEnd")
    ap.add_argument("--langs", default="ru,en,fr,ht")
    ap.add_argument("--out", default=OUT_DIR)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    for ln in [x.strip() for x in a.langs.split(",") if x.strip()]:
        src = os.path.join(a.src, ln + ".ini")
        if not os.path.exists(src):
            print(f"{ln}: нет файла {src}")
            continue
        d = parse_ini(src)
        dst = os.path.join(a.out, ln + ".json")
        with io.open(dst, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        print(f"{ln}: " + ", ".join(f"{k} {len(v)}" for k, v in d.items()) + f" -> {os.path.relpath(dst)}")


if __name__ == "__main__":
    main()
