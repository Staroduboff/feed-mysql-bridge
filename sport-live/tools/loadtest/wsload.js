// Генератор нагрузки на Centrifugo витрины sport-live: множество клиентов, ведущих себя как
// страницы прототипа. Запускается НЕ на целевом сервере, а на отдельной машине.
//
//   node wsload.js --clients 500 --mode wide --ramp 50 --duration 300 [--out /tmp/wsload/run.jsonl]
//
// Режимы подписки (моделируют две страницы прототипа):
//   wide   — страница списка: все каналы видов спорта + до --events каналов живых событий;
//   narrow — страница события: один канал вида спорта + один канал события;
//   mix    — доля --narrow-share клиентов работает в narrow, остальные в wide.
//
// Что меряется:
//   • принято сообщений и байт (всего и на клиента в секунду);
//   • разброс доставки одной публикации по клиентам: для выборки каналов запоминается,
//     когда каждый клиент увидел публикацию с данным offset, и считается задержка
//     относительно первого получателя — прямая метрика деградации fan-out;
//   • пропуски в нумерации offset (потеря публикаций), отказы подписки, обрывы.
//
// Предохранители: --max-rx-mbit прекращает набор и завершает прогон, если входящий поток
// превысил порог (на машине-генераторе может жить чужой трафик — насыщать канал нельзя).
'use strict';

const WebSocket = require('/opt/wsload/node_modules/ws');
const fs = require('fs');
const https = require('https');

function arg(name, def) {
  const i = process.argv.indexOf('--' + name);
  if (i < 0) return def;
  const v = process.argv[i + 1];
  return v === undefined || v.startsWith('--') ? true : v;
}

const CFG = {
  url: String(arg('url', 'wss://gem.x1b.site/centrifugo/connection/websocket')),
  origin: String(arg('origin', 'https://gem.x1b.site')),
  catalog: String(arg('catalog', 'https://gem.x1b.site/feed-health/catalog.json')),
  clients: +arg('clients', 100),
  mode: String(arg('mode', 'wide')),
  narrowShare: +arg('narrow-share', 0.3),
  events: +arg('events', 120),          // каналов событий на клиента в режиме wide
  ramp: +arg('ramp', 50),               // подключений в секунду
  duration: +arg('duration', 300),      // секунд полки после набора
  maxRxMbit: +arg('max-rx-mbit', 400),  // предохранитель по входящему потоку
  sampleChannels: +arg('sample-channels', 5),
  out: String(arg('out', '/tmp/wsload/run.jsonl')),
  label: String(arg('label', '')),
};

fs.mkdirSync(require('path').dirname(CFG.out), { recursive: true });
const log = (...a) => {
  const line = new Date().toISOString().slice(11, 19) + ' ' + a.join(' ');
  console.log(line);
};

let cpuPrev = process.cpuUsage();
let cpuPrevT = Date.now();
function cpuPct() {
  // Доля ядра, съеденная этим процессом с прошлого замера. Node однопоточен по JS:
  // при значении около 100 % измеренная задержка доставки принадлежит уже генератору,
  // а не серверу, и клиентов надо дробить на процессы (см. --shards в ladder.sh).
  const u = process.cpuUsage(cpuPrev);
  const now = Date.now();
  const pct = ((u.user + u.system) / 1000) / Math.max(1, now - cpuPrevT) * 100;
  cpuPrev = process.cpuUsage(); cpuPrevT = now;
  return Math.round(pct);
}

const S = {
  connected: 0, connecting: 0, closed: 0, failed: 0,
  msgs: 0, bytes: 0, pubs: 0, subOk: 0, subErr: 0, subErrCodes: {},
  gaps: 0, pings: 0, t0: 0,
  errors: {},
};
// разброс доставки: канал|offset → {t0, n}
const spread = new Map();
const spreadStats = [];
let rec0 = { cpuPct: 0 };
let sampleSet = new Set();
let stopping = false;

function fetchJson(url) {
  return new Promise((res, rej) => {
    https.get(url, (r) => {
      let b = '';
      r.on('data', (c) => (b += c));
      r.on('end', () => { try { res(JSON.parse(b)); } catch (e) { rej(e); } });
    }).on('error', rej);
  });
}

