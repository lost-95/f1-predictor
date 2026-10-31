// ---------- helpers ----------
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
// Static build (export_static.py): API responses are precomputed JSON files next to the page
const STATIC = !!window.F1_STATIC;
function apiPath(p) {
  if (!STATIC) return p;
  const u = new URL(p, 'http://x');
  const parts = u.pathname.split('/').filter(Boolean).slice(1);  // drop "api"
  if (parts[0] === 'next') return `api/next-${u.searchParams.get('scenario') || 'auto'}.json`;
  return `api/${parts.join('-')}.json`;
}
const api = (p, opt) => fetch(apiPath(p), opt).then(r => { if (!r.ok) throw new Error(r.status); return r.json(); });
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const store = {  // per-viewer preferences; works without storage too
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* storage blocked */ } },
};

// ---------- country: language + time zone ----------
function detectCountry() {
  const saved = store.get('f1-country');
  if (saved && COUNTRIES[saved]) return saved;
  let tz = '';
  try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone; } catch { /* old browser */ }
  const byTz = Object.keys(COUNTRIES).find(k => COUNTRIES[k].tz === tz);
  if (byTz) return byTz;
  return { it: 'it', es: 'es', en: 'uk' }[(navigator.language || 'it').slice(0, 2)] || 'it';
}
let country = detectCountry();
const C = () => COUNTRIES[country];
function t(key, vars = {}) {
  let s = I18N[C().lang][key] ?? I18N.it[key] ?? key;
  for (const [k, v] of Object.entries(vars)) s = s.split('{' + k + '}').join(v);
  return s;
}
const num = (v, d = 1) => v == null || isNaN(v) ? '–' : Number(v).toLocaleString(C().locale, { minimumFractionDigits: d, maximumFractionDigits: d });
const pct = (v, d = 1) => v == null ? '–' : num(v * 100, d) + '%';
const coins = v => num(v, 1);
const ord = n => C().lang === 'en' ? 'P' + n : n + (C().lang === 'es' ? 'º' : '°');
const fmtDate = d => new Date(d + 'T12:00:00Z').toLocaleDateString(C().locale, { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' });
const raceStart = r => r.time ? new Date(r.date + 'T' + (r.time.endsWith('Z') ? r.time : r.time + 'Z')) : null;
const fmtStart = dt => dt.toLocaleString(C().locale, { weekday: 'short', day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit', hourCycle: C().locale === 'en-US' ? 'h12' : 'h23', timeZone: C().tz });

function applyI18n() {
  document.documentElement.lang = C().lang;
  Chart.defaults.locale = C().locale;
  $$('[data-i18n]').forEach(el => el.textContent = t(el.dataset.i18n));
  $$('[data-i18n-html]').forEach(el => el.innerHTML = t(el.dataset.i18nHtml));
  $$('[data-i18n-title]').forEach(el => el.title = t(el.dataset.i18nTitle));
  $$('[data-i18n-aria]').forEach(el => el.setAttribute('aria-label', t(el.dataset.i18nAria)));
}
const countrySel = $('#country');
countrySel.innerHTML = Object.entries(COUNTRIES).map(([k, c]) => `<option value="${k}">${c.flag} ${esc(c.name)}</option>`).join('');
countrySel.value = country;
countrySel.onchange = () => {
  country = countrySel.value;
  store.set('f1-country', country);
  applyI18n();
  for (const k in loaded) loaded[k] = false;
  show(currentView);
};

// ---------- shared ----------
const TEAM_COLORS = {
  mercedes: '#27F4D2', ferrari: '#E8002D', red_bull: '#3671C6', mclaren: '#FF8000',
  aston_martin: '#229971', alpine: '#FF87BC', williams: '#64C4FF', rb: '#6692FF',
  haas: '#B6BABD', sauber: '#52E252', audi: '#C0C0C0', cadillac: '#D4AF37',
  alphatauri: '#5E8FAA', alfa: '#C92D4B', racing_point: '#F596C8', renault: '#FFF500',
  toro_rosso: '#469BFF', force_india: '#FF80C7', lotus_f1: '#FFB800', brawn: '#B8FD6E',
  toyota: '#CC1E1E', bmw_sauber: '#6CD3BF', honda: '#E6E6E6', bar: '#B0B0B0',
  jordan: '#F3D21F', jaguar: '#1F5A3B', minardi: '#333333', super_aguri: '#B3141B',
};
const FAMILY_COLORS = {
  brackley: '#27F4D2', ferrari: '#E8002D', red_bull: '#3671C6', mclaren: '#FF8000',
  enstone: '#FF87BC', silverstone: '#229971', williams: '#64C4FF', hinwil: '#52E252',
  faenza: '#6692FF', haas: '#B6BABD', toyota: '#CC1E1E', cadillac: '#D4AF37',
};
const teamColor = id => TEAM_COLORS[id] || '#777';
const weatherIcon = w => {
  if (!w || w.temp_c == null) return '<span class="muted">–</span>';
  if (w.wet) return `<span class="wet">🌧 ${num(w.temp_c)}°</span>`;
  const icon = w.cloud_cover > 70 ? '☁️' : w.cloud_cover > 30 ? '⛅' : '☀️';
  return `${icon} ${num(w.temp_c)}°`;
};
const STAT_FMT = {
  d_avg_pos_5: v => num(v), d_season_ppr: v => num(v), d_champ_rank: v => ord(v),
  d_circuit_avg: v => num(v), d_circuit_n: v => num(v, 0),
  d_wet_skill: v => (v > 0 ? '+' : '') + num(v) + ' ' + t('unit.pos'),
  d_qgap_5: v => num(v, 2) + '%', t_pts_5: v => num(v, 0), t_prev_rank: v => ord(v),
  t_win_rate_3y: v => num(v * 100, 0) + '%', t_titles: v => num(v, 0),
  d_elo: v => num(v, 0), t_elo: v => num(v, 0), d_rpace_6: v => num(v, 2) + '%',
};
const charts = {};
function chart(id, cfg) {
  charts[id]?.destroy();
  charts[id] = new Chart(document.getElementById(id), cfg);
}
Chart.defaults.color = '#8b91a1';
Chart.defaults.borderColor = '#2a2e3a';
Chart.defaults.font.family = 'Inter, system-ui, sans-serif';

// ---------- router ----------
const loaded = {};
let currentView = 'next';
function show(view) {
  if (!document.getElementById('view-' + view)) view = 'next';
  currentView = view;
  $('#modal').hidden = true;
  $$('.view').forEach(v => v.hidden = v.id !== 'view-' + view);
  $$('#nav a').forEach(a => a.classList.toggle('on', a.dataset.view === view));
  if (!loaded[view]) { loaded[view] = true; ({ next: loadNext, fanta: loadFanta, history: loadHistory, teams: loadTeams, model: loadModel })[view](); }
}
window.addEventListener('hashchange', () => show(location.hash.slice(1) || 'next'));

// ---------- PROSSIMA GARA ----------
let scenario = 'auto', countdownTimer;
async function loadNext() {
  $('#next-loading').hidden = false;
  $('#next-loading').textContent = t('next.loading');
  let d;
  try { d = await api('/api/next?scenario=' + scenario); }
  catch (e) { $('#next-loading').textContent = t('next.error', { e: e.message }); return; }
  if (!d.race) { $('#next-loading').textContent = t('season_over'); return; }
  $('#next-loading').hidden = true;
  $('#next-content').hidden = false;
  const r = d.race, w = d.weather || {};
  $('#next-round').textContent = t('round', { s: r.season, r: r.round });
  $('#next-name').textContent = r.raceName;
  $('#next-circuit').textContent = `${r.circuitName} — ${r.locality}, ${r.country}`;
  const start = raceStart(r);
  const local = w.race_start_local ? w.race_start_local.slice(11) : null;
  $('#next-date').textContent = start
    ? t('next.start', { time: fmtStart(start), place: C().name }) + (local ? ' · ' + t('next.local', { t: local }) : '')
    : fmtDate(r.date);
  $('#next-mode').textContent = t(d.mode === 'post_quali' ? 'mode.post' : 'mode.pre');
  clearInterval(countdownTimer);
  const target = start || new Date(r.date + 'T14:00:00');
  const tick = () => {
    const ms = target - new Date();
    if (ms <= 0) { $('#next-countdown').textContent = t('next.live'); return; }
    const h = Math.floor(ms / 3.6e6), m = Math.floor(ms % 3.6e6 / 6e4);
    $('#next-countdown').textContent = t('next.countdown', { d: Math.floor(h / 24), h: h % 24, m });
  };
  tick(); countdownTimer = setInterval(tick, 30000);

  const sim = scenario !== 'auto' ? `<b style="color:#ff8a80">${t('w.sim', { s: t(scenario === 'wet' ? 'w.sim_wet' : 'w.sim_dry') })}</b>` : t('w.used');
  $('#next-weather').innerHTML = w.temp_c == null
    ? `<div class="w-src">${t('w.na')}</div>`
    : `<div class="w-big">${weatherIcon(w)} ${w.wet ? `<span class="wet">${t('w.wet')}</span>` : `<span class="muted" style="font-size:15px;font-weight:500">${t('w.dry')}</span>`}</div>
       <div class="w-item">${t('w.rain')}<b>${num(w.rain_mm)} mm</b></div>
       <div class="w-item">${t('w.humidity')}<b>${num(w.humidity, 0)}%</b></div>
       <div class="w-item">${t('w.wind')}<b>${num(w.wind_kmh)} km/h</b></div>
       <div class="w-src">${t('w.source')}: ${esc(w.weather_source)} · ${sim}</div>`;

  const top = d.drivers.slice(0, 3);
  $('#podium').innerHTML = [1, 0, 2].map(i => {
    const x = top[i]; if (!x) return '<div></div>';
    return `<div class="pod p${i + 1}" style="--team:${teamColor(x.teamId)}">
      <div class="place">${t('podium.place', { n: i + 1 })}</div><div class="nm">${esc(x.name)}</div><div class="tm">${esc(x.team)}</div>
      <div class="pct">${pct(x.p_win)}<small>${t('podium.sub', { p: pct(x.p_podium, 0) })}</small></div></div>`;
  }).join('');

  const m = d.model.metrics['2016-now'], b = d.model.baseline_metrics['2016-now'];
  $('#next-note').textContent = t('pred.note', {
    w: pct(m.winner_accuracy, 0), p: pct(m.podium_overlap, 0), b: t(d.model.baseline === 'grid' ? 'base.grid' : 'base.standings'),
    bw: pct(b.winner_accuracy, 0), bp: pct(b.podium_overlap, 0),
  });

  const maxWin = Math.max(...d.drivers.map(x => x.p_win));
  const tb = $('#pred-table tbody');
  tb.innerHTML = d.drivers.map(x => {
    const stats = Object.entries(STAT_FMT).map(([k, f]) =>
      x.stats[k] == null ? '' : `<div>${t('stat.' + k)}: <b>${f(x.stats[k])}</b></div>`).join('');
    return `<tr class="row" style="--team:${teamColor(x.teamId)}">
      <td class="num">${x.pred_pos}</td>
      <td><div class="driver"><span class="tbar"></span><div><span class="code">${esc(x.code)}</span> ${esc(x.name)}<div class="sub">${esc(x.team)}</div></div></div></td>
      <td class="hide-sm">${esc(x.team)}</td>
      <td class="num">${x.grid ?? '–'}${x.q_gap != null ? `<div class="sub">+${num(x.q_gap, 2)}%</div>` : ''}</td>
      <td><div class="bar"><i style="width:${(x.p_win / maxWin * 100).toFixed(1)}%"></i><span>${pct(x.p_win)}</span></div></td>
      <td class="num">${pct(x.p_podium, 0)}</td>
      <td class="num hide-sm">${pct(x.p_points, 0)}</td>
      <td class="num hide-sm">${num(x.exp_pos)}</td></tr>
      <tr class="detail" hidden><td colspan="8"><div class="stat-grid">${stats}</div>
      ${x.pre_p_win != null ? `<div style="margin-top:8px">${t('pred.pre_was', { w: pct(x.pre_p_win), p: num(x.pre_exp_pos) })}</div>` : ''}
      <div class="sub" style="margin-top:6px">${t('pred.footnote', { f: esc(x.teamFamily) })}</div></td></tr>`;
  }).join('');
  $$('tr.row', tb).forEach(tr => tr.onclick = () => { tr.nextElementSibling.hidden = !tr.nextElementSibling.hidden; });
}
$('#scenario').onclick = e => {
  const b = e.target.closest('button'); if (!b) return;
  scenario = b.dataset.s;
  $$('#scenario button').forEach(x => x.classList.toggle('on', x === b));
  loadNext();
};

// ---------- FANTA F1 ----------
let fantaBase = null, fantaData = null, fantaMin = null;
let fantaSub = ['prices', 'drivers', 'teams'].includes(store.get('f1-fanta-sub')) ? store.get('f1-fanta-sub') : 'prices';
let picks = { race: null, drivers: [], teams: [] };

async function loadFanta() {
  if (!fantaBase) { $('#fanta-loading').hidden = false; $('#fanta-loading').textContent = t('fanta.loading'); }
  let d;
  try { d = await api('/api/fanta'); }
  catch (e) { $('#fanta-loading').hidden = false; $('#fanta-loading').textContent = t('fanta.error', { e: e.message }); return; }
  if (!d.race) { $('#fanta-loading').textContent = t('season_over'); return; }
  fantaBase = d;
  fantaData = reprice(d, fantaMin);
  loadPicks(`${d.race.season}-${d.race.round}`);
  $('#fanta-loading').hidden = true;
  $('#fanta-content').hidden = false;
  renderPrices();
  showFantaSub();
}
// Driver prices for a chosen minimum price (same rule as fanta.prices in the backend):
// top value = 30, lowest value = minimum price, everyone else linear in between, one decimal.
function reprice(base, min) {
  if (base.price_source === 'official') return { ...base };  // official prices are fixed
  const vals = base.drivers.map(x => x.value), lo = Math.min(...vals), hi = Math.max(...vals);
  const [a, b] = base.min_price_range;
  const pMin = min == null ? base.min_price_auto : Math.min(b, Math.max(a, Math.round(min * 10) / 10));
  const drivers = base.drivers.map(x => ({
    ...x, price: hi > lo ? Math.round((pMin + (base.top_price - pMin) * (x.value - lo) / (hi - lo)) * 10) / 10 : base.top_price,
  })).sort((x, y) => y.price - x.price || y.value - x.value);
  return { ...base, drivers, min_price: pMin };
}
function applyMinPrice() {
  fantaData = reprice(fantaBase, fantaMin);
  renderPrices();
  renderBuilder();
  renderBadges();
}

function renderPrices() {
  const d = fantaData, r = d.race;
  $('#fanta-race').textContent = t('fanta.race', { race: r.raceName, date: fmtDate(r.date), s: r.season, r: r.round });
  $('#fanta-min').value = d.min_price;
  $('#fanta-min-val').textContent = coins(d.min_price);
  $('#fanta-auto').disabled = fantaMin == null;
  const official = d.price_source === 'official';
  // official prices: the computed-price controls stay in the page, just hidden
  $('#fanta-min-card').hidden = official;
  $('#fanta-official-note').hidden = !official;
  $$('.model-pricing').forEach(el => el.hidden = official);
  const total = d.drivers.reduce((s, x) => s + x.price, 0);
  $('#fanta-summary').innerHTML = (fantaMin == null
    ? `<span class="chip">${t('fanta.min_auto', { v: coins(d.min_price) })}</span>`
    : `<span class="chip accent">${t('fanta.min_set', { v: coins(d.min_price) })}</span><span class="chip">${t('fanta.min_would', { v: coins(d.min_price_auto) })}</span>`) +
    `<span class="chip">${t('fanta.sum', { v: coins(total) })}</span>`;
  $('#fanta-table tbody').innerHTML = d.drivers.map((x, i) => `<tr style="--team:${teamColor(x.teamId)}">
    <td class="num">${i + 1}</td>
    <td><div class="driver"><span class="tbar"></span><div><span class="code">${esc(x.code)}</span> ${esc(x.name)}<div class="sub">${esc(x.team)}</div></div></div></td>
    <td class="hide-sm">${esc(x.team)}</td>
    <td><div class="bar price"><i style="width:${(x.price / d.top_price * 100).toFixed(1)}%"></i><span>${coins(x.price)}</span></div></td>
    <td class="num hide-sm">${coins(x.value_model)}<div class="sub">${t('fanta.qr', { q: coins(x.value_quali), r: coins(x.value_race) })}</div></td>
    <td class="num hide-sm">${x.form == null ? '–' : coins(x.form)}<div class="sub">${t('fanta.races_n', { n: x.form_n })}</div></td>
    <td class="num"><b>${coins(x.value)}</b></td></tr>`).join('');
  $('#fanta-teams-table tbody').innerHTML = d.teams.map((x, i) => `<tr style="--team:${teamColor(x.teamId)}">
    <td class="num">${i + 1}</td>
    <td><div class="driver"><span class="tbar"></span><b>${esc(x.name)}</b></div></td>
    <td class="hide-sm sub">${esc(x.drivers.join(' · '))}</td>
    <td><div class="bar price"><i style="width:${(x.price / d.team_price_range[1] * 100).toFixed(1)}%"></i><span>${coins(x.price)}</span></div></td>
    <td class="num">${coins(x.value)}</td></tr>`).join('');
  const c = d.calibration;
  if (c && c.form_weight) {
    const w = n => Math.round(c.form_weight * n / (n + c.form_k) * 100);
    const n = Math.max(...d.drivers.map(x => x.form_n));
    $('#fanta-blend').textContent = t('fanta.blend', { a: w(1), b: w(n), n, fit: c.fitted_on, check: c.checked_on.replace('today', { it: 'oggi', en: 'today', es: 'hoy' }[C().lang]) });
  }
  const pt = d.points_table;
  $('#fanta-points').innerHTML = `<thead><tr><th>${t('th.pos')}</th><th>${t('th.quali')}</th><th>${t('th.race')}</th></tr></thead><tbody>` +
    pt.race.map((v, i) => `<tr><td class="num">${i + 1}</td><td class="num">${num(pt.quali[i], 2)}</td><td class="num">${num(v, 2)}</td></tr>`).join('') + '</tbody>';
}

$('#fanta-min').oninput = e => { fantaMin = +e.target.value; applyMinPrice(); };
$('#fanta-auto').onclick = () => { fantaMin = null; applyMinPrice(); };
$('#fanta-menu').onclick = e => {
  const b = e.target.closest('button[data-sub]'); if (!b) return;
  fantaSub = b.dataset.sub;
  store.set('f1-fanta-sub', fantaSub);
  showFantaSub();
};
function showFantaSub() {
  $$('#fanta-menu button').forEach(b => b.classList.toggle('on', b.dataset.sub === fantaSub));
  $('#fanta-prices').hidden = fantaSub !== 'prices';
  $('#fanta-builder').hidden = fantaSub === 'prices';
  renderBuilder();
  renderBadges();
}

// --- team building ---
function loadPicks(raceKey) {
  let p = null;
  try { p = JSON.parse(store.get('f1-fanta-picks') || 'null'); } catch { p = null; }
  picks = p && p.race === raceKey ? p : { race: raceKey, drivers: [], teams: [] };
  const ids = { drivers: new Set(fantaData.drivers.map(x => x.driverId)), teams: new Set(fantaData.teams.map(x => x.teamId)) };
  for (const k of ['drivers', 'teams']) picks[k] = (picks[k] || []).filter(id => ids[k].has(id));
}
const savePicks = () => store.set('f1-fanta-picks', JSON.stringify(picks));
function builderItems(kind) {
  return kind === 'drivers'
    ? fantaData.drivers.map(x => ({ id: x.driverId, label: x.code || x.name, name: x.name, sub: x.team, teamId: x.teamId, price: x.price, value: x.value }))
    : fantaData.teams.map(x => ({ id: x.teamId, label: x.name, name: x.name, sub: x.drivers.join(' · '), teamId: x.teamId, price: x.price, value: x.value }));
}
const cents = v => Math.round(v * 10);  // prices have one decimal: compare in tenths
const KINDS = ['drivers', 'teams'];
// The whole squad: 6 drivers + 3 teams sharing one budget
function squad() {
  const r = fantaData.rules, S = { budget: r.budget, kinds: {}, spent: 0, value: 0 };
  for (const kind of KINDS) {
    const items = builderItems(kind), chosen = items.filter(i => picks[kind].includes(i.id));
    const spent = chosen.reduce((a, i) => a + i.price, 0);
    S.kinds[kind] = { items, chosen, spent, picks: r[kind].picks, need: r[kind].picks - chosen.length };
    S.spent += spent;
    S.value += chosen.reduce((a, i) => a + i.value, 0);
  }
  S.left = S.budget - S.spent;
  S.over = cents(S.spent) > cents(S.budget);
  S.valid = !S.over && KINDS.every(k => S.kinds[k].need === 0);
  return S;
}
// Cheapest possible cost of the picks still missing once `skipId` (of `kindAdding`) is added
function reserve(S, skipId, kindAdding) {
  let total = 0;
  for (const kind of KINDS) {
    const k = S.kinds[kind], need = k.need - (kind === kindAdding ? 1 : 0);
    if (need <= 0) continue;
    const free = k.items.filter(i => !picks[kind].includes(i.id) && i.id !== skipId).map(i => i.price).sort((a, b) => a - b);
    total += free.slice(0, need).reduce((a, p) => a + p, 0);
  }
  return total;
}
function renderBadges() {
  const S = squad();
  for (const kind of KINDS) {
    const k = S.kinds[kind], el = $('#badge-' + kind);
    el.textContent = `${k.chosen.length}/${k.picks}`;
    el.classList.toggle('ok', k.need === 0 && !S.over);
  }
}
function needText(S) {
  const d = S.kinds.drivers.need, tm = S.kinds.teams.need, parts = [];
  if (d > 0) parts.push(`${d} ${t(d === 1 ? 'b.n_driver1' : 'b.n_drivers')}`);
  if (tm > 0) parts.push(`${tm} ${t(tm === 1 ? 'b.n_team1' : 'b.n_teams')}`);
  return t('b.need', { parts: parts.join(t('b.and')) });
}
function renderBuilder() {
  const kind = fantaSub;
  if (kind === 'prices' || !fantaData) return;
  const S = squad(), K = S.kinds[kind];
  $('#b-title').textContent = t(kind === 'drivers' ? 'b.title_drivers' : 'b.title_teams');
  $('#b-rules').innerHTML = t('b.rules', { d: S.kinds.drivers.picks, t: S.kinds.teams.picks, b: num(S.budget, 0) });
  $('#b-th-name').textContent = t(kind === 'drivers' ? 'th.driver' : 'th.team');
  for (const k of KINDS) $('#b-count-' + k).textContent = `${S.kinds[k].chosen.length} / ${S.kinds[k].picks}`;
  $('#b-spent').textContent = `${coins(S.spent)} / ${num(S.budget, 0)}`;
  $('#b-split').textContent = t('b.split', { d: coins(S.kinds.drivers.spent), t: coins(S.kinds.teams.spent) });
  $('#b-left').textContent = coins(S.left);
  $('#b-left').classList.toggle('bad', S.over);
  $('#b-exp').textContent = num(S.value, 0);
  const target = fantaData.rules.target;
  $('#b-target').textContent = target ? (S.value >= target ? t('b.target_reached', { n: num(target, 0) })
    : t('b.target_left', { v: num(target - S.value, 0), n: num(target, 0) })) : '';
  $('#b-target').className = 'sub' + (target && S.value >= target ? ' good' : '');
  $('#b-bar').style.width = Math.min(100, S.spent / S.budget * 100).toFixed(1) + '%';
  $('#b-bar').classList.toggle('over', S.over);
  const st = $('#b-status');
  if (S.over) { st.className = 'b-status bad'; st.textContent = t('b.over', { v: coins(-S.left) }); }
  else if (!S.valid) { st.className = 'b-status'; st.textContent = needText(S); }
  else { st.className = 'b-status good'; st.textContent = t('b.ok'); }
  const all = KINDS.flatMap(k => S.kinds[k].chosen.map(i => ({ ...i, kind: k })));
  $('#b-picks').innerHTML = all.length
    ? all.map(i => `<span class="pick${i.kind === 'teams' ? ' team' : ''}" style="--team:${teamColor(i.teamId)}"><i></i>${esc(i.label)} <b>${coins(i.price)}</b>
        <button data-remove="${esc(i.id)}" data-kind="${i.kind}" aria-label="${t('b.remove')}">×</button></span>`).join('')
    : `<span class="muted small">${t('b.empty')}</span>`;

  $('#b-table tbody').innerHTML = K.items.map(i => {
    const on = picks[kind].includes(i.id);
    let why = '';
    if (!on && K.need <= 0) why = t('b.full', { n: K.picks });
    else if (!on && cents(i.price) > cents(S.left)) why = t('b.no_budget');
    else if (!on && cents(i.price + reserve(S, i.id, kind)) > cents(S.left)) why = t('b.no_room');
    return `<tr class="${on ? 'picked' : ''}" style="--team:${teamColor(i.teamId)}">
      <td><div class="driver"><span class="tbar"></span><div>${esc(i.name)}<div class="sub">${esc(i.sub)}</div></div></div></td>
      <td class="num"><b>${coins(i.price)}</b></td>
      <td class="num">${coins(i.value)}</td>
      <td class="right">${on
        ? `<button class="btn ghost sm" data-remove="${esc(i.id)}" data-kind="${kind}">${t('b.remove')}</button>`
        : `<button class="btn sm" data-add="${esc(i.id)}" ${why ? `disabled title="${esc(why)}"` : ''}>${t('b.add')}</button>`}</td></tr>`;
  }).join('');
}
function bestLineup(items, n, budget) {
  // depth-first search with an optimistic bound; items sorted by value so the bound is tight
  const arr = [...items].sort((a, b) => b.value - a.value);
  const cap = cents(budget), cur = [];
  let best = null, bestV = -1;
  (function rec(start, cost, val) {
    if (cur.length === n) { if (val > bestV) { bestV = val; best = [...cur]; } return; }
    let ub = val;
    for (let i = start, k = cur.length; i < arr.length && k < n; i++, k++) ub += arr[i].value;
    if (ub <= bestV) return;
    for (let i = start; i <= arr.length - (n - cur.length); i++) {
      const c = cost + cents(arr[i].price);
      if (c > cap) continue;
      cur.push(arr[i].id); rec(i + 1, c, val + arr[i].value); cur.pop();
    }
  })(0, 0, 0);
  return best;
}
// Best full squad: for every choice of teams, the best drivers with the budget that is left
function bestSquad() {
  const S = squad(), D = S.kinds.drivers, T = S.kinds.teams;
  const combos = (arr, k, start = 0, cur = [], out = []) => {
    if (cur.length === k) { out.push([...cur]); return out; }
    for (let i = start; i < arr.length; i++) { cur.push(arr[i]); combos(arr, k, i + 1, cur, out); cur.pop(); }
    return out;
  };
  const value = new Map(D.items.map(i => [i.id, i.value]));
  const memo = new Map();
  let best = null, bestV = -1;
  for (const tc of combos(T.items, T.picks)) {
    const rest = cents(S.budget) - tc.reduce((a, i) => a + cents(i.price), 0);
    if (rest < 0) continue;
    if (!memo.has(rest)) {
      const ids = bestLineup(D.items, D.picks, rest / 10);
      memo.set(rest, ids && { ids, v: ids.reduce((a, id) => a + value.get(id), 0) });
    }
    const dl = memo.get(rest);
    const v = dl ? dl.v + tc.reduce((a, i) => a + i.value, 0) : -1;
    if (dl && v > bestV) { bestV = v; best = { drivers: dl.ids, teams: tc.map(i => i.id) }; }
  }
  return best;
}
$('#fanta-builder').onclick = e => {
  const kind = fantaSub;
  const add = e.target.closest('[data-add]'), rem = e.target.closest('[data-remove]');
  if (add && !add.disabled) {
    if (squad().kinds[kind].need > 0) picks[kind].push(add.dataset.add);
  } else if (rem) {
    const k = rem.dataset.kind || kind;
    picks[k] = picks[k].filter(id => id !== rem.dataset.remove);
  } else if (e.target.id === 'b-best') {
    const best = bestSquad();
    if (best) { picks.drivers = best.drivers; picks.teams = best.teams; }
  } else if (e.target.id === 'b-clear') {
    picks.drivers = []; picks.teams = [];
  } else return;
  savePicks();
  renderBuilder();
  renderBadges();
};

// ---------- STORICO ----------
async function loadHistory() {
  const seasons = await api('/api/seasons');
  const sel = $('#season-select');
  const prev = +sel.value;
  sel.innerHTML = seasons.map(s => `<option value="${s.season}">${s.season}</option>`).join('');
  const pick = seasons.find(s => s.season === prev) || seasons[0];
  sel.value = pick.season;
  sel.onchange = () => loadSeason(+sel.value, seasons.find(s => s.season === +sel.value));
  loadSeason(pick.season, pick);
}
async function loadSeason(season, info) {
  const d = await api('/api/season/' + season);
  const bt = d.backtest, ongoing = info.completed < info.races;
  $('#champ').innerHTML = `
    <div><span class="muted small">${t(ongoing ? 'champ.leader_d' : 'champ.champ_d')}</span><b>${esc(info.driver ?? '–')}</b><span class="sub">${esc(info.driverTeam ?? '')}</span></div>
    <div><span class="muted small">${t(ongoing ? 'champ.leader_c' : 'champ.champ_c')}</span><b>${esc(info.constructor ?? '–')}</b></div>
    <div><span class="muted small">${t('champ.done')}</span><b>${info.completed} / ${info.races}</b></div>
    ${bt ? `<div><span class="muted small">${t('champ.model')}</span><b>${pct(bt.post_quali.winner_accuracy, 0)} <span class="sub">${t('champ.post')}</span> · ${pct(bt.pre_quali.winner_accuracy, 0)} <span class="sub">${t('champ.pre')}</span></b></div>` : ''}`;
  const tb = $('#races-table tbody');
  tb.innerHTML = d.races.map(r => `<tr data-round="${r.round}" ${r.done ? '' : 'style="opacity:.5"'}>
    <td class="num">${r.round}</td><td>${esc(r.raceName)}<div class="sub">${esc(r.circuitName)}</div></td>
    <td class="hide-sm">${fmtDate(r.date)}</td><td>${weatherIcon(r.weather)}</td>
    <td>${r.done ? esc(r.winner) + `<div class="sub">${esc(r.winnerTeam)}</div>` : `<span class="muted">${t('hist.todo')}</span>`}</td>
    <td class="hide-sm">${esc(r.pole ?? '')}</td>
    <td>${r.post_quali_pick ? `<span class="${r.post_quali_hit ? 'hit' : 'miss'}">${r.post_quali_hit ? '✓' : '✗'}</span> ${esc(r.post_quali_pick)}` : '<span class="muted">–</span>'}</td></tr>`).join('');
  $$('tr', tb).forEach(tr => tr.onclick = () => {
    const r = d.races.find(x => x.round === +tr.dataset.round);
    if (r.done) openRace(season, r.round);
  });
  $('#drivers-table tbody').innerHTML = d.drivers.map(s => `<tr><td class="num">${s.position || '–'}</td><td>${esc(s.name)}<div class="sub">${esc(s.team)}</div></td><td class="num">${num(s.points, s.points % 1 ? 1 : 0)}</td><td class="num sub">${t('wins_short', { n: s.wins })}</td></tr>`).join('');
  $('#constructors-table tbody').innerHTML = d.constructors.map(s => `<tr style="--team:${teamColor(s.id)}"><td class="num">${s.position || '–'}</td><td><div class="driver"><span class="tbar"></span>${esc(s.name)}</div></td><td class="num">${num(s.points, s.points % 1 ? 1 : 0)}</td><td class="num sub">${t('wins_short', { n: s.wins })}</td></tr>`).join('');
}
async function openRace(season, round) {
  const d = await api(`/api/race/${season}/${round}`);
  const w = d.weather || {}, r = d.race;
  $('#modal-body').innerHTML = `
    <div class="eyebrow">${t('round', { s: season, r: round })}</div><h1>${esc(r.raceName)}</h1>
    <div class="muted">${esc(r.circuitName)} — ${fmtDate(r.date)}</div>
    <div class="chips">${w.temp_c != null ? `<span class="chip">${weatherIcon(w)}</span><span class="chip">${t('m.rain', { v: num(w.rain_mm) })}</span><span class="chip">${t('m.prerain', { v: num(w.pre_rain_mm) })}</span><span class="chip">${t('m.hum', { v: num(w.humidity, 0) })}</span><span class="chip">${t('m.wind', { v: num(w.wind_kmh) })}</span>` : `<span class="chip">${t('m.no_weather')}</span>`}</div>
    <p class="sub">${t('m.weather_src', { src: esc(w.weather_source ?? ''), win: w.race_start_local ? t('m.window', { t: w.race_start_local.slice(11) }) : '' })} ${d.hasBacktest ? t('m.bt_note') : ''}</p>
    <div class="table-wrap"><table class="grid-table"><thead><tr><th>${t('th.pos')}</th><th>${t('th.driver')}</th><th>${t('th.grid')}</th><th class="hide-sm">${t('th.status')}</th><th>${t('th.points')}</th>
      ${d.hasBacktest ? `<th>${t('th.pred_pre')}</th><th>${t('th.pred_post')}</th><th class="hide-sm">${t('th.pwin')}</th>` : ''}</tr></thead><tbody>
    ${d.results.map(x => `<tr style="--team:${teamColor(x.constructorId)}"><td class="num">${esc(x.positionText)}</td>
      <td><div class="driver"><span class="tbar"></span><div>${esc(x.driverName)}<div class="sub">${esc(x.constructorName)}</div></div></div></td>
      <td class="num">${x.grid || 'PL'}</td><td class="hide-sm sub">${esc(x.status)}</td><td class="num">${num(x.points, x.points % 1 ? 1 : 0)}</td>
      ${d.hasBacktest ? `<td class="num">${x.pre_quali_rank ?? '–'}</td><td class="num">${x.post_quali_rank ?? '–'}</td><td class="num hide-sm">${pct(x.post_quali_pwin)}</td>` : ''}</tr>`).join('')}
    </tbody></table></div>`;
  $('#modal').hidden = false;
}
$('#modal-close').onclick = () => $('#modal').hidden = true;
$('#modal').onclick = e => { if (e.target.id === 'modal') $('#modal').hidden = true; };
document.addEventListener('keydown', e => { if (e.key === 'Escape') $('#modal').hidden = true; });

// ---------- TEAM ----------
let teamData, teamMetric = 'wins';
async function loadTeams() {
  teamData = await api('/api/teams');
  $('#teams-table tbody').innerHTML = teamData.teams.filter(x => x.podiums > 0 || x.seasons >= 3).map(x => `
    <tr style="--team:${FAMILY_COLORS[x.family] || '#777'}"><td><div class="driver"><span class="tbar"></span><b>${esc(x.name)}</b></div></td>
    <td class="hide-sm sub">${esc(x.lineage)}</td>
    <td class="num">${x.titles.length}${x.titles.length ? `<div class="sub">${x.titles.join(', ')}</div>` : ''}</td>
    <td class="num">${x.wins}</td><td class="num">${x.podiums}</td><td class="num hide-sm">${x.first}–${x.last}</td></tr>`).join('');
  drawTeamChart();
}
function drawTeamChart() {
  const seasons = [...new Set(teamData.seasons.map(s => s.season))].sort();
  const fams = teamData.teams.filter(x => x.podiums >= 5).map(x => x.family);
  const label = f => teamData.teams.find(x => x.family === f).name;
  $('#team-chart-title').textContent = t({ wins: 'teams.wins_title', podiums: 'teams.podiums_title', share: 'teams.share_title' }[teamMetric]);
  chart('team-chart', {
    type: 'line',
    data: {
      labels: seasons, datasets: fams.map(f => ({
        label: label(f), borderColor: FAMILY_COLORS[f] || '#777', backgroundColor: FAMILY_COLORS[f] || '#777',
        borderWidth: 2, pointRadius: 2, tension: .25,
        data: seasons.map(s => { const r = teamData.seasons.find(x => x.season === s && x.family === f); return r ? (teamMetric === 'share' ? +(r.share * 100).toFixed(1) : r[teamMetric]) : null; }),
      })),
    },
    options: { maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
      plugins: { legend: { position: 'bottom', labels: { boxWidth: 12 } } }, scales: { y: { beginAtZero: true } } },
  });
}
$('#team-metric').onclick = e => {
  const b = e.target.closest('button'); if (!b) return;
  teamMetric = b.dataset.m; $$('#team-metric button').forEach(x => x.classList.toggle('on', x === b)); drawTeamChart();
};

// ---------- MODELLO ----------
async function loadModel() {
  const rep = await api('/api/model');
  const M = rep.models, rows = [
    ['met.winner', 'winner_accuracy', true], ['met.podium', 'podium_overlap', true],
    ['met.spearman', 'spearman', false], ['met.mae', 'position_mae', false],
  ];
  const f = (v, isPct) => v == null ? '–' : isPct ? pct(v, 0) : num(v, 2);
  const trained = new Date(rep.trained_at).toLocaleString(C().locale, { dateStyle: 'medium', timeStyle: 'short' });
  $('#metrics-table').innerHTML = `<thead><tr><th></th><th>${t('met.pre')}</th><th class="sub">${t('met.ref_st')}</th><th>${t('met.post')}</th><th class="sub">${t('met.ref_grid')}</th></tr></thead><tbody>` +
    rows.map(([lab, k, p]) => `<tr><td>${t(lab)}</td>
      <td class="num">${f(M.pre_quali.metrics['2016-now'][k], p)}</td><td class="num sub">${f(M.pre_quali.baseline_metrics['2016-now'][k], p)}</td>
      <td class="num">${f(M.post_quali.metrics['2016-now'][k], p)}</td><td class="num sub">${f(M.post_quali.baseline_metrics['2016-now'][k], p)}</td></tr>`).join('') +
    `</tbody><caption class="sub" style="caption-side:bottom;text-align:left;padding-top:8px">${t('met.caption', { n: M.post_quali.metrics['2016-now'].races, d: trained })}</caption>`;

  const seasons = Object.keys(rep.by_season);
  chart('season-chart', {
    type: 'bar',
    data: { labels: seasons, datasets: [
      { label: t('met.post'), data: seasons.map(s => rep.by_season[s].post_quali.winner_accuracy * 100), backgroundColor: '#e10600' },
      { label: t('met.pre'), data: seasons.map(s => rep.by_season[s].pre_quali.winner_accuracy * 100), backgroundColor: '#8b91a1' },
      { label: t('chart.pole'), type: 'line', data: seasons.map(s => rep.by_season[s].grid_baseline.winner_accuracy * 100), borderColor: '#27F4D2', backgroundColor: '#27F4D2', pointRadius: 2, borderWidth: 2 },
    ] },
    options: { maintainAspectRatio: false, plugins: { legend: { position: 'bottom' }, tooltip: { callbacks: { label: c => `${c.dataset.label}: ${num(c.raw, 0)}%` } } },
      scales: { y: { beginAtZero: true, max: 100, ticks: { callback: v => v + '%' } } } },
  });
  for (const [id, name] of [['imp-pre', 'pre_quali'], ['imp-post', 'post_quali']]) {
    const imp = Object.entries(M[name].importance).slice(0, 12);
    chart(id, {
      type: 'bar',
      data: { labels: imp.map(([k]) => t('feat.' + k)), datasets: [{ data: imp.map(([, v]) => +(v * 100).toFixed(1)), backgroundColor: '#e10600' }] },
      options: { indexAxis: 'y', maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => t('chart.imp_tip', { v: num(c.raw) }) } } } },
    });
  }
  pollStatus();
}
async function pollStatus() {
  const s = await api('/api/status');
  const f = v => v ? new Date(v).toLocaleString(C().locale, { dateStyle: 'medium', timeStyle: 'short' }) : '–';
  if (STATIC) {  // updated by a scheduled job: no refresh button
    $('#refresh-btn').hidden = true;
    $('#refresh-status').textContent = t('status.auto', { d: f(s.last_update?.f1), g: f(s.generated_at) });
    return;
  }
  const r = s.refresh;
  $('#refresh-btn').disabled = r.running;
  $('#refresh-status').textContent = r.running ? t('status.running', { s: r.step }) : r.error ? t('status.error', { e: r.error })
    : t('status.updated', { d: f(s.last_update?.f1) }) + (r.finished_at ? t('status.last', { d: r.finished_at }) : '');
  if (r.running) setTimeout(pollStatus, 3000);
  else if (r.finished_at) { loaded.next = loaded.fanta = loaded.history = loaded.teams = false; fantaBase = null; }
}
$('#refresh-btn').onclick = async () => {
  await api('/api/refresh', { method: 'POST' });
  setTimeout(pollStatus, 500);
};

applyI18n();
show(location.hash.slice(1) || 'next');
