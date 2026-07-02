"""
config.py — настройки и загрузка конфигурации.

Назначение
----------
Единая точка для всех «магических чисел» проекта и для чтения `config.json`.
Если нужно изменить размер пакета записи в MySQL или параметры RabbitMQ —
правки делаются здесь, а не разбросаны по коду.

Константы
---------
PROJECT_DIR        Корень проекта (на уровень выше пакета feedbridge).
CONFIG_FILE        Путь к config.json в корне проекта.
REDIS_SCAN_COUNT   Подсказка размера пачки для команды Redis SCAN.
SQL_BATCH          Сколько строк отправлять в одном executemany.
FLUSH_EVERY        Через сколько накопленных строк делать commit (большие таблицы).
AMQP_PREFETCH      Сколько AMQP-сообщений RabbitMQ выдаёт до подтверждения (QoS).
AMQP_RETRY_DELAY   Базовая пауза (сек) между попытками переподключения к RabbitMQ.

Функции
-------
load_config()      Читает и возвращает config.json как dict; завершает процесс
                   с понятной ошибкой, если файл отсутствует.
"""

import json
import sys
from pathlib import Path

# Корень проекта = родитель каталога пакета feedbridge/
PROJECT_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = PROJECT_DIR / "config.json"

# Параметры пакетной загрузки Redis → MySQL
REDIS_SCAN_COUNT = 2000   # подсказка для Redis SCAN
SQL_BATCH        = 500    # строк на один executemany
FLUSH_EVERY      = 5000   # commit после N накопленных строк (большие таблицы)

# Параметры AMQP-листенера
AMQP_PREFETCH    = 200    # prefetch QoS
AMQP_RETRY_DELAY = 5      # базовая пауза (сек) между переподключениями


def load_config() -> dict:
    """Прочитать config.json из корня проекта и вернуть как dict.

    При отсутствии файла печатает сообщение и завершает процесс (exit 1).
    """
    if not CONFIG_FILE.exists():
        print(f"config.json не найден: {CONFIG_FILE}")
        sys.exit(1)
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
    except json.JSONDecodeError as e:
        print(f"config.json повреждён (невалидный JSON): {e}")
        sys.exit(1)
    for sect in ("mysql", "redis", "rabbitmq"):
        if sect not in cfg:
            print(f"config.json: отсутствует обязательная секция '{sect}'")
            sys.exit(1)
    return cfg
