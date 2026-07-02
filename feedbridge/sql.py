"""
sql.py — SQL-операторы вставки/обновления для всех таблиц фида.

Назначение
----------
Каждый объект фида (вид спорта, категория, турнир, участник, событие, маркет,
исход, имя типа маркета) пишется одним и тем же оператором и при снапшоте
(пакетно через executemany), и при инкрементальном обновлении из AMQP
(по одной строке). Чтобы оба пути использовали идентичный SQL, операторы
вынесены сюда как именованные константы.

Защита версией (version guard)
------------------------------
Обновления из фида могут приходить не по порядку. Чтобы старое сообщение не
затёрло более свежие данные, каждый UPDATE применяет поле только если входящая
версия не ниже сохранённой:
    dv-guard   — для справочников и событий: пишем, если VALUES(dv)  >= dv;
    ver-guard  — для маркетов и исходов:     пишем, если VALUES(ver) >= ver.

Константы
---------
SQL_SPORT   upsert в sports             (dv-guard)
SQL_CAT     upsert в categories         (dv-guard)
SQL_TRN     upsert в tournaments        (dv-guard)
SQL_COMP    upsert в competitors        (dv-guard)
SQL_MTN     upsert в market_type_names  (без guard — справочник имён)
SQL_EVENT   upsert в events             (dv-guard)
SQL_MKT     upsert в markets            (ver-guard)
SQL_OC      upsert в outcomes           (ver-guard)
TABLES      Порядок очистки таблиц для TRUNCATE (обратный порядку внешних ключей).
"""

# Порядок очистки таблиц при снапшоте: от зависимых к родительским,
# чтобы не нарушать внешние ключи (FK-проверки на время отключаются).
TABLES = [
    "outcomes", "markets", "events",
    "competitors", "tournaments", "categories",
    "market_type_names", "sports",
]


SQL_SPORT = """
INSERT INTO sports (feed_id, name_en, name_ru, dv)
VALUES (%s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
    name_en = IF(VALUES(dv)>=dv, VALUES(name_en), name_en),
    name_ru = IF(VALUES(dv)>=dv, VALUES(name_ru), name_ru),
    dv      = IF(VALUES(dv)>=dv, VALUES(dv),      dv)
"""

SQL_CAT = """
INSERT INTO categories (feed_hash, sport_id, name_en, name_ru, dv, ts, removed)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
    sport_id = IF(VALUES(dv)>=dv, VALUES(sport_id), sport_id),
    name_en  = IF(VALUES(dv)>=dv, VALUES(name_en),  name_en),
    name_ru  = IF(VALUES(dv)>=dv, VALUES(name_ru),  name_ru),
    ts       = IF(VALUES(dv)>=dv, VALUES(ts),       ts),
    removed  = IF(VALUES(dv)>=dv, VALUES(removed),  removed),
    dv       = IF(VALUES(dv)>=dv, VALUES(dv),       dv)
"""

SQL_TRN = """
INSERT INTO tournaments
    (feed_hash, sport_id, category_id, name_en, name_ru, dv, ts, cts, removed)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
    sport_id    = IF(VALUES(dv)>=dv, VALUES(sport_id),    sport_id),
    category_id = IF(VALUES(dv)>=dv, VALUES(category_id), category_id),
    name_en     = IF(VALUES(dv)>=dv, VALUES(name_en),     name_en),
    name_ru     = IF(VALUES(dv)>=dv, VALUES(name_ru),     name_ru),
    ts          = IF(VALUES(dv)>=dv, VALUES(ts),          ts),
    cts         = IF(VALUES(dv)>=dv, VALUES(cts),         cts),
    removed     = IF(VALUES(dv)>=dv, VALUES(removed),     removed),
    dv          = IF(VALUES(dv)>=dv, VALUES(dv),          dv)
"""

SQL_COMP = """
INSERT INTO competitors (feed_hash, sport_id, name_en, name_ru, dv, ts, removed)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
    sport_id = IF(VALUES(dv)>=dv, VALUES(sport_id), sport_id),
    name_en  = IF(VALUES(dv)>=dv, VALUES(name_en),  name_en),
    name_ru  = IF(VALUES(dv)>=dv, VALUES(name_ru),  name_ru),
    ts       = IF(VALUES(dv)>=dv, VALUES(ts),       ts),
    removed  = IF(VALUES(dv)>=dv, VALUES(removed),  removed),
    dv       = IF(VALUES(dv)>=dv, VALUES(dv),       dv)
"""

SQL_MTN = """
INSERT INTO market_type_names
    (sport_id, market_type, name_en, name_ru, outcomes, outcome_names)
VALUES (%s, %s, %s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
    name_en       = VALUES(name_en),
    name_ru       = VALUES(name_ru),
    outcomes      = VALUES(outcomes),
    outcome_names = VALUES(outcome_names)
"""

