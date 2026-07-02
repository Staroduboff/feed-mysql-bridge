-- =============================================================================
-- feed-mysql-bridge  —  Database Schema
-- =============================================================================
-- Conventions:
--   • Every table has a surrogate UINT32 AUTO_INCREMENT PK (id) used for FKs.
--   • The original feed identifier (hash or numeric) is stored in a separate
--     indexed column (feed_hash / feed_id) used for upsert / lookup by hash.
--   • Rows are never physically deleted. removed=1 means the feed marked the
--     object as gone. All application queries must add WHERE removed=0.
--   • dv (data version) is used to discard stale AMQP updates:
--     skip the update if incoming dv <= stored dv.
-- =============================================================================

CREATE DATABASE IF NOT EXISTS feed_bridge
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE feed_bridge;

-- -----------------------------------------------------------------------------
-- sports   source: s:{sportId}
-- -----------------------------------------------------------------------------
CREATE TABLE sports (
    id          SMALLINT UNSIGNED   NOT NULL AUTO_INCREMENT,
    feed_id     SMALLINT UNSIGNED   NOT NULL,   -- numeric sportId (1, 3, 7 …)
    name_en     VARCHAR(100)        NOT NULL DEFAULT '',
    name_ru     VARCHAR(100)        NOT NULL DEFAULT '',
    dv          INT UNSIGNED        NOT NULL DEFAULT 0,

    PRIMARY KEY (id),
    UNIQUE KEY  uq_feed_id (feed_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- -----------------------------------------------------------------------------
-- categories   source: c:{sportId}:{catId}
-- -----------------------------------------------------------------------------
CREATE TABLE categories (
    id          INT UNSIGNED        NOT NULL AUTO_INCREMENT,
    feed_hash   VARCHAR(64)         NOT NULL,   -- catId hex hash
    sport_id    SMALLINT UNSIGNED   NOT NULL,
    name_en     VARCHAR(200)        NOT NULL DEFAULT '',
    name_ru     VARCHAR(200)        NOT NULL DEFAULT '',
    dv          INT UNSIGNED        NOT NULL DEFAULT 0,
    ts          DATETIME            NOT NULL,
    removed     TINYINT(1)          NOT NULL DEFAULT 0,

    PRIMARY KEY (id),
    UNIQUE KEY  uq_feed_hash  (feed_hash),
    KEY         idx_sport     (sport_id),
    CONSTRAINT  fk_cat_sport  FOREIGN KEY (sport_id) REFERENCES sports(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- -----------------------------------------------------------------------------
-- tournaments   source: t:{sportId}:{catId}:{trnId}
-- -----------------------------------------------------------------------------
CREATE TABLE tournaments (
    id          INT UNSIGNED        NOT NULL AUTO_INCREMENT,
    feed_hash   VARCHAR(64)         NOT NULL,   -- trnId hex hash
    sport_id    SMALLINT UNSIGNED   NOT NULL,
    category_id INT UNSIGNED        NOT NULL,
    name_en     VARCHAR(200)        NOT NULL DEFAULT '',
    name_ru     VARCHAR(200)        NOT NULL DEFAULT '',
    dv          INT UNSIGNED        NOT NULL DEFAULT 0,
    ts          DATETIME            NOT NULL,
    cts         DATETIME            NOT NULL,
    removed     TINYINT(1)          NOT NULL DEFAULT 0,

    PRIMARY KEY (id),
    UNIQUE KEY  uq_feed_hash  (feed_hash),
    KEY         idx_sport     (sport_id),
    KEY         idx_category  (category_id),
    CONSTRAINT  fk_trn_sport  FOREIGN KEY (sport_id)    REFERENCES sports(id),
    CONSTRAINT  fk_trn_cat    FOREIGN KEY (category_id) REFERENCES categories(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- -----------------------------------------------------------------------------
-- competitors   source: v:{sportId}:{compId}
-- -----------------------------------------------------------------------------
CREATE TABLE competitors (
    id          INT UNSIGNED        NOT NULL AUTO_INCREMENT,
    feed_hash   VARCHAR(64)         NOT NULL,   -- compId hex hash
    sport_id    SMALLINT UNSIGNED   NOT NULL,
    name_en     VARCHAR(200)        NOT NULL DEFAULT '',
    name_ru     VARCHAR(200)        NOT NULL DEFAULT '',
    dv          INT UNSIGNED        NOT NULL DEFAULT 0,
    ts          DATETIME            NOT NULL,
    removed     TINYINT(1)          NOT NULL DEFAULT 0,

    PRIMARY KEY (id),
    UNIQUE KEY  uq_feed_hash   (feed_hash),
    KEY         idx_sport      (sport_id),
    CONSTRAINT  fk_comp_sport  FOREIGN KEY (sport_id) REFERENCES sports(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- -----------------------------------------------------------------------------
-- events   source: e:{sportId}:{catId}:{trnId}:{evId}
-- -----------------------------------------------------------------------------
CREATE TABLE events (
    id              INT UNSIGNED        NOT NULL AUTO_INCREMENT,
    feed_id         BIGINT UNSIGNED     NOT NULL,   -- numeric evId (e.g. 15932627)
    sport_id        SMALLINT UNSIGNED   NOT NULL,
    category_id     INT UNSIGNED        NOT NULL,
    tournament_id   INT UNSIGNED        NOT NULL,
    name_en         VARCHAR(300)        NOT NULL DEFAULT '',
    name_ru         VARCHAR(300)        NOT NULL DEFAULT '',
    sname_en        VARCHAR(300)        NOT NULL DEFAULT '',
    sname_ru        VARCHAR(300)        NOT NULL DEFAULT '',
    start_time      DATETIME            NOT NULL,
    stage           TINYINT UNSIGNED    NOT NULL DEFAULT 0,   -- 0=Unknown 1=Prematch 2=Live
    stagev2         VARCHAR(20)         NOT NULL DEFAULT '',
    status          TINYINT UNSIGNED    NOT NULL DEFAULT 0,   -- 0=Created 1=Open 2=Suspended 3=Ended
    statusv2        VARCHAR(20)         NOT NULL DEFAULT '',
    score           JSON                         DEFAULT NULL,
    dv              INT UNSIGNED        NOT NULL DEFAULT 0,
    sdv             INT UNSIGNED        NOT NULL DEFAULT 0,
    uts             DATETIME            NOT NULL,
    removed         TINYINT(1)          NOT NULL DEFAULT 0,

    PRIMARY KEY (id),
    UNIQUE KEY  uq_feed_id      (feed_id),
    KEY         idx_sport       (sport_id),
    KEY         idx_category    (category_id),
    KEY         idx_tournament  (tournament_id),
    KEY         idx_live        (stage, status, removed),   -- WHERE stage=2 AND removed=0
    CONSTRAINT  fk_ev_sport     FOREIGN KEY (sport_id)      REFERENCES sports(id),
    CONSTRAINT  fk_ev_cat       FOREIGN KEY (category_id)   REFERENCES categories(id),
    CONSTRAINT  fk_ev_trn       FOREIGN KEY (tournament_id) REFERENCES tournaments(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- -----------------------------------------------------------------------------
-- markets   source: m:{evId}:{mktId}
-- -----------------------------------------------------------------------------
CREATE TABLE markets (
    id              INT UNSIGNED        NOT NULL AUTO_INCREMENT,
    feed_hash       VARCHAR(64)         NOT NULL,   -- mktId hex hash (хэш ШАБЛОНА маркета, ОБЩИЙ для разных событий)
    event_id        INT UNSIGNED        NOT NULL,
    market_type     SMALLINT UNSIGNED   NOT NULL,
    period          TINYINT UNSIGNED    NOT NULL DEFAULT 0,
    name_en         VARCHAR(200)        NOT NULL DEFAULT '',
    name_ru         VARCHAR(200)        NOT NULL DEFAULT '',
    period_name_en  VARCHAR(100)        NOT NULL DEFAULT '',
    period_name_ru  VARCHAR(100)        NOT NULL DEFAULT '',
    value           VARCHAR(50)         NOT NULL DEFAULT '',
    open            TINYINT(1)          NOT NULL DEFAULT 0,
    removed         TINYINT(1)          NOT NULL DEFAULT 0,
    ver             INT UNSIGNED        NOT NULL DEFAULT 0,
    rver            INT UNSIGNED        NOT NULL DEFAULT 0,
    uts             DATETIME            NOT NULL,
    first_seen      DATETIME                     DEFAULT NULL,   -- когда маркет впервые появился в фиде (NOW() при первом INSERT)

    PRIMARY KEY (id),
    -- feed_hash маркета ОБЩИЙ для разных событий → маркет уникален парой (event_id, feed_hash).
    -- Уникальность по одному feed_hash схлопывала бы маркеты разных событий в одну строку.
    UNIQUE KEY  uq_event_feed_hash (event_id, feed_hash),
    KEY         idx_type_period (event_id, market_type, period),
    CONSTRAINT  fk_mkt_event    FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- -----------------------------------------------------------------------------
-- outcomes   source: o:{evId}:{mktId}:{ocId}
-- -----------------------------------------------------------------------------
CREATE TABLE outcomes (
    id              INT UNSIGNED        NOT NULL AUTO_INCREMENT,
    feed_hash       VARCHAR(128)        NOT NULL,   -- ocId (hash или иной формат)
    market_id       INT UNSIGNED        NOT NULL,
    event_id        INT UNSIGNED        NOT NULL,   -- денормализация для быстрых JOIN
    outcome_type    SMALLINT UNSIGNED   NOT NULL,
    name_en         VARCHAR(100)        NOT NULL DEFAULT '',
    name_ru         VARCHAR(100)        NOT NULL DEFAULT '',
    value           VARCHAR(50)         NOT NULL DEFAULT '',
    price           DECIMAL(10, 4)               DEFAULT NULL,
    status          TINYINT UNSIGNED    NOT NULL DEFAULT 0,   -- 1=Open 2=Suspended 3=Blocked 4=Resulted
    result          TINYINT UNSIGNED    NOT NULL DEFAULT 0,   -- 0=None 1=Win 2=Loss 3=Return 4=HalfWin 5=HalfLoss
    cancelled       TINYINT(1)          NOT NULL DEFAULT 0,
    removed         TINYINT(1)          NOT NULL DEFAULT 0,
    ver             INT UNSIGNED        NOT NULL DEFAULT 0,
    rver            INT UNSIGNED        NOT NULL DEFAULT 0,
    uts             DATETIME            NOT NULL,
    first_seen      DATETIME                     DEFAULT NULL,   -- когда исход впервые появился в фиде (NOW() при первом INSERT)
    resulted_at     DATETIME                     DEFAULT NULL,   -- когда исход рассчитан (status стал 4), NOW() при первом переходе

    PRIMARY KEY (id),
    -- feed_hash исхода (ocId) ОБЩИЙ для разных событий/маркетов → уникален парой (market_id, feed_hash).
    UNIQUE KEY  uq_market_feed_hash (market_id, feed_hash),
    KEY         idx_event     (event_id),
    CONSTRAINT  fk_oc_market  FOREIGN KEY (market_id) REFERENCES markets(id) ON DELETE CASCADE,
    CONSTRAINT  fk_oc_event   FOREIGN KEY (event_id)  REFERENCES events(id)  ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- -----------------------------------------------------------------------------
-- market_type_names   source: sm:{sportId}:{marketType}
-- -----------------------------------------------------------------------------
CREATE TABLE market_type_names (
    id              INT UNSIGNED        NOT NULL AUTO_INCREMENT,
    sport_id        SMALLINT UNSIGNED   NOT NULL,
    market_type     SMALLINT UNSIGNED   NOT NULL,
    name_en         VARCHAR(200)        NOT NULL DEFAULT '',
    name_ru         VARCHAR(200)        NOT NULL DEFAULT '',
    outcomes        JSON                NOT NULL,   -- [int, …]  outcome type codes
    outcome_names   JSON                NOT NULL,   -- [str, …]  outcome type labels

    PRIMARY KEY (id),
    UNIQUE KEY  uq_sport_type   (sport_id, market_type),
    CONSTRAINT  fk_mtn_sport    FOREIGN KEY (sport_id) REFERENCES sports(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
