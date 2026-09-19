// Монитор «WS → экран» для двух витрин лайва: партнёрский прототип (gem.x1b.site/sport-live)
// и Пижама (paryajpam.com/sport/live). Запускается на *10:  node wsmon.js <минут>
//
// Что меряется, для КАЖДОГО сайта отдельно:
//   • «истина WS» — состояние «исход открыт» по последним данным, которые сайт получил
//     по своему WS (у Пижамы: снимок live2 каждые 6 с + пуши update; у нас: дельты Centrifugo
//     + каталог раз в минуту). Исход открыт = маркет открыт ∧ статус исхода 1 ∧ цена есть.
//   • «экран» — реально ли кнопка кэфа кликабельна в DOM (button.disabled).
//   • эпизоды расхождения истина↔экран, их длительность и направление:
//       ws_closed_dom_open — WS запретил, а экран не закрыл (опасно);
//       ws_open_dom_closed — WS открыл, а экран держит закрытым (консервативно).
//   • задержка отрисовки: от смены состояния в WS до совпадения с экраном.
//   • полнота потока: сколько раз снимок (live2 / каталог) поправил состояние,
//     которого поток пушей/дельт не донёс.
// Между сайтами: совпадение «истины» (линия) и совпадение экранов (что видит игрок)
// по общим ключам событие|маркет|исход, эпизоды расхождения и кто отстаёт.
const { chromium } = require('/opt/pw-admin/node_modules/playwright');
const fs = require('fs');
const https = require('https');

const MIN = +(process.argv[2] || 180);
const DIR = '/tmp/wsmon';
const STEP = 1000;       // период опроса экранов, мс
const GRACE = 2500;      // допустимая задержка отрисовки, мс
const FLAG = 10000;      // эпизод длиннее — находка
fs.mkdirSync(DIR, { recursive: true });
try { fs.unlinkSync(DIR + '/DONE'); } catch (e) { /* ignore */ }
const ts = () => new Date().toISOString().slice(11, 19);
const log = (...a) => { const l = ts() + ' ' + a.join(' '); console.log(l); fs.appendFileSync(DIR + '/run.log', l + '\n'); };
const num = (v) => (v == null ? null : +v);

function site(name) {
  return { name, mkOpen: new Map(), ocOk: new Map(), evName: new Map(), truthT: new Map(),
           frames: 0, deltas: 0, snaps: 0, sockets: 0, lastFrame: 0, gaps: [], corrections: 0, corrEx: [],
           episodes: [], openEp: new Map(), pending: new Map(), delays: [], samples: 0, cells: 0,
           unmapped: 0, mapped: 0, modelMis: 0, acts: {}, lastDom: new Map() };
}
const S = { our: site('наш прототип'), pz: site('Пижама') };
const X = { samples: 0, truthBoth: 0, truthSame: 0, domBoth: 0, domSame: 0, epT: [], epD: [], openT: new Map(), openD: new Map() };

// ── история обновлений по ключу: чем и когда менялось состояние ──────────
function pushHist(s, which, k, item) {
  const m = (s[which] = s[which] || new Map());
  const a = m.get(k) || []; a.push(item); if (a.length > 8) a.shift(); m.set(k, a);
}

// ── истина ────────────────────────────────────────────────────────────────
function truth(s, key) {
  const p = key.split('|'); const mk = p[0] + '|' + p[1];
  const m = s.mkOpen.get(mk), o = s.ocOk.get(key);
  if (m === undefined || o === undefined) return undefined;
  const e = s.evOk ? s.evOk.get(p[0]) : undefined;   // у нас уровня события нет → undefined = ок
  return !!(m && o && e !== false);
}
function setEv(s, ev, ok, t, src, tRecv) {
  s.evOk = s.evOk || new Map(); s.evT = s.evT || new Map(); s.evSrc = s.evSrc || new Map();
  const prevT = s.evT.get(ev) || 0, prevSrc = s.evSrc.get(ev);
  if (src === 'snap' && prevSrc === 'ws' && prevT > t) return;
  const affected = [];
  for (const key of s.ocOk.keys()) if (key.startsWith(ev + '|')) affected.push([key, truth(s, key)]);
  s.evOk.set(ev, !!ok); s.evSrc.set(ev, src); s.evT.set(ev, t);
  affected.forEach(([key, before]) => noteChange(s, key, before, tRecv == null ? t : tRecv, src));
}
// Ожидание снято без замера: ключа не было на экране в момент смены либо он ушёл
// с экрана раньше, чем отрисовка стала наблюдаемой.
function dropPending(s, p) {
  if (p.src === 'ws') s.delaysSkip = (s.delaysSkip || 0) + 1;
  else s.delaysSkipSnap = (s.delaysSkipSnap || 0) + 1;
}
function noteChange(s, key, before, t, src) {
  const after = truth(s, key);
  if (after === undefined || before === undefined || after === before) return;
  // dom — был ли ключ на экране в момент прихода смены. Если не был, замерить
  // отрисовку нельзя: совпадение DOM позже покажет время появления строки, а не
  // время отрисовки. Такие ожидания снимаются без замера (см. dropPending).
  s.pending.set(key, { t, to: after, src, dom: s.lastDom.has(key) });
  s.truthT.set(key, t);
}
function setMk(s, ev, mk, open, t, src, tRecv) {
  const k = ev + '|' + mk; if (tRecv == null) tRecv = t;
  s.mkT = s.mkT || new Map(); s.mkSrc = s.mkSrc || new Map();
  const prev = s.mkOpen.get(k), prevT = s.mkT.get(k) || 0, prevSrc = s.mkSrc.get(k);
  // Снимок старее последней дельты потока — поток свежее, снимок игнорируем (так же
  // поступает фронт: состояние из WS не затирается более старым каталогом/снимком).
  if (src === 'snap' && prevSrc === 'ws' && prevT > t) return;
  // до применения — истина затронутых исходов
  const affected = [];
  for (const key of s.ocOk.keys()) if (key.startsWith(k + '|')) affected.push([key, truth(s, key)]);
  if (src === 'snap' && prevSrc === 'ws' && prev !== undefined && prev !== !!open) { s.corrections++; if (s.corrEx.length < 30) s.corrEx.push({ t: ts(), what: 'маркет', key: k, stream: prev, snap: !!open }); }
  s.mkOpen.set(k, !!open); s.mkSrc.set(k, src); s.mkT.set(k, t);
  pushHist(s, 'histMk', k, { t: tRecv, src, v: !!open });
  affected.forEach(([key, before]) => noteChange(s, key, before, tRecv, src));
}
function setOc(s, ev, mk, oc, ok, t, src, tRecv) {
  const key = ev + '|' + mk + '|' + oc; if (tRecv == null) tRecv = t;
  s.ocT = s.ocT || new Map(); s.ocSrc = s.ocSrc || new Map();
  const prev = s.ocOk.get(key), prevT = s.ocT.get(key) || 0, prevSrc = s.ocSrc.get(key);
  if (src === 'snap' && prevSrc === 'ws' && prevT > t) return;
  const before = truth(s, key);
  if (src === 'snap' && prevSrc === 'ws' && prev !== undefined && prev !== !!ok) { s.corrections++; if (s.corrEx.length < 30) s.corrEx.push({ t: ts(), what: 'исход', key, stream: prev, snap: !!ok }); }
  s.ocOk.set(key, !!ok); s.ocSrc.set(key, src); s.ocT.set(key, t);
  pushHist(s, 'histOc', key, { t: tRecv, src, v: !!ok });
  noteChange(s, key, before, tRecv, src);
}

