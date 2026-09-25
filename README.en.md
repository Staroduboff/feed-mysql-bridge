# feed-mysql-bridge

[Русская версия](README.md)

A bridge that loads the sports data feed into your MySQL database and keeps it
up to date in real time.

The feed has two parts:

- **Redis** — the full current state of the line (a snapshot). Used for the
  initial load (cold start).
- **RabbitMQ** — a stream of incremental updates (AMQP) applied on top of the
  snapshot.

`feed-mysql-bridge` first loads the snapshot from Redis into MySQL, then listens
to the RabbitMQ queue and applies updates as they arrive.

---

## Requirements

- Python 3.7+
- Libraries: `pip install redis PyMySQL pika`
- A reachable MySQL/MariaDB database

---

## Setup

1. **Create the database and tables.** `schema.sql` creates the `feed_bridge`
   database itself (`CREATE DATABASE IF NOT EXISTS feed_bridge`) along with all
   tables. Run it as a MySQL user allowed to create databases:
   ```
   mysql -u root -p < schema.sql
   ```

2. **Create a MySQL user for the bridge** and grant it access to `feed_bridge`
   (or reuse an existing user with access to that database):
   ```sql
   CREATE USER 'feed_bridge'@'127.0.0.1' IDENTIFIED BY 'YOUR_PASSWORD';
   GRANT SELECT, INSERT, UPDATE, DELETE ON feed_bridge.* TO 'feed_bridge'@'127.0.0.1';
   FLUSH PRIVILEGES;
   ```

3. **Fill in `config.json`.** In this repository `config.json` already contains the
   **feed access credentials** issued to you (`redis`, `rabbitmq` and your personal
   `rabbitmq.queue`) — no need to change them. Only fill in the `mysql` section:
   replace the `<mysql-user>` / `<mysql-password>` placeholders with the user and
   password from step 2 (`host`, `port` and `database: feed_bridge` are preset):
   ```json
   "mysql": { "host": "127.0.0.1", "port": 3306,
              "user": "feed_bridge", "password": "YOUR_PASSWORD",
              "database": "feed_bridge", "charset": "utf8mb4" }
   ```
   > The full field template is in `config.example.json`. `redis`/`rabbitmq` are the
   > feed operator's access credentials; in your copy they are already filled in.

---

## Running

```
python bridge.py              # snapshot, then listen to the stream (default)
python bridge.py --snapshot   # snapshot only, then exit
python bridge.py --listen     # skip snapshot, listen to the stream right away
python bridge.py --sweep      # sweeper: feed-TTL expiry, anomalies, retention; then exit
```

When started without a flag (or with `--snapshot`), the bridge purges the RabbitMQ
queue and the tables, then reloads everything from Redis. The `--listen` mode
applies updates on top of an already loaded database.

### Diagnostics

```
python db_info.py             # table sizes and a row breakdown by status
```

**Health page.** `health/` contains a collector that renders a static status page of
the whole pipeline (service, queue lag, write freshness, live-event reconciliation with
Redis, process resources, database and disk, RabbitMQ queues) once a minute for nginx.
Setup and thresholds: [`health/README.md`](health/README.md) (Russian).

**WebSocket delta fan-out.** After each commit the bridge publishes changes to
Centrifugo in batches (`/api/batch`, `centrifugo` section of `config.json`; a no-op
without it): events go to `sport:{sportId}` and `event:{eventId}`, markets and
outcomes to `event:{eventId}`; a message is the AMQP `data` entry wrapped as
`{ch, k, d}`. Centrifugo setup, config, nginx and a demo page: [`centrifugo/README.md`](centrifugo/README.md) (Russian).

**Sweeper (`--sweep`).** The feed drops objects from Redis by TTL without a message: an
event key lives while the event is active and exactly 24 h after `status: 3`. Every 5
minutes (cron, `deploy/feed-sweep.cron`) the sweeper marks such events and their
markets/outcomes `removed=1` with `gone_at`, checks active events with a pipelined Redis
`EXISTS` (vanished "zombies" are marked and pushed to Centrifugo as `{"d": null}`) and
deletes rows older than 7 days after `gone_at` (markets/outcomes cascade via FK).
Settings: `sweep` section of `config.json`; details in `feedbridge/sweep.py`.

---

## Layout

| Path | Purpose |
|------|---------|
| `bridge.py` | Entry point: argument parsing, connections, mode dispatch. |
| `feedbridge/` | Package with the bridge logic (see below). |
| `db_info.py` | Database state inspection utility. |
| `schema.sql` | DDL for the feed tables. |
| `config.example.json` | Configuration template. |

The `feedbridge/` package:

| Module | Purpose |
|--------|---------|
| `config` | Constants and `config.json` loading. |
| `console` | Colors and output formatting. |
| `transform` | Redis-JSON → MySQL value converters. |
| `sql` | Version-guarded upsert statements. |
| `db` | MySQL connection. |
| `amqp` | RabbitMQ connection, queue depth and purge. |
| `core` | `Bridge`: connections, ID caches, ID resolution. |
| `snapshot` | `Snapshotter`: full Redis → MySQL load. |
| `listener` | `Listener`: incremental AMQP → MySQL updates. |

Each module begins with a detailed description of its purpose and functions.
