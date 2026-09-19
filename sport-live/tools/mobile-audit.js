/**
 * Аудит мобильного вида sport-live. Запускается НА тестовом сервере *10
 * (playwright + chromium уже стоят в /opt/pw-admin).
 *
 *   BASE=https://gem.x1b.site/sport-live/ OUT=/tmp/sl-mobile node mobile-audit.js
 *
 * Что делает: открывает лайв, линию, страницу события, купон и «Мои ставки»
 * в мобильных вьюпортах, снимает скриншоты и метрики вёрстки (горизонтальное
 * переполнение, мелкие тап-таргеты, обрезанный текст, перекрытие нижней навигацией).
 */
const { chromium } = require('/opt/pw-admin/node_modules/playwright');
const fs = require('fs');

const BASE = process.env.BASE || 'https://gem.x1b.site/sport-live/';
const OUT = process.env.OUT || '/tmp/sl-mobile';
const USER = process.env.USER_ID || '7758222';
const LN = process.env.LN || 'ru';
const VIEWPORTS = (process.env.VP || 'iphone14:390x844,android:360x800').split(',').map((s) => {
  const [name, size] = s.split(':');
  const [w, h] = size.split('x').map(Number);
  return { name, w, h };
});

const METRICS = () => {
  const W = innerWidth;
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none';
  };
  const name = (el) => el.tagName.toLowerCase() + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.') : '');

  // 1. горизонтальное переполнение документа и виновники (кроме собственных скролл-контейнеров)
  const scrollers = new Set();
  document.querySelectorAll('*').forEach((el) => {
    const cs = getComputedStyle(el);
    if (/(auto|scroll)/.test(cs.overflowX)) scrollers.add(el);
  });
  const inScroller = (el) => { for (let p = el.parentElement; p; p = p.parentElement) if (scrollers.has(p)) return true; return false; };
  const overflow = [];
  document.querySelectorAll('body *').forEach((el) => {
    if (!vis(el) || inScroller(el)) return;
    const r = el.getBoundingClientRect();
    if (r.right > W + 1 || r.left < -1) overflow.push(name(el) + ' [' + Math.round(r.left) + '..' + Math.round(r.right) + ']');
  });

  // 2. мелкие тап-таргеты (рекомендация 44x44, тревога < 32)
  const small = [];
  document.querySelectorAll('button, a, [role=button], .sb-odd, .sb-sport, .sb-mnav__it, .sb-lang__btn').forEach((el) => {
    if (!vis(el)) return;
    const r = el.getBoundingClientRect();
    if (r.height < 32 || r.width < 28) small.push(name(el) + ' ' + Math.round(r.width) + 'x' + Math.round(r.height));
  });

  // 3. обрезанный текст (ellipsis / клип)
  const clipped = [];
  document.querySelectorAll('.sb-row__name, .sb-row__league, .sb-hot__league, .sb-event-head__team-name, .sb-mk__title, .sb-slip__item-match').forEach((el) => {
    if (!vis(el)) return;
    if (el.scrollWidth > el.clientWidth + 2) clipped.push(name(el) + ' "' + el.textContent.trim().slice(0, 28) + '" ' + el.clientWidth + '<' + el.scrollWidth);
  });

  // 4. нижняя навигация: высота и не перекрывает ли она низ контента
  const nav = document.querySelector('.sb-mnav');
  const navBox = nav ? nav.getBoundingClientRect() : null;
  let behindNav = null;
  if (navBox) {
    const doc = document.documentElement;
    const atBottom = Math.abs(doc.scrollHeight - (scrollY + innerHeight)) < 4;
    behindNav = { navTop: Math.round(navBox.top), navH: Math.round(navBox.height), atBottom };
  }

  // 5. мелкий шрифт
  const tiny = new Set();
  document.querySelectorAll('body *').forEach((el) => {
    if (!vis(el) || !el.childNodes.length) return;
    const hasText = Array.from(el.childNodes).some((n) => n.nodeType === 3 && n.textContent.trim());
    if (!hasText) return;
    const fs = parseFloat(getComputedStyle(el).fontSize);
    if (fs && fs < 10.5) tiny.add(name(el) + ' ' + fs + 'px');
  });

  return {
    innerWidth: W,
    scrollWidth: document.documentElement.scrollWidth,
    overflow: overflow.slice(0, 12),
    smallTargets: small.slice(0, 12),
    clipped: clipped.slice(0, 12),
    nav: behindNav,
    tinyFonts: Array.from(tiny).slice(0, 10),
  };
};

