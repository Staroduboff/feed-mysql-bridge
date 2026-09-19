<?php
/**
 * wallet.php — эмуляция кошелька партнёра для прототипа sport-live.
 *
 * Та же максимально простая логика, что у тестового стенда Borlette
 * (https://gem.x1b.site/, platform/v3/index.php): балансы в текстовом JSON-файле,
 * без БД; стартовый баланс 10 000 HTG (хранится в сантимах); списание при ставке,
 * начисление при выигрыше/возврате, дедупликация по receipt_id. Ставки (купон =
 * экспресс) записываются в тот же файл; рассчитывает их сборщик health/collect.py
 * по результатам исходов в feed_bridge (раз в минуту, тот же файл, тот же flock).
 *
 *   GET  ?action=get_balance&user=42                → {result, balance, currency}
 *   GET  ?action=bets&user=42                       → {result, bets:[…последние 50]}
 *   POST {action:"place_bet", user, receipt_id, stake, selections:[{ev,mk,oc,odds,label,event,market}]}
 *                                                   → {result, balance, bet} | {result:false, error}
 *        Приём с задержкой BET_DELAY_MS (как у букмекеров): запрос держится 3 с, затем кэфы и
 *        доступность исходов сверяются с feed_bridge (пользователь БД sport_ro): кэф упал хотя бы по
 *        одной ноге → {error:"odds_changed", changes:[{ev,mk,oc,odds_old,odds_new}]} (клиент показывает
 *        новые кэфы и просит подтвердить); кэф только вырос → принимаем по новым, большим кэфам без
 *        подтверждения (в ответе improved:[…]); исход закрыт/рассчитан → {error:"unavailable", selections:[…]}
 *   POST {action:"cancel_bet", user, receipt_id}    → возврат ставки (дедуп по receipt_id)
 *   POST {action:"hide_bet", user, bet_id}          → убрать рассчитанную ставку из списка (hidden=true, запись остаётся)
 *   GET  ?action=cashout_check&user=42&bet_id=…     → {result, enabled, price, ratio, delay_ms, reason} — котировка кешаута
 *   POST {action:"cashout", user, bet_id}           → выкуп открытой ставки (кешаут) по логике бэка Спорта: запрос держится
 *        CASHOUT_DELAY_MS, котировка считается заново и выплачивается по ней; {error:"cashout_unavailable", reason}
 *
 * Гейт свежести фида
 * ------------------
 * Приём ставки и выкуп разрешены только когда мост сообщает состояние OPEN
 * (feedbridge/gate.py пишет его в GATE_FILE раз в несколько секунд). Иначе —
 * {result:false, error:"feed_suspended", gate:{state, reasons, lag}}. Состояние
 * гейта отдаётся и в ответах get_balance/bets, чтобы интерфейс мог заранее
 * погасить кнопку, а не отказывать уже после трёхсекундной задержки приёма.
 *
 * Зачем: 12.09.2026 мост отставал от фида на 40-50 минут, гейт корректно стоял
 * в SUSPEND два с половиной часа, но никто его не спрашивал — ставки принимались
 * по устаревшим коэффициентам. Гейт был индикацией; теперь это запрет.
 *
 * Файл: /var/lib/sport-live/wallet.json = {balances:{user:cents}, processed:{receipt:resp}, bets:{id:bet}}
 */
declare(strict_types=1);

const WALLET_FILE = '/var/lib/sport-live/wallet.json';
const LOG_FILE = '/var/lib/sport-live/wallet.log';
const START_BALANCE = 1000000;   // 10 000 HTG в сантимах
const CURRENCY = 'HTG';
const MIN_STAKE = 1000;          // 10 HTG
const MAX_STAKE = 10000000;      // 100 000 HTG
const MAX_SELECTIONS = 20;
const BET_DELAY_MS = 3000;       // задержка приёма ставки
const DB_CFG = '/var/lib/sport-live/db.json';   // доступ только на чтение к feed_bridge (sport_ro)
// Гейт готовности приёма ставок по свежести фида: файл пишет мост (feedbridge/gate.py).
const GATE_FILE = '/var/lib/feed-health/gate.json';
const GATE_MAX_AGE = 30;         // с: состояние старше — считаем гейт закрытым
// Кешаут — логика и лимиты бэка Спорта (betting/BackEnd: Model\Bet::calc_cashout_ratio / cashout_enabled,
// FrontAPI\Api::cashout_check / cashout_get; system_limit типов 9/10/11 одинаковы на деве и проде):
// удержание 10 %, задержка 5 с, потолок выкупа ×5 от ставки, запрет для маркетов «точный счёт»
// (settings.market_cashout_disabled_types = CorrectScore по видам спорта).
const CASHOUT_PERCENT = 10;
const CASHOUT_DELAY_MS = 5000;
const CASHOUT_MAX_RATIO = 5.0;
const CASHOUT_DISABLED_MARKET = '/CorrectScore/';

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');

