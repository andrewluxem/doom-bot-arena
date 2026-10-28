/* Ports of the design/dba.js renderers. Same markup and classes (styled by design/dba.css),
   but every function takes live state for the current tic instead of the frozen snapshot. */
import { BEHAVIORS, fmtClock, keyLabel, weaponLabel } from './bundle.js';

export const col = (id) => `var(--bot-${id})`;
const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
export const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ESC[c]);
const pct = (v) => Math.max(0, Math.min(100, v));
export const behLabel = (b) => b.replace(/_/g, ' ');
const matchClock = (ctx, tic) => fmtClock((tic - ctx.f0) / ctx.rate);

/* One bot's state at a tic: the row plus what the events say about it. */
export function botState(B, slot, tic, score, rank) {
  const bot = B.bot(slot), t = B.tel[slot], sc = score[slot];
  const alive = !!t.alive[tic];
  const death = alive ? null : B.deathAt(slot, tic);
  const st = {
    slot, id: bot.id, code: bot.code, name: bot.name, archetype: bot.archetype,
    alive, dead: !alive, beh: alive ? BEHAVIORS[t.behavior[tic]] : 'DEAD',
    health: t.health[tic], armor: t.armor[tic], weapon: t.weapon[tic],
    x: t.x[tic], y: t.y[tic], angle: t.angle[tic],
    frags: sc.frags, deaths: sc.deaths, kills: sc.kills, telefrags: sc.telefrags, suicides: sc.suicides, env: sc.env,
    rank, death, remain: 0, progress: 0,
  };
  st.hot = alive && st.beh === 'IN_COMBAT';
  st.crit = alive && st.health <= 25;
  if (death) {
    st.remain = Math.max(0, (death.respawnTic - tic) / B.rate);
    st.progress = Math.min(1, (tic - death.tic) / B.respawnAfter);
  }
  if (B.recorded.target) st.target = t.target[tic];
  if (B.recorded.sector) st.sector = t.sector[tic];
  return st;
}

function whoDied(d, ctx) {
  const at = matchClock(ctx, d.tic);
  if (d.type === 'suicide') return `SELF · ${keyLabel(d.key)} · ${at}`;
  if (d.type === 'environmental_death') return `${keyLabel(d.key)} · ${at}`;
  const k = ctx.B.bot(d.killer);
  return `FRAGGED BY ${k.id} ${esc(k.code)} · ${at}`;
}

/* ---------- tile (A): nameplate, footer and respawn layer over the live POV video ---------- */
export function tileClass(b, size, extra) {
  return 'tile vt tile--' + size + (b.hot ? ' is-hot' : '') + (b.crit ? ' is-crit' : '') + (b.dead ? ' is-dead' : '') + (extra ? ' ' + extra : '');
}
export function tileHead(b) {
  return `<span class="sw"></span><span class="t-id">${b.id}</span><span class="t-nm">${esc(b.name)}</span>`
    + `<span class="t-st">${b.dead ? 'DEAD' : behLabel(b.beh)}</span><span class="t-fr">${b.frags}<em>F</em></span>`;
}
export function tileFoot(b) {
  if (b.dead) return '<span class="t-hpv">HP 0</span><span class="hp"></span><span class="t-wp">RESPAWNING</span>';
  return `<span class="t-hpv">HP ${b.health}</span><span class="hp"><i style="width:${pct(b.health)}%"></i></span>`
    + (b.crit ? '<span class="crit">CRIT</span>' : '') + `<span class="t-wp">${weaponLabel(b.weapon, true)}</span>`;
}
export function tileResp(b, ctx) {
  if (!b.dead) return '';
  const by = b.death ? whoDied(b.death, ctx) : 'NO DEATH EVENT';
  return `<div class="resp"><span class="resp-k">NO SIGNAL · RESPAWN IN</span><span class="resp-v">${b.remain.toFixed(1)}<small>s</small></span>`
    + `<span class="resp-bar"><i style="width:${(b.progress * 100).toFixed(1)}%"></i></span><span class="resp-by">${by}</span></div>`;
}

