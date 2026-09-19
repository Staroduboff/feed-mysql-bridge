#!/usr/bin/env python3
"""
db_info.py — quick overview of feed_bridge database tables.

Shows row counts, disk usage (data + index), and per-table breakdowns
for tables that benefit from it (events by stage, markets/outcomes by status).

Usage:
    python db_info.py

Requires: pip install PyMySQL
"""

import sys
from datetime import datetime

from feedbridge.config import load_config
from feedbridge.console import C, fmt_mb, fmt_rows
from feedbridge.db import mysql_connect

# Short description shown in the table header
TABLE_DESC = {
    "sports":            "Виды спорта  (s:*)",
    "categories":        "Категории  (c:*)",
    "tournaments":       "Турниры  (t:*)",
    "competitors":       "Участники  (v:*)",
    "events":            "События  (e:*)",
    "markets":           "Маркеты  (m:*)",
    "outcomes":          "Исходы  (o:*)",
    "market_type_names": "Названия типов маркетов  (sm:*)",
}

TABLE_ORDER = list(TABLE_DESC.keys())

# Цвета (C), форматирование (fmt_mb/fmt_rows), загрузка конфига и подключение к
# MySQL берутся из общего пакета feedbridge — см. импорты выше.

# ── data fetchers ──────────────────────────────────────────────────────────────

def fetch_table_stats(cur, db_name: str) -> dict:
    """Returns {table_name: {rows, data_bytes, index_bytes}} from information_schema."""
    cur.execute("""
        SELECT TABLE_NAME,
               TABLE_ROWS,
               DATA_LENGTH,
               INDEX_LENGTH
        FROM   information_schema.TABLES
        WHERE  TABLE_SCHEMA = %s
          AND  TABLE_TYPE   = 'BASE TABLE'
    """, (db_name,))
    return {
        row["TABLE_NAME"]: {
            "rows":        row["TABLE_ROWS"]  or 0,
            "data_bytes":  row["DATA_LENGTH"] or 0,
            "index_bytes": row["INDEX_LENGTH"] or 0,
        }
        for row in cur.fetchall()
    }

def fetch_event_breakdown(cur) -> dict:
    cur.execute("""
        SELECT
            SUM(stage = 2 AND removed = 0)  AS live,
            SUM(stage = 1 AND removed = 0)  AS prematch,
            SUM(stage = 0 AND removed = 0)  AS unknown,
            SUM(removed = 1)                AS removed_cnt,
            COUNT(*)                        AS total
        FROM events
    """)
    return cur.fetchone() or {}

def fetch_market_breakdown(cur) -> dict:
    cur.execute("""
        SELECT
            SUM(open = 1  AND removed = 0)  AS open_cnt,
            SUM(open = 0  AND removed = 0)  AS closed_cnt,
            SUM(removed = 1)                AS removed_cnt,
            COUNT(*)                        AS total
        FROM markets
    """)
    return cur.fetchone() or {}

def fetch_outcome_breakdown(cur) -> dict:
    cur.execute("""
        SELECT
            SUM(status = 1 AND removed = 0)  AS open_cnt,
            SUM(status = 2 AND removed = 0)  AS suspended_cnt,
            SUM(status = 4 AND removed = 0)  AS resulted_cnt,
            SUM(removed = 1)                 AS removed_cnt,
            COUNT(*)                         AS total
        FROM outcomes
    """)
    return cur.fetchone() or {}

def fetch_server_version(cur) -> str:
    cur.execute("SELECT VERSION() AS v")
    row = cur.fetchone()
    return row["v"] if row else "?"

# ── rendering ──────────────────────────────────────────────────────────────────