async function shot(page, dir, tag) {
  await page.screenshot({ path: `${dir}/${tag}.png` });
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  const report = {};

  for (const vp of VIEWPORTS) {
    const ctx = await browser.newContext({
      viewport: { width: vp.w, height: vp.h },
      deviceScaleFactor: 2,
      isMobile: true,
      hasTouch: true,
      userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
      locale: 'ru-RU',
    });
    const page = await ctx.newPage();
    const errors = [];
    page.on('console', (m) => { if (m.type() === 'error' && !/favicon/.test(m.text())) errors.push(m.text().slice(0, 160)); });
    page.on('pageerror', (e) => errors.push('pageerror: ' + String(e).slice(0, 160)));

    const dir = `${OUT}/${vp.name}`;
    fs.mkdirSync(dir, { recursive: true });
    const R = (report[vp.name] = { errors });

    // ── лайв
    await page.goto(`${BASE}?ln=${LN}&user=${USER}`, { waitUntil: 'networkidle', timeout: 60000 });
    await page.waitForTimeout(3500);
    await shot(page, dir, '1-live');
    R.live = await page.evaluate(METRICS);

    // прокрутка вниз — хвост списка и перекрытие навигацией
    await page.evaluate(() => scrollTo(0, document.documentElement.scrollHeight));
    await page.waitForTimeout(800);
    await shot(page, dir, '2-live-bottom');
    R.liveBottom = await page.evaluate(METRICS);
    await page.evaluate(() => scrollTo(0, 0));

    // ── линия
    await page.goto(`${BASE}line.html?ln=${LN}&user=${USER}`, { waitUntil: 'networkidle', timeout: 60000 });
    await page.waitForTimeout(3000);
    await shot(page, dir, '3-line');
    R.line = await page.evaluate(METRICS);

    // ── страница события (первая строка лайва)
    await page.goto(`${BASE}?ln=${LN}&user=${USER}`, { waitUntil: 'networkidle', timeout: 60000 });
    await page.waitForTimeout(3000);
    const eid = await page.evaluate(() => {
      const row = document.querySelector('.sb-row[data-eid]');
      return row ? row.dataset.eid : null;
    });
    R.eventId = eid;
    if (eid) {
      await page.goto(`${BASE}event.html?id=${eid}&ln=${LN}&user=${USER}`, { waitUntil: 'networkidle', timeout: 60000 });
      await page.waitForTimeout(4000);
      await shot(page, dir, '4-event');
      R.event = await page.evaluate(METRICS);
      await page.evaluate(() => scrollTo(0, 600));
      await page.waitForTimeout(500);
      await shot(page, dir, '5-event-markets');
    }

    // ── купон: тапнуть по кэфу и открыть вкладку купона
    await page.goto(`${BASE}?ln=${LN}&user=${USER}`, { waitUntil: 'networkidle', timeout: 60000 });
    await page.waitForTimeout(3500);
    const tapped = await page.evaluate(() => {
      const b = Array.from(document.querySelectorAll('.sb-odd')).find((x) => !x.disabled);
      if (!b) return null;
      b.click();
      return b.textContent.trim().replace(/\s+/g, ' ');
    });
    R.tappedOdd = tapped;
    await page.waitForTimeout(1200);
    await shot(page, dir, '6-after-tap');
    const openedCoupon = await page.evaluate(() => {
      const item = document.querySelector('[data-mnav="coupon"]');
      if (item) { item.click(); return item.textContent.trim().replace(/\s+/g, ' '); }
      return null;
    });
    R.couponNav = openedCoupon;
    await page.waitForTimeout(1200);
    await shot(page, dir, '7-coupon');
    R.coupon = await page.evaluate(METRICS);

    // ── мои ставки
    const openedBets = await page.evaluate(() => {
      const item = document.querySelector('[data-mnav="bets"]');
      if (item) { item.click(); return item.textContent.trim().replace(/\s+/g, ' '); }
      return null;
    });
    R.betsNav = openedBets;
    await page.waitForTimeout(1500);
    await shot(page, dir, '8-bets');
    R.bets = await page.evaluate(METRICS);

    await ctx.close();
  }

  await browser.close();
  fs.writeFileSync(`${OUT}/report.json`, JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
})();
