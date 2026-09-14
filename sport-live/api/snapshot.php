<?php
/**
 * snapshot.php — снимок события для страницы события: все маркеты и исходы из feed_bridge.
 *
 * Тот же формат, что у статического снимка сборщика (feed-health/snap/{id}.json), но по
 * запросу и для любого события в базе бриджа, а не только live и стартующих в ближайший час:
 *
 *   GET ?id=<feed_id> → {"t": unix, "ev": {id, sp, n, tr, cat, ch, th, st, stage, status, sv, sc, score},
 *                        "markets": [{h, n, p, type, period, v, open, oc: [{h, n, v, t, price, status}]}]}
 *   404 → {"error": "not found"}
 *
 * Читает БД пользователем только на чтение (sport_ro), доступы — /var/lib/sport-live/db.json
 * (root:www-data 640). Дальше по WS (Centrifugo, канал event:{id}) приходят дельты.
 */
declare(strict_types=1);

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
header('Access-Control-Allow-Origin: *');

function fail(int $code, string $msg): void { http_response_code($code); echo json_encode(['error' => $msg]); exit; }

$id = (int)($_GET['id'] ?? 0);
if ($id <= 0) fail(400, 'bad id');

$cfg = json_decode((string)@file_get_contents('/var/lib/sport-live/db.json'), true);
if (!is_array($cfg)) fail(500, 'db config unavailable');
mysqli_report(MYSQLI_REPORT_OFF);
$db = @new mysqli($cfg['host'] ?? '127.0.0.1', $cfg['user'] ?? '', $cfg['password'] ?? '', $cfg['database'] ?? 'feed_bridge', (int)($cfg['port'] ?? 3306));
if ($db->connect_error) fail(500, 'db unavailable');
$db->set_charset('utf8mb4');

$st = $db->prepare("SELECT e.id iid, e.feed_id id, s.feed_id sp, e.name_en n, t.name_en tr, c.name_en cat,
                           c.feed_hash ch, t.feed_hash th, e.start_time st, e.stage, e.status, e.statusv2 sv, e.score, e.removed
                    FROM events e JOIN sports s ON s.id = e.sport_id
                         JOIN tournaments t ON t.id = e.tournament_id JOIN categories c ON c.id = e.category_id
                    WHERE e.feed_id = ? LIMIT 1");
$st->bind_param('i', $id);
$st->execute();
$e = $st->get_result()->fetch_assoc();
if (!$e) fail(404, 'not found');

$score = null; $sc = null;
if ($e['score'] !== null && $e['score'] !== '') {
    $score = json_decode($e['score'], true);
    if (is_array($score) && isset($score['list']['0']) && is_array($score['list']['0']) && count($score['list']['0']) >= 2) {
        $sc = $score['list']['0'][0] . ':' . $score['list']['0'][1];
    }
}
$ev = [
    'id' => (int)$e['id'], 'sp' => (int)$e['sp'], 'n' => $e['n'], 'tr' => $e['tr'], 'cat' => $e['cat'],
    'ch' => $e['ch'], 'th' => $e['th'],
    'st' => $e['st'] ? gmdate('Y-m-d\TH:i:s\Z', strtotime($e['st'] . ' UTC')) : null,
    'stage' => (int)$e['stage'], 'status' => (int)$e['status'], 'sv' => $e['sv'], 'sc' => $sc,
    'score' => ((int)$e['stage'] === 2 && is_array($score)) ? $score : null,
    'removed' => (int)$e['removed'],
];

$mk = [];
$st = $db->prepare("SELECT m.id mid, m.feed_hash h, m.name_en n, m.period_name_en p, m.market_type mt, m.period pr, m.value v, m.open o
                    FROM markets m WHERE m.event_id = ? AND m.removed = 0");
$st->bind_param('i', $e['iid']);
$st->execute();
$rs = $st->get_result();
while ($r = $rs->fetch_assoc()) {
    $mk[(int)$r['mid']] = ['h' => $r['h'], 'n' => $r['n'], 'p' => $r['p'], 'type' => (int)$r['mt'], 'period' => (int)$r['pr'],
                           'v' => $r['v'], 'open' => (int)$r['o'], 'oc' => []];
}
if ($mk) {
    $st = $db->prepare("SELECT o.market_id mid, o.feed_hash h, o.name_en n, o.value v, o.price, o.status s, o.outcome_type t
                        FROM outcomes o WHERE o.event_id = ? AND o.removed = 0");
    $st->bind_param('i', $e['iid']);
    $st->execute();
    $rs = $st->get_result();
    while ($r = $rs->fetch_assoc()) {
        $m = (int)$r['mid'];
        if (!isset($mk[$m])) continue;
        $mk[$m]['oc'][] = ['h' => $r['h'], 'n' => $r['n'], 'v' => $r['v'], 't' => $r['t'] === null ? null : (int)$r['t'],
                           'price' => $r['price'] === null ? null : (float)$r['price'], 'status' => (int)$r['s']];
    }
}
echo json_encode(['t' => time(), 'ev' => $ev, 'markets' => array_values($mk)], JSON_UNESCAPED_UNICODE);