def print_main_table(stats: dict) -> None:
    W  = 22   # table name column
    RW =  9   # rows column
    MW =  9   # MB column

    header = (
        f"  {'Таблица':<{W}}"
        f"{'Строк':>{RW}}"
        f"{'Данные':>{MW}}"
        f"{'Индексы':>{MW}}"
        f"{'Итого':>{MW}}"
        f"  Содержимое"
    )
    sep = "  " + "─" * (W + RW + MW * 3 + 2 + 36)

    print(f"\n{C.BOLD}{C.WHITE}{header}{C.RESET}")
    print(f"{C.GRAY}{sep}{C.RESET}")

    total_rows = total_data = total_index = 0

    for tname in TABLE_ORDER:
        s    = stats.get(tname, {"rows": 0, "data_bytes": 0, "index_bytes": 0})
        rows = s["rows"]
        data = s["data_bytes"]
        idx  = s["index_bytes"]
        total_rows  += rows
        total_data  += data
        total_index += idx

        desc   = TABLE_DESC.get(tname, "")
        r_col  = fmt_rows(rows)
        d_col  = fmt_mb(data)
        i_col  = fmt_mb(idx)
        t_col  = fmt_mb(data + idx)

        row_color = C.WHITE if rows > 0 else C.GRAY
        print(
            f"  {C.CYAN}{tname:<{W}}{C.RESET}"
            f"{row_color}{r_col:>{RW}}{C.RESET}"
            f"{C.GRAY}{d_col:>{MW}}{C.RESET}"
            f"{C.GRAY}{i_col:>{MW}}{C.RESET}"
            f"{C.WHITE}{t_col:>{MW}}{C.RESET}"
            f"  {C.DIM}{desc}{C.RESET}"
        )

    print(f"{C.GRAY}{sep}{C.RESET}")
    print(
        f"  {C.BOLD}{'ИТОГО':<{W}}"
        f"{fmt_rows(total_rows):>{RW}}"
        f"{fmt_mb(total_data):>{MW}}"
        f"{fmt_mb(total_index):>{MW}}"
        f"{fmt_mb(total_data + total_index):>{MW}}{C.RESET}"
        f"  {C.DIM}МБ = мебибайты (1 МБ = 1 048 576 байт){C.RESET}"
    )


def print_breakdown(label: str, rows: list) -> None:
    print(f"\n  {C.BOLD}{C.WHITE}{label}{C.RESET}")
    for name, val, color in rows:
        n = int(val) if val is not None else 0
        print(f"    {color}{name:<28}{C.WHITE}{fmt_rows(n):>10}{C.RESET}")


# ── main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    cfg = load_config()
    db_name = cfg["mysql"]["database"]

    try:
        conn = mysql_connect(cfg, autocommit=True)
    except Exception as e:
        print(f"MySQL недоступен: {e}")
        sys.exit(1)

    with conn.cursor() as cur:
        version = fetch_server_version(cur)
        stats   = fetch_table_stats(cur, db_name)

        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        width = 80
        print(f"\n{C.BOLD}{'═' * width}")
        print(f"  {db_name}  ·  MariaDB/MySQL {version}  ·  {ts}")
        print(f"{'═' * width}{C.RESET}")

        print_main_table(stats)

        # Per-table breakdowns (only if table has rows)
        if stats.get("events", {}).get("rows", 0) > 0:
            ev = fetch_event_breakdown(cur)
            print_breakdown("События по стадиям:", [
                ("Live (stage=2)",          ev.get("live"),        C.GREEN),
                ("Prematch (stage=1)",       ev.get("prematch"),    C.CYAN),
                ("Unknown (stage=0)",        ev.get("unknown"),     C.GRAY),
                ("Помечены removed=1",       ev.get("removed_cnt"), C.GRAY),
            ])

        if stats.get("markets", {}).get("rows", 0) > 0:
            mk = fetch_market_breakdown(cur)
            print_breakdown("Маркеты:", [
                ("Открыты (open=1)",         mk.get("open_cnt"),    C.GREEN),
                ("Закрыты (open=0)",         mk.get("closed_cnt"),  C.YELLOW),
                ("Помечены removed=1",       mk.get("removed_cnt"), C.GRAY),
            ])

        if stats.get("outcomes", {}).get("rows", 0) > 0:
            oc = fetch_outcome_breakdown(cur)
            print_breakdown("Исходы:", [
                ("Открыты (status=1)",       oc.get("open_cnt"),       C.GREEN),
                ("Приостановлены (status=2)", oc.get("suspended_cnt"), C.YELLOW),
                ("Рассчитаны (status=4)",    oc.get("resulted_cnt"),   C.CYAN),
                ("Помечены removed=1",       oc.get("removed_cnt"),    C.GRAY),
            ])

    print()
    conn.close()


if __name__ == "__main__":
    main()