function respond(array $data): void { echo json_encode($data, JSON_UNESCAPED_UNICODE); exit; }
function fail(string $error): void { respond(['result' => false, 'error' => $error]); }

/**
 * Состояние гейта свежести фида: ['open'=>bool, 'state'=>…, 'reasons'=>[…], 'lag'=>…, 'age'=>…].
 *
 * Деньги принимаем ТОЛЬКО в состоянии OPEN. HOLD — это «сигналы в норме, но выдержка
 * ещё не выдержана»: асимметрия у гейта намеренная (закрываемся сразу, открываемся
 * через паузу), и обходить её здесь нельзя. Недоступный или устаревший файл — тоже
 * отказ: молчание моста не повод считать линию свежей.
 */
function gate(): array {
    $st = ['open' => false, 'state' => 'UNKNOWN', 'reasons' => ['gate_unavailable'], 'lag' => null, 'age' => null];
    $raw = @file_get_contents(GATE_FILE);
    if ($raw === false) return $st;
    $g = json_decode($raw, true);
    if (!is_array($g) || !isset($g['state_name'])) return $st;
    $st['age'] = round(microtime(true) - (float)($g['t'] ?? 0), 1);
    $st['lag'] = $g['metrics']['lag'] ?? null;
    if ($st['age'] > GATE_MAX_AGE) { $st['state'] = 'STALE'; $st['reasons'] = ['gate_stale']; return $st; }
    $st['state'] = (string)$g['state_name'];
    $st['reasons'] = array_values((array)($g['reasons'] ?? []));
    $st['open'] = $st['state'] === 'OPEN';
    return $st;
}

$raw = file_get_contents('php://input');
$in = ($raw !== '' && $raw !== false) ? json_decode($raw, true) : null;
if (!is_array($in)) $in = $_POST + $_GET;
$action = (string)($in['action'] ?? $_GET['action'] ?? '');
$user = (string)($in['user'] ?? $_GET['user'] ?? '42');
if (!preg_match('/^[A-Za-z0-9_\-]{1,32}$/', $user)) fail('Bad user');

/** открыть файл кошелька под блокировкой: [$fh, $w, $txt] */
function wallet_open(string $user): array {
    $fh = fopen(WALLET_FILE, 'c+');
    if (!$fh) fail('Wallet storage unavailable');
    if (!flock($fh, LOCK_EX)) fail('Wallet locked');
    $txt = stream_get_contents($fh);
    $w = $txt ? json_decode($txt, true) : null;
    if (!is_array($w)) $w = [];
    $w += ['balances' => [], 'processed' => [], 'bets' => []];
    if (!isset($w['balances'][$user])) $w['balances'][$user] = START_BALANCE;
    return [$fh, $w, $txt];
}
/** подключение к feed_bridge (sport_ro, только чтение); null — без доступа */
function db_connect(): ?mysqli {
    static $db = null;
    if ($db instanceof mysqli) return $db;
    $cfg = json_decode((string)@file_get_contents(DB_CFG), true);
    if (!is_array($cfg)) return null;
    mysqli_report(MYSQLI_REPORT_OFF);
    $c = @new mysqli($cfg['host'] ?? '127.0.0.1', $cfg['user'] ?? '', $cfg['password'] ?? '', $cfg['database'] ?? 'feed_bridge', (int)($cfg['port'] ?? 3306));
    if ($c->connect_error) return null;
    return $db = $c;
}
/** Котировка кешаута открытой ставки — как Model\Bet::cashout_enabled + calc_cashout_ratio бэка Спорта:
 *  actual = произведение текущих кэфов ног из feed_bridge; ratio = round(odds × (100 − P) / (100 × actual), 2);
 *  price = floor(stake × ratio). Недоступно, если хоть одна нога: событие не открыто (status ≠ 1) или снято,
 *  маркет закрыт/снят, исход не открыт (status ≠ 1) или снят, кэф < 1, маркет «точный счёт»; а также если
 *  ratio > потолка. Без доступа к БД — недоступно (в Спорте так же: нет данных Redis → false). */