class Client {
  constructor(id, channels) {
    this.id = id;
    this.channels = channels;
    this.rid = 2;
    this.pending = new Map();
    this.lastOffset = new Map();
    S.connecting++;
    this.ws = new WebSocket(CFG.url, { origin: CFG.origin, perMessageDeflate: false, handshakeTimeout: 20000 });
    this.ws.on('open', () => this.onOpen());
    this.ws.on('message', (data) => this.onMessage(data));
    this.ws.on('close', () => { if (this.up) { S.connected--; S.closed++; } else { S.connecting--; } this.up = false; });
    this.ws.on('error', (e) => {
      const k = String(e && e.message || e).slice(0, 60);
      S.errors[k] = (S.errors[k] || 0) + 1;
      if (!this.up) { S.connecting--; S.failed++; }
    });
  }

  onOpen() {
    S.connecting--; S.connected++; this.up = true;
    this.send({ connect: { name: 'wsload' }, id: 1 });
    this.channels.forEach((ch) => { this.pending.set(this.rid, ch); this.send({ subscribe: { channel: ch }, id: this.rid++ }); });
  }

  send(obj) { if (this.ws.readyState === 1) this.ws.send(JSON.stringify(obj)); }

  onMessage(data) {
    const raw = typeof data === 'string' ? data : data.toString('utf8');
    S.msgs++; S.bytes += Buffer.byteLength(raw);
    const now = Date.now();
    for (const line of raw.split('\n')) {
      if (!line) continue;
      let m;
      try { m = JSON.parse(line); } catch (e) { continue; }
      if (Object.keys(m).length === 0) { S.pings++; this.send({}); continue; }   // ping → pong
      if (m.id && this.pending.has(m.id)) {
        const ch = this.pending.get(m.id); this.pending.delete(m.id);
        if (m.error) {
          S.subErr++; const c = m.error.code; S.subErrCodes[c] = (S.subErrCodes[c] || 0) + 1;
        } else { S.subOk++; if (m.subscribe && m.subscribe.offset != null) this.lastOffset.set(ch, +m.subscribe.offset); }
        continue;
      }
      const push = m.push;
      if (!push || !push.pub) continue;
      S.pubs++;
      const ch = push.channel, off = push.pub.offset;
      if (off != null) {
        const prev = this.lastOffset.get(ch);
        if (prev != null && off > prev + 1) S.gaps += off - prev - 1;
        this.lastOffset.set(ch, off);
        if (sampleSet.has(ch)) {
          const key = ch + '|' + off;
          const rec = spread.get(key);
          if (!rec) { spread.set(key, { t: now, n: 1 }); }
          else { rec.n++; spreadStats.push(now - rec.t); if (spreadStats.length > 200000) spreadStats.splice(0, 100000); }
        }
      }
    }
  }

  close() { try { this.ws.close(); } catch (e) { /* ignore */ } }
}

const q = (a, p) => { if (!a.length) return null; const s = a.slice().sort((x, y) => x - y); return s[Math.min(s.length - 1, Math.floor(s.length * p))]; };

