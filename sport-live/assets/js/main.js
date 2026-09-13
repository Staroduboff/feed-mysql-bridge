/* eslint-disable no-undef */
/**
 * Sportsbook iframe — дизайн 01 «Пижама-фирменный» на живых данных.
 * Движок рендера взят из sportsbook-iframe-designs/designs/01-pyzhama-original/assets/js/main.js
 * (разметка и классы 1-в-1), изменения только под живой источник:
 *  - данные приходят из assets/js/live-data.js (window.SB того же контракта, что у мока);
 *  - id исходов строковые ("ev|market|outcome"), купон хранит строки;
 *  - изменение кэфа подсвечивается (зелёный рост / красное падение);
 *  - период и счёт обновляются на месте без перерисовки таблицы; таблицы и купон
 *    пересобираются только при реальном изменении состава (иначе строки мигали);
 *  - «Сделать ставку» списывает ставку с эмулированного кошелька партнёра
 *    (api/wallet.php), баланс в шапке — оттуда же; выигрыш начисляет сборщик.
 */

(function () {
  const $  = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const T = (k, v) => SB_I18N.t(k, v);   // строки интерфейса (assets/js/i18n.js), язык — SB_I18N.lang
  const fmtMoney = (n) => new Intl.NumberFormat(SB_I18N.LOCALE, { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n);
  const fmtOdds  = (n) => (n > 0 ? n.toFixed(2) : '—');
  const fmtTime  = (ts) => new Date(ts).toLocaleString(SB_I18N.LOCALE, { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  /* Внутренние переходы сохраняют параметры встраивания: ?user= (кошелёк посетителя) и
     ?ln= (язык). Витрина product-showcase открывает страницу в iframe с этими параметрами,
     и клик по событию не должен уводить на страницу с другим пользователем или языком. */
  const NAV_KEEP = ['user', 'ln'];
  function navUrl(href) {
    const cur = new URLSearchParams(location.search);
    const u = new URL(href, location.href);
    NAV_KEEP.forEach((k) => { const v = cur.get(k); if (v && !u.searchParams.has(k)) u.searchParams.set(k, v); });
    return u.pathname + u.search + u.hash;
  }
  /* статические ссылки шапки («← К списку событий») — те же параметры */
  function keepNavParams() { $$('.sb-header__nav a[href]').forEach((a) => { a.href = navUrl(a.getAttribute('href')); }); }

  /* Лайв и линия — разные страницы, как у букмекеров: index.html (лайв) и line.html (линия).
     У каждой свой URL и своя запись в истории браузера; переключатель — над списком видов
     спорта в сайдбаре. Выбранный вид спорта переносится между страницами через хэш. */
  let PAGE = 'live';                                          // 'live' | 'line'
  const PAGE_FILE = { live: 'index.html', line: 'line.html' };
  const pageState = () => (PAGE === 'line' ? 'prematch' : 'live');

  let activeSportAlias = null; // null = все виды спорта
  let activeLeagueId = null;   // null = все лиги выбранного вида спорта
  let openSport = null;        // вид спорта с раскрытым списком лиг (как в 1xBet)
  let sidebarLinks = false;    // страница события: клики по сайдбару ведут на список (#sport=…)
  let layouts = {};            // геометрия колонок кэфов по видам спорта (набор маркетов у каждого свой)

  /** события текущей страницы: лайв или линия */
  const modeEvents = () => SB.visibleEvents().filter((e) => e.state === pageState());
  /** фильтр сайдбара: выбранный вид спорта и лига */
  const inFilter = (e) => (!activeSportAlias || e.sportAlias === activeSportAlias)
    && (!activeLeagueId || e.leagueId === activeLeagueId);

  /* ── Логотипы команд (монограмма; карта логотипов мока не используется) ── */
  const TEAM_LOGOS = (typeof window !== 'undefined' && window.__SB_LOGOS__) || {};
  function logoHue(s) { let h = 0; for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360; return h; }
  function logoInitials(name) {
    const c = name.replace(/[^\p{L}\p{N} .&]/gu, ' ').replace(/\./g, ' ').trim();
    const w = c.split(/\s+/).filter(Boolean);
    return (w.length >= 2 ? (w[0][0] + w[1][0]) : c.slice(0, 2)).toUpperCase();
  }
  // Логотип: ссылка из каталога (репо betting/team_logo, раздача /team-logo/) поверх монограммы;
  // если картинки нет или она не загрузилась — остаётся монограмма.
  function teamLogo(name, size) {
    const url = (window.SB && SB.logoUrl && SB.logoUrl(name)) || (TEAM_LOGOS[name] ? `assets/logos/${TEAM_LOGOS[name]}` : '');
    const img = url
      ? `<img class="sb-logo__img" src="${esc(url)}" alt="" loading="lazy" onerror="this.remove()">`
      : '';
    return `<span class="sb-logo" style="width:${size}px;height:${size}px">`
      + `<span class="sb-logo__mono" style="--h:${logoHue(name)};font-size:${Math.round(size * 0.4)}px">${esc(logoInitials(name || '?'))}</span>`
      + `${img}</span>`;
  }

  /* ── Toast ─────────────────────────────────────────────────────────────── */
  let toastTimeout = null;
  function toast(text) {
    let el = $('.sb-toast');
    if (!el) {
      el = document.createElement('div');
      el.className = 'sb-toast';
      document.body.appendChild(el);
    }
    el.textContent = text;
    el.classList.add('show');
    clearTimeout(toastTimeout);
    toastTimeout = setTimeout(() => el.classList.remove('show'), 1800);
  }

  /* ── Sidebar: переключатель «Лайв / Линия» + виды спорта с лигами ──────── */
  const CHEV_SVG = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18l6-6-6-6"/></svg>';
  let sidebarHtml = '';

  /** фильтр в адресной строке: #sport=s1&league=12 — переживает перезагрузку и уходит в историю */
  function syncHash() {
    const h = activeSportAlias ? ('#sport=' + activeSportAlias + (activeLeagueId ? '&league=' + activeLeagueId : '')) : '';
    history.replaceState(null, '', location.pathname + location.search + h);
  }
  /** выбрать вид спорта / лигу; со страницы события — переход на список */
  function pickFilter(alias, leagueId) {
    if (sidebarLinks) {
      location.href = navUrl(PAGE_FILE[PAGE] + (alias ? '#sport=' + alias + (leagueId ? '&league=' + leagueId : '') : ''));
      return;
    }
    activeSportAlias = alias; activeLeagueId = leagueId;
    syncHash(); renderSidebar(); renderAllSections(true);
  }
  function wireSidebar() {
    const sb = $('#sb-sidebar');
    if (!sb || sb.dataset.wired) return;
    sb.dataset.wired = '1';   // делегирование: сайдбар пересобирается целиком, обработчик один
    sb.addEventListener('click', (ev) => {
      const lg = ev.target.closest('[data-league]');
      if (lg) { pickFilter(lg.dataset.sport, +lg.dataset.league); return; }
      const sp = ev.target.closest('[data-sport]');
      if (sp) {
        const a = sp.dataset.sport;
        // клик по виду спорта: фильтр по нему и раскрытие лиг; повторный клик — свернуть
        openSport = (openSport === a && activeSportAlias === a && !activeLeagueId) ? null : a;
        pickFilter(a, null);
        return;
      }
      if (ev.target.closest('[data-all]')) { openSport = null; pickFilter(null, null); }
    });
  }

  function renderSidebar() {
    const sb = $('#sb-sidebar');
    if (!sb) return;
    const all = SB.visibleEvents();
    const evs = all.filter((e) => e.state === pageState());
    // фильтр, которому на этой странице нечего показать (переключили лайв↔линию) — снимаем
    if (!sidebarLinks) {
      if (activeSportAlias && !evs.some((e) => e.sportAlias === activeSportAlias)) { activeSportAlias = null; activeLeagueId = null; openSport = null; }
      if (activeLeagueId && !evs.some((e) => e.leagueId === activeLeagueId)) activeLeagueId = null;
    }

    // счётчики вкладок — по всем видимым событиям, независимо от фильтра
    const nLive = all.filter((e) => e.state === 'live').length;
    const keep = activeSportAlias ? '#sport=' + activeSportAlias : '';   // лигу не переносим: на другой странице она другая
    const tab = (p, label, n) => `<a class="sb-tab sb-tab--${p}${PAGE === p ? ' active' : ''}" href="${esc(navUrl(PAGE_FILE[p] + keep))}">`
      + `${esc(label)}<span class="sb-tab__n">${n}</span></a>`;

    // лиги — в порядке главной таблицы: по первому появлению в уже отсортированном списке событий
    const cnt = new Map(), leagues = new Map();
    evs.forEach((e) => {
      cnt.set(e.sportAlias, (cnt.get(e.sportAlias) || 0) + 1);
      let list = leagues.get(e.sportAlias);
      if (!list) leagues.set(e.sportAlias, list = []);
      let l = list.find((x) => x.id === e.leagueId);
      if (!l) { const lg = SB.league(e.leagueId); list.push(l = { id: e.leagueId, name: lg.name, country: lg.country, n: 0 }); }
      l.n++;
    });
    // порядок видов спорта — из админки (cfg:s), без ранга — по числу событий
    const sports = SB.SPORTS.filter((s) => cnt.get(s.alias))
      .sort((a, b) => ((a.order == null ? 999999 : a.order) - (b.order == null ? 999999 : b.order)) || (cnt.get(b.alias) - cnt.get(a.alias)));

    let html = `<div class="sb-tabs">${tab('live', T('tab_live'), nLive)}${tab('line', T('tab_line'), all.length - nLive)}</div>`
      + `<div class="sb-sidebar__title">${T('sports')}</div>`
      + `<div class="sb-sport${activeSportAlias === null ? ' active' : ''}" data-all="1">`
      + `<span class="sb-sport__icon">🎯</span><span class="sb-sport__name">${T('all')}</span>`
      + `<span class="sb-sport__count">${evs.length}</span></div>`;
    sports.forEach((s) => {
      const open = openSport === s.alias;
      html += `<div class="sb-sport${activeSportAlias === s.alias && !activeLeagueId ? ' active' : ''}${open ? ' open' : ''}" data-sport="${esc(s.alias)}">`
        + `<span class="sb-sport__icon">${s.icon}</span><span class="sb-sport__name">${esc(s.label)}</span>`
        + `<span class="sb-sport__count">${cnt.get(s.alias)}</span>`
        + `<span class="sb-sport__chev">${CHEV_SVG}</span></div>`;
      if (!open) return;
      html += '<div class="sb-leagues">' + (leagues.get(s.alias) || []).map((l) => {
        const full = l.country ? l.country + ' · ' + l.name : l.name;
        return `<div class="sb-league${activeLeagueId === l.id ? ' active' : ''}" data-league="${l.id}" data-sport="${esc(s.alias)}" title="${esc(full)}">`
          + `<span class="sb-league__name">${esc(full)}</span><span class="sb-league__count">${l.n}</span></div>`;
      }).join('') + '</div>';
    });

    if (html === sidebarHtml) return;      // состав не изменился — DOM не трогаем
    sidebarHtml = html;
    const top = sb.scrollTop;              // список длинный: не сбрасываем прокрутку при обновлении каталога
    sb.innerHTML = html;
    sb.scrollTop = top;
  }

  /* ── Odds button ──────────────────────────────────────────────────────── */
  function oddBtn(selection, marketName, extraCls) {
    const slip = SB.loadSlip();
    const active = slip.selections.includes(String(selection.id));
    const cls = ['sb-odd'];
    if (extraCls) cls.push(extraCls);
    if (!selection.available) cls.push('sb-odd--disabled');
    else if (active) cls.push('sb-odd--active');
    return `<button class="${cls.join(' ')}" data-sid="${esc(selection.id)}" data-market="${esc(marketName || '')}" ${selection.available ? '' : 'disabled'}>
      <span class="sb-odd__label">${esc(selection.label)}</span>
      <span class="sb-odd__value">${fmtOdds(selection.odds)}</span>
    </button>`;
  }

  /* ── Главная таблица «как у 1xBet» ─────────────────────────────────────────
     Заголовок лиги несёт подписи колонок (1 X 2 · Б Тотал М · Ф1 Фора Ф2 · +), строка события —
     две строки команд с логотипами и счётом по периодам (итог + периоды), статус под командами,
     ячейки только с кэфами, значение линии между ячейками тотала/форы и «+N» остальных маркетов.
     Колонки считаются по событиям группы (--gcols), поэтому ячейки строк совпадают с подписями;
     отсутствующий у события маркет — пустая ячейка. */
  const COL = { cell: '54px', hcp: '72px', line: '46px', more: '40px', sep: '9px' };   // line — под слово «Тотал»
  const SEP = `<span class="sb-row__sep"></span>`;
  /* Геометрия колонок считается ОДИН РАЗ на весь список, а не по каждой лиге.
     Раньше лига без тотала/форы получала меньше колонок -> блок кэфов уже -> колонка команд
     (1fr) шире -> счёт уезжал вправо относительно соседних лиг. Теперь набор колонок общий,
     а маркет, недоступный у события в моменте, занимает своё место задисабленной плашкой
     с прочерком (как у 1xBet), а не исчезает. */
  /** маркет события в слоте i набора его вида спорта */
  const mainAt = (event, i) => event.mainMarkets.find((m) => m._slot === i) || null;
  /** Геометрия колонок кэфов для вида спорта. Набор главных маркетов настраивается в админке
      Спорта («Сортировка / Маркеты»): первые три пары «тип + период». Оттуда же его берёт
      основной сайт Спорта, поэтому колонки совпадают — у футбола победитель, двойной шанс и
      фора, у тенниса победитель, фора и тотал геймов и т.д. Значение линии (тотал 2.5, фора +1)
      идёт отдельной колонкой между кэфами, как у 1xBet; группы разделены тонкой линией. */
  function sportLayout(list) {
    const defs = list.length ? SB.mainCols(list[0].sportId) : [];
    const groups = defs.map((d, i) => {
      const n = Math.min(3, (d.oc || []).length);
      // Значение линии бывает двух видов. У тотала оно общее (лежит на маркете) — показываем его
      // колонкой между кэфами. У форы у каждой стороны своё («−1» и «+1», лежит на исходах) —
      // тогда, как в основном Спорте, значение идёт внутрь ячейки, а колонки называются «Фора 1»
      // и «Фора 2»; отдельной колонки со значением у такой группы нет.
      const line = n === 2 && list.some((e) => { const m = mainAt(e, i); return m && m._v; });
      const hcp = n === 2 && !line && list.some((e) => { const m = mainAt(e, i); return m && m.selections.some((x) => x._v); });
      return { i, n, line, hcp, span: line ? 3 : hcp ? 2 : n, def: d };
    }).filter((g) => g.n >= 2);
    const cols = [];
    groups.forEach((g, gi) => {
      if (gi) cols.push(COL.sep);
      if (g.line) cols.push(COL.cell, COL.line, COL.cell);
      else if (g.hcp) cols.push(COL.hcp, COL.hcp);
      else for (let k = 0; k < g.n; k++) cols.push(COL.cell);
    });
    cols.push(COL.sep, COL.more);
    return { groups, tpl: cols.join(' ') };
  }
  /** геометрия по видам спорта: у каждого свой набор колонок, внутри вида она общая на страницу */
  function buildLayouts(list) {
    const bySport = new Map();
    list.forEach((e) => { if (!bySport.has(e.sportAlias)) bySport.set(e.sportAlias, []); bySport.get(e.sportAlias).push(e); });
    const out = {};
    bySport.forEach((evs, alias) => { out[alias] = sportLayout(evs); });
    return out;
  }
  /** есть ли кэфы этого маркета хоть у одного события группы */
  function groupHasOdds(list, g) {
    const codes = (g.def.oc || []).slice(0, g.n);
    return list.some((e) => { const m = mainAt(e, g.i); return m && codes.some((c) => m.selections.some((s) => s._n === c)); });
  }
  function leagueHeader(sport, league, layout, list) {
    const heads = [];
    layout.groups.forEach((g, gi) => {
      if (gi) heads.push(null);
      const codes = (g.def.oc || []).slice(0, g.n);
      // маркета нет ни у одного события лиги — вместо подписей столбцов «Ещё»
      if (!groupHasOdds(list, g)) { heads.push({ t: T('more_btn'), span: g.span }); return; }
      if (g.line) heads.push(SB.live.colLabel(codes[0]), SB.live.marketLabel(g.def.n), SB.live.colLabel(codes[1]));
      // подпись столбцов форы — общее слово «Фора», как в основном Спорте: точное имя типа
      // («Фора по очкам», «Фора по геймам») в узкий столбец не помещается и обрезается
      else if (g.hcp) { const h = SB.live.marketLabel('Handicap'); heads.push(h + ' 1', h + ' 2'); }
      else codes.forEach((c) => heads.push(SB.live.colLabel(c)));
    });
    heads.push(null, '+');
    return `
      <div class="sb-row sb-row--league" style="--gcols:${layout.tpl}">
        <div class="sb-row__sport">${sport.icon}</div>
        <div class="sb-row__league-name">${esc(league.country ? league.country + ' · ' : '')}${esc(league.name)}</div>
        ${heads.map((h) => (h === null ? SEP
          : typeof h === 'object' ? `<div class="sb-row__mkhead sb-row__mkhead--more" style="grid-column:span ${h.span}">${esc(h.t)}</div>`
          : `<div class="sb-row__mkhead" title="${esc(h)}">${esc(h)}</div>`)).join('')}
      </div>`;
  }

  /** события → HTML c заголовками лиг; порядок групп = порядок первого появления */
  function groupedRows(events) {
    const groups = new Map();
    events.forEach((e) => { const k = e.sportAlias + '|' + e.leagueId; if (!groups.has(k)) groups.set(k, []); groups.get(k).push(e); });
    // геометрия общая на страницу в пределах вида спорта (задаётся в renderAllSections) — иначе
    // колонки и счёт «пляшут» между лигами; у разных видов спорта наборы маркетов разные
    let html = '';
    groups.forEach((list) => {
      const alias = list[0].sportAlias;
      const layout = layouts[alias] || sportLayout(list);
      html += leagueHeader(SB.sport(alias), SB.league(list[0].leagueId), layout, list);
      html += list.map((e) => eventRow(e, layout)).join('');
    });
    return html;
  }

  /* ── Event row ─────────────────────────────────────────────────────────── */
  function oddCell(s, mkName, hv) {
    const active = s.available && SB.loadSlip().selections.includes(String(s.id));
    return `<button class="sb-odd sb-odd--cell${hv ? ' sb-odd--hcp' : ''}${s.available ? '' : ' sb-odd--disabled'}${active ? ' sb-odd--active' : ''}" data-sid="${esc(s.id)}" data-market="${esc(mkName || '')}" ${s.available ? '' : 'disabled'}>${hv ? `<span class="sb-odd__hv">${esc(hv)}</span>` : ''}<span class="sb-odd__value">${fmtOdds(s.odds)}</span></button>`;
  }
  const emptyCell = (hcp) => `<span class="sb-odd sb-odd--empty${hcp ? ' sb-odd--hcp' : ''}">–</span>`;
  /** «Ещё» во всю ширину группы, когда маркета у события нет (как в основном Спорте):
      это не кнопка исхода, поэтому клик уходит строке и открывает страницу события */
  const moreCell = (span) => `<span class="sb-row__more-btn" style="grid-column:span ${span}" title="${T('all_markets')}">${T('more_btn')}</span>`;
  /** значение форы для стороны исхода: «-1» → «−1» */
  const hcpText = (s, mk) => String((s && s._v) || (mk && mk._v) || '').replace('-', '−');
  /** значение линии тотала/форы для колонки между ячейками */
  function lineText(mk) {
    if (!mk) return '';
    const s1 = mk.selections.find((s) => s._n === 'Win1');
    const v = mk._v || (s1 && s1._v) || (mk.selections[0] && mk.selections[0]._v) || '';
    return String(v).replace('-', '−');
  }
  /** счёт по колонкам: итог + периоды (как у 1xBet); [] для prematch */
  function scoreCols(event) {
    if (event.state !== 'live') return { h: [], a: [] };
    const l = event._ev && event._ev.score && event._ev.score.list;
    if (!l) return event.score ? { h: [event.score.home], a: [event.score.away] } : { h: [], a: [] };
    const keys = Object.keys(l).map(Number).sort((a, b) => a - b);
    return { h: keys.map((k) => l[k][0]), a: keys.map((k) => l[k][1]) };
  }
  const scHtml = (arr) => arr.map((v, i) => (i === 0 ? `<b>${esc(v)}</b>` : `<i>${esc(v)}</i>`)).join('');
  function eventRow(event, layout) {
    // ячейки раскладываются по колонкам вида спорта: исход ищем по его коду из настройки
    // (Win1/Draw/Win2, 1X/X2/12, Over/Under); если у события такого маркета нет — пустая плашка
    const cells = [];
    layout.groups.forEach((g, gi) => {
      if (gi) cells.push(SEP);
      const mk = mainAt(event, g.i);
      const codes = (g.def.oc || []).slice(0, g.n);
      const sel = (code) => (mk ? mk.selections.find((s) => s._n === code) : null);
      // ни одного исхода этого маркета у события нет — «Ещё» на всю группу вместо пустых плашек
      if (!codes.some((c) => sel(c))) { cells.push(moreCell(g.span)); return; }
      if (g.line) {
        const a = sel(codes[0]), b = sel(codes[1]);
        cells.push(a ? oddCell(a, mk.name) : emptyCell(),
          `<span class="sb-row__line">${esc(mk ? lineText(mk) : '')}</span>`,
          b ? oddCell(b, mk.name) : emptyCell());
      } else if (g.hcp) {
        codes.forEach((c) => { const s = sel(c); cells.push(s ? oddCell(s, mk.name, hcpText(s, mk)) : emptyCell(true)); });
      } else {
        codes.forEach((c) => { const s = sel(c); cells.push(s ? oddCell(s, mk.name) : emptyCell()); });
      }
    });
    const more = Math.max(0, (event._nm || 0) - event.mainMarkets.length);
    cells.push(SEP, `<span class="sb-row__more" data-more="${event.id}" title="${T('all_markets')}">${more ? '+' + more : ''}</span>`);
    const sc = scoreCols(event);
    const live = event.state === 'live';
    return `
      <div class="sb-row sb-row--x${live ? ' sb-row--live' : ''}" data-eid="${event.id}" style="--gcols:${layout.tpl}">
        <div class="sb-row__sport"></div>
        <div class="sb-row__teams sb-row__teams--stack">
          <span class="sb-row__teams-line">${teamLogo(event.home, 16)}<span class="sb-row__name">${esc(event.home)}</span><span class="sb-row__sc" data-sc="${event.id}:h">${scHtml(sc.h)}</span></span>
          <span class="sb-row__teams-line">${teamLogo(event.away, 16)}<span class="sb-row__name">${esc(event.away)}</span><span class="sb-row__sc" data-sc="${event.id}:a">${scHtml(sc.a)}</span></span>
          ${live ? `<span class="sb-row__status"><span data-period="${event.id}">${esc(event.period || '')}</span></span>` : `<span class="sb-row__status sb-row__status--pre">${fmtTime(event.startTs)}</span>`}
        </div>
        ${cells.join('')}
      </div>`;
  }

  function attachRowClicks(root) {
    $$('.sb-row[data-eid]', root).forEach((row) => {
      row.addEventListener('click', (e) => {
        if (e.target.closest('.sb-odd')) return;
        const id = row.dataset.eid;
        location.href = navUrl(`event.html?id=${id}`);
      });
    });
    $$('.sb-odd', root).forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (btn.disabled) return;
        const sid = btn.dataset.sid;
        const slip = SB.toggleInSlip(sid);
        const isInSlip = slip.selections.includes(sid);
        toast(isInSlip ? T('added_to_slip') : T('removed_from_slip'));
        slipNeedsConfirm = false;
        renderAllOddButtons();
        if (slipTab !== 'coupon') setSlipTab('coupon');
        renderSlip();
      });
    });
  }

  /* ── Section ──────────────────────────────────────────────────────────── */
  function renderSection(rootSel, state, title, chipClass) {
    const root = $(rootSel);
    if (!root) return;
    const events = SB.visibleEvents().filter((e) => e.state === state).filter(inFilter);
    root.innerHTML = `
      <div class="sb-section sb-section--${state === 'live' ? 'live' : 'prematch'}">
        <div class="sb-section__head">
          <span class="sb-section__title">${title}</span>
          ${chipClass ? `<span class="sb-section__chip sb-section__chip--${chipClass}">${state === 'live' ? T('inplay') : T('pre')}</span>` : ''}
          <span class="sb-section__count">${events.length}</span>
        </div>
        <div class="sb-table sb-table--x">
          ${events.length ? groupedRows(events) : `<div style="padding:24px;text-align:center;color:var(--foreground)">${T('no_events')}</div>`}
        </div>
      </div>`;
    attachRowClicks(root);
  }

  function renderHot(rootSel) {
    const root = $(rootSel);
    if (!root) return;
    // на странице лайва — горячие live-события, на линии — горячие prematch; пусто — секции нет
    const events = SB.visibleEvents().filter((e) => e.isHot && e.state === pageState()).filter(inFilter);
    if (!events.length) { root.innerHTML = ''; return; }
    root.innerHTML = `
      <div class="sb-section sb-section--hot">
        <div class="sb-section__head">
          <span class="sb-section__title">${T('hot')}</span>
          <span class="sb-section__chip sb-section__chip--hot">${T('top')}</span>
          <span class="sb-section__count">${events.length}</span>
        </div>
        <div class="sb-hot__list">
          ${events.map((event) => {
            const league = SB.league(event.leagueId);
            const isLive = event.state === 'live';
            const m1 = event.mainMarkets[0];
            return `
              <div class="sb-hot__card" data-eid="${event.id}">
                <div class="sb-hot__top">
                  <span class="sb-hot__when${isLive ? ' sb-hot__when--live' : ''}" data-hotperiod="${event.id}">${isLive ? esc(event.period) : fmtTime(event.startTs)}</span>
                  <span class="sb-hot__league">${esc(league.name)}</span>
                  ${isLive ? '<span class="sb-hot__pill">LIVE</span>' : ''}
                </div>
                <div class="sb-hot__mid">
                  <span class="sb-hot__logos">${teamLogo(event.home, 26)}${teamLogo(event.away, 26)}</span>
                  <span class="sb-hot__names"><span>${esc(event.home)}</span><span>${esc(event.away)}</span></span>
                  ${isLive ? `<span class="sb-hot__score" data-hotscore="${event.id}"><span>${esc(event.score.home)}</span><span>${esc(event.score.away)}</span></span>` : ''}
                </div>
                <div class="sb-hot__odds">
                  ${m1 ? m1.selections.map((s) => oddBtn(s, m1.name, 'sb-odd--inline')).join('') : ''}
                </div>
              </div>`;
          }).join('')}
        </div>
      </div>`;
    $$('.sb-hot__card', root).forEach((card) => card.addEventListener('click', (e) => { if (e.target.closest('.sb-odd')) return; location.href = navUrl(`event.html?id=${card.dataset.eid}`); }));
    attachRowClicks(root);
  }

  /* ── Slip ─────────────────────────────────────────────────────────────── */
  /* ── Правая колонка: вкладки «Купон» / «Мои ставки» ─────────────────── */
  const SLIP_TAB_KEY = 'sportsbook-live-slip-tab';
  let slipTab = (() => { try { return localStorage.getItem(SLIP_TAB_KEY) === 'bets' ? 'bets' : 'coupon'; } catch (e) { return 'coupon'; } })();
  function setSlipTab(tab) { slipTab = tab; try { localStorage.setItem(SLIP_TAB_KEY, tab); } catch (e) { /* ignore */ } }
  function slipTabs(nCoupon, nBets) {
    return `
      <div class="sb-slip__tabs">
        <button class="sb-slip__tab${slipTab === 'coupon' ? ' active' : ''}" data-tab="coupon">${T('coupon')}${nCoupon ? ` <span class="sb-slip__tab-count">${nCoupon}</span>` : ''}</button>
        <button class="sb-slip__tab${slipTab === 'bets' ? ' active' : ''}" data-tab="bets">${T('my_bets')}${nBets ? ` <span class="sb-slip__tab-count">${nBets}</span>` : ''}</button>
      </div>`;
  }
  const BET_STATUS = { open: T('st_open'), win: T('st_win'), lose: T('st_lose'), return: T('st_return'), cancelled: T('st_cancelled'), cashout: T('st_cashout') };
  const money = (cents) => `${fmtMoney((cents || 0) / 100)} ${SB.wallet.currency}`;
  /** строка «где сейчас матч» для ноги ставки: live — период и счёт, prematch — время, завершён — итог */
  function betLiveText(sel) {
    const ev = SB.eventById(sel.ev);
    if (!ev) return '';
    if (ev._finished) return `${T('finished')} · ${ev.score ? ev.score.home + ':' + ev.score.away : ''}`;
    if (ev.state === 'live') return `${ev.period || 'LIVE'} · ${ev.score ? ev.score.home + ':' + ev.score.away : '-:-'}`;
    return fmtTime(ev.startTs);
  }
  // «Мои ставки» делятся на «Текущие» (открытые) и «Архив» (рассчитанные и отменённые)
  // Кешаут открытой ставки — по логике бэка Спорта: котировка приходит вместе со списком ставок
  // (bet.cashout {enabled, price}); кнопка «Кешаут ~сумма» слева внизу карточки; по клику — блок
  // подтверждения, сумма «≈» в нём пересчитывается каждые 2 с; выкуп — с серверной задержкой и отсчётом.
  let coUi = null;   // { id, mode: 'confirm' | 'busy', until }
  let coPoll = null, coTick = null;
  function betActionsHtml(b) {
    const co = b.cashout || {};
    const eta = b.eta ? `<span class="sb-bet__eta" data-eta="${+b.eta}">${esc(etaText(+b.eta))}</span>` : '';
    if (coUi && coUi.id === b.id) {
      const busy = coUi.mode === 'busy';
      const left = busy ? Math.max(0, Math.ceil((coUi.until - Date.now()) / 1000)) : 0;
      return `
        <div class="sb-bet__co" data-co="${esc(b.id)}">
          <div class="sb-bet__co-q">${T('cashout_for')} <b>~<span data-co-sum>${fmtMoney((co.price || 0) / 100)}</span> ${SB.wallet.currency}</b>?</div>
          <div class="sb-bet__co-note">${T('cashout_note')}</div>
          <div class="sb-bet__co-btns">
            <button class="sb-bet__co-go" data-co-go="${esc(b.id)}"${busy ? ' disabled' : ''}>${busy ? T('cashout_wait', { s: left }) : T('cashout_go')}</button>
            <button class="sb-bet__co-no" data-co-no="${esc(b.id)}"${busy ? ' disabled' : ''}>${T('cancel')}</button>
          </div>
        </div>${eta}`;
    }
    if (!co.enabled) return eta ? `<div class="sb-bet__actions">${eta}</div>` : '';
    return `
      <div class="sb-bet__actions">
        <button class="sb-bet__cashout" data-co-open="${esc(b.id)}">${T('cashout')} <b>~${fmtMoney((co.price || 0) / 100)}</b></button>
        ${eta}
      </div>`;
  }
  function coClose() { coUi = null; if (coPoll) clearInterval(coPoll); if (coTick) clearInterval(coTick); coPoll = coTick = null; renderSlip(true); }
  function coOpen(id) {
    coUi = { id, mode: 'confirm', until: 0 };
    renderSlip(true);
    if (coPoll) clearInterval(coPoll);
    coPoll = setInterval(async () => {
      if (!coUi || coUi.id !== id || coUi.mode !== 'confirm') return;
      const r = await SB.wallet.cashoutCheck(id);
      if (!coUi || coUi.id !== id || coUi.mode !== 'confirm') return;
      // причина «линия несвежая» называется отдельно: это не свойство ставки, а состояние фида
      if (!r || !r.result || !r.enabled) { coClose(); toast(T(r && r.reason === 'feed_suspended' ? 'feed_suspended' : 'cashout_unavail')); SB.wallet.refresh(); return; }
      const b = (SB.wallet.bets || []).find((x) => x.id === id);
      if (b) b.cashout = { enabled: true, price: r.price };
      const el = $(`.sb-bet__co[data-co="${CSS.escape(id)}"] [data-co-sum]`);
      if (el) el.textContent = fmtMoney(r.price / 100);
    }, 2000);
  }
  async function coGo(id) {
    if (!coUi || coUi.id !== id || coUi.mode === 'busy') return;
    coUi = { id, mode: 'busy', until: Date.now() + SB.wallet.cashoutDelayMs };
    if (coPoll) clearInterval(coPoll); coPoll = null;
    renderSlip(true);
    coTick = setInterval(() => { const btn = $(`[data-co-go="${CSS.escape(id)}"]`); if (btn && coUi) btn.textContent = T('cashout_wait', { s: Math.max(0, Math.ceil((coUi.until - Date.now()) / 1000)) }); }, 500);
    const r = await SB.wallet.cashout(id);
    if (coTick) clearInterval(coTick); coTick = null;
    coUi = null;
    if (r && r.result && r.bet) toast(T('cashout_done', { sum: fmtMoney((r.bet.payout || 0) / 100), cur: SB.wallet.currency }));
    else { toast(T(r && r.error === 'feed_suspended' ? 'feed_suspended' : 'cashout_unavail')); SB.wallet.refresh(); }
    renderSlip(true);
  }
  const BETS_SUB_KEY = 'sportsbook-live-bets-sub';
  let betsSub = (() => { try { return localStorage.getItem(BETS_SUB_KEY) === 'archive' ? 'archive' : 'open'; } catch (e) { return 'open'; } })();
  function setBetsSub(v) { betsSub = v; try { localStorage.setItem(BETS_SUB_KEY, v); } catch (e) { /* ignore */ } }
  function betsHtml() {
    const all = SB.wallet.bets || [];
    const open = all.filter((b) => b.status === 'open'), arch = all.filter((b) => b.status !== 'open');
    const bets = betsSub === 'archive' ? arch : open;
    const sub = `
      <div class="sb-bets__sub">
        <button class="sb-bets__sub-btn${betsSub === 'open' ? ' active' : ''}" data-sub="open">${T('bets_open')}${open.length ? ` <b>${open.length}</b>` : ''}</button>
        <button class="sb-bets__sub-btn${betsSub === 'archive' ? ' active' : ''}" data-sub="archive">${T('bets_archive')}${arch.length ? ` <b>${arch.length}</b>` : ''}</button>
      </div>`;
    if (!bets.length) return sub + `
      <div class="sb-slip__list">
        <div class="sb-slip__empty">
          <div class="sb-slip__empty-icon">📋</div>
          <div>${T(all.length ? (betsSub === 'archive' ? 'no_archive' : 'no_open_bets') : 'no_bets')}</div>
        </div>
      </div>`;
    return sub + `<div class="sb-slip__list sb-bets">${bets.map((b) => `
      <div class="sb-bet sb-bet--${esc(b.status)}">
        <div class="sb-bet__head">
          <span class="sb-bet__status">${BET_STATUS[b.status] || esc(b.status)}</span>
          <span class="sb-bet__time">${fmtTime((b.placed || 0) * 1000)}</span>
        </div>
        ${(b.sel || []).map((sel) => `
          <div class="sb-bet__sel">
            <div class="sb-slip__item-match">${esc(sel.event)}</div>
            <div class="sb-slip__item-market">${esc(sel.market)}</div>
            <div class="sb-slip__item-pick">
              <span class="sb-slip__item-pick-label">${esc(sel.label)}</span>
              <span class="sb-slip__item-pick-odds">${fmtOdds(+sel.odds)}</span>
            </div>
            ${b.status === 'open' ? `<div class="sb-bet__live" data-betlive="${sel.ev}">${esc(betLiveText(sel))}</div>` : ''}
          </div>`).join('')}
        <div class="sb-bet__foot">
          <span>${T('stake')} <b>${money(b.stake)}</b></span>
          <span>${T('odds')} <b>${fmtOdds(+b.odds)}</b></span>
          <span>${b.status === 'open' ? `${T('potential_win')} <b>${money(b.potential)}</b>` : (b.status === 'lose' ? '' : `${T('payout')} <b>${money(b.payout)}</b>`)}</span>
          ${b.status === 'open' ? '' : `<span class="sb-bet__eta">${b.settled ? T('settled_at', { t: fmtTime(b.settled * 1000) }) : ''}</span>`}
        </div>
        ${b.status === 'open' ? betActionsHtml(b) : ''}
        ${b.status !== 'open' ? `<button class="sb-bet__del" data-del="${esc(b.id)}" title="${T('del_from_list')}" aria-label="${T('del_from_list')}">${TRASH_SVG}</button>` : ''}
      </div>`).join('')}</div>`;
  }
  const TRASH_SVG = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14M10 11v6M14 11v6"/></svg>';
  function wireBetCards(slipEl) {
    $$('.sb-bets__sub-btn', slipEl).forEach((b) => b.addEventListener('click', () => { if (b.dataset.sub !== betsSub) { setBetsSub(b.dataset.sub); renderSlip(true); } }));
    $$('[data-co-open]', slipEl).forEach((btn) => btn.addEventListener('click', (e) => { e.stopPropagation(); coOpen(btn.dataset.coOpen); }));
    $$('[data-co-no]', slipEl).forEach((btn) => btn.addEventListener('click', (e) => { e.stopPropagation(); coClose(); }));
    $$('[data-co-go]', slipEl).forEach((btn) => btn.addEventListener('click', (e) => { e.stopPropagation(); coGo(btn.dataset.coGo); }));
    $$('.sb-bet__del', slipEl).forEach((btn) => btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      btn.disabled = true;
      const r = await SB.wallet.hideBet(btn.dataset.del);
      if (r && r.result) toast(T('bet_hidden'));
      else { btn.disabled = false; toast(T('bet_hide_fail')); }
    }));
  }
  function wireSlipTabs(slipEl) {
    $$('.sb-slip__tab', slipEl).forEach((t) => t.addEventListener('click', () => { if (t.dataset.tab !== slipTab) { setSlipTab(t.dataset.tab); renderSlip(true); } }));
  }
  const betsSig = () => betsSub + '|' + (coUi ? coUi.id + ':' + coUi.mode : '') + '|'
    + (SB.wallet.bets || []).map((b) => b.id + ':' + b.status + ':' + (b.eta || 0) + ':' + (b.cashout ? (b.cashout.enabled ? 1 : 0) + ':' + (b.cashout.price || 0) : '')).join(',');
  /** «Расчёт ≈ через N мин» — сервер даёт момент расчёта (с запасом), страница ведёт отсчёт сама, точность минута */
  function etaText(eta) {
    const left = Math.ceil((eta * 1000 - Date.now()) / 60000);
    if (left <= 0) return T('settling');
    const h = Math.floor(left / 60), m = left % 60;
    return T('settle_in', { t: h ? T('h_min', { h, m }) : T('min', { m }) });
  }
  function renderEtas() { $$('[data-eta]').forEach((el) => { const t = etaText(+el.dataset.eta); if (el.textContent !== t) el.textContent = t; }); }

  let slipSig = '';
  let slipNeedsConfirm = false;   // после ответа odds_changed: кэфы в купоне обновлены, нужна повторная отправка
  function renderSlip(force) {
    const slip = SB.loadSlip();
    const idx  = SB.indexSelections();
    const items = slip.selections.map((sid) => idx.get(sid)).filter(Boolean);

    const slipEl = $('#sb-slip');
    const badge  = $('#sb-fab-badge');
    if (badge) badge.textContent = items.length > 0 ? items.length : '';
    const nbadge = $('#sb-mnav-badge');
    if (nbadge) { nbadge.textContent = items.length > 0 ? items.length : ''; nbadge.hidden = !items.length; }
    markMobileNav();

    if (!slipEl) return;

    const totalOdds = items.reduce((acc, it) => acc * (it.selection.odds || 1), 1);
    const stake = slip.stake || 0;
    const win = totalOdds * stake;
    const anyUnavailable = items.some((it) => !it.selection.available);
    // Гейт свежести фида закрыт — сервер ставку не примет, гасим кнопку заранее
    const feedSuspended = !SB.wallet.betsOpen;
    const canBet = items.length > 0 && stake > 0 && !anyUnavailable && !feedSuspended;

    // Перерисовываем купон только когда он реально изменился (состав, кэфы, доступность,
    // ставка): раньше каждая WS-дельта пересобирала его целиком — терялись фокус в поле
    // ставки и hover. Пока пользователь печатает ставку, меняем только цифры на месте.
    const sig = items.map((it) => it.selection.id + ':' + it.selection.odds + ':' + it.selection.available).join('|') + '#' + stake + '#' + slipTab + '#' + betsSig() + '#' + (slipNeedsConfirm ? 1 : 0);
    if (!force && sig === slipSig) return;
    const typing = slipTab === 'coupon' && document.activeElement && document.activeElement.id === 'sb-slip-stake' && slipEl.contains(document.activeElement);
    if (!force && typing && $$('.sb-slip__item', slipEl).length === items.length) {
      slipSig = sig;
      items.forEach((it) => { const o = slipEl.querySelector(`.sb-slip__item[data-sid="${CSS.escape(String(it.selection.id))}"] .sb-slip__item-pick-odds`); if (o) o.textContent = fmtOdds(it.selection.odds); });
      const oddsRow = slipEl.querySelector('[data-total-odds]'); if (oddsRow) oddsRow.textContent = fmtOdds(totalOdds);
      const totalRow = slipEl.querySelector('.sb-slip__row--total span:last-child'); if (totalRow) totalRow.textContent = `${fmtMoney(win)} HTG`;
      const cta = slipEl.querySelector('.sb-slip__cta'); if (cta) cta.disabled = !canBet;
      return;
    }
    slipSig = sig;

    const couponHtml = `
      <div class="sb-slip__head">
        <span class="sb-slip__title">${T('coupon')}</span>
        <span class="sb-slip__count">${items.length}</span>
        <button class="sb-slip__clear" id="sb-slip-clear">${T('clear')}</button>
      </div>
      <div class="sb-slip__list">
        ${items.length === 0 ? `
          <div class="sb-slip__empty">
            <div class="sb-slip__empty-icon">🎟️</div>
            <div>${T('slip_empty')}</div>
          </div>` : items.map((it) => {
            const cls = ['sb-slip__item'];
            if (!it.selection.available) cls.push('sb-slip__item--unavailable');
            return `
              <div class="${cls.join(' ')}" data-sid="${esc(it.selection.id)}">
                <div class="sb-slip__item-match">${teamLogo(it.event.home, 15)} ${esc(it.event.home)} — ${teamLogo(it.event.away, 15)} ${esc(it.event.away)}</div>
                <div class="sb-slip__item-market">${esc(it.market.name)}</div>
                <div class="sb-slip__item-pick">
                  <span class="sb-slip__item-pick-label">${esc(it.selection.label)}</span>
                  <span class="sb-slip__item-pick-odds">${fmtOdds(it.selection.odds)}</span>
                </div>
                <button class="sb-slip__item-remove" data-rm="${esc(it.selection.id)}" title="${T('remove')}">✕</button>
              </div>`;
          }).join('')}
      </div>
      ${items.length > 0 ? `
        <div class="sb-slip__foot">
          <div class="sb-slip__stake-presets">
            ${[100, 250, 500, 1000].map((n) => `<button data-stake-set="${n}"${stake === n ? ' class="active"' : ''}>${n}</button>`).join('')}
          </div>
          <div class="sb-slip__stake-input">
            <input type="number" id="sb-slip-stake" value="${stake || ''}" placeholder="${T('stake')}" inputmode="decimal" min="0" />
            <span>HTG</span>
          </div>
          <div class="sb-slip__row">
            <span>${T('total_odds')}</span>
            <span data-total-odds>${fmtOdds(totalOdds)}</span>
          </div>
          <div class="sb-slip__row sb-slip__row--total">
            <span>${T('potential_win')}</span>
            <span>${fmtMoney(win)} HTG</span>
          </div>
          ${feedSuspended ? `<div class="sb-slip__notice">${T('feed_suspended')}</div>` : ''}
          ${anyUnavailable ? `<div style="font-size:11px;color:var(--orange);text-align:center">${T('unavailable_warn')}</div>` : ''}
          ${slipNeedsConfirm ? `<div class="sb-slip__notice">${T('odds_changed')}</div>` : ''}
          <button class="sb-slip__cta" ${canBet ? '' : 'disabled'}>${slipNeedsConfirm ? T('confirm_bet') : T('place_bet')}</button>
          <div class="sb-slip__hint">${T('delay_note', { s: Math.round(SB.wallet.delayMs / 1000) })}</div>
        </div>` : ''}
    `;
    // на вкладке «Мои ставки» — число только открытых ставок (архив не считаем)
    slipEl.innerHTML = slipTabs(items.length, (SB.wallet.bets || []).filter((b) => b.status === 'open').length) + (slipTab === 'bets' ? betsHtml() : couponHtml);
    wireSlipTabs(slipEl);
    if (slipTab === 'bets') { wireBetCards(slipEl); return; }

    $('#sb-slip-clear')?.addEventListener('click', () => { SB.clearSlip(); slipNeedsConfirm = false; renderAllOddButtons(); renderSlip(true); });
    $$('button[data-rm]', slipEl).forEach((b) => b.addEventListener('click', () => {
      SB.toggleInSlip(b.dataset.rm); slipNeedsConfirm = false;
      renderAllOddButtons();
      renderSlip();
    }));
    // предустановленные суммы заменяют ставку (не плюсуют)
    $$('button[data-stake-set]', slipEl).forEach((b) => b.addEventListener('click', () => {
      SB.setStake(+b.dataset.stakeSet);
      renderSlip();
    }));
    $('#sb-slip-stake')?.addEventListener('input', (e) => {
      SB.setStake(+e.target.value || 0);
      const slip2 = SB.loadSlip();
      const win2 = totalOdds * slip2.stake;
      const totalRow = slipEl.querySelector('.sb-slip__row--total span:last-child');
      if (totalRow) totalRow.textContent = `${fmtMoney(win2)} HTG`;
    });
    // Ставка: списание со счёта кошелька партнёра (эмуляция, api/wallet.php); купон = экспресс.
    // Расчёт и начисление выигрыша делает сборщик по результатам исходов (см. README).
    $('.sb-slip__cta', slipEl)?.addEventListener('click', async () => {
      const cur = SB.loadSlip();
      const stakeNow = +cur.stake || 0;
      if (!items.length || stakeNow <= 0 || anyUnavailable || !SB.wallet.betsOpen) return;
      // Задержка приёма на сервере (3 с): на кнопке обратный отсчёт; после паузы сервер сверяет
      // кэфы с фидом — при изменении купон получает новые кэфы и просит подтвердить ещё раз.
      const cta = $('.sb-slip__cta', slipEl);
      cta.disabled = true;
      $$('input, button', slipEl).forEach((el) => { if (el !== cta) el.disabled = true; });
      let left = Math.round(SB.wallet.delayMs / 1000);
      cta.textContent = T('accepting', { s: left });
      const tick = setInterval(() => { left = Math.max(0, left - 1); cta.textContent = left ? T('accepting', { s: left }) : T('sending'); }, 1000);
      const r = await SB.wallet.placeBet(Math.round(stakeNow * 100), items);
      clearInterval(tick);
      if (r && r.result) {
        slipNeedsConfirm = false;
        const win = fmtMoney((r.bet ? r.bet.potential : 0) / 100);
        // кэф вырос за время приёма — сервер принял по новому, большему кэфу без подтверждения
        toast(r.improved && r.improved.length ? T('bet_accepted_up', { odds: fmtOdds(+r.bet.odds), win, cur: SB.wallet.currency })
          : T('bet_accepted', { stake: fmtMoney(stakeNow), win, cur: SB.wallet.currency }));
        SB.clearSlip(); renderAllOddButtons(); setSlipTab('bets'); setBetsSub('open'); renderSlip(true);
      } else if (r && r.error === 'odds_changed') {
        (r.changes || []).forEach((ch) => { const it = items.find((x) => x.selection.id === ch.ev + '|' + ch.mk + '|' + ch.oc); if (it) it.selection.odds = +ch.odds_new; });
        slipNeedsConfirm = true;
        toast(T('odds_changed'));
        renderAllOddButtons(); renderSlip(true);
      } else if (r && r.error === 'feed_suspended') {
        // Линия несвежая: гейт моста закрыт. Показываем это явно, а не «ставка не принята».
        slipNeedsConfirm = false;
        toast(T('feed_suspended'));
        renderSlip(true);
      } else if (r && r.error === 'unavailable') {
        (r.selections || []).forEach((u) => { const it = items.find((x) => x.selection.id === u.ev + '|' + u.mk + '|' + u.oc); if (it) it.selection.available = false; });
        slipNeedsConfirm = false;
        toast(T('sel_unavailable'));
        renderAllOddButtons(); renderSlip(true);
      } else {
        toast(T('bet_rejected', { reason: r && r.error === 'No enough money' ? T('no_money') : (r && r.error) || T('no_conn') }));
        renderSlip(true);
      }
    });
  }

  /* ── Баланс кошелька партнёра в шапке (эмуляция, api/wallet.php) ───────── */
  function renderBalance(info) {
    renderSlip();
    const el = $('#sb-balance');
    if (!el) return;
    const w = SB.wallet;
    el.textContent = w.balance == null ? '—' : `${fmtMoney(w.balance / 100)} ${w.currency}`;
    if (info && info.delta) { el.classList.remove('up', 'down'); void el.offsetWidth; el.classList.add(info.delta > 0 ? 'up' : 'down'); }
    ((info && info.settled) || []).forEach((b) => {
      const sum = fmtMoney((b.payout || 0) / 100) + ' ' + w.currency;
      if (b.status === 'win') toast(T('bet_won', { sum }));
      else if (b.status === 'return') toast(T('bet_returned', { sum }));
      else if (b.status === 'cashout') toast(T('cashout_done', { sum: fmtMoney((b.payout || 0) / 100), cur: w.currency }));
      else if (b.status === 'lose') toast(T('bet_lost', { sum: fmtMoney(b.stake / 100) + ' ' + w.currency }));
    });
  }

  /* ── Перерисовка кнопок-исходов: кэфы, доступность, купон, подсветка ──── */
  function renderAllOddButtons() {
    const slip = SB.loadSlip();
    const slipSet = new Set(slip.selections);
    const idxAll = SB.indexSelections();
    $$('.sb-odd').forEach((btn) => {
      const sid = btn.dataset.sid;
      const idx = idxAll.get(sid);
      if (!idx) return;
      const s = idx.selection;
      btn.classList.toggle('sb-odd--active', slipSet.has(sid) && s.available);
      btn.classList.toggle('sb-odd--disabled', !s.available);
      btn.disabled = !s.available;
      const valueEl = btn.querySelector('.sb-odd__value');
      if (valueEl) {
        const next = fmtOdds(s.odds), prev = valueEl.textContent;
        if (prev !== next) {
          valueEl.textContent = next;
          const a = parseFloat(prev), b = parseFloat(next);
          if (!isNaN(a) && !isNaN(b) && a !== b) {
            btn.classList.remove('sb-odd--up', 'sb-odd--down'); void btn.offsetWidth;
            btn.classList.add(b > a ? 'sb-odd--up' : 'sb-odd--down');
          }
        }
      }
      const labelEl = btn.querySelector('.sb-odd__label');
      if (labelEl && labelEl.textContent !== s.label) labelEl.textContent = s.label;
    });
  }

  /* ── Период и счёт на месте (без перерисовки таблицы) ─────────────────── */
  function renderPeriods() {
    $$('[data-period]').forEach((el) => { const ev = SB.eventById(el.dataset.period); if (ev && el.textContent !== ev.period) el.textContent = ev.period; });
    $$('[data-sc]').forEach((el) => { const [id, side] = el.dataset.sc.split(':'); const ev = SB.eventById(id); if (!ev) return; const h = scHtml(scoreCols(ev)[side]); if (el.innerHTML !== h) el.innerHTML = h; });
    $$('[data-more]').forEach((el) => { const ev = SB.eventById(el.dataset.more); if (!ev) return; const m = Math.max(0, (ev._nm || 0) - ev.mainMarkets.length); const t = m ? '+' + m : ''; if (el.textContent !== t) el.textContent = t; });
    $$('[data-betlive]').forEach((el) => { const sel = { ev: +el.dataset.betlive }; const t = betLiveText(sel); if (t && el.textContent !== t) el.textContent = t; });
    $$('[data-hotperiod]').forEach((el) => { const ev = SB.eventById(el.dataset.hotperiod); if (!ev || ev.state !== 'live') return; const t = String(ev.period || ''); if (el.textContent !== t) el.textContent = t; });
    $$('[data-hotscore]').forEach((el) => {
      const ev = SB.eventById(el.dataset.hotscore);
      if (!ev || ev.state !== 'live') return;
      const parts = [String(ev.score.home), String(ev.score.away)];
      el.querySelectorAll('span').forEach((sp, i) => { if (sp.textContent !== parts[i]) sp.textContent = parts[i]; });
    });
  }

  /* ── Mobile slip toggle ──────────────────────────────────────────────── */
  /** подсветка активного пункта нижней панели: открыт купон — купон/ставки, иначе текущая страница */
  function markMobileNav() {
    const nav = $('#sb-mnav'), slip = $('#sb-slip');
    if (!nav || !slip) return;
    const sheet = slip.classList.contains('open');
    const cur = sheet ? (slipTab === 'bets' ? 'bets' : 'coupon') : (PAGE === 'line' ? 'line' : 'live');
    $$('.sb-mnav__it', nav).forEach((el) => el.classList.toggle('active', el.dataset.mnav === cur));
  }
  /** Купон на телефоне: шторка снизу. Открывается из нижней панели (лайв/линия/купон/ставки),
      она же заменяет верхний переключатель страниц и прежнюю плавающую кнопку. */
  function wireMobileSlip() {
    const fab = $('#sb-fab'), slip = $('#sb-slip'), backdrop = $('#sb-slip-backdrop'), nav = $('#sb-mnav');
    if (!slip) return;
    const open = () => { slip.classList.add('open'); backdrop?.classList.add('open'); markMobileNav(); };
    const close = () => { slip.classList.remove('open'); backdrop?.classList.remove('open'); markMobileNav(); };
    fab?.addEventListener('click', () => (slip.classList.contains('open') ? close() : open()));
    backdrop?.addEventListener('click', close);
    if (!nav) return;
    $$('a.sb-mnav__it', nav).forEach((a) => { a.href = navUrl(a.getAttribute('href')); });
    nav.addEventListener('click', (e) => {
      const b = e.target.closest('button.sb-mnav__it');
      if (!b) return;
      const tab = b.dataset.mnav === 'bets' ? 'bets' : 'coupon';
      if (slip.classList.contains('open') && slipTab === tab) { close(); return; }
      if (slipTab !== tab) { setSlipTab(tab); renderSlip(true); }
      open();
    });
    markMobileNav();
  }

  /* ── Главный рендер ──────────────────────────────────────────────────── */
  // Таблицы пересобираются только когда меняется состав видимых событий, их порядок,
  // live/prematch, hot-набор или главные маркеты (подпись состава); иначе кэфы, счёт и
  // минута правятся на месте — DOM строк не трогается, hover и подсветка не сбрасываются.
  let sectionsSig = '';
  function sectionsSignature() {
    return SB.visibleEvents().map((e) => e.id + ':' + e.state + (e.isHot ? 'h' : '') + (SB.logoUrl(e.home) ? 'L' : '') + (SB.logoUrl(e.away) ? 'L' : '') + ':' + e.mainMarkets.map((m) => m._h).join(',')).join('|') + '#' + PAGE + ':' + (activeSportAlias || '') + ':' + (activeLeagueId || '');
  }
  function renderAllSections(force) {
    const sig = sectionsSignature();
    if (!force && sig === sectionsSig) { renderAllOddButtons(); renderPeriods(); return false; }
    sectionsSig = sig;
    // Геометрия колонок — общая для LIVE и «Расписания»: секции идут одна под другой,
    // и разъехавшиеся между ними колонки читаются так же плохо, как разъехавшиеся внутри.
    layouts = buildLayouts(modeEvents().filter(inFilter));
    renderHot('#sb-hot');
    renderSection('#sb-live', 'live', T('live_now'), 'live');
    renderSection('#sb-prematch', 'prematch', T('schedule'), null);
    return true;
  }

  // Init для главной
  window.SBInit = function (mode) {
    PAGE = mode === 'line' ? 'line' : 'live';
    SB_I18N.apply(); SB_I18N.switcher($('#sb-lang')); keepNavParams();
    const status = $('#sb-conn');
    const hs = /sport=([a-z0-9]+)/.exec(location.hash || '');
    if (hs) { activeSportAlias = hs[1]; openSport = hs[1]; }
    const hl = /league=([0-9]+)/.exec(location.hash || '');
    if (hl) activeLeagueId = +hl[1];
    wireSidebar();
    renderSlip();
    wireMobileSlip();
    SB.live.initIndex((kind, info) => {
      if (kind === 'sections') { if (renderAllSections()) renderSidebar(); renderSlip(); }
      else if (kind === 'odds') { renderAllOddButtons(); renderPeriods(); renderSlip(); }
      else if (kind === 'period') renderPeriods();
      else if (kind === 'balance') renderBalance(info);
    }, status).then(() => { renderSidebar(); renderAllSections(true); renderSlip(true); renderBalance(); });
    setInterval(renderEtas, 30000);
  };

  // Init для event.html
  window.SBInitEvent = function (eventId) {
    SB_I18N.apply(); SB_I18N.switcher($('#sb-lang')); keepNavParams();
    const status = $('#sb-conn');
    let event = null;

    // Шапка и сетка маркетов пересобираются только при реальном изменении (та же причина,
    // что и мигание строк на главной: любая WS-дельта события перерисовывала весь DOM).
    const headHtml = {};
    const setHtml = (sel, html) => { if (headHtml[sel] !== html) { headHtml[sel] = html; $(sel).innerHTML = html; } };
    function renderHead() {
      const isLive = event.state === 'live';
      const sport  = SB.sport(event.sportAlias);
      const league = SB.league(event.leagueId);
      setHtml('#sb-event-meta', `
        <span>${sport.icon} <span class="sb-event-head__league">${esc(league.name)}</span> · ${esc(league.country)}</span>
        ${isLive ? '<span class="sb-event-head__live">LIVE</span>' : `<span>${fmtTime(event.startTs)}</span>`}
      `);
      const ps = SB.live.periodScores(event);
      setHtml('#sb-event-teams', `
        <div class="sb-event-head__team">
          ${teamLogo(event.home, 56)}
          <div class="sb-event-head__team-name">${esc(event.home)}</div>
          <div class="sb-event-head__team-meta">${T('home')}</div>
        </div>
        <div>
          <div class="sb-event-head__score"></div>
          <div class="sb-event-head__periods" style="display:none"></div>
        </div>
        <div class="sb-event-head__team">
          ${teamLogo(event.away, 56)}
          <div class="sb-event-head__team-name">${esc(event.away)}</div>
          <div class="sb-event-head__team-meta">${T('away')}</div>
        </div>
      `);
      const scEl = $('#sb-event-teams .sb-event-head__score'), scT = isLive ? `${event.score.home} : ${event.score.away}` : 'vs';
      if (scEl.textContent !== scT) scEl.textContent = scT;
      const pdEl = $('#sb-event-teams .sb-event-head__periods'), pdH = isLive && ps.length ? ps.map((x) => `<span>P${esc(x.p)} <b>${esc(x.s)}</b></span>`).join('') : '';
      if (pdEl.innerHTML !== pdH) pdEl.innerHTML = pdH;
      pdEl.style.display = pdH ? '' : 'none';
      const per = $('#sb-event-period');
      if (isLive) { const t = esc(event.period); if (per.innerHTML !== t) per.innerHTML = t; per.style.display = 'flex'; } else per.style.display = 'none';
    }
    // Компактная сетка маркетов «как в 1xBet»: линии одного типа и периода (все форы, все
    // тоталы …) собраны в один блок с заголовком и сворачиванием; каждая линия — строка из
    // ячеек «подпись слева, кэф справа»; периоды (1-й тайм, сет 2 …) отделены полосой.
    // DOM сверяется по ключам блоков и id линий: новые вставляются, исчезнувшие убираются,
    // порядок правится перестановкой узлов, состав исходов линии — заменой её содержимого;
    // кэфы, доступность и «закрыт» — на месте (полная пересборка на каждую дельту мигала).
    const collapsed = new Set();
    const selSig = (mk) => mk.selections.map((s) => s.id).join(',');
    const lineCols = (mk) => (mk.selections.length <= 3 ? mk.selections.length : 2);
    const lineCap = (mk) => SB.live.lineCaption(mk);
    function lineHtml(mk) {
      const cap = lineCap(mk);
      return `<div class="sb-line${SB.live.marketOpen(mk) ? '' : ' sb-line--closed'}" data-mid="${esc(mk.id)}" data-sel="${esc(selSig(mk))}" style="--cols:${lineCols(mk)}">${cap ? `<div class="sb-line__cap">${esc(cap)}</div>` : ''}${mk.selections.map((s) => oddBtn(s, mk.name)).join('')}</div>`;
    }
    // Блоки идут по периодам (основное время, затем 1-й тайм / сет 1 …), внутри периода —
    // в порядке cfg:m; так у каждого периода ровно одна полоса, а узлы не гуляют по DOM.
    const periodNo = (mk) => (!mk._p || mk._p === 'MainTime' || mk._p === 'Match') ? 0 : (mk._period || 99);
    function groupItems(markets) {
      const groups = new Map();
      markets.forEach((mk) => {
        if (!mk.selections.length) return;
        const key = (mk._n || '') + '|' + (mk._p || '');
        let g = groups.get(key);
        if (!g) {
          const pt = SB.live.periodTitle(mk);
          g = { key, period: mk._p || '', pno: periodNo(mk), periodTitle: pt, title: SB.live.marketGroupTitle(mk) + (pt ? ' · ' + pt : ''), lines: [] };
          groups.set(key, g);
        }
        g.lines.push(mk);
      });
      const ordered = [...groups.values()].sort((a, b) => a.pno - b.pno);   // стабильная сортировка: внутри периода порядок cfg:m
      const items = []; let lastPeriod = null;
      ordered.forEach((g) => {
        if (g.period !== lastPeriod) { lastPeriod = g.period; if (g.periodTitle) items.push({ type: 'band', key: 'band:' + g.period, title: g.periodTitle }); }
        items.push({ type: 'group', key: g.key, g });
      });
      return items;
    }
    function itemHtml(it) {
      if (it.type === 'band') return `<div class="sb-pband" data-key="${esc(it.key)}">${esc(it.title)}</div>`;
      return `<div class="sb-mgroup${collapsed.has(it.key) ? ' collapsed' : ''}" data-key="${esc(it.key)}">
        <div class="sb-mgroup__head"><span class="sb-mgroup__title">${esc(it.g.title)}</span><span class="sb-mgroup__n">${it.g.lines.length > 1 ? it.g.lines.length : ''}</span></div>
        <div class="sb-mgroup__body">${it.g.lines.map(lineHtml).join('')}</div>
      </div>`;
    }
    function wireGroup(el) {
      const head = el.querySelector('.sb-mgroup__head');
      if (head) head.addEventListener('click', () => { const k = el.dataset.key; el.classList.toggle('collapsed'); if (el.classList.contains('collapsed')) collapsed.add(k); else collapsed.delete(k); });
      attachRowClicks(el);
    }
    function reconcile(parent, items, keyAttr, html, wire) {
      const nodes = new Map($$(':scope > [data-' + keyAttr + ']', parent).map((n) => [n.dataset[keyAttr], n]));
      const want = new Set(items.map((it) => String(it.key)));
      nodes.forEach((n, k) => { if (!want.has(k)) n.remove(); });
      let cursor = parent.firstElementChild;
      const out = [];
      items.forEach((it) => {
        let node = nodes.get(String(it.key));
        if (!node) { const tpl = document.createElement('template'); tpl.innerHTML = html(it).trim(); node = tpl.content.firstElementChild; if (wire) wire(node, it); }
        if (node === cursor) cursor = cursor.nextElementSibling; else parent.insertBefore(node, cursor);
        out.push([node, it]);
      });
      return out;
    }
    // ── Панель фильтра маркетов (как у 1xBet): период · Все / Тотал / Фора / Популярные · поиск · свернуть все
    const mfilter = { period: '', tab: 'all', q: '' };
    const TABS = [['all', T('tab_all')], ['total', T('tab_total')], ['handicap', T('tab_handicap')], ['popular', T('tab_popular')]];
    const tabPred = {
      all: () => true,
      total: (m) => /Total/.test(m._n || ''),
      handicap: (m) => /Handicap/.test(m._n || ''),
      popular: (m) => SB.live.isPopular(event, m),
    };
    const periodPred = (m) => !mfilter.period || (m._p || '') === mfilter.period;
    function qPred(m) {
      const q = mfilter.q.trim().toLowerCase();
      if (!q) return true;
      return (m.name || '').toLowerCase().includes(q) || m.selections.some((s) => String(s.label).toLowerCase().includes(q));
    }
    const marketsFiltered = (all) => all.filter((m) => periodPred(m) && tabPred[mfilter.tab](m) && qPred(m));
    let barSig = '';
    function renderMarketBar(all) {
      const bar = $('#sb-mbar');
      if (!bar) return;
      if (!bar.dataset.ready) {
        bar.dataset.ready = '1';
        bar.innerHTML = `
          <select class="sb-mbar__period" title="${T('period')}"></select>
          <div class="sb-mbar__tabs">${TABS.map(([k, l]) => `<button class="sb-mbar__tab" data-tab="${k}">${l}<b></b></button>`).join('')}</div>
          <input class="sb-mbar__q" type="search" placeholder="${T('search')}" aria-label="${T('search_markets')}" />
          <button class="sb-mbar__fold" title="${T('fold_all')}"></button>`;
        bar.querySelector('.sb-mbar__period').addEventListener('change', (e) => { mfilter.period = e.target.value; renderMarkets(); });
        $$('.sb-mbar__tab', bar).forEach((b) => b.addEventListener('click', () => { mfilter.tab = b.dataset.tab; renderMarkets(); }));
        let qt = null;
        bar.querySelector('.sb-mbar__q').addEventListener('input', (e) => { clearTimeout(qt); qt = setTimeout(() => { mfilter.q = e.target.value; renderMarkets(); }, 150); });
        bar.querySelector('.sb-mbar__fold').addEventListener('click', () => {
          const groups = $$('.sb-mgroup', $('#sb-event-markets'));
          const fold = !groups.every((g) => g.classList.contains('collapsed'));
          groups.forEach((g) => { g.classList.toggle('collapsed', fold); if (fold) collapsed.add(g.dataset.key); else collapsed.delete(g.dataset.key); });
          bar.querySelector('.sb-mbar__fold').classList.toggle('folded', fold);
        });
      }
      // периоды — по порядку появления в отсортированном списке (основное время первым)
      const periods = [];
      all.forEach((m) => { const p = m._p || ''; if (!periods.some((x) => x[0] === p)) periods.push([p, SB.live.periodTitle(m) || T('main_time')]); });
      const sel = bar.querySelector('.sb-mbar__period');
      const psig = periods.map((x) => x[0]).join('|');
      if (sel.dataset.sig !== psig) {
        sel.dataset.sig = psig;
        sel.innerHTML = `<option value="">${T('all_periods')}</option>` + periods.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join('');
        if (!periods.some((x) => x[0] === mfilter.period)) mfilter.period = '';
        sel.value = mfilter.period;
      }
      const inPeriod = all.filter((m) => periodPred(m) && qPred(m));
      const counts = TABS.map(([k]) => inPeriod.filter(tabPred[k]).length).join(',');
      if (barSig !== counts + '#' + mfilter.tab) {
        barSig = counts + '#' + mfilter.tab;
        $$('.sb-mbar__tab', bar).forEach((b, i) => { b.querySelector('b').textContent = counts.split(',')[i]; b.classList.toggle('active', b.dataset.tab === mfilter.tab); });
      }
    }
    function renderMarkets(force) {
      const all = SB.buildAllMarkets(event);
      renderMarketBar(all);
      const markets = marketsFiltered(all);
      const root = $('#sb-event-markets');
      root.classList.add('sb-markets--compact');
      const items = groupItems(markets);
      if (force || !markets.length || !root.querySelector('.sb-mgroup')) {
        root.innerHTML = markets.length ? items.map(itemHtml).join('')
          : `<div style="padding:24px;text-align:center;color:var(--foreground)">${all.length ? T('no_markets_filter') : T('markets_loading')}</div>`;
        $$('.sb-mgroup', root).forEach(wireGroup);
        return;
      }
      reconcile(root, items, 'key', itemHtml, (node, it) => { if (it.type === 'group') wireGroup(node); }).forEach(([node, it]) => {
        if (it.type !== 'group') return;
        const title = node.querySelector('.sb-mgroup__title'); if (title.textContent !== it.g.title) title.textContent = it.g.title;
        const n = node.querySelector('.sb-mgroup__n'); const nt = it.g.lines.length > 1 ? String(it.g.lines.length) : ''; if (n.textContent !== nt) n.textContent = nt;
        const body = node.querySelector('.sb-mgroup__body');
        reconcile(body, it.g.lines.map((mk) => ({ key: mk.id, mk })), 'mid', (li) => lineHtml(li.mk), (ln) => attachRowClicks(ln)).forEach(([ln, li]) => {
          const mk = li.mk;
          if (ln.dataset.sel !== selSig(mk)) { const cap = lineCap(mk); ln.innerHTML = (cap ? `<div class="sb-line__cap">${esc(cap)}</div>` : '') + mk.selections.map((s) => oddBtn(s, mk.name)).join(''); ln.dataset.sel = selSig(mk); ln.style.setProperty('--cols', lineCols(mk)); attachRowClicks(ln); }
          ln.classList.toggle('sb-line--closed', !SB.live.marketOpen(mk));
        });
      });
      renderAllOddButtons();
    }

    SB.live.initEvent(eventId, (kind, info) => {
      if (kind === 'balance') { renderBalance(info); return; }   // баланс приходит ещё до готовности события
      if (!event) return;
      if (kind === 'event' || kind === 'sections') { renderHead(); renderMarkets(); renderSlip(); if (kind === 'sections') renderSidebar(); }
      else if (kind === 'odds') { renderAllOddButtons(); renderHead(); renderSlip(); }
      else if (kind === 'period') renderHead();
    }, status).then((ev) => {
      event = ev;
      if (!event) { document.body.innerHTML = '<div style="padding:40px;text-align:center">' + T('event_not_found') + '</div>'; return; }
      // сайдбар страницы события: подсвечены вид спорта и лига матча, а переключатель и клики
      // ведут на ту страницу списка, откуда матч — лайв или линия
      PAGE = event.state === 'prematch' ? 'line' : 'live';
      sidebarLinks = true;
      activeSportAlias = event.sportAlias; openSport = event.sportAlias; activeLeagueId = event.leagueId;
      const back = $('.sb-header__nav a');
      if (back) back.href = navUrl(PAGE_FILE[PAGE]);
      wireSidebar(); renderSidebar();
      renderHead();
      renderMarkets(true);
      renderSlip(true);
      renderBalance();
      setInterval(renderEtas, 30000);
      wireMobileSlip();
    });
  };
})();