function cashout_quote(array $bet): array {
    $no = fn(string $why) => ['enabled' => false, 'price' => 0, 'ratio' => 0.0, 'actual' => 0.0, 'reason' => $why];
    if (($bet['status'] ?? '') !== 'open') return $no('not_open');
    $db = db_connect();
    if (!$db) return $no('no_db');
    $st = $db->prepare("SELECT oc.price, oc.status, oc.removed, m.open, m.removed mr, m.name_en, e.status es, e.removed er\n"
                     . "FROM outcomes oc JOIN markets m ON m.id = oc.market_id JOIN events e ON e.id = m.event_id\n"
                     . "WHERE e.feed_id = ? AND m.feed_hash = ? AND oc.feed_hash = ? LIMIT 1");
    $actual = 1.0;
    foreach ($bet['sel'] ?? [] as $s) {
        $st->bind_param('iss', $s['ev'], $s['mk'], $s['oc']);
        $st->execute();
        $r = $st->get_result()->fetch_assoc();
        if (!$r) return $no('leg_missing');
        if (preg_match(CASHOUT_DISABLED_MARKET, (string)$r['name_en'])) return $no('market_disabled');
        if ((int)$r['es'] !== 1 || (int)$r['er']) return $no('event_closed');
        if (!(int)$r['open'] || (int)$r['mr']) return $no('market_closed');
        if ((int)$r['status'] !== 1 || (int)$r['removed'] || $r['price'] === null || (float)$r['price'] < 1) return $no('outcome_closed');
        $actual *= (float)$r['price'];
    }
    $actual = round($actual, 2);
    if ($actual <= 0) return $no('no_price');
    $ratio = round((float)$bet['odds'] * (100 - CASHOUT_PERCENT) / (100 * $actual), 2);
    if ($ratio > CASHOUT_MAX_RATIO) return $no('ratio_cap');
    $price = (int)floor((int)$bet['stake'] * $ratio);
    if ($price < 1) return $no('zero');
    return ['enabled' => true, 'price' => $price, 'ratio' => $ratio, 'actual' => $actual, 'reason' => null];
}
/** текущие кэфы и доступность исходов из feed_bridge: ['changed' => [...], 'unavailable' => [...]];
 *  без доступа к БД — пусто (принимаем как есть) */
function check_odds(array $sels): array {
    $out = ['changed' => [], 'unavailable' => []];
    $db = db_connect();
    if (!$db) return $out;
    $st = $db->prepare("SELECT oc.price, oc.status, oc.removed, m.open FROM outcomes oc JOIN markets m ON m.id = oc.market_id\n                        JOIN events e ON e.id = m.event_id WHERE e.feed_id = ? AND m.feed_hash = ? AND oc.feed_hash = ? LIMIT 1");
    foreach ($sels as $s) {
        $st->bind_param('iss', $s['ev'], $s['mk'], $s['oc']);
        $st->execute();
        $r = $st->get_result()->fetch_assoc();
        if (!$r || (int)$r['status'] !== 1 || (int)$r['removed'] || !(int)$r['open'] || $r['price'] === null) {
            $out['unavailable'][] = ['ev' => $s['ev'], 'mk' => $s['mk'], 'oc' => $s['oc']];
            continue;
        }
        $now = round((float)$r['price'], 3);
        if (abs($now - (float)$s['odds']) > 0.0015) $out['changed'][] = ['ev' => $s['ev'], 'mk' => $s['mk'], 'oc' => $s['oc'], 'odds_old' => (float)$s['odds'], 'odds_new' => $now];
    }
    return $out;
}

[$fh, $w, $txt] = wallet_open($user);

function save($fh, array $w): void {
    ftruncate($fh, 0); rewind($fh);
    fwrite($fh, json_encode($w, JSON_UNESCAPED_UNICODE));
    fflush($fh);
}
function done($fh, array $w, bool $dirty, string $action, array $in, array $resp): void {
    if ($dirty) save($fh, $w);
    flock($fh, LOCK_UN); fclose($fh);
    if ($dirty || $action === 'place_bet' || $action === 'cashout') @file_put_contents(LOG_FILE, date('Y-m-d H:i:s') . " [$action] " . json_encode($in, JSON_UNESCAPED_UNICODE) . ' => ' . json_encode($resp, JSON_UNESCAPED_UNICODE) . "\n", FILE_APPEND);
    respond($resp);
}