// ── наш поток: дельты Centrifugo + каталог ────────────────────────────────
function ourDelta(d, t) {
  const s = S.our; s.deltas++;
  const k = String(d.k || ''), p = k.split(':'), o = d.d;
  if (d.ch === 'markets' && p.length >= 3) { setMk(s, p[1], p[2], o !== null && !!o.open && !o.removed, t, 'ws'); }
  else if (d.ch === 'outcomes' && p.length >= 4) { const oc = p.slice(3).join(':'); setOc(s, p[1], p[2], oc, o !== null && o.status === 1 && !o.removed && o.price != null, t, 'ws'); }
  else if (d.ch === 'events' && o && o.name) { const id = p[p.length - 1]; s.evName.set(String(id), (o.name && (o.name.EN || o.name)) || ''); }
}
function walkDeltas(obj, t) {
  if (!obj || typeof obj !== 'object') return;
  if (Array.isArray(obj)) { obj.forEach((x) => walkDeltas(x, t)); return; }
  if ('ch' in obj && 'k' in obj && 'd' in obj) { ourDelta(obj, t); return; }
  Object.keys(obj).forEach((key) => walkDeltas(obj[key], t));
}
function fetchJson(url) {
  return new Promise((res, rej) => { https.get(url, (r) => { let b = ''; r.on('data', (c) => (b += c)); r.on('end', () => { try { res(JSON.parse(b)); } catch (e) { rej(e); } }); }).on('error', rej); });
}
// ── собственная подписка монитора на наш Centrifugo ──────────────────────────
// Независима от испытуемой страницы: витрина подписывается по видимой области и
// маркеты событий за экраном не получает, а монитору нужна полная линия — иначе
// он не отличит «фронт не показал» от «фронт и не знал», и сравнение витрин между
// собой теряет смысл. Нагрузка здесь безопасна: Node только обновляет Map, DOM нет.
const OWN_WS = 'wss://gem.x1b.site/centrifugo/connection/websocket';
const OWN_CATALOG = 'https://gem.x1b.site/feed-health/catalog.json';
const own = { cf: null, subs: new Map(), pubs: 0, errors: 0, connected: false };

function ownSubscribe(ch) {
  if (own.subs.has(ch) || !own.cf) return;
  const sub = own.cf.newSubscription(ch);
  sub.on('publication', (ctx) => { own.pubs++; walkDeltas(ctx.data || {}, Date.now()); });
  sub.on('error', () => { own.errors++; });
  sub.subscribe();
  own.subs.set(ch, sub);
}

// Состав каналов берём из каталога и освежаем раз в минуту: новые живые события
// должны попадать под наблюдение без перезапуска.
async function ownSyncChannels() {
  let cat;
  try { cat = await fetchJson(OWN_CATALOG + '?' + Date.now()); } catch (e) { own.errors++; return; }
  (cat.sports || []).forEach((sp) => ownSubscribe('sport:' + sp.id));
  (cat.events || []).forEach((e) => { if (e && e.id && e.main) ownSubscribe('event:' + e.id); });
}

