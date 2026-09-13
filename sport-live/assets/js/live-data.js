/* eslint-disable no-undef */
/**
 * live-data.js — адаптер живых данных под контракт window.SB мока
 * sportsbook-iframe-designs (дизайн 01 «Пижама»). Заменяет data/mock-events.js:
 * те же структуры SPORTS / LEAGUES / EVENTS / Market / Selection и те же
 * helper-методы, но наполняются из фида:
 *
 *   /feed-health/catalog.json   — виды спорта, live и ближайшие prematch-события
 *                                 с тремя главными маркетами (main) — раз в минуту
 *   api/snapshot.php?id=       — все маркеты события (страница события, REST из feed_bridge);
 *   /feed-health/snap/{id}.json   запасной статический снимок сборщика (live и ближайший час)
 *   Centrifugo                  — дельты: sport:{sportId} (события),
 *                                 event:{eventId} (событие, маркеты, исходы)
 *
 * Идентификаторы: selection.id = "ev|mktHash|ocHash", market.id = "ev|mktHash"
 * (строки; движок main.js адаптирован под строковые id). Купон — тот же
 * localStorage-механизм мока.
 */
(function () {
  const DATA_BASE = '/feed-health/';
  const WS_URL = (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/centrifugo/connection/websocket';
  // Кэфы приходят только в канал события (event:{id}), поэтому подписываемся на ВСЕ live-матчи:
  // при лимите 90 у остальных цены обновлялись лишь раз в минуту из каталога и на странице
  // висели устаревшие кэфы (замер 12.09: расходилось 10–15 % цен). Centrifugo держит сотни
  // подписок на соединение; сейчас в лайве бывает 120–200 событий.
  const MAX_EVENT_SUBS = 400;

  const RU_SPORT = { 1: 'Футбол', 2: 'Киберспорт', 3: 'Баскетбол', 4: 'Теннис', 5: 'Волейбол', 6: 'Наст. теннис', 7: 'Хоккей', 8: 'Гандбол', 12: 'Амер. футбол', 13: 'Футзал', 14: 'Бейсбол', 15: 'MMA' };
  const ICON = { 1: '⚽', 2: '🎮', 3: '🏀', 4: '🎾', 5: '🏐', 6: '🏓', 7: '🏒', 8: '🤾', 12: '🏈', 13: '🥅', 14: '⚾', 15: '🥊' };
  const PERIOD_RU = { MainTime: '', Match: '', Half1: '1-й тайм', Half2: '2-й тайм', Overtime: 'Овертайм', Set1: 'Сет 1', Set2: 'Сет 2', Set3: 'Сет 3', Set4: 'Сет 4', Set5: 'Сет 5', Quarter1: 'Q1', Quarter2: 'Q2', Quarter3: 'Q3', Quarter4: 'Q4', Period1: '1-й период', Period2: '2-й период', Period3: '3-й период' };
  // Названия типов маркетов фида → подписи витрины (90 самых частых типов по feed_bridge на 08.09.2026);
  // неизвестные типы — CamelCase разбивается на слова (humanize).
  const MARKET_RU = {
    Winner3Ways: '1X2', Winner2Ways: 'Победитель', Total: 'Тотал', Handicap: 'Фора', DoubleChance: 'Двойной шанс',
    CorrectScore: 'Точный счёт', TotalOddEven: 'Тотал чёт / нечёт', DrawNoBet: 'Победа без ничьей', BothTeamsToScore: 'Обе забьют',
    BothToScore: 'Обе забьют', ExactTotal: 'Точный тотал', Team1Total: 'Инд. тотал 1', Team2Total: 'Инд. тотал 2',
    HalfTimeAndMainTime: 'Тайм / матч', TeamToScoreGoalNumberN3Ways: 'Кто забьёт N-й гол', BothToScoreAndMatchResult: 'Обе забьют + результат',
    TeamToScoreLastGoal3Ways: 'Кто забьёт последний гол', Team1ToScoreInBothHalves: 'Команда 1 забьёт в обоих таймах',
    Team2ToScoreInBothHalves: 'Команда 2 забьёт в обоих таймах', ResultAndTotal: 'Результат + тотал', BothToScoreAndTotal: 'Обе забьют + тотал',
    Team1ToScore: 'Команда 1 забьёт', Team2ToScore: 'Команда 2 забьёт', Team1ToWinAtLeastOneHalf: 'Команда 1 выиграет хотя бы один тайм',
    Team2ToWinAtLeastOneHalf: 'Команда 2 выиграет хотя бы один тайм', Team1ToWinBothHalves: 'Команда 1 выиграет оба тайма',
    Team2ToWinBothHalves: 'Команда 2 выиграет оба тайма', GoalsBothHalves: 'Голы в обоих таймах', Team1ToWinToNil: 'Команда 1 выиграет всухую',
    Team2ToWinToNil: 'Команда 2 выиграет всухую', Team1TotalOddEven: 'Инд. тотал 1 чёт / нечёт', Team2TotalOddEven: 'Инд. тотал 2 чёт / нечёт',
    WinningMargin: 'Разница в счёте', AtLeastOneTeamToScoreNOrMore: 'Хотя бы одна команда забьёт N+', NumberOfGoals: 'Количество голов',
    HalfWithMostGoals: 'Самый результативный тайм', SetsHandicap: 'Фора по сетам', RaceTo: 'Кто первым наберёт', RaceTo2Ways: 'Кто первым наберёт',
    CornersTotal: 'Тотал угловых', GamesHandicap: 'Фора по геймам', Team1ExactTotal: 'Точный инд. тотал 1', Team2ExactTotal: 'Точный инд. тотал 2',
    GamesTotal: 'Тотал геймов', CornersTeam1Total: 'Инд. тотал угловых 1', CornersTeam2Total: 'Инд. тотал угловых 2', CornersHandicap: 'Фора по угловым',
    CornersWinner3Ways: 'Угловые: 1X2', CornersTotalOddEven: 'Угловые: чёт / нечёт', CornersDoubleChance: 'Угловые: двойной шанс',
    CornersRaceTo: 'Угловые: кто первым наберёт', FirstToScoreAndMatchResult: 'Первый гол + результат',
    ShotsOnTargetTeam1Total: 'Инд. тотал ударов в створ 1', ShotsOnTargetTeam2Total: 'Инд. тотал ударов в створ 2', ShotsOnTargetTotal: 'Тотал ударов в створ',
    ShotsAllTeam1Total: 'Инд. тотал ударов 1', ShotsAllTeam2Total: 'Инд. тотал ударов 2', ShotsAllTotal: 'Тотал ударов',
    MatchResultAndTotal: 'Результат + тотал', 'WillBeTie-break': 'Будет ли тай-брейк', OffsidesTotal: 'Тотал офсайдов',
    ThrowInsTotal: 'Тотал аутов', ThrowInsTeam1Total: 'Инд. тотал аутов 1', ThrowInsTeam2Total: 'Инд. тотал аутов 2',
    WillBeGoalBeforeNMinute: 'Гол до N-й минуты', SetsTotal: 'Тотал сетов', MatchResultAndGamesTotal: 'Результат + тотал геймов',
    Team1ToComeFromBehindAndWin: 'Команда 1 отыграется и победит', Team2ToComeFromBehindAndWin: 'Команда 2 отыграется и победит',
    BothToScoreHalfAndBothToScoreHalf2: 'Обе забьют в 1-м и во 2-м тайме', AtLeastOneTeamWillScoreNGoalsInARow: 'Одна команда забьёт N голов подряд',
    Team1NumberOfScoredGoalsRange: 'Голы команды 1 (диапазон)', Team2NumberOfScoredGoalsRange: 'Голы команды 2 (диапазон)',
    ScoredMoreThanNGoals: 'Больше N голов', GoalKicksTeam1Total: 'Инд. тотал ударов от ворот 1', GoalKicksTeam2Total: 'Инд. тотал ударов от ворот 2',
    GoalKicksTotal: 'Тотал ударов от ворот', CorrectScoreAfterNGames: 'Точный счёт после N геймов', MatchResultAndSetsTotal: 'Результат + тотал сетов',
    TacklesTeam1Total: 'Инд. тотал отборов 1', TacklesTeam2Total: 'Инд. тотал отборов 2', TacklesTotal: 'Тотал отборов',
    FoulsTotal: 'Тотал фолов', FoulsTeam1Total: 'Инд. тотал фолов 1', FoulsTeam2Total: 'Инд. тотал фолов 2', SetWithMostGames: 'Сет с наибольшим числом геймов',
    YellowCardsTotal: 'Тотал жёлтых карточек', YellowCardsHandicap: 'Фора по жёлтым карточкам', YellowCardsTeam1Total: 'Инд. тотал жёлтых карточек 1',
    YellowCardsTeam2Total: 'Инд. тотал жёлтых карточек 2', ToLose1stSetAndWinMatch: 'Проиграть 1-й сет и выиграть матч',
    PointsHandicap: 'Фора по очкам', PointsTotal: 'Тотал очков', HighestScoringHalf: 'Самый результативный тайм', Overtime: 'Будет ли овертайм',
  };
  const humanize = (n) => String(n || '').replace(/Team1/g, T('team', { n: 1 }) + ' ').replace(/Team2/g, T('team', { n: 2 }) + ' ').replace(/([a-z0-9])([A-Z])/g, '$1 $2').replace(/\s+/g, ' ').trim();
  // Подписи кодов фида — из словарей фронта Спорта (BackEnd/dict/frontend/{ln}.ini →
  // assets/dict/{ln}.json, см. tools/export_dict.py): маркеты, исходы, периоды, виды спорта.
  // Цепочка: словарь → правила с значениями (Б 2.5, Ф1 −1, счёт 2:0) → свои дополнения (ru) →
  // разбиение кода на слова. Язык — ?ln= (ru по умолчанию; en, fr, ht).
  const LN = (window.SB_I18N && SB_I18N.lang) || 'ru';   // язык страницы (assets/js/i18n.js): ?ln= → localStorage → ru
  const T = (k, v) => SB_I18N.t(k, v);
  const DICT = { market: {}, outcome: {}, period: {}, sport: {} };
  async function loadDict() {
    try { const r = await fetch('assets/dict/' + LN + '.json', { cache: 'no-store' }); if (r.ok) { const d = await r.json(); Object.keys(DICT).forEach((k) => Object.assign(DICT[k], d[k] || {})); } }
    catch (e) { /* без словаря показываем коды */ }
  }
  // Подписи исходов без сокращений, как у 1xBet: тотал — «Более 2.5 / Менее 2.5» (слова из словаря
  // Спорта: en Over/Under, es Más/Menos, pt Acima/Abaixo), фора — «1 (+1) / 2 (−1)», победитель —
  // «П1 / X / П2» (en W1 / X / W2, es и pt — 1 / X / 2).
  const WIN_SHORT = {
    ru: { Win1: 'П1', Win2: 'П2', Draw: 'X' },
    en: { Win1: 'W1', Win2: 'W2', Draw: 'X' },
    es: { Win1: '1', Win2: '2', Draw: 'X' },
    pt: { Win1: '1', Win2: '2', Draw: 'X' },
  };
  const short = (k) => {
    if (k === 'Over' || k === 'Under') return DICT.outcome[k] || k;
    return (WIN_SHORT[LN] && WIN_SHORT[LN][k]) || DICT.outcome[k] || k;
  };
  const hshort = (side) => String(side);
  const OUTCOME_RU = { FirstHalf: '1-й тайм', SecondHalf: '2-й тайм', TeamRegularTime: 'В основное время', TeamAfterRegularTime: 'В овертайме', TeamShootoutPenalties: 'По пенальти', TwoAndMore: '2 и более', Even: 'Чёт', Odd: 'Нечёт' };
  const marketName = (n) => DICT.market[n] || (LN === 'ru' && MARKET_RU[n]) || humanize(n) || 'Маркет';
  const outcomeName = (n) => DICT.outcome[n] || (LN === 'ru' && OUTCOME_RU[n]) || humanize(n);
  const PERIOD_WORD = { ru: { Inning: 'Иннинг', Set: 'Сет', Quarter: 'Четверть', Period: 'Период', Half: 'Тайм', Map: 'Карта', Game: 'Гейм' } };
  function periodName(p) {
    if (!p || p === 'MainTime' || p === 'Match') return '';
    if (DICT.period[p]) return DICT.period[p];
    if (LN === 'ru' && PERIOD_RU[p] !== undefined) return PERIOD_RU[p];
    const m = /^(Inning|Set|Quarter|Period|Half|Map|Game)(\d+)$/.exec(p);
    if (m) return ((PERIOD_WORD[LN] || {})[m[1]] || m[1]) + ' ' + m[2];
    return humanize(p);
  }

  const EN = (v) => (v && typeof v === 'object') ? (v.EN || v.RU || Object.values(v)[0] || '') : (v == null ? '' : String(v));

  // ── состояние ────────────────────────────────────────────────────────────
  const SPORTS = [];            // {id, alias, label, icon}
  const LEAGUES = [];           // {id, sport, country, name}
  const EVENTS = [];            // события в формате мока + служебные поля (_ev — сырой объект)
  const byId = new Map();       // event.id → event
  const leagueByKey = new Map();
  const selIndex = new Map();   // selection.id → {selection, market, event}
  const LOGOS = new Map();      // имя команды → ссылка на логотип (из каталога: lh/la, репо betting/team_logo)
  const subs = {};
  let cf = null;
  let onChange = () => {};      // вызывается адаптером при изменениях: (kind) kind = 'odds'|'sections'|'event'
  const dirty = { odds: false, sections: false, event: false };
  let flushTimer = null;

  function schedule(kind) {
    dirty[kind] = true;
    if (flushTimer) return;
    flushTimer = setTimeout(() => {
      flushTimer = null;
      const d = { ...dirty };
      dirty.odds = dirty.sections = dirty.event = false;
      if (d.sections) onChange('sections');
      else if (d.odds) onChange('odds');
      if (d.event) onChange('event');
    }, 250);
  }

  // ── сборка объектов мока ─────────────────────────────────────────────────
  function sportAlias(sp) { return 's' + sp; }
  function ensureSport(s) {
    let sp = SPORTS.find((x) => x.id === +s.id);
    if (!sp) { sp = { id: +s.id, alias: sportAlias(s.id), label: DICT.sport[s.n] || (LN === 'ru' && RU_SPORT[s.id]) || s.n, icon: ICON[s.id] || '🏅', order: 999999, excluded: false }; SPORTS.push(sp); }
    if (s.o != null) sp.order = +s.o;        // порядок вида спорта из админки (cfg:s)
    if (s.x != null) sp.excluded = !!s.x;
  }
  const sportOrder = (id) => { const s = SPORTS.find((x) => x.id === id); return s ? s.order : 999999; };

  // ── сортировка витрины из админки Спорта (cfg:* — ведёт спортаналитик) ────
  // Ранг события: вид спорта → (live) позиция турнира в live-списке cfg:tl → страна cfg:c →
  // турнир cfg:t → хвост без рангов (order 999999): лиги с большим числом маркетов выше,
  // затем по имени → внутри лиги по времени начала и названию. Партнёрский слой
  // переопределения этих весов планируется отдельно (не сейчас).
  const MCFG = {};   // sportId → [[type, period], …] — порядок маркетов витрины (cfg:m)
  const POPULAR_TOP = 16;   // сколько первых пар cfg:m считать «популярными» на вкладке страницы события
  const MEXCL = {};  // sportId → [[type, period], …] — маркеты, исключённые с витрины (cfg:me)
  // Набор главных маркетов таблицы у каждого вида спорта свой — из админки Спорта (первые три
  // пары «тип+период» настройки маркетов); тем же набором пользуется основной сайт Спорта.
  const MMAIN = {};  // sportId → [{t:type, p:period, n:имя типа, oc:[коды исходов]}, …]
  const marketExcluded = (sportId, m) => (MEXCL[String(sportId)] || []).some((x) => x[0] === m.type && x[1] === m.period);
  let leagueNm = new Map();
  function cmpRank(a, b) {
    const la = SB.league(a.leagueId), lb = SB.league(b.leagueId);
    const bySport = sportOrder(a.sportId) - sportOrder(b.sportId);
    if (bySport) return bySport;
    if ((a.state === 'live') !== (b.state === 'live')) return a.state === 'live' ? -1 : 1;
    if (a.state === 'live') {
      // Лайв — тот же порядок, что в основном Спорте (betsportwss, get_events_for_live +
      // getLive2SportEvents): сначала турниры из cfg:tl в порядке этого списка, затем все прочие
      // просто по времени начала. Лига в хвосте встаёт туда, где начался её самый ранний матч:
      // группировка по лигам делается уже после сортировки, как и у них.
      const pa = a._lp == null ? 1e9 : a._lp, pb = b._lp == null ? 1e9 : b._lp;
      return (pa - pb) || (a.startTs - b.startTs) || (a.leagueId - b.leagueId)
        || (a.home || '').localeCompare(b.home || '');
    }
    // Расписание: порядок стран и турниров из админки, хвост без рангов — лиги с большим числом
    // маркетов выше, затем по названию.
    return a._co - b._co || a._to - b._to
      || (leagueNm.get(b.leagueId) || 0) - (leagueNm.get(a.leagueId) || 0)
      || (la.country || '').localeCompare(lb.country || '') || (la.name || '').localeCompare(lb.name || '')
      || a.leagueId - b.leagueId
      || a.startTs - b.startTs || (a.home || '').localeCompare(b.home || '');
  }
  function marketRank(sportId, m) {
    const list = MCFG[String(sportId)];
    if (!list) return (m.period || 0) * 1000 + (m.type || 0);
    let i = list.findIndex((x) => x[0] === m.type && x[1] === m.period);
    if (i >= 0) return i;
    i = list.findIndex((x) => x[0] === m.type);
    return i >= 0 ? 100000 + i * 100 + (m.period || 0) : 200000 + (m.period || 0) * 1000 + (m.type || 0);
  }
  function ensureLeague(sp, cat, tr) {
    const key = sp + '|' + (cat || '') + '|' + (tr || '');
    let l = leagueByKey.get(key);
    if (!l) { l = { id: LEAGUES.length + 1, sport: sportAlias(sp), country: cat || '', name: tr || '' }; LEAGUES.push(l); leagueByKey.set(key, l); }
    return l;
  }
  function splitName(n) { const i = (n || '').indexOf(' - '); return i > 0 ? [n.slice(0, i), n.slice(i + 3)] : [n || '', '']; }

  // подписи исходов и имена маркетов в стиле мока (П1/X/П2, Б/М, Ф1/Ф2)
  const hv = (v) => (v ? ' ' + String(v).replace('-', '−') : '');
  function selLabel(mkName, o) {
    const n = o.n || '', v = o.v || '';
    if (/^over$/i.test(n)) return short('Over') + ' ' + (v || o._mv || '');
    if (/^under$/i.test(n)) return short('Under') + ' ' + (v || o._mv || '');
    if (/Handicap$/.test(mkName || '') && (n === 'Win1' || n === 'Win2')) return hshort(n === 'Win1' ? 1 : 2) + (v ? ' (' + String(v).replace('-', '−') + ')' : '');
    if (n === 'Win1' || n === 'Win2' || n === 'Draw') return short(n) + hv(v);
    if (n === 'Score' || n === 'CorrectScore') return v ? String(v).replace('-', ':') : outcomeName(n);
    if (n === 'RangedTotal') return v ? String(v).replace('-', '–') : outcomeName(n);
    return outcomeName(n) + hv(v);
  }
  const withValue = (base, v) => (/\bN\b/.test(base) ? base.replace(/\bN\b/, v) : base + ' ' + v);
  function marketTitle(mk) {
    const base = marketName(mk.n);
    let t = base;
    if (/Handicap$/.test(mk.n || '')) { const o = (mk.oc || []).find((x) => x.n === 'Win1'); t = base + (o && o.v ? hv(o.v) : (mk.v ? ' ' + mk.v : '')); }
    else if (mk.v) t = withValue(base, mk.v);
    const p = periodName(mk.p);
    return p ? t + ' · ' + p : t;
  }
  /** подпись линии в компактной сетке, когда параметр линии не входит в подписи исходов
   *  (тоталы/форы несут значение в ячейках; «гол N», «минута N», «раунд N» — нет) */
  function lineCaption(m) {
    if (!m._v || /Total|Handicap|OddEven/.test(m._n || '')) return '';   // значение линии уже в ячейках (Б 2.5 / Ф1 −1)
    if (m.selections.some((s) => s._v)) return '';                        // значение несут сами исходы
    return withValue(marketName(m._n), m._v);
  }
  function selId(evId, mkHash, ocHash) { return evId + '|' + mkHash + '|' + ocHash; }
  function sortOc(oc) { return oc.slice().sort((a, b) => ((a.t == null ? 99 : a.t) - (b.t == null ? 99 : b.t)) || String(a.n).localeCompare(String(b.n))); }

  /** Market мока из маркета фида {h,n,p,v,open,oc:[{h,n,v,t,price,status}]} */
  function toMarket(event, mk) {
    const m = { id: event.id + '|' + mk.h, name: marketTitle(mk), _h: mk.h, _n: mk.n, _p: mk.p, _period: mk.period, _type: mk.type, _v: mk.v || '', _open: !!mk.open, _removed: !!mk.removed, selections: [] };
    sortOc(mk.oc || []).forEach((o) => {
      if (o.removed) return;
      o._mv = mk.v || '';
      const s = { id: selId(event.id, mk.h, o.h), label: selLabel(mk.n, o), odds: o.price != null ? +o.price : 0, available: !!mk.open && o.status === 1 && o.price != null, _h: o.h, _status: o.status, _result: o.result || 0, _t: o.t, _n: o.n || '', _v: o.v || '' };
      m.selections.push(s);
      selIndex.set(s.id, { selection: s, market: m, event });
    });
    return m;
  }

  // ── период/счёт в стиле мока ("2Т 67'", "Set 3, 4-2", "Q3 8:42") ─────────
  function minute(sc) { if (!sc || sc.timer_v == null) return null; let v = +sc.timer_v; if (sc.timer_d === 1 && sc.timer_t) { const t = Date.parse(sc.timer_t); if (!isNaN(t)) v += Math.max(0, (Date.now() - t) / 1000); } return v; }
  function scoreMain(sc) { const l = sc && sc.list; const m = l && (l['0'] || l[0]); return (m && m.length >= 2) ? { home: m[0], away: m[1] } : null; }
  function periodLabel(ev, sp, sc) {
    if (ev.status === 3) return T('finished');
    if (ev.status === 2) return T('pause');
    if (!sc) return ev.sv === 'Created' ? T('soon') : 'LIVE';
    const keys = Object.keys(sc.list || {}).filter((k) => k !== '0').map(Number).sort((a, b) => a - b);
    const cur = keys.length ? keys[keys.length - 1] : 0;
    const secs = minute(sc);
    const clock = secs != null && (secs > 0 || sc.timer_d === 1);
    const mm = clock ? Math.floor(secs / 60) : null;
    if (sp === 4 || sp === 5 || sp === 6) {           // теннис / волейбол / наст. теннис: сеты + геймы
      const g = sc.list && sc.list[String(cur)];
      return T('set', { n: cur || 1 }) + (g ? ', ' + g[0] + '-' + g[1] : '');
    }
    if (sp === 3 || sp === 12) {                     // баскетбол / амер. футбол: четверть + время
      const q = cur || 1; const s = clock ? Math.floor(secs % 60) : 0;
      return T('quarter', { n: q }) + (clock ? ' ' + Math.floor(secs / 60) + ':' + String(s).padStart(2, '0') : '');
    }
    if (sp === 7) return T('period_n', { n: cur || 1 }) + (clock ? ' ' + mm + "'" : '');
    if (sp === 2) return T('map', { n: cur || 1 });
    // футбол и прочее: тайм + минута
    const half = cur >= 2 ? T('half2') : T('half1');
    return clock ? half + ' ' + mm + "'" : (ev.status === 2 ? T('pause') : (cur >= 2 ? T('half2') : (ev.sv === 'Created' ? T('soon') : T('half1'))));
  }

  /** обновить производные поля события из сырого объекта */
  function refreshDerived(event) {
    const r = event._ev;
    event.state = r.stage === 2 ? 'live' : 'prematch';
    event.score = scoreMain(r.score) || (r.sc ? { home: r.sc.split(':')[0], away: r.sc.split(':')[1] } : { home: '-', away: '-' });
    event.period = periodLabel(r, event.sportId, r.score);
    event._finished = r.status === 3;
    event._removed = !!r.removed;
  }

  /** главные маркеты события по слотам вида спорта: main[i] соответствует MMAIN[sport][i] */
  function mainMarketsFrom(event, main) {
    const out = [];
    if (!Array.isArray(main)) return out;
    const defs = MMAIN[String(event.sportId)] || [];
    main.forEach((mk, i) => {
      const d = defs[i];
      if (!d || !mk || !mk.oc || !mk.oc.length) return;
      const m = toMarket(event, Object.assign({ period: d.p, type: d.t }, mk));
      m._slot = i;
      out.push(m);
    });
    return out;
  }

  function upsertEvent(e) {
    let event = byId.get(e.id);
    const league = ensureLeague(e.sp, e.cat, e.tr);
    if (!event) {
      event = { id: e.id, sportId: +e.sp, sportAlias: sportAlias(e.sp), leagueId: league.id, countryCode: e.cat || '', startTs: e.st ? Date.parse(e.st) : Date.now(), state: 'prematch', score: null, period: '', home: '', away: '', mainMarkets: [], allMarketsCache: null, isHot: false, _ev: {}, _nm: 0, _finAt: null, _co: 999999, _to: 999999, _lp: null, _hotT: false, _x: false };
      byId.set(e.id, event); EVENTS.push(event);
    }
    [event.home, event.away] = splitName(e.n || event._ev.n);
    if (e.sp && +e.sp !== event.sportId) { event.sportId = +e.sp; event.sportAlias = sportAlias(e.sp); }   // заглушка страницы события создаётся с sp=1
    if (e.lh && event.home) LOGOS.set(event.home, e.lh);
    if (e.la && event.away) LOGOS.set(event.away, e.la);
    event.leagueId = league.id; event.countryCode = e.cat || event.countryCode;
    event._nm = e.nm || event._nm;
    if (e.co !== undefined) {   // ранги из админки (есть только в каталоге)
      event._co = +e.co; event._to = +e.to; event._lp = e.lp == null ? null : +e.lp; event._hotT = !!e.hot; event._x = !!e.x;
    }
    // Сырое состояние: обычно WS-дельты свежее каталога, но сравниваем не «по времени прихода», а
    // по версии события фида (dv) — она монотонна и приходит из одного источника. Каталог не старше
    // своей версии применяем целиком: иначе одна потерянная дельта (переподключение, отброшенная
    // публикация) навсегда оставила бы на странице старый счёт — минутный каталог его не лечил.
    // у события две версии: общая dv и версия счёта sdv — сравниваем пару
    const notOlder = (d1, s1, d2, s2) => (+d1 > +d2) || (+d1 === +d2 && +s1 >= +s2);
    const catNotOlder = e.dv != null && (event._ev.dv == null
      || notOlder(e.dv, e.sdv || 0, event._ev.dv, event._ev.sdv || 0));
    if (!event._ev.dv || event._ev.dv < 0 || catNotOlder) {
      Object.assign(event._ev, { n: e.n, stage: e.stage, status: e.status, sv: e.sv,
                                 score: e.score || event._ev.score, sc: e.sc,
                                 dv: e.dv != null ? +e.dv : (event._ev.dv || null),
                                 sdv: e.sdv != null ? +e.sdv : event._ev.sdv });
    } else { event._ev.n = event._ev.n || e.n; if (!event._ev.score && e.score) event._ev.score = e.score; }
    if (e.st) event.startTs = Date.parse(e.st);
    if (e.main) {
      // главные маркеты всегда берём из каталога (он не старше минуты), но цены/статусы,
      // обновлённые по WS ПОЗЖЕ момента сборки каталога, сохраняем — иначе кэф на минуту
      // откатился бы к устаревшему значению
      const catT = (e._catT || 0);
      const old = new Map();
      event.mainMarkets.forEach((m) => m.selections.forEach((s) => { old.set(s.id, s); selIndex.delete(s.id); }));
      event.mainMarkets = mainMarketsFrom(event, e.main);
      event.mainMarkets.forEach((m) => m.selections.forEach((s) => { const o = old.get(s.id); if (o && o._wsAt && o._wsAt > catT) { s.odds = o.odds; s.available = o.available; s._status = o._status; s._result = o._result; s._wsAt = o._wsAt; } }));
    }
    event._snap = !!e.snap;
    event._inCatalog = true;
    refreshDerived(event);
    return event;
  }

  // Hot events: сначала события hot-турниров из админки (cfg:th) — live в порядке лиг,
  // затем ближайшие prematch; если их меньше 6 live + 3 prematch — добираем в порядке лиг
  // (prematch — на ближайшие 3 часа).
  function pickHot() {
    EVENTS.forEach((e) => { e.isHot = false; });
    const ok = (e) => !e._finished && !e._removed && !e._x && e.mainMarkets.length > 0;
    const hotLive = EVENTS.filter((e) => ok(e) && e.state === 'live' && e._hotT).sort(cmpRank);
    const hotPre = EVENTS.filter((e) => ok(e) && e.state === 'prematch' && e._hotT && e.startTs - Date.now() < 12 * 3600e3).sort((a, b) => a.startTs - b.startTs);
    const pick = hotLive.slice(0, 6).concat(hotPre.slice(0, 3));
    const nLive = pick.filter((e) => e.state === 'live').length, nPre = pick.length - nLive;
    if (nLive < 6) EVENTS.filter((e) => ok(e) && e.state === 'live' && !pick.includes(e)).sort(cmpRank).slice(0, 6 - nLive).forEach((e) => pick.push(e));
    if (nPre < 3) EVENTS.filter((e) => ok(e) && e.state === 'prematch' && !pick.includes(e) && e.startTs - Date.now() < 3 * 3600e3).sort(cmpRank).slice(0, 3 - nPre).forEach((e) => pick.push(e));
    pick.forEach((e) => { e.isHot = true; });
  }

  // Как на live-странице Пижамы: завершённые матчи, матчи без маркетов и исключённые в
  // админке (вид спорта / страна / турнир, cfg:*.excl) не показываются. Событие, пришедшее
  // только по WS (без каталога), появится в списке, когда каталог (раз в минуту) отдаст
  // его главные маркеты.
  const isVis = (e) => !e._removed && !e._finished && !e._x && e.mainMarkets.length > 0;
  function visibleEvents() { return EVENTS.filter(isVis); }

  // ── каталог ──────────────────────────────────────────────────────────────
  // Часы браузера могут отличаться от серверных на минуты. Время сборки каталога (c.t) —
  // серверное, а моменты WS-обновлений мы засекаем локально, поэтому сравнивать их напрямую нельзя:
  // при спешащих часах кэф, обновлённый по WS, навсегда считался бы свежее каталога и застывал.
  // Держим поправку по заголовку Date ответа каталога и пишем моменты WS в серверной шкале.
  let SRV_SKEW = 0;                      // серверное время − локальное
  const srvNow = () => Date.now() + SRV_SKEW;
  async function loadCatalog() {
    const r = await fetch(DATA_BASE + 'catalog.json?' + Date.now(), { cache: 'no-store' });
    const hdr = Date.parse(r.headers.get('date') || '');
    if (!isNaN(hdr)) SRV_SKEW = hdr - Date.now();
    const c = await r.json();
    (c.sports || []).forEach(ensureSport);
    Object.keys(c.mcfg || {}).forEach((k) => { MCFG[k] = c.mcfg[k]; });
    Object.keys(MMAIN).forEach((k) => { delete MMAIN[k]; });
    Object.keys(c.mmain || {}).forEach((k) => { MMAIN[k] = c.mmain[k]; });
    Object.keys(MEXCL).forEach((k) => { delete MEXCL[k]; });
    Object.keys(c.mexcl || {}).forEach((k) => { MEXCL[k] = c.mexcl[k]; });
    const seen = new Set();
    const catT = (c.t || 0) * 1000;
    (c.events || []).forEach((e) => { seen.add(e.id); e._catT = catT; upsertEvent(e); });
    // События вне каталога: prematch — убираем; live — оставляем, пока по ним идут WS-дельты
    // (каталог обновляется раз в минуту), но если дельт нет 10 минут — это зависшая строка.
    // Завершённые (status 3) держатся 2 минуты и уходят.
    EVENTS.forEach((e) => {
      if (!seen.has(e.id)) {
        if (e.state !== 'live') e._removed = true;
        else if (!e._finished && (Date.now() - (e._lastWs || 0)) > 10 * 60000) e._removed = true;
      }
      if (e._finished && !e._finAt) e._finAt = Date.now();
    });
    sortEvents();
    pickHot();
    return c;
  }
  function sortEvents() {
    leagueNm = new Map();
    EVENTS.forEach((e) => { if (!e._removed && !e._finished) leagueNm.set(e.leagueId, Math.max(leagueNm.get(e.leagueId) || 0, e._nm || 0)); });
    EVENTS.sort((a, b) => (a.state === b.state ? 0 : a.state === 'live' ? -1 : 1) || cmpRank(a, b));
  }

  // ── снимок события (страница события) ────────────────────────────────────
  // Снимок события: REST api/snapshot.php (любое событие из БД бриджа, свежие данные), при
  // недоступности — статический снимок сборщика snap/{id}.json (только live и ближайший час).
  async function fetchSnapshot(id) {
    try { const r = await fetch('api/snapshot.php?id=' + id, { cache: 'no-store' }); if (r.ok) return await r.json(); if (r.status === 404) return null; } catch (e) { /* fallback */ }
    try { const r = await fetch(DATA_BASE + 'snap/' + id + '.json?' + Date.now(), { cache: 'no-store' }); if (r.ok) return await r.json(); } catch (e) { /* ignore */ }
    return null;
  }
  async function loadSnapshot(event) {
    const s = await fetchSnapshot(event.id);
    if (!s) return false;
    // событие вне каталога (ссылка на старое или далёкое): имена, лига, старт — из снимка
    if (s.ev && s.ev.n && !event.home) upsertEvent(Object.assign({ main: null, nm: 0 }, s.ev));
    event._markets = event._markets || {};
    (s.markets || []).forEach((m) => {
      const cur = event._markets[m.h];
      if (cur && cur._ws) return;
      event._markets[m.h] = Object.assign(cur || {}, { h: m.h, n: m.n, p: m.p, period: m.period, type: m.type, v: m.v || '', open: !!m.open, removed: false, oc: Object.fromEntries((m.oc || []).map((o) => [o.h, Object.assign({}, o)])) });
    });
    if (s.ev && !event._ev.score && s.ev.score) event._ev.score = s.ev.score;
    rebuildAll(event);
    return true;
  }
  function rebuildAll(event) {
    (event.allMarketsCache || []).forEach((m) => m.selections.forEach((s) => selIndex.delete(s.id)));
    const ms = Object.values(event._markets || {}).filter((m) => !m.removed && !marketExcluded(event.sportId, m));
    // порядок маркетов — как в админке (cfg:m по виду спорта): позиция стабильна, закрытый
    // маркет остаётся на месте (серым), а не прыгает вниз
    const numv = (m) => { if (m.v !== '' && m.v != null && !isNaN(parseFloat(m.v))) return parseFloat(m.v); const o = Object.values(m.oc || {}).find((x) => x.n === 'Win1') || Object.values(m.oc || {})[0]; return o && o.v != null && !isNaN(parseFloat(o.v)) ? parseFloat(o.v) : 0; };
    ms.sort((a, b) => (marketRank(event.sportId, a) - marketRank(event.sportId, b)) || (numv(a) - numv(b)) || String(a.n).localeCompare(String(b.n)));
    event.allMarketsCache = ms.map((m) => toMarket(event, Object.assign({}, m, { oc: Object.values(m.oc || {}) })));
  }

  // ── WS-дельты ────────────────────────────────────────────────────────────
  function onDelta(d) {
    const k = d.k || '', o = d.d, parts = k.split(':');
    if (d.ch === 'events') {
      const id = +parts[parts.length - 1];
      const event = byId.get(id);
      if (o === null) { if (event) { const v = isVis(event); event._removed = true; if (v) schedule('sections'); } return; }
      if (!event) {
        // новое событие вне каталога: заводим; без главных маркетов в списке его нет, секции
        // не трогаем — появится с ближайшим обновлением каталога
        const cat = EN(o.cname), tr = EN(o.tname);
        const ev = upsertEvent({ id, sp: +parts[1], n: EN(o.name), cat, tr, st: o.start, stage: o.stage, status: o.status, sv: o.statusv2, score: o.score, main: null, nm: 0 });
        ev._ev.dv = o.dv; refreshDerived(ev); if (ev.state === 'live') subscribeEvent(id);
        if (isVis(ev)) { sortEvents(); pickHot(); schedule('sections'); }
        return;
      }
      const r = event._ev;
      event._lastWs = Date.now();
      if (o.dv != null && r.dv != null && o.dv < r.dv) return;
      // Перерисовка секций — только если событие появилось/исчезло из видимого списка или
      // сменило live/prematch; остальное (счёт, минута, кэфы) правится на месте. Раньше любая
      // дельта завершённого или невидимого события перерисовывала всю таблицу — строки
      // «мигали» под курсором.
      const wasLive = event.state === 'live', wasVis = isVis(event);
      Object.assign(r, { n: EN(o.name) || r.n, stage: o.stage, status: o.status, sv: o.statusv2, score: o.score || r.score, dv: o.dv, sdv: o.sdv, removed: !!o.removed });
      if (o.start) event.startTs = Date.parse(o.start);
      refreshDerived(event);
      if (event._finished && !event._finAt) event._finAt = Date.now();
      if (event.state === 'live' && !wasLive) { subscribeEvent(event.id); sortEvents(); pickHot(); }
      const nowVis = isVis(event);
      if (nowVis !== wasVis || (nowVis && (event.state === 'live') !== wasLive)) schedule('sections');
      else if (nowVis) schedule('odds');
      schedule('event');
      return;
    }
    const evId = +parts[1], h = parts[2];
    const event = byId.get(evId);
    if (!event || o === null) return;
    event._lastWs = Date.now();
    if (d.ch === 'markets') {
      const mk = { h, n: EN(o.name), p: EN(o.period_name), period: o.period, type: o.type, v: o.value || '', open: !!o.open, removed: !!o.removed, ver: o.ver };
      // главные маркеты строки
      const main = event.mainMarkets.find((m) => m._h === h);
      if (main) { main._open = mk.open; main._removed = mk.removed; main.selections.forEach((s) => { s.available = mk.open && s._status === 1 && s.odds > 0; s._wsAt = srvNow(); }); if (isVis(event)) schedule('odds'); }
      // полный набор (страница события)
      if (event._markets) {
        const cur = event._markets[h];
        if (!cur || (cur.ver == null) || mk.ver == null || mk.ver >= cur.ver) { event._markets[h] = Object.assign(cur || { oc: {} }, mk, { _ws: true, oc: (cur && cur.oc) || {} }); rebuildAll(event); schedule('event'); }
      }
      return;
    }
    if (d.ch === 'outcomes') {
      const oh = parts.slice(3).join(':');
      const price = o.price != null ? +o.price : null;
      const main = event.mainMarkets.find((m) => m._h === h);
      if (main) {
        const s = main.selections.find((x) => x._h === oh);
        if (s) { s.odds = price != null ? price : s.odds; s._status = o.status; s._result = o.result || 0; s.available = main._open && o.status === 1 && !o.removed && price != null; s._wsAt = srvNow(); if (isVis(event)) schedule('odds'); }
      }
      if (event._markets) {
        const mk = event._markets[h] || (event._markets[h] = { h, n: '', p: '', period: 1, v: '', open: true, removed: false, oc: {}, _ws: true });
        const prev = mk.oc[oh];
        mk.oc[oh] = Object.assign(prev || { h: oh }, { n: EN(o.name), v: o.value || '', t: o.type, price, status: o.status, result: o.result || 0, removed: !!o.removed });
        if (!prev || o.removed) { rebuildAll(event); schedule('event'); }
        else { const m = (event.allMarketsCache || []).find((x) => x._h === h); const s = m && m.selections.find((x) => x._h === oh); if (s) { s.odds = price != null ? price : s.odds; s._status = o.status; s.available = m._open && o.status === 1 && !o.removed && price != null; } schedule('odds'); }
      }
    }
  }

  function subscribe(ch) { if (!cf || subs[ch]) return; const s = cf.newSubscription(ch); s.on('publication', (ctx) => onDelta(ctx.data || {})); s.subscribe(); subs[ch] = s; }
  function unsubscribe(ch) { const s = subs[ch]; if (!s) return; s.unsubscribe(); cf.removeSubscription(s); delete subs[ch]; }
  function subscribeEvent(id) { if (Object.keys(subs).filter((c) => c.startsWith('event:')).length >= MAX_EVENT_SUBS) return; subscribe('event:' + id); }
  function syncSubs(mode, eventId) {
    const want = new Set();
    if (mode === 'index') {
      SPORTS.forEach((s) => want.add('sport:' + s.id));
      EVENTS.filter((e) => e.state === 'live' && !e._finished && !e._removed).slice(0, MAX_EVENT_SUBS).forEach((e) => want.add('event:' + e.id));
      EVENTS.filter((e) => e.isHot && e.state === 'prematch').forEach((e) => want.add('event:' + e.id));
    } else if (mode === 'event' && eventId) { const ev = byId.get(eventId); if (ev) want.add('sport:' + ev.sportId); want.add('event:' + eventId); }
    Object.keys(subs).forEach((ch) => { if (!want.has(ch)) unsubscribe(ch); });
    want.forEach(subscribe);
  }

  function connect(statusEl) {
    cf = new Centrifuge(WS_URL);
    // LED «Live» в шапке: серый мигающий — подключение, зелёный — WS подключён, красный — нет связи
    const set = (cls, t) => { if (statusEl) { statusEl.className = 'sb-conn ' + cls; statusEl.title = t; } };
    cf.on('connecting', () => set('', T('ws_connecting')));
    cf.on('connected', () => set('ok', T('ws_ok')));
    cf.on('disconnected', () => set('bad', T('ws_bad')));
    cf.connect();
  }

  // ── кошелёк партнёра (эмуляция, api/wallet.php — балансы в текстовом файле) ─
  // Та же логика, что на тестовом стенде Borlette (gem.x1b.site): стартовые 10 000 HTG,
  // списание при ставке, начисление при выигрыше/возврате (рассчитывает сборщик
  // health/collect.py по результатам исходов). Пользователь — ?user= (по умолчанию 42).
  const WALLET_URL = 'api/wallet.php';
  // Пользователь кошелька: ?user= → localStorage → 42. Параметр запоминается так же, как
  // язык (assets/js/i18n.js): витрина product-showcase открывает страницу в iframe со своим
  // идентификатором посетителя, а переход на страницу события — обычная навигация внутри
  // iframe, и без запоминания демо-баланс сваливался бы на общего пользователя 42.
  const USER_KEY = 'sportsbook-live-user';
  const USER = (() => {
    const clean = (v) => String(v || '').replace(/[^A-Za-z0-9_-]/g, '').slice(0, 32);
    const param = clean(new URLSearchParams(location.search).get('user'));
    if (param) { try { localStorage.setItem(USER_KEY, param); } catch (e) { /* ignore */ } return param; }
    let stored = '';
    try { stored = clean(localStorage.getItem(USER_KEY)); } catch (e) { /* ignore */ }
    return stored || '42';
  })();
  // gate — состояние гейта свежести фида (api/wallet.php → feedbridge/gate.py). Пока он не OPEN,
  // сервер не примет ни ставку, ни выкуп; интерфейс гасит кнопку заранее, не дожидаясь отказа.
  const wallet = { user: USER, balance: null, currency: 'HTG', bets: [], gate: null, _status: new Map() };
  async function walletCall(body) {
    try {
      const r = await fetch(WALLET_URL, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(Object.assign({ user: USER }, body)), cache: 'no-store' });
      return await r.json();
    } catch (e) { return { result: false, error: 'network' }; }
  }
  async function walletRefresh(first) {
    const r = await walletCall({ action: 'bets' });
    if (!r || !r.result) { if (first) onChange('balance', { first: true }); return; }
    const prev = wallet.balance;
    wallet.balance = r.balance; wallet.currency = r.currency || wallet.currency; wallet.bets = r.bets || [];
    const gateWas = wallet.gate && wallet.gate.open;
    wallet.gate = r.gate || null;
    if (r.delay_ms) wallet.delayMs = +r.delay_ms;
    if (r.cashout_delay_ms) wallet.cashoutDelayMs = +r.cashout_delay_ms;
    const settled = [];
    wallet.bets.forEach((b) => { const was = wallet._status.get(b.id); if (was === 'open' && b.status !== 'open') settled.push(b); wallet._status.set(b.id, b.status); });
    if (first) { onChange('balance', { first: true }); return; }
    const gateNow = wallet.gate && wallet.gate.open;
    if (prev !== wallet.balance || settled.length || gateWas !== gateNow) {
      onChange('balance', { delta: wallet.balance - (prev || 0), settled, gateChanged: gateWas !== gateNow });
    }
  }
  async function placeBet(stakeCents, items) {
    const receipt = 'r' + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
    const selections = items.map((it) => {
      const p = String(it.selection.id).split('|');
      return { ev: +p[0], mk: p[1], oc: p.slice(2).join('|'), odds: it.selection.odds, label: it.selection.label, event: it.event.home + ' - ' + it.event.away, market: it.market.name };
    });
    const r = await walletCall({ action: 'place_bet', receipt_id: receipt, stake: stakeCents, selections });
    if (r && r.result) {
      wallet.balance = r.balance;
      if (r.bet) { wallet.bets.unshift(r.bet); wallet._status.set(r.bet.id, r.bet.status); }
      onChange('balance', { delta: -stakeCents });
    } else if (r && r.balance != null) { wallet.balance = r.balance; onChange('balance', {}); }
    return r || { result: false, error: 'network' };
  }

  // ── window.SB (контракт мока) ────────────────────────────────────────────
  const SLIP_KEY = 'sportsbook-live-slip';
  function loadSlip() { try { const raw = JSON.parse(localStorage.getItem(SLIP_KEY) || 'null'); if (raw && Array.isArray(raw.selections)) return { selections: raw.selections.map(String), stake: +raw.stake || 0 }; } catch (e) { /* ignore */ } return { selections: [], stake: 0 }; }
  function saveSlip(s) { try { localStorage.setItem(SLIP_KEY, JSON.stringify(s)); } catch (e) { /* ignore */ } }

  window.SB = {
    SPORTS, LEAGUES, EVENTS,
    get NOW() { return Date.now(); },
    sport: (alias) => SPORTS.find((s) => s.alias === alias) || { id: 0, alias, label: alias, icon: '🏅' },
    league: (id) => LEAGUES.find((l) => l.id === id) || { id, sport: '', country: '', name: '' },
    eventById: (id) => byId.get(+id) || null,
    visibleEvents,
    buildAllMarkets: (event) => event.allMarketsCache || [],
    loadSlip,
    toggleInSlip(sid) { const s = loadSlip(); const i = s.selections.indexOf(String(sid)); if (i >= 0) s.selections.splice(i, 1); else s.selections.push(String(sid)); saveSlip(s); return s; },
    setStake(v) { const s = loadSlip(); s.stake = +v || 0; saveSlip(s); return s; },
    clearSlip() { saveSlip({ selections: [], stake: 0 }); },
    indexSelections: () => selIndex,
    logoUrl: (name) => LOGOS.get(name) || '',
    /** колонки таблицы для вида спорта: набор главных маркетов из админки Спорта */
    mainCols: (sportId) => MMAIN[String(sportId)] || [],
    wallet: {
      get user() { return wallet.user; }, get balance() { return wallet.balance; }, get currency() { return wallet.currency; }, get bets() { return wallet.bets; },
      get delayMs() { return wallet.delayMs || 3000; },   // задержка приёма ставки на сервере (BET_DELAY_MS)
      /** состояние гейта свежести фида; betsOpen=false — сервер ставку не примет */
      get gate() { return wallet.gate; },
      get betsOpen() { return !wallet.gate || !!wallet.gate.open; },   // до первого ответа не пугаем
      get cashoutDelayMs() { return wallet.cashoutDelayMs || 5000; },   // задержка выкупа (CASHOUT_DELAY_MS)
      /** котировка кешаута открытой ставки (как cashout_check бэка Спорта): {enabled, price, ratio} */
      cashoutCheck: (id) => walletCall({ action: 'cashout_check', bet_id: String(id) }),
      /** выкуп ставки: сервер держит запрос cashoutDelayMs и платит по котировке на момент выкупа */
      async cashout(id) {
        const r = await walletCall({ action: 'cashout', bet_id: String(id) });
        if (r && r.result && r.bet) {
          wallet.balance = r.balance;
          wallet.bets = wallet.bets.map((b) => (b.id === r.bet.id ? r.bet : b));
          wallet._status.set(r.bet.id, r.bet.status);
          onChange('balance', { delta: r.bet.payout || 0 });
        }
        return r || { result: false, error: 'network' };
      },
      refresh: () => walletRefresh(false), placeBet,
      /** убрать рассчитанную ставку из «Моих ставок» */
      async hideBet(id) {
        const r = await walletCall({ action: 'hide_bet', bet_id: String(id) });
        if (r && r.result) { wallet.bets = wallet.bets.filter((b) => b.id !== id); wallet._status.delete(id); onChange('balance', {}); }
        return r || { result: false, error: 'network' };
      },
    },
    startLiveOutcomeRandomization() { /* живые данные: рандомизация мока не нужна */ },
    live: {
      /** главная: каталог + WS; cb(kind) — просьба перерисовать */
      async initIndex(cb, statusEl) {
        onChange = cb;
        await loadDict();
        await loadCatalog();
        connect(statusEl); syncSubs('index');
        walletRefresh(true); setInterval(() => walletRefresh(false), 30000);
        setInterval(async () => { try { await loadCatalog(); syncSubs('index'); onChange('sections'); } catch (e) { /* ignore */ } }, 60000);
        // минута матча на месте; завершённые/зависшие строки снимаются перерисовкой секций
        let lastVisible = visibleEvents().length;
        setInterval(() => {
          let ch = false;
          EVENTS.forEach((e) => { if (e.state === 'live') { const p = periodLabel(e._ev, e.sportId, e._ev.score); if (p !== e.period) { e.period = p; ch = true; } } });
          const vis = visibleEvents().length;
          if (vis !== lastVisible) { lastVisible = vis; onChange('sections'); } else if (ch) onChange('period');
        }, 5000);
      },
      /** страница события: каталог (имена) + снимок + WS */
      async initEvent(id, cb, statusEl) {
        onChange = cb;
        await loadDict();
        try { await loadCatalog(); } catch (e) { /* ignore */ }
        let event = byId.get(+id);
        if (!event) { event = upsertEvent({ id: +id, sp: 1, n: '', cat: '', tr: '', stage: 2, status: 1, main: null, nm: 0 }); }
        await loadSnapshot(event).catch(() => false);
        connect(statusEl); syncSubs('event', +id);
        walletRefresh(true); setInterval(() => walletRefresh(false), 30000);
        setInterval(() => { const p = periodLabel(event._ev, event.sportId, event._ev.score); if (p !== event.period) { event.period = p; onChange('event'); } }, 5000);
        setInterval(async () => { try { if (!event._markets || !Object.keys(event._markets).length) await loadSnapshot(event); } catch (e) { /* ignore */ } }, 60000);
        return event;
      },
      periodScores(event) { const l = event._ev.score && event._ev.score.list; if (!l) return []; return Object.keys(l).filter((k) => k !== '0').sort((a, b) => +a - +b).map((k) => ({ p: k, s: l[k][0] + ':' + l[k][1] })); },
      marketPeriod: (m) => m._p || '',
      marketOpen: (m) => m._open,
      /** заголовок блока линий одного типа («Тотал», «Фора», «Двойной шанс») и подпись периода («1-й тайм», '' для основного времени) */
      marketGroupTitle: (m) => marketName(m._n),
      periodTitle: (m) => periodName(m._p),
      lang: LN,
      lineCaption,
      /** подписи колонок главной таблицы (заголовок лиги): Б / Тотал / М, Ф1 / Фора / Ф2 на языке страницы */
      /** подпись колонки таблицы по коду исхода: Win1 → «1», Draw → «X», 1X → «1X», Over → «Более» */
      colLabel: (code) => (code === 'Win1' ? '1' : code === 'Win2' ? '2' : code === 'Draw' ? 'X' : outcomeName(code)),
      /** название типа маркета для средней колонки группы («Тотал», «Фора») */
      marketLabel: (code) => marketName(code),
      /** «Популярные» — первые POPULAR_TOP пар (тип, период) из порядка маркетов админки (cfg:m) для вида
       *  спорта события; без cfg:m — основные типы */
      isPopular(event, m) {
        const list = MCFG[String(event.sportId)];
        if (!list) return /^(Winner3Ways|Winner2Ways|DoubleChance|Total|Handicap|BothToScore|CorrectScore|DrawNoBet)$/.test(m._n || '') && (!m._p || m._p === 'MainTime' || m._p === 'Match');
        return list.slice(0, POPULAR_TOP).some((x) => x[0] === m._type && x[1] === m._period);
      },
    },
  };
})();
