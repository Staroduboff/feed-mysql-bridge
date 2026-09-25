// Независимая подписка монитора на наш Centrifugo — ОТДЕЛЬНЫМ процессом.
//
// Почему отдельным. Сначала подписка жила прямо в wsmon.js, рядом с двумя
// браузерами Playwright. 19.09.2026 она отвалилась на шестой минуте трёхчасового
// прогона и не вернулась: 174 минуты замер шёл с мёртвой линией. Проба тем же
// кодом, но без браузеров, отработала 12 минут подряд без единого разрыва на
// ~40 тыс. публикаций в минуту. Причина не в сервере и не в объёме: главный
// процесс секунду за секундой ждёт ответа от Playwright, и сокету не хватает
// времени вычитывать поток и отвечать на пинги.
//
// Здесь процесс занят только сокетом. Дельты уходят родителю пачками раз в 100 мс
// (по одной — лишние переключения на 700 сообщений в секунду), состояние — раз в
// 10 с, чтобы отчёт мог честно написать, сколько подписка молчала.
const { Centrifuge } = require('/opt/pw-admin/node_modules/centrifuge');
const WebSocket = require('/opt/pw-admin/node_modules/ws');
const https = require('https');

const WS_URL = process.env.WSMON_FEED_WS || 'wss://gem.x1b.site/centrifugo/connection/websocket';
const CATALOG = process.env.WSMON_FEED_CATALOG || 'https://gem.x1b.site/feed-health/catalog.json';
const FLUSH_MS = 100;
const SYNC_MS = 60000;
const STAT_MS = 10000;

const state = { pubs: 0, drops: 0, errors: 0, connected: false, downSince: Date.now(), downMs: 0, lastCode: null, lastReason: '' };
const subs = new Map();
let queue = [];

const send = (msg) => { try { if (process.send) process.send(msg); } catch (e) { /* родитель ушёл */ } };
const note = (text) => send({ log: text });

function fetchJson(url) {
	return new Promise((res, rej) => {
		https.get(url, (r) => {
			let b = '';
			r.on('data', (c) => (b += c));
			r.on('end', () => { try { res(JSON.parse(b)); } catch (e) { rej(e); } });
		}).on('error', rej);
	});
}

const cf = new Centrifuge(WS_URL, { websocket: WebSocket });

cf.on('connected', () => {
	state.connected = true;
	if (state.downSince) { state.downMs += Date.now() - state.downSince; state.downSince = null; }
	note('соединение есть');
});
// В centrifuge-js `disconnected` — состояние терминальное: автоповтор живёт в
// `connecting`, сюда попадают, когда клиент сдался. Поднимаем сами.
cf.on('disconnected', (ctx) => {
	state.connected = false;
	state.drops++;
	if (!state.downSince) state.downSince = Date.now();
	state.lastCode = ctx && ctx.code;
	state.lastReason = (ctx && ctx.reason) || '';
	note('разрыв ' + state.lastCode + ' ' + state.lastReason + ' — поднимаю заново');
	setTimeout(() => { try { cf.connect(); } catch (e) { /* уже поднимается */ } }, 3000);
});
cf.on('error', (ctx) => {
	state.errors++;
	if (state.errors <= 3) note('ошибка клиента: ' + JSON.stringify((ctx && ctx.error) || ctx).slice(0, 160));
});

function subscribe(channel) {
	if (subs.has(channel)) return;
	const sub = cf.newSubscription(channel);
	sub.on('publication', (ctx) => { state.pubs++; queue.push({ t: Date.now(), d: ctx.data || {} }); });
	sub.on('error', () => { state.errors++; });
	sub.subscribe();
	subs.set(channel, sub);
}

// Состав каналов берём из каталога и освежаем раз в минуту: новые живые события
// должны попадать под наблюдение без перезапуска прогона.
async function syncChannels() {
	let cat;
	try { cat = await fetchJson(CATALOG + '?' + Date.now()); } catch (e) { state.errors++; return; }
	(cat.sports || []).forEach((sp) => subscribe('sport:' + sp.id));
	(cat.events || []).forEach((e) => { if (e && e.id && e.main) subscribe('event:' + e.id); });
}

setInterval(() => { if (queue.length) { send({ deltas: queue }); queue = []; } }, FLUSH_MS);
setInterval(() => {
	const down = state.downMs + (state.downSince ? Date.now() - state.downSince : 0);
	send({ stat: { channels: subs.size, pubs: state.pubs, drops: state.drops, errors: state.errors,
		connected: state.connected, downMs: down, lastCode: state.lastCode, lastReason: state.lastReason } });
}, STAT_MS);
setInterval(syncChannels, SYNC_MS);

cf.connect();
syncChannels().then(() => note('каналов ' + subs.size));

process.on('message', (m) => { if (m === 'stop') { try { cf.disconnect(); } catch (e) { /* уже закрыт */ } process.exit(0); } });
