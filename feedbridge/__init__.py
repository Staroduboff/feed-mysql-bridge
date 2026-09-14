"""
feedbridge — пакет Feed → MySQL Bridge.

Загружает снимок (snapshot) из Redis в MySQL и поддерживает данные в актуальном
состоянии, потребляя поток обновлений из RabbitMQ (AMQP).

Карта модулей
-------------
    config      Тюнинг-константы и загрузка config.json.
    console     ANSI-цвета и помощники форматирования/прогресса для консоли.
    transform   Конвертеры значений Redis-JSON → значения для MySQL.
    sql         Операторы INSERT … ON DUPLICATE KEY UPDATE (с защитой версией).
    db          Подключение к MySQL (только PyMySQL).
    amqp        Подключение к RabbitMQ, глубина и очистка очереди (только pika).
    core        Bridge — общие соединения, кэши и разрешение ID.
    snapshot    Snapshotter — полная загрузка Redis → MySQL.
    listener    Listener — инкрементальные обновления AMQP → MySQL.

Примечание
----------
Подмодули НЕ импортируются здесь намеренно: так утилиты, которым нужна лишь часть
пакета (например db_info.py — только config/console/db), не тянут зависимости
redis и pika. Импортируйте из подмодулей напрямую, например:

    from feedbridge.core import Bridge
    from feedbridge.config import load_config
"""