/* ---------- scoreboard ---------- */
function scoreTitle(b, ctx) {
  const e = ctx.B.engine[b.slot];
  return `${b.id} ${b.name}: ${b.kills} kills, ${b.telefrags} telefrags, ${b.suicides} suicides, ${b.env} environmental deaths at ${matchClock(ctx, ctx.tic)}. `
    + `Engine counters at the last tic, pre-roll included: FRAGCOUNT ${e.frags}, DEATHCOUNT ${e.deaths}.`;
}
export function scoreStrip(states, order, ctx) {
  return order.map((slot, i) => {
    const b = states[slot - 1];
    return `<li class="sb-cell${b.hot ? ' is-hot' : ''}${b.dead ? ' is-dead' : ''}" style="--c:${col(b.id)}" data-slot="${slot}" title="${esc(scoreTitle(b, ctx))}">`
      + `<span class="sb-rk">${i + 1}</span><span class="sw"></span><span class="sb-id">${b.id}</span><span class="sb-nm">${esc(b.name)}</span>`
      + `<span class="sb-f">${b.frags}<em>F</em></span><span class="sb-d">${b.deaths}<em>D</em></span></li>`;
  }).join('');
}
export function scoreTable(states, order, ctx) {
  let h = '<table class="sb-table"><thead><tr><th class="c-rk">#</th><th class="c-idh">ID</th><th class="c-nmh">Name</th><th class="c-n">F</th><th class="c-n">D</th></tr></thead><tbody>';
  order.forEach((slot, i) => {
    const b = states[slot - 1];
    h += `<tr class="${b.hot ? 'is-hot' : ''}${b.dead ? ' is-dead' : ''}" style="--c:${col(b.id)}" data-slot="${slot}" title="${esc(scoreTitle(b, ctx))}">`
      + `<td class="c-rk">${i + 1}</td><td class="c-idc"><span class="sw"></span>${b.id}</td><td class="c-nmc">${esc(b.name)}<span class="sb-state">${b.dead ? 'DEAD' : behLabel(b.beh)}</span></td>`
      + `<td class="c-n c-f">${b.frags}</td><td class="c-n">${b.deaths}</td></tr>`;
  });
  return h + '</tbody></table>';
}
export function leaders(states, order) {
  return order.slice(0, 3).map((slot, i) => {
    const b = states[slot - 1];
    return `<span style="--c:${col(b.id)}">${i + 1} <span class="sw"></span>${esc(b.code)} ${b.frags}</span>`;
  }).join('');
}

/* ---------- kill feed ---------- */
function who(slot, ctx, cls) {
  const b = ctx.B.bot(slot);
  return `<span class="${cls}" style="--c:${col(b.id)}"><span class="sw"></span>${b.id} ${esc(b.code)}</span>`;
}
export function feedItem(d, ctx) {
  let k, v;
  if (d.type === 'suicide') { k = who(d.victim, ctx, 'kf-k'); v = '<span class="kf-v kf-self">SELF</span>'; }
  else if (d.type === 'environmental_death') { k = '<span class="kf-k kf-self">ENV</span>'; v = who(d.victim, ctx, 'kf-v'); }
  else { k = who(d.killer, ctx, 'kf-k'); v = who(d.victim, ctx, 'kf-v'); }
  return `<li data-tic="${d.tic}"><span class="kf-t">${matchClock(ctx, d.tic)}</span>${k}<span class="kf-x">✕</span>${v}<span class="kf-w">${keyLabel(d.key)}</span></li>`;
}
export function killfeed(list, n, ctx) {
  if (!list.length) return '<ol class="kf"><li class="kf-none">NO FRAGS YET</li></ol>';
  return '<ol class="kf">' + list.slice(-n).reverse().map((d) => feedItem(d, ctx)).join('') + '</ol>';
}