$dirty = false;
switch ($action) {
    case 'get_balance':
        $dirty = ($txt === '' || $txt === false) || !str_contains($txt, '"' . $user . '"');
        done($fh, $w, $dirty, $action, ['user' => $user], ['result' => true, 'balance' => $w['balances'][$user], 'currency' => CURRENCY, 'delay_ms' => BET_DELAY_MS, 'gate' => gate()]);

    case 'bets':
        $list = array_values(array_filter($w['bets'], fn($b) => ($b['user'] ?? '') === $user && empty($b['hidden'])));
        usort($list, fn($a, $b) => $b['placed'] <=> $a['placed']);
        $list = array_slice($list, 0, 50);
        // у открытых ставок — котировка кешаута (как cashout_enabled / cashout_price в истории ставок Спорта)
        foreach ($list as &$b) if (($b['status'] ?? '') === 'open') { $q = cashout_quote($b); $b['cashout'] = ['enabled' => $q['enabled'], 'price' => $q['price'], 'reason' => $q['reason']]; }
        unset($b);
        done($fh, $w, false, $action, [], ['result' => true, 'balance' => $w['balances'][$user], 'currency' => CURRENCY, 'delay_ms' => BET_DELAY_MS, 'gate' => gate(),
                                          'cashout_delay_ms' => CASHOUT_DELAY_MS, 'bets' => $list]);

    case 'place_bet':
        $receipt = (string)($in['receipt_id'] ?? '');
        if (!preg_match('/^[A-Za-z0-9_\-]{6,64}$/', $receipt)) fail('Bad receipt_id');
        if (isset($w['processed'][$receipt])) done($fh, $w, false, $action, [], $w['processed'][$receipt]);
        // Ранний отказ: нет смысла держать пользователя три секунды, если линия уже несвежая.
        $g = gate();
        if (!$g['open']) done($fh, $w, false, $action, ['user' => $user, 'gate' => $g['state']],
                              ['result' => false, 'error' => 'feed_suspended', 'gate' => $g, 'balance' => $w['balances'][$user]]);
        $stake = (int)($in['stake'] ?? 0);
        $sels = $in['selections'] ?? [];
        if ($stake < MIN_STAKE || $stake > MAX_STAKE) fail('Bad stake');
        if (!is_array($sels) || !count($sels) || count($sels) > MAX_SELECTIONS) fail('Bad selections');
        $clean = []; $odds = 1.0; $seen = [];
        foreach ($sels as $s) {
            if (!is_array($s)) fail('Bad selection');
            $ev = (int)($s['ev'] ?? 0); $mk = (string)($s['mk'] ?? ''); $oc = (string)($s['oc'] ?? ''); $o = (float)($s['odds'] ?? 0);
            if ($ev <= 0 || !preg_match('/^[0-9a-f]{32}$/', $mk) || $oc === '' || strlen($oc) > 64 || $o < 1.01 || $o > 1000) fail('Bad selection');
            if (isset($seen[$ev])) fail('Two selections from one event');
            $seen[$ev] = 1;
            $odds *= $o;
            $clean[] = ['ev' => $ev, 'mk' => $mk, 'oc' => $oc, 'odds' => round($o, 3),
                        'label' => mb_substr((string)($s['label'] ?? ''), 0, 40), 'event' => mb_substr((string)($s['event'] ?? ''), 0, 80),
                        'market' => mb_substr((string)($s['market'] ?? ''), 0, 60)];
        }
        // Задержка приёма (как у букмекеров): файл на это время отпускаем, чтобы не блокировать
        // других; после паузы сверяем кэфы и доступность с feed_bridge и только потом списываем.
        flock($fh, LOCK_UN); fclose($fh);
        usleep(BET_DELAY_MS * 1000);
        $chk = check_odds($clean);
        [$fh, $w, $txt] = wallet_open($user);
        if (isset($w['processed'][$receipt])) done($fh, $w, false, $action, [], $w['processed'][$receipt]);
        // Решающая проверка — на момент фактического приёма: за три секунды задержки
        // линия могла протухнуть, и именно по этой цене мы бы и приняли ставку.
        $g = gate();
        if (!$g['open']) done($fh, $w, false, $action, ['user' => $user, 'stake' => $stake, 'gate' => $g['state']],
                              ['result' => false, 'error' => 'feed_suspended', 'gate' => $g, 'balance' => $w['balances'][$user]]);
        if ($chk['unavailable']) done($fh, $w, false, $action, ['user' => $user, 'stake' => $stake, 'unavailable' => count($chk['unavailable'])],
                                       ['result' => false, 'error' => 'unavailable', 'selections' => $chk['unavailable'], 'balance' => $w['balances'][$user]]);
        // кэф упал хотя бы по одной ноге — просим подтвердить; только рост — принимаем по новым кэфам
        $down = array_values(array_filter($chk['changed'], fn($c) => $c['odds_new'] < $c['odds_old']));
        if ($down) done($fh, $w, false, $action, ['user' => $user, 'stake' => $stake, 'changed' => $chk['changed']],
                        ['result' => false, 'error' => 'odds_changed', 'changes' => $chk['changed'], 'balance' => $w['balances'][$user]]);
        $improved = $chk['changed'];
        if ($improved) {
            $odds = 1.0;
            foreach ($clean as &$s) {
                foreach ($improved as $c) if ($c['ev'] === $s['ev'] && $c['mk'] === $s['mk'] && $c['oc'] === $s['oc']) $s['odds'] = $c['odds_new'];
                $odds *= (float)$s['odds'];
            }
            unset($s);
        }
        if ($w['balances'][$user] < $stake) {
            $resp = ['result' => false, 'error' => 'No enough money', 'balance' => $w['balances'][$user]];
            $w['processed'][$receipt] = $resp;
            done($fh, $w, true, $action, $in, $resp);
        }
        $w['balances'][$user] -= $stake;
        $id = date('ymdHis') . '-' . substr(md5($receipt), 0, 8);   // хэш чека: две ставки в одну секунду не пересекаются
        while (isset($w['bets'][$id])) $id .= 'x';
        $bet = ['id' => $id, 'user' => $user, 'receipt' => $receipt, 'stake' => $stake, 'odds' => round($odds, 3),
                'potential' => (int)round($stake * $odds), 'sel' => $clean, 'status' => 'open',
                'placed' => time(), 'settled' => null, 'payout' => 0];
        $w['bets'][$id] = $bet;
        $resp = ['result' => true, 'balance' => $w['balances'][$user], 'bet' => $bet, 'improved' => $improved];
        $w['processed'][$receipt] = $resp;
        done($fh, $w, true, $action, ['user' => $user, 'stake' => $stake, 'n' => count($clean)], $resp);

    case 'cashout_check':
        $bid = (string)($in['bet_id'] ?? $_GET['bet_id'] ?? '');
        if (!isset($w['bets'][$bid]) || ($w['bets'][$bid]['user'] ?? '') !== $user) fail('Unknown bet');
        $q = cashout_quote($w['bets'][$bid]);
        // Выкуп считается по тем же ценам фида, что и приём ставки: несвежая линия даёт
        // несвежую котировку, и ошибка в эту сторону стоит нам денег.
        $g = gate();
        if (!$g['open']) { $q['enabled'] = false; $q['reason'] = 'feed_suspended'; }
        done($fh, $w, false, $action, [], ['result' => true, 'enabled' => $q['enabled'], 'price' => $q['price'], 'ratio' => $q['ratio'], 'delay_ms' => CASHOUT_DELAY_MS, 'reason' => $q['reason'], 'gate' => $g]);

    case 'cashout':
        // Выкуп ставки — как cashout_get бэка Спорта: котировка считается заново в момент выкупа (на странице
        // сумма показана с «≈»), выплата по ней. Задержка CASHOUT_DELAY_MS серверная, как приём ставки:
        // файл кошелька на время паузы отпускается. Дедуп — по bet_id (повторный запрос вернёт тот же ответ).
        $bid = (string)($in['bet_id'] ?? '');
        if (!isset($w['bets'][$bid]) || ($w['bets'][$bid]['user'] ?? '') !== $user) fail('Unknown bet');
        if (isset($w['processed']['cashout_' . $bid])) done($fh, $w, false, $action, [], $w['processed']['cashout_' . $bid]);
        if (($w['bets'][$bid]['status'] ?? '') !== 'open') fail('Bet is not open');
        $g = gate();
        if (!$g['open']) done($fh, $w, false, $action, ['user' => $user, 'bet_id' => $bid, 'gate' => $g['state']],
                              ['result' => false, 'error' => 'feed_suspended', 'gate' => $g]);
        $q0 = cashout_quote($w['bets'][$bid]);
        if (!$q0['enabled']) done($fh, $w, false, $action, ['user' => $user, 'bet_id' => $bid, 'reason' => $q0['reason']],
                                  ['result' => false, 'error' => 'cashout_unavailable', 'reason' => $q0['reason']]);
        flock($fh, LOCK_UN); fclose($fh);
        usleep(CASHOUT_DELAY_MS * 1000);
        [$fh, $w, $txt] = wallet_open($user);
        if (isset($w['processed']['cashout_' . $bid])) done($fh, $w, false, $action, [], $w['processed']['cashout_' . $bid]);
        if (($w['bets'][$bid]['status'] ?? '') !== 'open') fail('Bet is not open');
        $g = gate();   // за паузу выкупа линия могла протухнуть — платим только по свежей
        if (!$g['open']) done($fh, $w, false, $action, ['user' => $user, 'bet_id' => $bid, 'gate' => $g['state']],
                              ['result' => false, 'error' => 'feed_suspended', 'gate' => $g]);
        $q = cashout_quote($w['bets'][$bid]);
        if (!$q['enabled']) done($fh, $w, false, $action, ['user' => $user, 'bet_id' => $bid, 'reason' => $q['reason']],
                                 ['result' => false, 'error' => 'cashout_unavailable', 'reason' => $q['reason']]);
        $b = &$w['bets'][$bid];
        $b['status'] = 'cashout'; $b['payout'] = $q['price']; $b['settled'] = time();
        $b['cashout'] = ['enabled' => false, 'price' => $q['price'], 'ratio' => $q['ratio'], 'actual' => $q['actual'], 'percent' => CASHOUT_PERCENT, 'quoted' => $q0['price']];
        unset($b);
        $w['balances'][$user] += $q['price'];
        $resp = ['result' => true, 'balance' => $w['balances'][$user], 'bet' => $w['bets'][$bid]];
        $w['processed']['cashout_' . $bid] = $resp;
        done($fh, $w, true, $action, ['user' => $user, 'bet_id' => $bid, 'price' => $q['price'], 'ratio' => $q['ratio'], 'actual' => $q['actual'], 'quoted' => $q0['price']], $resp);

    case 'hide_bet':
        // убрать рассчитанную ставку из списка «Мои ставки» (запись остаётся в файле с hidden=true)
        $bid = (string)($in['bet_id'] ?? '');
        if (!isset($w['bets'][$bid]) || ($w['bets'][$bid]['user'] ?? '') !== $user) fail('Unknown bet');
        if (($w['bets'][$bid]['status'] ?? '') === 'open') fail('Bet is open');
        $w['bets'][$bid]['hidden'] = true;
        done($fh, $w, true, $action, ['user' => $user, 'bet_id' => $bid], ['result' => true]);

    case 'cancel_bet':
        $receipt = (string)($in['receipt_id'] ?? '');
        if (!preg_match('/^[A-Za-z0-9_\-]{6,64}$/', $receipt)) fail('Bad receipt_id');
        if (isset($w['processed']['cancel_' . $receipt])) done($fh, $w, false, $action, [], $w['processed']['cancel_' . $receipt]);
        $found = null;
        foreach ($w['bets'] as $id => $b) if (($b['receipt'] ?? '') === $receipt && $b['user'] === $user) { $found = $id; break; }
        if ($found === null) fail('Unknown bet');
        if ($w['bets'][$found]['status'] !== 'open') fail('Bet already settled');
        $w['bets'][$found]['status'] = 'cancelled'; $w['bets'][$found]['settled'] = time();
        $w['balances'][$user] += $w['bets'][$found]['stake'];
        $resp = ['result' => true, 'balance' => $w['balances'][$user]];
        $w['processed']['cancel_' . $receipt] = $resp;
        done($fh, $w, true, $action, ['user' => $user, 'receipt' => $receipt], $resp);

    default:
        fail('Unknown action');
}
