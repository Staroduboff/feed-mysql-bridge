/**
 * Проверка «устойчивости демонстрации» sport-live: шесть сценариев, каждый — подмена
 * ответа сервера в браузере, чтобы не трогать живой стенд.
 *   1 базовый          — полосы нет, лайв на месте
 *   2 каталог отстал    — t каталога на 10 минут назад → жёлтая полоса
 *   3 нет связи с WS    — соединение Centrifugo закрывается → красная полоса
 *   4 связь вернулась   — полоса уходит сама
 *   5 гейт закрыт       — wallet.php отдаёт gate.open=false → полоса + запрет ставки
 *   6 нет лайва         — все события каталога переведены в предматч → объяснение
 *                         и ближайшие матчи линии вместо пустой таблицы
 * Запускается на тестовом сервере: node resilience-test.js
 */
const { chromium } = require('/opt/pw-admin/node_modules/playwright');
const fs = require('fs');

const BASE = 'https://gem.x1b.site/sport-live/';
const OUT = process.env.OUT || '/opt/sl-mobile/resilience';
const U = '?ln=ru&user=7758222';

const snap = (page) => page.evaluate(() => {
  const a = document.querySelector('#sb-alert');
  const empty = document.querySelector('.sb-empty');
  const cta = document.querySelector('.sb-slip__cta');
  return {
    alert: a && !a.hidden ? { cls: a.className, text: a.textContent.trim().slice(0, 120) } : null,
    conn: (document.querySelector('#sb-conn') || {}).className,
    liveRows: document.querySelectorAll('#sb-live .sb-row[data-eid]').length,
    empty: empty ? { title: (empty.querySelector('.sb-empty__title') || {}).textContent, text: (empty.querySelector('.sb-empty__text') || {}).textContent, btn: (empty.querySelector('.sb-empty__btn') || {}).textContent } : null,
    emptyRows: document.querySelectorAll('#sb-live .sb-table--x .sb-row[data-eid]').length,
    upcomingHead: (document.querySelector('.sb-empty__sep') || {}).textContent,
    slipNotice: (document.querySelector('.sb-slip__notice') || {}).textContent,
    ctaDisabled: cta ? cta.disabled : null,
  };
});

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  const res = {};
  const mk = async () => {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 }, locale: 'ru-RU' });
    const page = await ctx.newPage();
    page.on('pageerror', (e) => (res.errors = (res.errors || []).concat(String(e).slice(0, 140))));
    return { ctx, page };
  };

  // ── 1. базовый ──────────────────────────────────────────────────────────────
  {
    const { ctx, page } = await mk();
    await page.goto(BASE + U, { waitUntil: 'networkidle', timeout: 60000 });
    await page.waitForTimeout(4000);
    res['1-base'] = await snap(page);
    await page.screenshot({ path: `${OUT}/1-base.png` });
    await ctx.close();
  }

  // ── 2. каталог отстал на 10 минут ──────────────────────────────────────────
  {
    const { ctx, page } = await mk();
    await ctx.route('**/feed-health/catalog.json*', async (route) => {
      const r = await route.fetch();
      const j = await r.json();
      j.t = j.t - 600;                                  // «собран 10 минут назад»
      await route.fulfill({ response: r, json: j });
    });
    await page.goto(BASE + U, { waitUntil: 'networkidle', timeout: 60000 });
    await page.waitForTimeout(4000);
    res['2-stale'] = await snap(page);
    await page.screenshot({ path: `${OUT}/2-stale.png` });
    await ctx.close();
  }

  // ── 3 и 4. обрыв связи с сервером котировок и восстановление ───────────────
  {
    const { ctx, page } = await mk();
    let kill = true;
    await page.routeWebSocket('**/centrifugo/**', (ws) => { if (kill) ws.close(); else ws.connectToServer(); });
    await page.goto(BASE + U, { waitUntil: 'networkidle', timeout: 60000 });
    await page.waitForTimeout(13000);
    res['3-offline'] = await snap(page);
    await page.screenshot({ path: `${OUT}/3-offline.png` });
    kill = false;                                       // пускаем соединение — клиент переподключается сам
    await page.waitForTimeout(35000);                   // у centrifuge экспоненциальная пауза между попытками
    res['4-recovered'] = await snap(page);
    await page.screenshot({ path: `${OUT}/4-recovered.png` });
    await ctx.close();
  }

  // ── 5. гейт закрыл приём ставок ────────────────────────────────────────────
  {
    const { ctx, page } = await mk();
    await ctx.route('**/api/wallet.php*', async (route) => {
      const r = await route.fetch();
      let j;
      try { j = await r.json(); } catch (e) { return route.fulfill({ response: r }); }
      if (j && j.result) j.gate = { open: false, state: 'CLOSED', reasons: ['amqp_bad'], lag: 42, age: 3 };
      await route.fulfill({ response: r, json: j });
    });
    await page.goto(BASE + U, { waitUntil: 'networkidle', timeout: 60000 });
    await page.waitForTimeout(5000);
    // положим исход в купон — проверим, что ставку не дают сделать
    await page.evaluate(() => { const b = Array.from(document.querySelectorAll('.sb-odd')).find((x) => !x.disabled); if (b) b.click(); });
    await page.waitForTimeout(1200);
    res['5-gate'] = await snap(page);
    await page.screenshot({ path: `${OUT}/5-gate.png` });
    await ctx.close();
  }

  // ── 6. нет матчей в лайве ──────────────────────────────────────────────────
  {
    const { ctx, page } = await mk();
    await ctx.route('**/feed-health/catalog.json*', async (route) => {
      const r = await route.fetch();
      const j = await r.json();
      (j.events || []).forEach((e) => { e.stage = 1; e.sv = 'Created'; delete e.score; delete e.sc; });  // stage=2 — лайв, остальное предматч
      await route.fulfill({ response: r, json: j });
    });
    await page.routeWebSocket('**/centrifugo/**', (ws) => ws.close());   // иначе дельты вернут матчам лайв
    await page.goto(BASE + U, { waitUntil: 'networkidle', timeout: 60000 });
    await page.waitForTimeout(5000);                                     // до порога полосы «нет связи» (8 с)
    res['6-nolive'] = await snap(page);
    await page.screenshot({ path: `${OUT}/6-nolive.png` });
    await page.screenshot({ path: `${OUT}/6-nolive-full.png`, fullPage: true });
    await ctx.close();
  }

  await browser.close();
  fs.writeFileSync(`${OUT}/report.json`, JSON.stringify(res, null, 1));
  console.log(JSON.stringify(res, null, 1));
})();