async function startOwnFeed() {
  // Скрипт запускают копией из /tmp, поэтому модули берём там же, где playwright.
  const { Centrifuge } = require('/opt/pw-admin/node_modules/centrifuge');
  const WebSocket = require('/opt/pw-admin/node_modules/ws');
  own.cf = new Centrifuge(OWN_WS, { websocket: WebSocket });
  own.cf.on('connected', () => { own.connected = true; log('[feed] своя подписка: соединение есть'); });
  own.cf.on('disconnected', () => { own.connected = false; log('[feed] своя подписка: соединение потеряно'); });
  own.cf.on('error', () => { own.errors++; });
  own.cf.connect();
  await ownSyncChannels();
  log('[feed] своя подписка: каналов', own.subs.size);
  setInterval(ownSyncChannels, 60000);
}

function applyCatalog(cat, tRecv) {
  // t каталога — момент сборки; всё, что пришло по WS позже, свежее каталога и им не
  // перекрывается (так же решает фронт). Задержка отрисовки считается от tRecv — момента,
  // когда каталог получила сама страница.
  let t = +cat.t || tRecv; if (t < 1e12) t *= 1000;
  const s = S.our; s.snaps++; s.lastSnapAge = tRecv - t;
  (cat.events || []).forEach((e) => {
    if (!e.main) return;
    s.evName.set(String(e.id), e.n || '');
    e.main.forEach((m) => {
      if (!m || !m.h) return;
      setMk(s, String(e.id), m.h, !!m.open, t, 'snap', tRecv);
      (m.oc || []).forEach((o) => setOc(s, String(e.id), m.h, o.h, o.status === 1 && o.price != null, t, 'snap', tRecv));
    });
  });
}

// ── поток Пижамы: снимки live2 + пуши update ──────────────────────────────
let pzNames = [];            // названия событий для поиска строк
const pzEvSt = new Map();    // событие → статус (информационно)
function pzSnapshot(data, t, tRecv) {
  const s = S.pz; s.snaps++;
  const names = new Set();
  Object.values(data || {}).forEach((groups) => (Array.isArray(groups) ? groups : Object.values(groups || {})).forEach((g) => (g.events || []).forEach((e) => {
    const ev = String(e.id); s.evName.set(ev, e.nm || ''); names.add(e.nm || ''); pzEvSt.set(ev, { st: e.st, sg: e.sg });
    const mr = e.mr || {};
    Object.keys(mr).forEach((h) => {
      const m = mr[h];
      setMk(s, ev, h, !!m.op, t, 'snap', tRecv);
      (Array.isArray(m.ou) ? m.ou : []).forEach((it) => Object.keys(it || {}).forEach((oc) => setOc(s, ev, h, oc, it[oc].st === 1 && it[oc].kf != null, t, 'snap', tRecv)));
    });
    // событие целиком: st≠1, sg=0 или ar — строка гасится (правило купона/списка фронта Спорта)
    setEv(s, ev, e.st === 1 && e.sg !== 0 && !e.ar, t, 'snap', tRecv);
  })));
  pzNames = [...names].filter(Boolean);
  s.lastSnap = data;
}
function pzUpdate(items, t) {
  const s = S.pz;
  items.forEach((it) => {
    s.deltas++; s.acts[it.type + ':' + it.act] = (s.acts[it.type + ':' + it.act] || 0) + 1;
    const d = it.data;
    if (it.type === 'market' && it.event && it.id) setMk(s, String(it.event), String(it.id), !!(d && d.op), t, 'ws');
    else if (it.type === 'outcome' && it.event && it.market && it.id) setOc(s, String(it.event), String(it.market), String(it.id), !!(d && d.st === 1 && d.kf != null), t, 'ws');
    else if (it.type === 'event' && d) { pzEvSt.set(String(it.id), { st: d.st, sg: d.sg }); if (d.nm) s.evName.set(String(it.id), d.nm); if (d.st !== undefined) setEv(s, String(it.id), d.st === 1 && d.sg !== 0 && !d.ar, t, 'ws'); }
  });
}