async function main() {
  const cat = await fetchJson(CFG.catalog);
  const sports = (cat.sports || []).map((s) => 'sport:' + s.id);
  const live = (cat.events || []).filter((e) => +e.stage === 2 && +e.status === 1 && !e.x).map((e) => 'event:' + e.id);
  log(`каналов доступно: видов спорта ${sports.length}, живых событий ${live.length}`);
  if (!live.length) { log('живых событий нет — тест бессмысленен, выходим'); process.exit(2); }
  sampleSet = new Set(live.slice(0, CFG.sampleChannels));

  const clients = [];
  const mkChannels = (i) => {
    const narrow = CFG.mode === 'narrow' || (CFG.mode === 'mix' && (i % 100) / 100 < CFG.narrowShare);
    if (narrow) return [sports[i % sports.length], live[i % live.length]];
    return sports.concat(live.slice(0, Math.min(CFG.events, live.length)));
  };

  S.t0 = Date.now();
  const stat = fs.createWriteStream(CFG.out, { flags: 'a' });
  let rxPrev = { msgs: 0, bytes: 0, t: Date.now() };
  const timer = setInterval(() => {
    const now = Date.now(), dt = (now - rxPrev.t) / 1000;
    const mbit = ((S.bytes - rxPrev.bytes) * 8) / dt / 1e6;
    const mps = (S.msgs - rxPrev.msgs) / dt;
    rxPrev = { msgs: S.msgs, bytes: S.bytes, t: now };
    const rec = {
      t: Math.round(now / 1000), label: CFG.label, mode: CFG.mode,
      connected: S.connected, connecting: S.connecting, closed: S.closed, failed: S.failed,
      subOk: S.subOk, subErr: S.subErr, subErrCodes: S.subErrCodes,
      msgs: S.msgs, pubs: S.pubs, gaps: S.gaps, mbit: +mbit.toFixed(1), mps: Math.round(mps),
      spreadP50: q(spreadStats, 0.5), spreadP95: q(spreadStats, 0.95), spreadMax: q(spreadStats, 1),
      rssMb: Math.round(process.memoryUsage().rss / 1048576),
      cpuPct: cpuPct(),
    };
    rec0 = rec;
    stat.write(JSON.stringify(rec) + '\n');
    log(`клиентов ${S.connected} (набор ${S.connecting}, обрывов ${S.closed}, отказов ${S.failed}) | ` +
        `подписки ok ${S.subOk} err ${S.subErr}${Object.keys(S.subErrCodes).length ? ' ' + JSON.stringify(S.subErrCodes) : ''} | ` +
        `${Math.round(mps)} сообщ/с, ${mbit.toFixed(1)} Мбит/с | разброс доставки p50 ${q(spreadStats, 0.5)} мс p95 ${q(spreadStats, 0.95)} мс | ` +
        `пропусков ${S.gaps} | RSS ${rec.rssMb} МБ, CPU процесса ${rec.cpuPct} %`);
    if (mbit > CFG.maxRxMbit && !stopping) { log(`ПРЕДОХРАНИТЕЛЬ: ${mbit.toFixed(0)} Мбит/с выше порога ${CFG.maxRxMbit} — останавливаемся`); finish(); }
    if (spread.size > 50000) spread.clear();
  }, 5000);

  const finish = () => {
    if (stopping) return;
    stopping = true;
    clearInterval(timer);
    log('завершение, закрываю соединения…');
    clients.forEach((c) => c.close());
    setTimeout(() => {
      const el = (Date.now() - S.t0) / 1000;
      log('==== ИТОГ ====');
      log(`длительность ${el.toFixed(0)} с, пик клиентов ${CFG.clients}, подписок принято ${S.subOk}, отказано ${S.subErr} ${JSON.stringify(S.subErrCodes)}`);
      log(`сообщений ${S.msgs}, публикаций ${S.pubs}, байт ${(S.bytes / 1048576).toFixed(0)} МБ, средний поток ${((S.bytes * 8) / el / 1e6).toFixed(1)} Мбит/с`);
      log(`обрывов ${S.closed}, отказов подключения ${S.failed}, пропусков публикаций ${S.gaps}`);
      log(`разброс доставки между клиентами: p50 ${q(spreadStats, 0.5)} мс, p95 ${q(spreadStats, 0.95)} мс, макс ${q(spreadStats, 1)} мс (замеров ${spreadStats.length})`);
      log(`CPU процесса-генератора на последнем замере: ${rec0.cpuPct} % ядра` + (rec0.cpuPct > 80 ? ' — БЛИЗКО К ПОТОЛКУ, задержка доставки завышена, дробите на процессы' : ''));
      if (Object.keys(S.errors).length) log('ошибки сокетов: ' + JSON.stringify(S.errors));
      stat.end(); process.exit(0);
    }, 2000);
  };
  process.on('SIGINT', finish);
  process.on('SIGTERM', finish);

  // набор
  let i = 0;
  const rampTimer = setInterval(() => {
    if (stopping) { clearInterval(rampTimer); return; }
    for (let k = 0; k < CFG.ramp && i < CFG.clients; k++, i++) clients.push(new Client(i, mkChannels(i)));
    if (i >= CFG.clients) {
      clearInterval(rampTimer);
      log(`набор завершён: ${CFG.clients} клиентов, полка ${CFG.duration} с`);
      setTimeout(finish, CFG.duration * 1000);
    }
  }, 1000);
}

main().catch((e) => { log('АВАРИЯ: ' + e); process.exit(1); });