/* ---------- telemetry ---------- */
export function telemetry(b, ctx, o = {}) {
  const e = ctx.B.engine[b.slot];
  const btn = `<button class="btn-ret${o.compact ? ' btn-ret--sm' : ''}" type="button" data-act="return">← Return to Multiview <kbd>ESC</kbd></button>`;
  let h = `<header class="tele-hd"><span class="sw"></span><span class="tele-id">${b.id}</span><span class="tele-nm">${esc(b.name)}</span>`
    + `<span class="tele-role">${esc(b.archetype)}</span>` + (o.compact ? btn : `<span class="tele-rank">RANK ${b.rank}/8</span>`) + '</header>';
  h += `<div class="tele-beh"><span class="tele-k">BEHAVIOR</span><span class="tele-chip">${b.dead ? 'DEAD · RESPAWN IN ' + b.remain.toFixed(1) + 's' : behLabel(b.beh)}</span></div>`;
  h += '<dl class="tele-grid">';
  h += `<div class="tg tg-hp"><dt>HP</dt><dd>${b.dead ? 0 : b.health}<small>/100</small>${b.crit ? ' <span class="crit">CRIT</span>' : ''}</dd><span class="hp"><i style="width:${b.dead ? 0 : pct(b.health)}%"></i></span></div>`;
  h += `<div class="tg"><dt>ARMOR</dt><dd>${b.armor}<small>/100</small></dd><span class="hp hp--ar"><i style="width:${pct(b.armor)}%"></i></span></div>`;
  h += `<div class="tg"><dt>WEAPON · SLOT ${b.weapon}</dt><dd>${weaponLabel(b.weapon)}</dd></div>`;
  h += `<div class="tg"><dt>FRAGS / DEATHS</dt><dd>${b.frags} <small>/</small> ${b.deaths}</dd></div>`;
  h += `<div class="tg" title="Engine FRAGCOUNT and DEATHCOUNT at the last tic. They include the pre-roll, so they can differ from the displayed columns."><dt>ENGINE AT END</dt><dd>${e.frags} <small>/</small> ${e.deaths}</dd></div>`;
  h += `<div class="tg"><dt>KILLS · TELE · SELF</dt><dd>${b.kills} <small>·</small> ${b.telefrags} <small>·</small> ${b.suicides}</dd></div>`;
  if ('target' in b) h += `<div class="tg"><dt>TARGET</dt><dd>${b.target == null ? '—' : esc(b.target)}</dd></div>`;
  if ('sector' in b) h += `<div class="tg"><dt>SECTOR</dt><dd>${b.sector == null ? '—' : esc(b.sector)}</dd></div>`;
  h += `<div class="tg tg-wide"><dt>POSITION · FACING</dt><dd>X ${Math.round(b.x)} <small>·</small> Y ${Math.round(b.y)} <small>·</small> ${Math.round(b.angle)}°</dd></div>`;
  h += '</dl>';
  if (!o.compact) {
    const mine = ctx.shownNow.filter((d) => d.killer === b.slot && d.type !== 'suicide').slice(-4).reverse();
    h += `<div class="tele-log"><span class="tele-k">RECENT FRAGS · ${esc(b.code)}</span><ol class="kf">`
      + (mine.length ? mine.map((d) => feedItem(d, ctx)).join('') : '<li class="kf-none">NONE YET</li>') + '</ol></div>';
    h += btn;
  }
  return h;
}

/* ---------- clock ---------- */
export function clock(ctx, playing) {
  return `<span class="live"><i></i>${playing ? 'REPLAY' : 'PAUSED'}</span><span class="clk-t">${matchClock(ctx, ctx.tic)}</span>`
    + `<span class="clk-of">/ ${fmtClock(ctx.B.n / ctx.rate)}</span>`;
}
