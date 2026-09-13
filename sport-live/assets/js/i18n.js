/* eslint-disable no-undef */
/**
 * i18n.js — язык интерфейса прототипа sport-live: ru, en, es, pt (fr/ht — только словари фида,
 * интерфейс на английском). Язык берётся из ?ln=, иначе из localStorage, иначе ru; выбор в
 * переключателе шапки пишется в localStorage и перезагружает страницу с ?ln=.
 * Подписи маркетов/исходов/периодов/видов спорта — отдельно, из словарей фронта Спорта
 * (assets/dict/{ln}.json, см. live-data.js). Здесь только строки самого интерфейса.
 */
window.SB_I18N = (function () {
  const D = {
    ru: {
      event_title: 'Событие — Sportsbook Paryajpam (live)', back: '← К списку событий',
      conn_title: 'Подключение к WS-серверу', balance_title: 'Баланс кошелька партнёра (эмуляция)',
      tab_live: 'Лайв', tab_line: 'Линия', nav_bets: 'Ставки',
      sports: 'Виды спорта', all: 'Все', hot: '🔥 Hot events', top: 'TOP', live_now: '🔴 LIVE сейчас', schedule: '📅 Расписание',
      inplay: 'IN-PLAY', pre: 'PRE', no_events: 'Нет событий', all_markets: 'Все маркеты', more_btn: 'Ещё',
      coupon: 'Купон', my_bets: 'Мои ставки', clear: 'Очистить', slip_empty: 'Выберите исходы в таблице — они появятся здесь.',
      remove: 'Удалить', stake: 'Ставка', total_odds: 'Общий коэффициент', potential_win: 'Возможный выигрыш',
      unavailable_warn: '⚠ Один из исходов сейчас недоступен. Ставка временно заблокирована.',
      place_bet: 'Сделать ставку', sending: 'Отправка…',
      bet_accepted: 'Ставка принята: {stake} {cur}, возможный выигрыш {win} {cur}', bet_rejected: 'Ставка не принята: {reason}',
      no_money: 'недостаточно средств', no_conn: 'нет связи', added_to_slip: 'Исход добавлен в купон', removed_from_slip: 'Исход удалён из купона',
      bet_won: 'Ставка выиграла: +{sum}', bet_returned: 'Ставка рассчитана возвратом: +{sum}', bet_lost: 'Ставка {sum} проиграла',
      bet_hidden: 'Ставка убрана из списка', bet_hide_fail: 'Не удалось убрать ставку', no_bets: 'Сделанных ставок пока нет.',
      st_open: 'Открыта', st_win: 'Выиграла', st_lose: 'Проиграла', st_return: 'Возврат', st_cancelled: 'Отменена',
      odds: 'Кэф', payout: 'Выплата', settling: 'Рассчитывается…', settle_in: 'Расчёт ≈ через {t}', h_min: '{h} ч {m} мин', min: '{m} мин',
      settled_at: 'Рассчитана {t}', del_from_list: 'Удалить из списка',
      cashout: 'Кешаут', cashout_for: 'Выкупить за', cashout_note: 'Сумма выкупа меняется со временем', cashout_go: 'Выкупить',
      cashout_wait: 'Выкуп… {s}', cashout_done: 'Ставка выкуплена за {sum} {cur}', cashout_unavail: 'Кешаут сейчас недоступен',
      cancel: 'Отмена', st_cashout: 'Выкуплена',
      feed_suspended: '⏸ Приём ставок приостановлен: данные линии сейчас несвежие',
      finished: 'Завершён', pause: 'Пауза', soon: 'Скоро', set: 'Сет {n}', quarter: '{n}-я четверть', period_n: '{n}-й период', map: 'Карта {n}',
      half1: '1Т', half2: '2Т', team: 'Команда {n}',
      home: 'Хозяева', away: 'Гости', markets_loading: 'Маркеты загружаются…', no_markets_filter: 'По этому фильтру маркетов нет',
      tab_all: 'Все', tab_total: 'Тотал', tab_handicap: 'Фора', tab_popular: 'Популярные',
      period: 'Период', all_periods: 'Все периоды', main_time: 'Основное время', search: 'Поиск', search_markets: 'Поиск по маркетам',
      fold_all: 'Свернуть / развернуть все', event_not_found: 'Событие не найдено.',
      ws_connecting: 'Подключение к WS-серверу…', ws_ok: 'WS-сервер подключён: кэфы обновляются в реальном времени', ws_bad: 'Нет связи с WS-сервером',
      accepting: 'Приём ставки… {s}', odds_changed: 'Коэффициенты изменились — проверьте купон и подтвердите ставку', confirm_bet: 'Подтвердить ставку',
      sel_unavailable: 'Исход больше недоступен', delay_note: 'Ставка принимается с задержкой {s} с',
      bet_accepted_up: 'Кэф вырос — ставка принята по {odds}, возможный выигрыш {win} {cur}',
      bets_open: 'Текущие', bets_archive: 'Архив', no_open_bets: 'Открытых ставок нет.', no_archive: 'В архиве пока пусто.',
      lang: 'Язык',
    },
    en: {
      event_title: 'Event — Sportsbook Paryajpam (live)', back: '← Back to events',
      conn_title: 'WebSocket connection', balance_title: 'Partner wallet balance (emulation)',
      tab_live: 'Live', tab_line: 'Line', nav_bets: 'Bets',
      sports: 'Sports', all: 'All', hot: '🔥 Hot events', top: 'TOP', live_now: '🔴 LIVE now', schedule: '📅 Upcoming',
      inplay: 'IN-PLAY', pre: 'PRE', no_events: 'No events', all_markets: 'All markets', more_btn: 'More',
      coupon: 'Bet slip', my_bets: 'My bets', clear: 'Clear', slip_empty: 'Pick outcomes in the table — they will appear here.',
      remove: 'Remove', stake: 'Stake', total_odds: 'Total odds', potential_win: 'Potential win',
      unavailable_warn: '⚠ One of the outcomes is unavailable right now. Betting is temporarily blocked.',
      place_bet: 'Place bet', sending: 'Sending…',
      bet_accepted: 'Bet accepted: {stake} {cur}, potential win {win} {cur}', bet_rejected: 'Bet rejected: {reason}',
      no_money: 'insufficient funds', no_conn: 'no connection', added_to_slip: 'Added to bet slip', removed_from_slip: 'Removed from bet slip',
      bet_won: 'Bet won: +{sum}', bet_returned: 'Bet settled as void: +{sum}', bet_lost: 'Bet {sum} lost',
      bet_hidden: 'Bet removed from the list', bet_hide_fail: 'Could not remove the bet', no_bets: 'No bets yet.',
      st_open: 'Open', st_win: 'Won', st_lose: 'Lost', st_return: 'Void', st_cancelled: 'Cancelled',
      odds: 'Odds', payout: 'Payout', settling: 'Settling…', settle_in: 'Settles in ≈ {t}', h_min: '{h} h {m} min', min: '{m} min',
      settled_at: 'Settled {t}', del_from_list: 'Remove from list',
      cashout: 'Cash out', cashout_for: 'Cash out for', cashout_note: 'The cash-out amount changes over time', cashout_go: 'Cash out',
      cashout_wait: 'Cashing out… {s}', cashout_done: 'Bet cashed out for {sum} {cur}', cashout_unavail: 'Cash out is not available right now',
      cancel: 'Cancel', st_cashout: 'Cashed out',
      feed_suspended: '⏸ Betting is paused: the line data is not fresh right now',
      finished: 'Finished', pause: 'Break', soon: 'Soon', set: 'Set {n}', quarter: 'Q{n}', period_n: 'Period {n}', map: 'Map {n}',
      half1: '1H', half2: '2H', team: 'Team {n}',
      home: 'Home', away: 'Away', markets_loading: 'Loading markets…', no_markets_filter: 'No markets match this filter',
      tab_all: 'All', tab_total: 'Totals', tab_handicap: 'Handicaps', tab_popular: 'Popular',
      period: 'Period', all_periods: 'All periods', main_time: 'Regular time', search: 'Search', search_markets: 'Search markets',
      fold_all: 'Collapse / expand all', event_not_found: 'Event not found.',
      ws_connecting: 'Connecting to WS server…', ws_ok: 'WS connected: odds update in real time', ws_bad: 'No connection to WS server',
      accepting: 'Placing bet… {s}', odds_changed: 'Odds have changed — check the slip and confirm the bet', confirm_bet: 'Confirm bet',
      sel_unavailable: 'Outcome is no longer available', delay_note: 'Bets are accepted with a {s} s delay',
      bet_accepted_up: 'Odds went up — bet accepted at {odds}, potential win {win} {cur}',
      bets_open: 'Open', bets_archive: 'Archive', no_open_bets: 'No open bets.', no_archive: 'Archive is empty.',
      lang: 'Language',
    },
    es: {
      event_title: 'Evento — Sportsbook Paryajpam (live)', back: '← A la lista de eventos',
      conn_title: 'Conexión WebSocket', balance_title: 'Saldo de la billetera del socio (emulación)',
      tab_live: 'En vivo', tab_line: 'Línea', nav_bets: 'Apuestas',
      sports: 'Deportes', all: 'Todos', hot: '🔥 Eventos destacados', top: 'TOP', live_now: '🔴 EN VIVO', schedule: '📅 Próximos',
      inplay: 'EN JUEGO', pre: 'PRE', no_events: 'Sin eventos', all_markets: 'Todos los mercados', more_btn: 'Más',
      coupon: 'Cupón', my_bets: 'Mis apuestas', clear: 'Limpiar', slip_empty: 'Elige resultados en la tabla: aparecerán aquí.',
      remove: 'Quitar', stake: 'Apuesta', total_odds: 'Cuota total', potential_win: 'Ganancia posible',
      unavailable_warn: '⚠ Uno de los resultados no está disponible ahora. La apuesta está bloqueada temporalmente.',
      place_bet: 'Hacer apuesta', sending: 'Enviando…',
      bet_accepted: 'Apuesta aceptada: {stake} {cur}, ganancia posible {win} {cur}', bet_rejected: 'Apuesta rechazada: {reason}',
      no_money: 'fondos insuficientes', no_conn: 'sin conexión', added_to_slip: 'Añadido al cupón', removed_from_slip: 'Quitado del cupón',
      bet_won: 'Apuesta ganada: +{sum}', bet_returned: 'Apuesta devuelta: +{sum}', bet_lost: 'Apuesta {sum} perdida',
      bet_hidden: 'Apuesta eliminada de la lista', bet_hide_fail: 'No se pudo eliminar la apuesta', no_bets: 'Aún no hay apuestas.',
      st_open: 'Abierta', st_win: 'Ganada', st_lose: 'Perdida', st_return: 'Devuelta', st_cancelled: 'Cancelada',
      odds: 'Cuota', payout: 'Pago', settling: 'Liquidando…', settle_in: 'Liquidación ≈ en {t}', h_min: '{h} h {m} min', min: '{m} min',
      settled_at: 'Liquidada {t}', del_from_list: 'Eliminar de la lista',
      cashout: 'Cash out', cashout_for: 'Cobrar por', cashout_note: 'El importe del cash out cambia con el tiempo', cashout_go: 'Cobrar',
      cashout_wait: 'Cobrando… {s}', cashout_done: 'Apuesta cobrada por {sum} {cur}', cashout_unavail: 'El cash out no está disponible ahora',
      cancel: 'Cancelar', st_cashout: 'Cobrada',
      feed_suspended: '⏸ Las apuestas están en pausa: los datos de la línea no están actualizados',
      finished: 'Finalizado', pause: 'Descanso', soon: 'Pronto', set: 'Set {n}', quarter: '{n}º cuarto', period_n: '{n}º período', map: 'Mapa {n}',
      half1: '1T', half2: '2T', team: 'Equipo {n}',
      home: 'Local', away: 'Visitante', markets_loading: 'Cargando mercados…', no_markets_filter: 'No hay mercados con este filtro',
      tab_all: 'Todos', tab_total: 'Totales', tab_handicap: 'Hándicaps', tab_popular: 'Populares',
      period: 'Período', all_periods: 'Todos los períodos', main_time: 'Tiempo reglamentario', search: 'Buscar', search_markets: 'Buscar mercados',
      fold_all: 'Plegar / desplegar todo', event_not_found: 'Evento no encontrado.',
      ws_connecting: 'Conectando al servidor WS…', ws_ok: 'WS conectado: las cuotas se actualizan en tiempo real', ws_bad: 'Sin conexión con el servidor WS',
      accepting: 'Aceptando apuesta… {s}', odds_changed: 'Las cuotas cambiaron: revisa el cupón y confirma la apuesta', confirm_bet: 'Confirmar apuesta',
      sel_unavailable: 'El resultado ya no está disponible', delay_note: 'La apuesta se acepta con {s} s de retraso',
      bet_accepted_up: 'La cuota subió: apuesta aceptada a {odds}, ganancia posible {win} {cur}',
      bets_open: 'Abiertas', bets_archive: 'Archivo', no_open_bets: 'No hay apuestas abiertas.', no_archive: 'El archivo está vacío.',
      lang: 'Idioma',
    },
    pt: {
      event_title: 'Evento — Sportsbook Paryajpam (live)', back: '← Voltar aos eventos',
      conn_title: 'Conexão WebSocket', balance_title: 'Saldo da carteira do parceiro (emulação)',
      tab_live: 'Ao vivo', tab_line: 'Linha', nav_bets: 'Apostas',
      sports: 'Esportes', all: 'Todos', hot: '🔥 Eventos em destaque', top: 'TOP', live_now: '🔴 AO VIVO', schedule: '📅 Próximos',
      inplay: 'EM JOGO', pre: 'PRÉ', no_events: 'Sem eventos', all_markets: 'Todos os mercados', more_btn: 'Mais',
      coupon: 'Cupom', my_bets: 'Minhas apostas', clear: 'Limpar', slip_empty: 'Escolha resultados na tabela — eles aparecerão aqui.',
      remove: 'Remover', stake: 'Aposta', total_odds: 'Odd total', potential_win: 'Ganho possível',
      unavailable_warn: '⚠ Um dos resultados está indisponível agora. A aposta está temporariamente bloqueada.',
      place_bet: 'Fazer aposta', sending: 'Enviando…',
      bet_accepted: 'Aposta aceita: {stake} {cur}, ganho possível {win} {cur}', bet_rejected: 'Aposta recusada: {reason}',
      no_money: 'saldo insuficiente', no_conn: 'sem conexão', added_to_slip: 'Adicionado ao cupom', removed_from_slip: 'Removido do cupom',
      bet_won: 'Aposta ganha: +{sum}', bet_returned: 'Aposta devolvida: +{sum}', bet_lost: 'Aposta {sum} perdida',
      bet_hidden: 'Aposta removida da lista', bet_hide_fail: 'Não foi possível remover a aposta', no_bets: 'Ainda não há apostas.',
      st_open: 'Aberta', st_win: 'Ganha', st_lose: 'Perdida', st_return: 'Devolvida', st_cancelled: 'Cancelada',
      odds: 'Odd', payout: 'Pagamento', settling: 'Liquidando…', settle_in: 'Liquidação ≈ em {t}', h_min: '{h} h {m} min', min: '{m} min',
      settled_at: 'Liquidada {t}', del_from_list: 'Remover da lista',
      cashout: 'Cash out', cashout_for: 'Encerrar por', cashout_note: 'O valor do cash out muda com o tempo', cashout_go: 'Encerrar',
      cashout_wait: 'Encerrando… {s}', cashout_done: 'Aposta encerrada por {sum} {cur}', cashout_unavail: 'Cash out indisponível no momento',
      cancel: 'Cancelar', st_cashout: 'Encerrada',
      feed_suspended: '⏸ As apostas estão pausadas: os dados da linha não estão atualizados',
      finished: 'Encerrado', pause: 'Intervalo', soon: 'Em breve', set: 'Set {n}', quarter: '{n}º quarto', period_n: '{n}º período', map: 'Mapa {n}',
      half1: '1T', half2: '2T', team: 'Equipe {n}',
      home: 'Casa', away: 'Visitante', markets_loading: 'Carregando mercados…', no_markets_filter: 'Nenhum mercado com este filtro',
      tab_all: 'Todos', tab_total: 'Totais', tab_handicap: 'Handicaps', tab_popular: 'Populares',
      period: 'Período', all_periods: 'Todos os períodos', main_time: 'Tempo regulamentar', search: 'Buscar', search_markets: 'Buscar mercados',
      fold_all: 'Recolher / expandir tudo', event_not_found: 'Evento não encontrado.',
      ws_connecting: 'Conectando ao servidor WS…', ws_ok: 'WS conectado: odds atualizadas em tempo real', ws_bad: 'Sem conexão com o servidor WS',
      accepting: 'Aceitando aposta… {s}', odds_changed: 'As odds mudaram — confira o cupom e confirme a aposta', confirm_bet: 'Confirmar aposta',
      sel_unavailable: 'O resultado não está mais disponível', delay_note: 'A aposta é aceita com {s} s de atraso',
      bet_accepted_up: 'A odd subiu — aposta aceita a {odds}, ganho possível {win} {cur}',
      bets_open: 'Abertas', bets_archive: 'Arquivo', no_open_bets: 'Não há apostas abertas.', no_archive: 'O arquivo está vazio.',
      lang: 'Idioma',
    },
  };
  const ALL = ['ru', 'en', 'es', 'pt', 'fr', 'ht'];      // fr/ht: словари фида есть, интерфейс — английский
  const SWITCH = [['ru', 'RU'], ['en', 'EN'], ['es', 'ES'], ['pt', 'PT']];
  const KEY = 'sportsbook-live-lang';
  const param = (new URLSearchParams(location.search).get('ln') || '').toLowerCase();
  let stored = '';
  try { stored = (localStorage.getItem(KEY) || '').toLowerCase(); } catch (e) { /* ignore */ }
  const lang = [param, stored].find((x) => ALL.includes(x)) || 'ru';
  try { localStorage.setItem(KEY, lang); } catch (e) { /* ignore */ }
  const ui = D[lang] ? lang : 'en';
  const LOCALE = { ru: 'ru-RU', en: 'en-GB', es: 'es-ES', pt: 'pt-BR', fr: 'fr-FR', ht: 'fr-HT' }[lang];

  function t(key, vars) {
    let s = D[ui][key];
    if (s === undefined) s = D.en[key] !== undefined ? D.en[key] : (D.ru[key] !== undefined ? D.ru[key] : key);
    if (vars) Object.keys(vars).forEach((k) => { s = s.split('{' + k + '}').join(String(vars[k])); });
    return s;
  }
  /** статические подписи разметки: data-i18n (текст), data-i18n-title, data-i18n-aria, data-i18n-ph (placeholder) */
  function apply() {
    document.documentElement.lang = lang;
    document.querySelectorAll('[data-i18n]').forEach((el) => { el.textContent = t(el.dataset.i18n); });
    document.querySelectorAll('[data-i18n-title]').forEach((el) => { el.title = t(el.dataset.i18nTitle); });
    document.querySelectorAll('[data-i18n-aria]').forEach((el) => { el.setAttribute('aria-label', t(el.dataset.i18nAria)); });
    document.querySelectorAll('[data-i18n-ph]').forEach((el) => { el.placeholder = t(el.dataset.i18nPh); });
  }
  /** переключатель языка в шапке: RU EN ES PT; выбор → localStorage + перезагрузка с ?ln= */
  function switcher(el) {
    if (!el) return;
    el.innerHTML = SWITCH.map(([code, label]) => `<button class="sb-lang__btn${code === lang ? ' active' : ''}" data-ln="${code}" title="${t('lang')}">${label}</button>`).join('');
    el.querySelectorAll('.sb-lang__btn').forEach((b) => b.addEventListener('click', () => {
      const code = b.dataset.ln;
      if (code === lang) return;
      try { localStorage.setItem(KEY, code); } catch (e) { /* ignore */ }
      const u = new URL(location.href);
      u.searchParams.set('ln', code);
      location.href = u.toString();
    }));
  }
  return { lang, ui, LOCALE, t, apply, switcher };
})();