// ── экраны ────────────────────────────────────────────────────────────────
const OUR_DOM = () => {
  const cells = {}; const model = {}; const vis = {};
  // Запас тот же, что у подписки витрин: строка в этой полосе считается видимой.
  const H = window.innerHeight || 0, OV = 400;
  document.querySelectorAll('.sb-odd[data-sid]').forEach((b) => {
    if (b.offsetParent === null) return;
    const sid = b.dataset.sid;
    if (cells[sid] === undefined) cells[sid] = !b.disabled; else if (cells[sid] !== !b.disabled) cells[sid] = null;
    const r = b.getBoundingClientRect();
    if (r.bottom >= -OV && r.top <= H + OV) vis[sid] = 1;
  });
  try { window.SB.indexSelections().forEach((v, sid) => { if (cells[sid] !== undefined) model[sid] = !!v.selection.available; }); } catch (e) { /* ignore */ }
  return { cells, model, vis, conn: (document.querySelector('.sb-conn') || {}).className || '' };
};
const PZ_DOM = (names) => {
  const set = new Set(names); const rows = [];
  const H = window.innerHeight || 0, OV = 400;
  const leaves = [...document.querySelectorAll('div,span,a,p')].filter((el) => !el.children.length && el.offsetParent !== null && set.has((el.textContent || '').trim()));
  const seen = new Set();
  leaves.forEach((leaf) => {
    const name = leaf.textContent.trim(); if (seen.has(name)) return;
    let row = leaf, btns = [];
    for (let i = 0; i < 8 && row; i++) {
      row = row.parentElement; if (!row) break;
      btns = [...row.querySelectorAll('button')].filter((b) => b.offsetParent !== null && /(^|\s)\d+(\.\d+)?$/.test((b.innerText || '').trim()));
      if (btns.length >= 2) break;
    }
    if (!row || btns.length < 2) return;
    seen.add(name);
    const groups = []; const parents = [];
    btns.forEach((b) => { let gi = parents.indexOf(b.parentElement); if (gi < 0) { parents.push(b.parentElement); groups.push([]); gi = groups.length - 1; } const m = (b.innerText || '').trim().match(/(\d+(\.\d+)?)\s*$/); groups[gi].push({ t: m ? m[1] : '', d: !!b.disabled }); });
    const rr = row.getBoundingClientRect();
    rows.push({ name, groups, vis: (rr.bottom >= -OV && rr.top <= H + OV) ? 1 : 0 });
  });
  return { rows, demo: (document.body.innerText || '').includes('demo mode') };
};
// дамп состояния прототипа по ключу: модель адаптера и кнопки в DOM
const OUR_DUMP = (key) => {
  const p = key.split('|'); const ev = +p[0], mk = p[1];
  const idx = window.SB.indexSelections().get(key);
  const e = window.SB.eventById(ev);
  const btns = [...document.querySelectorAll('.sb-odd[data-sid]')].filter((b) => b.dataset.sid === key)
    .map((b) => ({ dis: b.disabled, inline: b.classList.contains('sb-odd--inline'), vis: b.offsetParent !== null, val: (b.querySelector('.sb-odd__value') || {}).textContent }));
  return {
    inIndex: !!idx,
    sel: idx ? { av: idx.selection.available, st: idx.selection._status, odds: idx.selection.odds, wsAt: idx.selection._wsAt || null } : null,
    mkt: idx ? { open: idx.market._open, removed: idx.market._removed, wsAt: idx.market._wsAt || null, slot: idx.market._slot } : null,
    inMain: e ? e.mainMarkets.some((m) => m._h === mk) : null,
    main: e ? e.mainMarkets.map((m) => m._h.slice(0, 6) + ':' + (m._open ? 1 : 0) + ':s' + m._slot) : null,
    ev: e ? { state: e.state, fin: !!e._finished, rem: !!e._removed, cat: !!e._inCatalog, lastWs: e._lastWs ? Math.round((Date.now() - e._lastWs) / 1000) : null } : null,
    btns, catAge: (window.SB.live && window.SB.live.catalogAge) ? Math.round(window.SB.live.catalogAge()) : null,
  };
};

// привязка строк Пижамы к ключам через последний снимок
function pzMap(rows) {
  const s = S.pz; const out = new Map(); const snap = s.lastSnap; if (!snap) return out;
  s.lastVis = new Map();
  const byName = new Map();
  Object.values(snap).forEach((groups) => (Array.isArray(groups) ? groups : Object.values(groups || {})).forEach((g) => (g.events || []).forEach((e) => byName.set(e.nm, e))));
  rows.forEach((r) => {
    const e = byName.get(r.name); if (!e) { s.unmapped++; return; }
    const mkts = (e.mro || []).filter((m) => m && m.k && e.mr && e.mr[m.k]).map((m) => e.mr[m.k] && Object.assign({ k: m.k }, e.mr[m.k]));
    const used = new Set();
    mkts.forEach((m) => {
      const ous = (Array.isArray(m.ou) ? m.ou : []).map((it) => { const oc = Object.keys(it || {})[0]; return { oc, kf: it[oc] && it[oc].kf }; });
      // группа DOM с наибольшим совпадением цен
      let best = -1, bestHit = 0;
      r.groups.forEach((g, gi) => { if (used.has(gi) || g.length !== ous.length) return; let hit = 0; g.forEach((c, i) => { if (ous[i] && ous[i].kf != null && String(ous[i].kf) === c.t) hit++; }); if (hit > bestHit) { bestHit = hit; best = gi; } });
      if (best < 0) { s.unmapped++; return; }
      used.add(best); s.mapped++;
      // Порядок колонок в группе у фронта свой (у «двойного шанса» — 1X, 12, X2, а в ou —
      // 1X, X2, 12), поэтому ячейка сопоставляется исходу по значению кэфа; когда значение
      // не найдено ровно у одного исхода (цена только что сменилась или две одинаковые),
      // остаток спаривается только если он однозначен, иначе ячейка пропускается.
      const cells = r.groups[best].map((c) => ({ c, oc: null }));
      const freeOus = ous.slice();
      cells.forEach((cell) => {
        const hits = freeOus.filter((o) => o.kf != null && String(o.kf) === cell.c.t);
        if (hits.length === 1) { cell.oc = hits[0]; freeOus.splice(freeOus.indexOf(hits[0]), 1); }
      });
      const rest = cells.filter((x) => !x.oc);
      if (rest.length === 1 && freeOus.length === 1) { rest[0].oc = freeOus[0]; freeOus.length = 0; }
      cells.forEach((cell) => {
        if (!cell.oc) { s.unmappedCells = (s.unmappedCells || 0) + 1; return; }
        const key = String(e.id) + '|' + m.k + '|' + cell.oc.oc; out.set(key, !cell.c.d); if (r.vis) s.lastVis.set(key, 1); (s.lastText = s.lastText || new Map()).set(key, cell.c.t);
      });
    });
  });
  return out;
}

