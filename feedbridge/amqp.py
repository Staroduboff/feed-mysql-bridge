"""
amqp.py — работа с RabbitMQ.

Назначение
----------
Собирает всё взаимодействие с RabbitMQ в одном месте: построение параметров
подключения (pika), запрос глубины очереди через HTTP Management API и очистку
очереди перед снапшотом. Так листенер и снапшот не дублируют низкоуровневые детали
AMQP. Проверка наличия библиотеки pika выполняется здесь.

Почему Management API для глубины очереди
-----------------------------------------
У внешнего клиента нет права configure на очереди, поэтому пассивный
queue_declare возвращает 403. Счётчик сообщений берём по HTTP из Management API
(порт 15672), где достаточно прав на чтение.

Функции
-------
connection_params(rc, *, heartbeat=None, connection_attempts=1)
    Собирает pika.ConnectionParameters из секции rabbitmq конфигурации.
queue_depth(rc)
    Возвращает число сообщений в очереди (HTTP Management API).
purge_queue(rc)
    Очищает очередь и возвращает количество удалённых сообщений (AMQP).
"""

import base64
import json
import sys
import urllib.request

try:
    import pika
except ImportError:
    print("pika не установлен: pip install pika")
    sys.exit(1)


def connection_params(rc: dict, *, heartbeat=None, connection_attempts: int = 1):
    """Построить pika.ConnectionParameters из секции 'rabbitmq' конфигурации.

    heartbeat — интервал heartbeat (для долгоживущего листенера);
    connection_attempts — число попыток на одно подключение.
    """
    creds = pika.PlainCredentials(rc["username"], rc["password"])
    kwargs = dict(
        host=rc["host"], port=rc.get("port", 5672),
        virtual_host=rc.get("vhost", "/"),
        credentials=creds,
        connection_attempts=connection_attempts,
        # Не зависать бесконечно, если брокер встал в flow-control (memory/disk alarm)
        # — это особенно важно для долгого queue_purge на раздутой очереди.
        blocked_connection_timeout=60,
        socket_timeout=15,
    )
    if heartbeat is not None:
        kwargs["heartbeat"] = heartbeat
    return pika.ConnectionParameters(**kwargs)


def queue_depth(rc: dict) -> int:
    """Текущее число сообщений в очереди через RabbitMQ Management API (HTTP)."""
    mgmt  = rc.get("management_url", "").rstrip("/")
    vhost = urllib.request.quote(rc.get("vhost", "/"), safe="")
    queue = urllib.request.quote(rc["queue"], safe="")
    url   = f"{mgmt}/api/queues/{vhost}/{queue}"
    token = base64.b64encode(f"{rc['username']}:{rc['password']}".encode()).decode()
    req   = urllib.request.Request(url, headers={"Authorization": f"Basic {token}"})
    with urllib.request.urlopen(req, timeout=3) as resp:
        return json.loads(resp.read())["messages"]


def purge_queue(rc: dict) -> int:
    """Очистить очередь от всех ожидающих сообщений; вернуть число удалённых."""
    params  = connection_params(rc, connection_attempts=3)
    conn    = pika.BlockingConnection(params)
    channel = conn.channel()
    result  = channel.queue_purge(queue=rc["queue"])
    count   = result.method.message_count
    conn.close()
    return count


def connect(rc: dict, *, heartbeat=None, connection_attempts: int = 1):
    """Открыть BlockingConnection к RabbitMQ и вернуть её (для листенера)."""
    return pika.BlockingConnection(
        connection_params(rc, heartbeat=heartbeat, connection_attempts=connection_attempts)
    )
