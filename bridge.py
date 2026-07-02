#!/usr/bin/env python3
"""
bridge.py — точка входа Feed → MySQL Bridge.

Назначение
----------
Тонкая обёртка над пакетом feedbridge: разбирает аргументы командной строки,
загружает конфигурацию, открывает соединения и запускает нужные режимы.
Вся логика вынесена в модули пакета (см. feedbridge/__init__.py).

Режимы запуска
--------------
    python bridge.py              # снапшот → затем слушать поток (по умолчанию)
    python bridge.py --snapshot   # только снапшот, затем выход
    python bridge.py --listen     # без снапшота, сразу слушать поток

Логика выбора режима:
    do_snapshot = не указан --listen
    do_listen   = не указан --snapshot
В режиме --listen (без снапшота) перед прослушиванием прогреваются ID-кэши.

Требования: pip install redis PyMySQL pika

Функция
-------
main()  Разбор аргументов, загрузка конфига, подключения и запуск режимов.
"""

import argparse
import sys

from feedbridge.config import load_config
from feedbridge.console import C
from feedbridge.core import Bridge
from feedbridge.listener import Listener
from feedbridge.snapshot import Snapshotter


def main() -> None:
    """Разобрать аргументы, подключиться и запустить снапшот и/или листенер."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Feed → MySQL Bridge",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "  python bridge.py             # snapshot → listen\n"
            "  python bridge.py --snapshot  # snapshot only\n"
            "  python bridge.py --listen    # listen only (no snapshot)\n"
        ),
    )
    parser.add_argument("--snapshot", action="store_true",
                        help="Выполнить только снапшот и выйти")
    parser.add_argument("--listen", action="store_true",
                        help="Пропустить снапшот, сразу слушать поток AMQP")
    args = parser.parse_args()

    cfg = load_config()

    print(f"\n{C.BOLD}  Feed → MySQL Bridge{C.RESET}")

    bridge = Bridge(cfg)
    try:
        bridge.connect_redis()
        bridge.connect_mysql()
    except Exception as e:
        print(f"  {C.RED}Ошибка подключения: {e}{C.RESET}")
        sys.exit(1)

    do_snapshot = not args.listen
    do_listen   = not args.snapshot

    if do_snapshot:
        Snapshotter(bridge).run()
    if do_listen:
        if not do_snapshot:
            bridge.warm_caches()
        Listener(bridge).run()


if __name__ == "__main__":
    main()
