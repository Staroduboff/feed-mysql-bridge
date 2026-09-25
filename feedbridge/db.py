"""
db.py — подключение к MySQL.

Назначение
----------
Единственная точка создания соединения с MySQL (через PyMySQL). Вынесена
отдельно, потому что соединение нужно и мосту (bridge.py, autocommit=False —
транзакции при снапшоте и применении обновлений), и диагностической утилите
(db_info.py, autocommit=True — только чтение). Здесь же лежит «дружелюбная»
проверка наличия библиотеки PyMySQL.

Функции
-------
mysql_connect(cfg, autocommit=False)
    Открывает соединение по параметрам из cfg['mysql'] и возвращает его.
    DictCursor — строки приходят как dict. autocommit управляет режимом
    транзакций (False для записи, True для разовых чтений).
"""

import sys

try:
    import pymysql
    import pymysql.cursors
except ImportError:
    print("PyMySQL не установлен: pip install PyMySQL")
    sys.exit(1)


def mysql_connect(cfg: dict, autocommit: bool = False):
    """Открыть соединение с MySQL по параметрам cfg['mysql'].

    autocommit=False — для моста (явные commit/rollback);
    autocommit=True  — для разовых чтений (db_info).
    Курсор — DictCursor (строки как dict).
    """
    mc = cfg["mysql"]
    return pymysql.connect(
        host=mc["host"], port=mc.get("port", 3306),
        user=mc["user"], password=mc["password"],
        database=mc["database"], charset=mc.get("charset", "utf8mb4"),
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=autocommit,
        # Таймауты сокета: без них мёртвое соединение (рестарт/failover MySQL, обрыв
        # сети) висит бесконечно вместо того, чтобы дать ошибку, которую листенер
        # ловит и переподключается. read_timeout щедрый — снапшот делает долгие запросы.
        connect_timeout=mc.get("connect_timeout", 10),
        read_timeout=mc.get("read_timeout", 600),
        write_timeout=mc.get("write_timeout", 600),
    )