SQL_EVENT = """
INSERT INTO events
    (feed_id, sport_id, category_id, tournament_id,
     name_en, name_ru, sname_en, sname_ru,
     start_time, stage, stagev2, status, statusv2,
     score, dv, sdv, uts, removed)
VALUES (%s,%s,%s,%s, %s,%s,%s,%s, %s,%s,%s,%s,%s, %s,%s,%s,%s,%s)
ON DUPLICATE KEY UPDATE
    sport_id      = IF(VALUES(dv)>=dv, VALUES(sport_id),      sport_id),
    category_id   = IF(VALUES(dv)>=dv, VALUES(category_id),   category_id),
    tournament_id = IF(VALUES(dv)>=dv, VALUES(tournament_id), tournament_id),
    name_en       = IF(VALUES(dv)>=dv, VALUES(name_en),       name_en),
    name_ru       = IF(VALUES(dv)>=dv, VALUES(name_ru),       name_ru),
    sname_en      = IF(VALUES(dv)>=dv, VALUES(sname_en),      sname_en),
    sname_ru      = IF(VALUES(dv)>=dv, VALUES(sname_ru),      sname_ru),
    start_time    = IF(VALUES(dv)>=dv, VALUES(start_time),    start_time),
    stage         = IF(VALUES(dv)>=dv, VALUES(stage),         stage),
    stagev2       = IF(VALUES(dv)>=dv, VALUES(stagev2),       stagev2),
    status        = IF(VALUES(dv)>=dv, VALUES(status),        status),
    statusv2      = IF(VALUES(dv)>=dv, VALUES(statusv2),      statusv2),
    score         = IF(VALUES(dv)>=dv, VALUES(score),         score),
    sdv           = IF(VALUES(dv)>=dv, VALUES(sdv),           sdv),
    uts           = IF(VALUES(dv)>=dv, VALUES(uts),           uts),
    removed       = IF(VALUES(dv)>=dv, VALUES(removed),       removed),
    dv            = IF(VALUES(dv)>=dv, VALUES(dv),            dv)
"""

SQL_MKT = """
INSERT INTO markets
    (feed_hash, event_id, market_type, period,
     name_en, name_ru, period_name_en, period_name_ru,
     value, open, removed, ver, rver, uts, first_seen)
VALUES (%s,%s,%s,%s, %s,%s,%s,%s, %s,%s,%s,%s,%s,%s, NOW())
ON DUPLICATE KEY UPDATE
    event_id       = IF(VALUES(ver)>=ver, VALUES(event_id),       event_id),
    market_type    = IF(VALUES(ver)>=ver, VALUES(market_type),    market_type),
    period         = IF(VALUES(ver)>=ver, VALUES(period),         period),
    name_en        = IF(VALUES(ver)>=ver, VALUES(name_en),        name_en),
    name_ru        = IF(VALUES(ver)>=ver, VALUES(name_ru),        name_ru),
    period_name_en = IF(VALUES(ver)>=ver, VALUES(period_name_en), period_name_en),
    period_name_ru = IF(VALUES(ver)>=ver, VALUES(period_name_ru), period_name_ru),
    value          = IF(VALUES(ver)>=ver, VALUES(value),          value),
    open           = IF(VALUES(ver)>=ver, VALUES(open),           open),
    removed        = IF(VALUES(ver)>=ver, VALUES(removed),        removed),
    rver           = IF(VALUES(ver)>=ver, VALUES(rver),           rver),
    uts            = IF(VALUES(ver)>=ver, VALUES(uts),            uts),
    ver            = IF(VALUES(ver)>=ver, VALUES(ver),            ver)
"""

SQL_OC = """
INSERT INTO outcomes
    (feed_hash, market_id, event_id, outcome_type,
     name_en, name_ru, value, price,
     status, result, cancelled, removed, ver, rver, uts, first_seen, resulted_at)
VALUES (%s,%s,%s,%s, %s,%s,%s,%s, %s,%s,%s,%s,%s,%s,%s, NOW(), NULL)
ON DUPLICATE KEY UPDATE
    market_id    = IF(VALUES(ver)>=ver, VALUES(market_id),    market_id),
    event_id     = IF(VALUES(ver)>=ver, VALUES(event_id),     event_id),
    outcome_type = IF(VALUES(ver)>=ver, VALUES(outcome_type), outcome_type),
    name_en      = IF(VALUES(ver)>=ver, VALUES(name_en),      name_en),
    name_ru      = IF(VALUES(ver)>=ver, VALUES(name_ru),      name_ru),
    value        = IF(VALUES(ver)>=ver, VALUES(value),        value),
    price        = IF(VALUES(ver)>=ver, VALUES(price),        price),
    status       = IF(VALUES(ver)>=ver, VALUES(status),       status),
    result       = IF(VALUES(ver)>=ver, VALUES(result),       result),
    cancelled    = IF(VALUES(ver)>=ver, VALUES(cancelled),    cancelled),
    removed      = IF(VALUES(ver)>=ver, VALUES(removed),      removed),
    rver         = IF(VALUES(ver)>=ver, VALUES(rver),         rver),
    uts          = IF(VALUES(ver)>=ver, VALUES(uts),          uts),
    resulted_at  = IF(VALUES(ver)>=ver AND VALUES(status)=4 AND resulted_at IS NULL, NOW(), resulted_at),
    ver          = IF(VALUES(ver)>=ver, VALUES(ver),          ver)
    -- first_seen намеренно НЕ обновляется: фиксирует момент первого INSERT
"""