// ── сравнение экрана с истиной ────────────────────────────────────────────
function compare(s, dom, now) {
  s.samples++; s.cells += dom.size; s.lastDom = dom;
  const vis = s.lastVis || new Map();
  s.cellsVis = (s.cellsVis || 0) + vis.size;
  dom.forEach((open, key) => {
    const tr = truth(s, key); if (tr === undefined || open === null) return;
    const ep = s.openEp.get(key);
    if (open !== tr) {
      if (!ep) {
        const p = s.pending.get(key) || {}; const parts = key.split('|');
        s.openEp.set(key, { key, ev: parts[0], dir: tr ? 'ws_open_dom_closed' : 'ws_closed_dom_open', since: now, changeAt: p.t || null,
          inView: vis.has(key) ? 1 : 0,
          src: p.src || (s.ocSrc && s.ocSrc.get(key)) || '?',
          mk: s.mkOpen.get(parts[0] + '|' + parts[1]), oc: s.ocOk.get(key), evOk: s.evOk ? s.evOk.get(parts[0]) : undefined,
          text: s.lastText ? s.lastText.get(key) : undefined });
      }
    } else {
      if (ep) {
        ep.until = now; ep.dur = now - ep.since;
        const parts = key.split('|'); const mkKey = parts[0] + '|' + parts[1];
        const hm = (s.histMk && s.histMk.get(mkKey)) || [], ho = (s.histOc && s.histOc.get(key)) || [];
        ep.hist = { mk: hm.map((h) => ({ dt: Math.round((h.t - ep.since) / 100) / 10, src: h.src, v: h.v ? 1 : 0 })),
                    oc: ho.map((h) => ({ dt: Math.round((h.t - ep.since) / 100) / 10, src: h.src, v: h.v ? 1 : 0 })) };
        // чем закончился: было ли обновление ПОТОКА по этому ключу за 3 с до совпадения
        // (значение = то, к чему пришёл экран), или экран догнал истину по снимку
        const target = open;
        const recentWs = hm.concat(ho).filter((h) => h.src === 'ws' && h.v === target && now - h.t <= 3000).length;
        const anySnap = hm.concat(ho).filter((h) => h.src === 'snap' && h.v === target && h.t >= ep.since).length;
        ep.endedBy = recentWs ? 'push' : (anySnap ? 'snapshot' : 'unknown');
        ep.pushesDuring = hm.concat(ho).filter((h) => h.src === 'ws' && h.t >= ep.since).length;
        if (s.subs) { const sub = s.subs.get(parts[0]); ep.subscribed = sub ? (sub.has('all') || sub.has(parts[1]) ? 1 : 0) : 0; }
        s.episodes.push(ep); s.openEp.delete(key);
        if (ep.dump) s.endDumps = (s.endDumps || []).concat([ep]);
        fs.appendFileSync(DIR + '/episodes.jsonl', JSON.stringify(Object.assign({ site: s.name, name: s.evName.get(ep.ev) || '' }, ep)) + '\n');
      }
      const p = s.pending.get(key);
      if (p && p.to === open) {
        if (p.dom) (p.src === 'ws' ? s.delays : (s.delaysSnap = s.delaysSnap || [])).push(now - p.t);
        else dropPending(s, p);
        s.pending.delete(key);
      }
    }
  });
  // исчезнувшие с экрана ключи — эпизод закрываем как «ушёл с экрана», не как расхождение
  s.openEp.forEach((ep, key) => { if (!dom.has(key)) { ep.until = now; ep.dur = now - ep.since; ep.gone = true; s.episodes.push(ep); s.openEp.delete(key); } });
  // Ключ ушёл с экрана — отрисовку по нему уже не наблюдать. Ожидание снимаем сразу:
  // доживи оно до возвращения строки, дало бы ложную задержку в десятки секунд.
  s.pending.forEach((p, key) => { if (!dom.has(key)) { dropPending(s, p); s.pending.delete(key); } });
  s.pending.forEach((p, key) => { if (now - p.t > 120000) { dropPending(s, p); s.pending.delete(key); } });
}
function cross(now) {
  const a = S.our.lastDom, b = S.pz.lastDom; if (!a.size || !b.size) return;
  X.samples++;
  const keys = new Set([...a.keys()].filter((k) => b.has(k)));
  keys.forEach((k) => {
    const ta = truth(S.our, k), tb = truth(S.pz, k), da = a.get(k), db = b.get(k);
    if (ta !== undefined && tb !== undefined) { X.truthBoth++; if (ta === tb) X.truthSame++; track(X.openT, X.epT, k, ta === tb, ta ? 'наш открыт, Пижама закрыта' : 'наш закрыт, Пижама открыта', now); }
    if (da != null && db != null) { X.domBoth++; if (da === db) X.domSame++; track(X.openD, X.epD, k, da === db, da ? 'наш экран открыт, Пижама закрыта' : 'наш экран закрыт, Пижама открыта', now); }
  });
  [X.openT, X.openD].forEach((m, i) => m.forEach((ep, k) => { if (!keys.has(k)) { ep.dur = now - ep.since; ep.gone = true; (i ? X.epD : X.epT).push(ep); m.delete(k); } }));
}
function track(openMap, list, key, same, dir, now) {
  const ep = openMap.get(key);
  if (!same) { if (!ep) openMap.set(key, { key, dir, since: now }); }
  else if (ep) { ep.dur = now - ep.since; list.push(ep); openMap.delete(key); }
}

