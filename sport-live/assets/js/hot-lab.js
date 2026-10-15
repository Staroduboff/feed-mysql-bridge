/* eslint-disable no-undef */
/**
 * hot-lab.js — витрина вариантов блока «Горячие события».
 *
 * Берёт тот же набор событий, что и боевой блок на index.html (SB.visibleEvents()
 * с флагом isHot, добор из live/prematch, если горячих мало), и рисует его шестью
 * композициями: нынешняя, лента, герой+список, плитки, строки-хайлайты, тикер, табло.
 * Данные живые — live-data.js держит каталог и дельты Centrifugo; купон не задействован,
 * плашки исходов здесь только показывают цену.
 */
(function () {
  const $ = (sel) => document.querySelector(sel);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const fmtOdds = (n) => (n > 0 ? n.toFixed(2) : '—');
  const LOCALE = (window.SB_I18N && SB_I18N.LOCALE) || 'ru-RU';
  const fmtTime = (ts) => new Date(ts).toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
  const fmtHM = (ts) => new Date(ts).toLocaleString(LOCALE, { hour: '2-digit', minute: '2-digit' });

  function logoHue(s) { let h = 0; for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360; return h; }
  function logoInitials(name) {
    const c = String(name || '?').replace(/[^\p{L}\p{N} .&]/gu, ' ').replace(/\./g, ' ').trim();
    const w = c.split(/\s+/).filter(Boolean);
    return (w.length >= 2 ? (w[0][0] + w[1][0]) : c.slice(0, 2)).toUpperCase();
  }
  function teamLogo(name, size) {
    const url = (window.SB && SB.logoUrl && SB.logoUrl(name)) || '';
    const img = url ? `<img class="sb-logo__img" src="${esc(url)}" alt="" loading="lazy" onerror="this.remove()">` : '';
    return `<span class="sb-logo" style="width:${size}px;height:${size}px">`
      + `<span class="sb-logo__mono" style="--h:${logoHue(String(name || ''))};font-size:${Math.round(size * 0.4)}px">${esc(logoInitials(name))}</span>`
      + `${img}</span>`;
  }

  /** горячие события: сначала помеченные фидом, затем добор живыми и ближайшими */
  function hotEvents(limit) {
    const all = SB.visibleEvents();
    const seen = new Set();
    const out = [];
    const push = (list) => list.forEach((e) => { if (!seen.has(e.id) && out.length < limit) { seen.add(e.id); out.push(e); } });
    push(all.filter((e) => e.isHot && e.state === 'live'));
    push(all.filter((e) => e.isHot));
    push(all.filter((e) => e.state === 'live'));
    push(all);
    return out;
  }

  const isLive = (e) => e.state === 'live';
  const scoreOf = (e) => (isLive(e) && e.score ? `${e.score.home}:${e.score.away}` : '');
  const whenOf = (e) => (isLive(e) ? (e.period || 'LIVE') : fmtTime(e.startTs));
  const mkt = (e) => e.mainMarkets[0] || null;
  const sels = (e) => (mkt(e) ? mkt(e).selections.slice(0, 3) : []);
  const leagueName = (e) => SB.league(e.leagueId).name || '';
  const sportIcon = (e) => SB.sport(e.sportAlias).icon || '🏅';
  const href = (e) => `event.html?id=${e.id}`;

  function odd(s, extra) {
    if (!s) return '<span class="sb-odd sb-odd--disabled"><span class="sb-odd__value">—</span></span>';
    const cls = 'sb-odd' + (s.available ? '' : ' sb-odd--disabled') + (extra ? ' ' + extra : '');
    return `<span class="${cls}"><span class="sb-odd__label">${esc(s.label)}</span><span class="sb-odd__value">${fmtOdds(s.odds)}</span></span>`;
  }
  function oddInline(s) {
    if (!s) return '<span class="lab-odd-inline sb-odd--disabled">—</span>';
    const cls = 'lab-odd-inline' + (s.available ? '' : ' sb-odd--disabled');
    return `<span class="${cls}"><b>${esc(s.label)}</b> ${fmtOdds(s.odds)}</span>`;
  }

  function sectionHead(title, chip, count) {
    return `<div class="sb-section__head">
      <span class="sb-section__title">${title}</span>
      ${chip ? `<span class="sb-section__chip sb-section__chip--hot">${chip}</span>` : ''}
      <span class="sb-section__count">${count}</span>
    </div>`;
  }

  const pill = (e) => (isLive(e)
    ? '<span class="lab-pill lab-pill--live">LIVE</span>'
    : `<span class="lab-pill">${esc(fmtHM(e.startTs))}</span>`);

  /* ── 0. Как сейчас ─────────────────────────────────────────────────────── */
  function v0(list) {
    return `<div class="sb-section sb-section--hot">
      ${sectionHead('🔥 Hot events', 'ТОП', list.length)}
      <div class="sb-hot__list">
        ${list.map((e) => `
          <a class="sb-hot__card" href="${href(e)}">
            <div class="sb-hot__card-head">
              <span style="font-size:18px">${sportIcon(e)}</span>
              <span class="sb-hot__card-league">${esc(leagueName(e))}</span>
              <span class="sb-hot__card-pill ${isLive(e) ? 'sb-hot__card-pill--live' : ''}">${isLive(e) ? 'LIVE' : 'Пре'}</span>
            </div>
            <div class="sb-hot__card-teams">
              <div class="sb-hot__team">${teamLogo(e.home, 48)}<span class="sb-hot__team-name">${esc(e.home)}</span></div>
              <div class="sb-hot__center">
                <span class="sb-hot__score${isLive(e) ? '' : ' sb-hot__score--vs'}">${isLive(e) ? esc(scoreOf(e)) : 'vs'}</span>
                <span class="sb-hot__when${isLive(e) ? ' sb-hot__when--live' : ''}">${esc(whenOf(e))}</span>
              </div>
              <div class="sb-hot__team">${teamLogo(e.away, 48)}<span class="sb-hot__team-name">${esc(e.away)}</span></div>
            </div>
            <div class="sb-hot__card-odds">${sels(e).map((s) => odd(s)).join('')}</div>
          </a>`).join('')}
      </div>
    </div>`;
  }

  /* ── A. Лента карточек ─────────────────────────────────────────────────── */
  function vA(list) {
    return `<div class="sb-section lab-sec">
      ${sectionHead('🔥 Горячие', 'ТОП', list.length)}
      <div class="labA__wrap">
        <div class="labA__list">
          ${list.map((e) => `
            <a class="labA__card" href="${href(e)}" style="--hue:${logoHue(e.home + e.away)}">
              <div class="labA__head">
                <span class="labA__sport">${sportIcon(e)}</span>
                <span class="labA__league">${esc(leagueName(e))}</span>
                ${pill(e)}
              </div>
              <div class="labA__team">
                ${teamLogo(e.home, 22)}<span class="labA__name">${esc(e.home)}</span>
                <span class="labA__sc">${isLive(e) && e.score ? esc(String(e.score.home)) : ''}</span>
              </div>
              <div class="labA__team">
                ${teamLogo(e.away, 22)}<span class="labA__name">${esc(e.away)}</span>
                <span class="labA__sc">${isLive(e) && e.score ? esc(String(e.score.away)) : ''}</span>
              </div>
              <div class="labA__min">${esc(isLive(e) ? whenOf(e) : fmtTime(e.startTs))}</div>
              <div class="labA__odds">${sels(e).map((s) => odd(s)).join('')}</div>
            </a>`).join('')}
        </div>
      </div>
    </div>`;
  }

  /* ── B. Герой + список ─────────────────────────────────────────────────── */
  function vB(list) {
    const hero = list[0];
    const rest = list.slice(1, 6);
    if (!hero) return '';
    return `<div class="sb-section lab-sec">
      ${sectionHead('🔥 Главные события', null, list.length)}
      <div class="labB">
        <a class="labB__hero" href="${href(hero)}">
          <div class="labB__hero-top">
            <span class="labB__hero-league">${sportIcon(hero)} ${esc(leagueName(hero))}</span>
            ${pill(hero)}
          </div>
          <div class="labB__hero-body">
            <div class="labB__hero-team">${teamLogo(hero.home, 52)}<span>${esc(hero.home)}</span></div>
            <div class="labB__hero-mid">
              <span class="labB__hero-score">${isLive(hero) ? esc(scoreOf(hero)) : fmtHM(hero.startTs)}</span>
              <span class="labB__hero-when">${esc(isLive(hero) ? whenOf(hero) : fmtTime(hero.startTs))}</span>
            </div>
            <div class="labB__hero-team">${teamLogo(hero.away, 52)}<span>${esc(hero.away)}</span></div>
          </div>
          <div class="labB__hero-odds">${sels(hero).map((s) => odd(s, 'sb-odd--wide')).join('')}</div>
        </a>
        <div class="labB__list">
          ${rest.map((e) => `
            <a class="labB__row" href="${href(e)}">
              <div class="labB__row-main">
                <div class="labB__row-league">${sportIcon(e)} ${esc(leagueName(e))} · <span class="labB__row-when">${esc(isLive(e) ? whenOf(e) : fmtHM(e.startTs))}</span></div>
                <div class="labB__row-teams">
                  <span>${esc(e.home)}</span>
                  <span>${esc(e.away)}</span>
                </div>
              </div>
              ${isLive(e) && e.score ? `<div class="labB__row-score"><span>${esc(String(e.score.home))}</span><span>${esc(String(e.score.away))}</span></div>` : ''}
              <div class="labB__row-odds">${sels(e).map((s) => odd(s)).join('')}</div>
            </a>`).join('')}
        </div>
      </div>
    </div>`;
  }

  /* ── C. Плитки «Топ линия» ─────────────────────────────────────────────── */
  function vC(list) {
    return `<div class="sb-section lab-sec">
      ${sectionHead('🔥 Топ линия', null, list.length)}
      <div class="labC">
        ${list.map((e) => `
          <a class="labC__card" href="${href(e)}">
            <div class="labC__top">
              <span class="labC__when">${esc(isLive(e) ? whenOf(e) : fmtTime(e.startTs))}</span>
              <span class="labC__league">${esc(leagueName(e))}</span>
              ${isLive(e) ? '<span class="lab-pill lab-pill--live">LIVE</span>' : ''}
            </div>
            <div class="labC__mid">
              <span class="labC__logos">${teamLogo(e.home, 26)}${teamLogo(e.away, 26)}</span>
              <span class="labC__names"><span>${esc(e.home)}</span><span>${esc(e.away)}</span></span>
              ${isLive(e) && e.score ? `<span class="labC__score"><span>${esc(String(e.score.home))}</span><span>${esc(String(e.score.away))}</span></span>` : ''}
            </div>
            <div class="labC__odds">${sels(e).map((s) => oddInline(s)).join('')}</div>
          </a>`).join('')}
      </div>
    </div>`;
  }

  /* ── D. Строки-хайлайты ────────────────────────────────────────────────── */
  function vD(list) {
    const m = list[0] ? mkt(list[0]) : null;
    const cols = (m ? m.selections.slice(0, 3) : []).map((s) => esc(s.label));
    return `<div class="sb-section lab-sec">
      ${sectionHead('🔥 Горячее сейчас', null, list.length)}
      <div class="labD">
        <div class="labD__cols">
          <span></span><span></span><span></span>
          ${cols.map((c) => `<span>${c}</span>`).join('')}
        </div>
        ${list.map((e) => `
          <a class="labD__row" href="${href(e)}">
            <span class="labD__flame">🔥</span>
            <div class="labD__main">
              <div class="labD__teams">
                <span>${teamLogo(e.home, 18)}<em class="labD__nm">${esc(e.home)}</em></span>
                <span>${teamLogo(e.away, 18)}<em class="labD__nm">${esc(e.away)}</em></span>
              </div>
              <div class="labD__meta">${sportIcon(e)} ${esc(leagueName(e))} · <span class="${isLive(e) ? 'labD__live' : ''}">${esc(isLive(e) ? whenOf(e) : fmtTime(e.startTs))}</span></div>
            </div>
            ${isLive(e) && e.score ? `<div class="labD__score"><span>${esc(String(e.score.home))}</span><span>${esc(String(e.score.away))}</span></div>` : '<div class="labD__score labD__score--empty"></div>'}
            <div class="labD__odds">${sels(e).map((s) => odd(s)).join('')}</div>
          </a>`).join('')}
      </div>
    </div>`;
  }

  /* ── E. Тикер ──────────────────────────────────────────────────────────── */
  function vE(list) {
    return `<div class="labE">
      <span class="labE__tag">🔥 ТОП</span>
      <div class="labE__track">
        ${list.map((e) => `
          <a class="labE__chip" href="${href(e)}">
            <span class="labE__sport">${sportIcon(e)}</span>
            <span class="labE__names">${esc(e.home)} <i>—</i> ${esc(e.away)}</span>
            <span class="labE__sc ${isLive(e) ? 'labE__sc--live' : ''}">${isLive(e) ? esc(scoreOf(e)) : esc(fmtHM(e.startTs))}</span>
            <span class="labE__odds">${sels(e).map((s) => `<b>${fmtOdds(s.odds)}</b>`).join('')}</span>
          </a>`).join('')}
      </div>
    </div>`;
  }

  /* ── F. Табло 3 в ряд ──────────────────────────────────────────────────── */
  function vF(list) {
    return `<div class="sb-section lab-sec">
      ${sectionHead('🔥 Горячие события', 'ТОП', list.length)}
      <div class="labF">
        ${list.map((e) => `
          <a class="labF__card" href="${href(e)}">
            <div class="labF__head">
              <span class="labF__league">${sportIcon(e)} ${esc(leagueName(e))}</span>
              ${pill(e)}
            </div>
            <div class="labF__row">
              ${teamLogo(e.home, 30)}
              <span class="labF__name">${esc(e.home)}</span>
              <span class="labF__sc">${isLive(e) && e.score ? esc(String(e.score.home)) : ''}</span>
            </div>
            <div class="labF__row">
              ${teamLogo(e.away, 30)}
              <span class="labF__name">${esc(e.away)}</span>
              <span class="labF__sc">${isLive(e) && e.score ? esc(String(e.score.away)) : ''}</span>
            </div>
            <div class="labF__foot">
              <span class="labF__when ${isLive(e) ? 'labF__when--live' : ''}">${esc(isLive(e) ? whenOf(e) : fmtTime(e.startTs))}</span>
              <div class="labF__odds">${sels(e).map((s) => odd(s)).join('')}</div>
            </div>
          </a>`).join('')}
      </div>
    </div>`;
  }

  /* ── Рендер ────────────────────────────────────────────────────────────── */
  let pending = null;
  function renderAll() {
    const list = hotEvents(8);
    if (!list.length) return;
    $('#v0').innerHTML = v0(list.slice(0, 6));
    $('#vA').innerHTML = vA(list);
    $('#vB').innerHTML = vB(list);
    $('#vC').innerHTML = vC(list.slice(0, 6));
    $('#vD').innerHTML = vD(list.slice(0, 5));
    $('#vE').innerHTML = vE(list);
    $('#vF').innerHTML = vF(list.slice(0, 6));
  }
  function schedule() {
    if (pending) return;
    pending = setTimeout(() => { pending = null; renderAll(); }, 900);
  }

  SB.live.initIndex(() => schedule(), document.querySelector('#sb-conn')).then(renderAll);
})();