// ── отчёт ────────────────────────────────────────────────────────────────
const q = (arr, p) => { if (!arr.length) return null; const a = arr.slice().sort((x, y) => x - y); return a[Math.min(a.length - 1, Math.floor(a.length * p))]; };
const fmt = (ms) => (ms == null ? '—' : (ms / 1000).toFixed(1) + ' с');
function siteReport(s) {
  const eps = s.episodes.filter((e) => !e.gone);
  const byDir = (dir) => eps.filter((e) => e.dir === dir);
  const lines = [];
  lines.push(`── ${s.name} ──`);
  lines.push(`WS: сокетов ${s.sockets}, кадров ${s.frames}, объектов обновлений ${s.deltas}, снимков ${s.snaps}, пауз >30 с: ${s.gaps.length}${s.gaps.length ? ' (' + s.gaps.map((g) => g.at + ' ' + fmt(g.ms)).join(', ') + ')' : ''}`);
  lines.push(`Экран: опросов ${s.samples}, ячеек в среднем ${s.samples ? Math.round(s.cells / s.samples) : 0} (в окне ${s.samples ? Math.round((s.cellsVis || 0) / s.samples) : 0})` + (s.name === 'Пижама' ? `, привязано групп ${s.mapped}, не привязано групп ${s.unmapped}, ячеек без исхода ${s.unmappedCells || 0}` : `, модель≠экран: ${s.modelMis}`));
  if (s.name !== 'Пижама') {
    lines.push(`Своя подписка монитора (не через страницу): каналов ${own.subs.size}, публикаций ${own.pubs}, ошибок ${own.errors}, соединение ${own.connected ? 'есть' : 'потеряно'}`);
  }
  lines.push(`Снимок поправил поток (пуш/дельта не донесли смену состояния): ${s.corrections}`);
  s.corrEx.slice(0, 8).forEach((c) => lines.push(`    ${c.t} ${c.what} ${c.key.slice(0, 60)} поток=${c.stream ? 'открыт' : 'закрыт'} снимок=${c.snap ? 'открыт' : 'закрыт'}`));
  lines.push(`Задержка отрисовки смены состояния из потока (пуш/дельта → экран): n=${s.delays.length}, медиана ${fmt(q(s.delays, 0.5))}, p95 ${fmt(q(s.delays, 0.95))}, максимум ${fmt(q(s.delays, 1))}` + (s.delaysSkip ? ` · без замера ${s.delaysSkip} (ключа не было на экране)` : ''));
  const ds = s.delaysSnap || [];
  lines.push(`Задержка отрисовки смены из снимка (live2/каталог → экран): n=${ds.length}, медиана ${fmt(q(ds, 0.5))}, p95 ${fmt(q(ds, 0.95))}, максимум ${fmt(q(ds, 1))}` + (s.delaysSkipSnap ? ` · без замера ${s.delaysSkipSnap} (ключа не было на экране)` : ''));
  for (const dir of ['ws_closed_dom_open', 'ws_open_dom_closed']) {
    const e = byDir(dir); const d = e.map((x) => x.dur);
    const label = dir === 'ws_closed_dom_open' ? 'WS ЗАКРЫЛ, экран остался открыт' : 'WS открыл, экран остался закрыт';
    lines.push(`${label}: эпизодов ${e.length}, из них >${GRACE / 1000} с: ${d.filter((x) => x > GRACE).length}, >${FLAG / 1000} с: ${d.filter((x) => x > FLAG).length}; медиана ${fmt(q(d, 0.5))}, p95 ${fmt(q(d, 0.95))}, максимум ${fmt(q(d, 1))}`);
    // Главное число — по видимой области: именно её игрок видит и по ней ставит.
    // Полная строка выше оставлена для сравнения с прогонами до 18.09.2026.
    const ev2 = e.filter((x) => x.inView); const d2 = ev2.map((x) => x.dur);
    lines.push(`    из них по видимой области: ${ev2.length}, >${GRACE / 1000} с: ${d2.filter((x) => x > GRACE).length}, >${FLAG / 1000} с: ${d2.filter((x) => x > FLAG).length}; медиана ${fmt(q(d2, 0.5))}, p95 ${fmt(q(d2, 0.95))}, максимум ${fmt(q(d2, 1))}`);
    const bySrc = {}; e.forEach((x) => { bySrc[x.src] = (bySrc[x.src] || 0) + 1; });
    const evLevel = e.filter((x) => x.evOk === false).length;
    if (e.length) lines.push(`    по источнику последней смены: ${JSON.stringify(bySrc)}; из них при погашенном событии (st≠1/sg=0): ${evLevel}`);
    e.filter((x) => x.dur > FLAG).sort((p1, p2) => p2.dur - p1.dur).slice(0, 10).forEach((x) => lines.push(`    ${fmt(x.dur)}  [${x.src}] mk=${x.mk ? 1 : 0} oc=${x.oc ? 1 : 0} ev=${x.evOk === undefined ? '-' : (x.evOk ? 1 : 0)}${x.text ? ' кэф=' + x.text : ''}  ${s.evName.get(x.ev) || x.ev}  ${x.key.slice(0, 64)}`));
  }
  const gone = s.episodes.filter((e) => e.gone).length; if (gone) lines.push(`(эпизодов, оборвавшихся уходом исхода с экрана: ${gone})`);
  if (s.name === 'Пижама') {
    lines.push(`типы пушей update: ${JSON.stringify(s.acts)}`);
    const subs = s.subs || new Map(); let mk = 0; subs.forEach((v) => { mk += v.size; });
    let onScreen = 0, covered = 0;
    s.lastDom.forEach((_, key) => { const p = key.split('|'); onScreen++; const sub = subs.get(p[0]); if (sub && (sub.has('all') || sub.has(p[1]))) covered++; });
    lines.push(`подписка фронта на пуши: событий ${subs.size}, маркетов ${mk}; ключей на экране ${onScreen}, из них в подписке ${covered}; последняя подписка ${s.subsT ? Math.round((Date.now() - s.subsT) / 1000) + ' с назад' : 'не видел'}`);
    if (subs.size) { const ev0 = [...subs.keys()][0]; lines.push(`    пример подписки: событие ${ev0} → ${[...subs.get(ev0)].map((x) => x.slice(0, 8)).join(',')}`); }
  }
  // чем заканчивались эпизоды дольше допуска: по пушу потока или по снимку; была ли подписка
  for (const dir of ['ws_closed_dom_open', 'ws_open_dom_closed']) {
    const e = eps.filter((x) => x.dir === dir && x.dur > GRACE);
    if (!e.length) continue;
    const by = {}; e.forEach((x) => { const k = (x.endedBy || '?') + (x.subscribed === undefined ? '' : (x.subscribed ? ' / подписан' : ' / НЕ подписан')); by[k] = (by[k] || 0) + 1; });
    const pd = e.map((x) => x.pushesDuring || 0);
    lines.push(`${dir === 'ws_closed_dom_open' ? 'WS закрыл→экран открыт' : 'WS открыл→экран закрыт'} (>${GRACE / 1000} с): чем закончились ${JSON.stringify(by)}; пушей по ключу за эпизод: медиана ${q(pd, 0.5)}, максимум ${q(pd, 1)}`);
  }
  if (s.name === 'наш прототип') {
    const d = eps.filter((x) => x.dir === 'ws_closed_dom_open' && x.dump);
    if (d.length) {
      const pat = {}; d.forEach((x) => { const m = x.dump; const k = `inMain=${m.inMain ? 1 : 0} inIndex=${m.inIndex ? 1 : 0} mktOpen=${m.mkt ? (m.mkt.open ? 1 : 0) : '-'} selAv=${m.sel ? (m.sel.av ? 1 : 0) : '-'} btnDis=${m.btns.map((b) => (b.dis ? 1 : 0) + (b.inline ? 'i' : 't')).join(',')}`; pat[k] = (pat[k] || 0) + 1; });
      lines.push('Дампы опасных эпизодов (модель/DOM на старте):');
      Object.keys(pat).sort((a, b) => pat[b] - pat[a]).forEach((k) => lines.push(`    ${pat[k]} × ${k}`));
      d.slice(0, 4).forEach((x) => lines.push(`    пример ${fmt(x.dur)} ${s.evName.get(x.ev) || x.ev}: старт ${JSON.stringify(x.dump).slice(0, 330)}`));
      d.filter((x) => x.dumpEnd).slice(0, 2).forEach((x) => lines.push(`    конец ${fmt(x.dur)} ${s.evName.get(x.ev) || x.ev}: ${JSON.stringify(x.dumpEnd).slice(0, 330)}`));
      d.slice(0, 3).forEach((x) => lines.push(`    история ${x.key.slice(0, 40)}: mk ${JSON.stringify(x.hist.mk)} oc ${JSON.stringify(x.hist.oc)}`));
    }
  }
  return lines.join('\n');
}
function crossReport() {
  const lines = ['── между сайтами (общие ключи событие|маркет|исход) ──'];
  lines.push(`Линия (истина WS обоих): сравнений ${X.truthBoth}, совпало ${(100 * X.truthSame / Math.max(1, X.truthBoth)).toFixed(2)} %`);
  lines.push(`Экран (что видит игрок): сравнений ${X.domBoth}, совпало ${(100 * X.domSame / Math.max(1, X.domBoth)).toFixed(2)} %`);
  const sum = (list, label) => {
    const byDir = {}; list.filter((e) => !e.gone).forEach((e) => { (byDir[e.dir] = byDir[e.dir] || []).push(e.dur); });
    lines.push(label + ':');
    Object.keys(byDir).forEach((d) => lines.push(`    ${d}: ${byDir[d].length} эпизодов, медиана ${fmt(q(byDir[d], 0.5))}, p95 ${fmt(q(byDir[d], 0.95))}, максимум ${fmt(q(byDir[d], 1))}`));
    if (!Object.keys(byDir).length) lines.push('    расхождений не было');
  };
  sum(X.epT, 'Расхождения линии'); sum(X.epD, 'Расхождения экранов');
  return lines.join('\n');
}
function report(final) {
  const head = `${final ? 'ИТОГ' : 'Промежуточно'} ${ts()} UTC, прошло ${((Date.now() - T0) / 60000).toFixed(0)} мин из ${MIN}`;
  const txt = [head, siteReport(S.our), siteReport(S.pz), crossReport()].join('\n\n') + '\n';
  fs.writeFileSync(DIR + (final ? '/report.txt' : '/status.txt'), txt);
  if (final) console.log(txt);
}

// ── запуск ────────────────────────────────────────────────────────────────
let T0 = Date.now();
(async () => {
  const bOur = await chromium.launch(); const bPz = await chromium.launch();
  const our = await (await bOur.newContext({ viewport: { width: 1600, height: 1000 }, locale: 'en-US' })).newPage();
  const pz = await (await bPz.newContext({ viewport: { width: 1700, height: 1000 }, locale: 'en-US' })).newPage();

  our.on('websocket', (ws) => {
    const s = S.our; s.sockets++; log('[our] WS #' + s.sockets, ws.url().slice(0, 60));
    ws.on('framereceived', (f) => { const t = Date.now(); s.frames++; if (s.lastFrame && t - s.lastFrame > 30000) s.gaps.push({ at: ts(), ms: t - s.lastFrame }); s.lastFrame = t;
      String(f.payload).split('\n').forEach((line) => { if (!line.trim()) return; try { walkDeltas(JSON.parse(line), t); } catch (e) { /* не JSON */ } }); });
    ws.on('close', () => log('[our] WS закрыт'));
  });
  pz.on('websocket', (ws) => {
    if (!/sport\.paryajpam/.test(ws.url())) return;
    const s = S.pz; s.sockets++; log('[pz] WS #' + s.sockets, ws.url().slice(0, 60));
    ws.on('framesent', (f) => {
      const p = String(f.payload);
      if (/"action":"live2"/.test(p)) s.lastReqT = Date.now();
      if (/"action":"subscribe"/.test(p)) {
        try {
          const j = JSON.parse(p);
          if (j.events && typeof j.events === 'object') {
            if (j.unsubscribe || !s.subs) s.subs = new Map();
            Object.keys(j.events).forEach((ev) => { const v = j.events[ev]; s.subs.set(String(ev), new Set(Array.isArray(v) ? v.map(String) : ['all'])); });
            s.subsT = Date.now();
          }
        } catch (e) { /* не JSON */ }
      }
    });
    ws.on('framereceived', (f) => { const t = Date.now(); s.frames++; if (s.lastFrame && t - s.lastFrame > 30000) s.gaps.push({ at: ts(), ms: t - s.lastFrame }); s.lastFrame = t;
      let j; try { j = JSON.parse(String(f.payload)); } catch (e) { return; }
      if (j.action === 'live2' && j.data && typeof j.data === 'object') pzSnapshot(j.data, s.lastReqT || (t - 500), t);
      else if (Array.isArray(j.update)) pzUpdate(j.update, t); });
    ws.on('close', () => log('[pz] WS закрыт'));
  });

  our.on('response', async (r) => {
    if (!r.url().includes('/feed-health/catalog.json')) return;
    try { const cat = await r.json(); applyCatalog(cat, Date.now()); } catch (e) { log('[our] каталог со страницы не разобран:', String(e).slice(0, 80)); }
  });
  await our.goto('https://gem.x1b.site/sport-live/index.html?ln=en', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await pz.goto('https://paryajpam.com/sport/live', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await startOwnFeed();
  await new Promise((r) => setTimeout(r, 15000));
  T0 = Date.now();
  log('старт мониторинга на', MIN, 'мин');

  let lastCat = Date.now(), lastStatus = Date.now(), tick = 0;
  while (Date.now() - T0 < MIN * 60000) {
    const now = Date.now(); tick++;
    try {
      const o = await our.evaluate(OUR_DOM);
      const dom = new Map(Object.keys(o.cells).map((k) => [k, o.cells[k]]));
      S.our.lastVis = new Map(Object.keys(o.vis || {}).map((k) => [k, 1]));
      Object.keys(o.model).forEach((k) => { if (o.cells[k] != null && o.model[k] !== o.cells[k]) S.our.modelMis++; });
      compare(S.our, dom, now);
      // дамп по опасным эпизодам, пережившим допуск на отрисовку: на старте и на конце
      for (const ep of S.our.openEp.values()) {
        if (ep.dir === 'ws_closed_dom_open' && !ep.dump && now - ep.since > GRACE) {
          ep.dump = await our.evaluate(OUR_DUMP, ep.key); ep.dumpAt = Math.round((Date.now() - ep.since) / 1000);
        }
      }
      const ends = S.our.endDumps || []; S.our.endDumps = [];
      for (const ep of ends) {
        ep.dumpEnd = await our.evaluate(OUR_DUMP, ep.key);
        fs.appendFileSync(DIR + '/dumps.jsonl', JSON.stringify(Object.assign({ name: S.our.evName.get(ep.ev) || '' }, ep)) + '\n');
      }
    } catch (e) { log('[our] опрос экрана:', String(e).slice(0, 100)); }
    try {
      const p = await pz.evaluate(PZ_DOM, pzNames);
      compare(S.pz, pzMap(p.rows), now);
      if (tick === 5) log('[pz] строк на экране', p.rows.length, '| демо-режим:', p.demo);
    } catch (e) { log('[pz] опрос экрана:', String(e).slice(0, 100)); }
    cross(now);
    if (now - lastStatus > 300000) { lastStatus = now; report(false); log('промежуточный отчёт записан; эпизодов: наш', S.our.episodes.length, 'Пижама', S.pz.episodes.length); }
    const spent = Date.now() - now; if (spent < STEP) await new Promise((r) => setTimeout(r, STEP - spent));
  }
  report(true);
  fs.writeFileSync(DIR + '/DONE', ts());
  await bOur.close(); await bPz.close();
})().catch((e) => { log('АВАРИЯ:', String(e)); report(true); fs.writeFileSync(DIR + '/DONE', 'crash'); process.exit(1); });
