/* Milestone 3: the match list and season table from index.json, and the pipeline footer from status.json.
   Index entries carry each bundle's displayed standings (tools/export_sample.py standings), so neither view
   loads a bundle. Times are UTC, like bundle ids. */
import { fmtClock } from './bundle.js';
import { col, esc } from './render.js';

const STALE_H = 10; // three runs a day, eight hours apart: over ten hours without a success means one was missed

const pad = (n) => String(n).padStart(2, '0');
const utcDay = (d) => `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
const utcTime = (d) => `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
const botId = (slot) => pad(slot);

function who(s, cls = '') {
  return `<span class="ml-bot ${cls}" style="--c:${col(botId(s.slot))}"><span class="sw"></span>${esc(s.code)} <b>${s.frags}</b></span>`;
}

/* Newest first, grouped by UTC date: time, map, duration, winner, then second and third. */
export function matchList(index, currentId) {
  const groups = new Map();
  for (const e of index.bundles) {
    const day = utcDay(new Date(e.date));
    if (!groups.has(day)) groups.set(day, []);
    groups.get(day).push(e);
  }
  let h = '';
  for (const [day, list] of groups) {
    h += `<li class="ml-day"><span class="kick">${day}</span><ol>`;
    for (const e of list) {
      const st = e.standings || [], tie = st.length > 1 && st[0].frags === st[1].frags;
      const top = st.length
        ? `<span class="ml-win">${tie ? 'TIE' : 'WIN'}</span>${who(st[0], 'ml-first')}${st.slice(1, 3).map((s) => who(s)).join('')}`
        : '<span class="ml-win">NO STANDINGS</span>';
      const cur = e.id === currentId;
      // The open match's row just closes the sheet; any other loads that bundle through the URL hash.
      h += `<li><a class="ml-row${cur ? ' is-cur' : ''}" href="#b=${encodeURIComponent(e.id)}"${cur ? ' aria-current="true" data-act="matches"' : ''}>`
        + `<span class="ml-t">${utcTime(new Date(e.date))}</span><span class="ml-map">${esc(e.map)}</span>`
        + `<span class="ml-dur">${fmtClock(e.duration_tics / (e.ticrate || 35))}</span><span class="ml-top">${top}</span></a></li>`;
    }
    h += '</ol></li>';
  }
  return `<ol class="ml-list">${h}</ol>`;
}

/* Displayed frags and deaths per bot summed over every indexed match, ranked by frags (then K/D). */
export function seasonRows(index) {
  const bots = new Map();
  for (const e of index.bundles) {
    for (const s of e.standings || []) {
      const b = bots.get(s.code) || { code: s.code, name: s.name, slot: s.slot, frags: 0, deaths: 0, played: 0 };
      b.frags += s.frags;
      b.deaths += s.deaths;
      b.played += 1;
      bots.set(s.code, b);
    }
  }
  const kd = (b) => (b.deaths ? b.frags / b.deaths : b.frags);
  return [...bots.values()].sort((a, b) => b.frags - a.frags || kd(b) - kd(a) || a.slot - b.slot);
}

export function seasonTable(index) {
  const rows = seasonRows(index), n = index.bundles.filter((e) => e.standings).length;
  let h = `<table class="sb-table ss-table"><caption class="kick">SEASON · ${n} MATCH${n === 1 ? '' : 'ES'}</caption>`
    + '<thead><tr><th class="c-rk">#</th><th class="c-idh">ID</th><th class="c-nmh">Name</th>'
    + '<th class="c-n" title="Matches played">MP</th><th class="c-n">F</th><th class="c-n">D</th><th class="c-n c-kd">K/D</th></tr></thead><tbody>';
  rows.forEach((b, i) => {
    h += `<tr style="--c:${col(botId(b.slot))}"><td class="c-rk">${i + 1}</td><td class="c-idc"><span class="sw"></span>${botId(b.slot)}</td>`
      + `<td class="c-nmc">${esc(b.name)}</td><td class="c-n">${b.played}</td><td class="c-n c-f">${b.frags}</td>`
      + `<td class="c-n">${b.deaths}</td><td class="c-n c-kd">${b.deaths ? (b.frags / b.deaths).toFixed(2) : '—'}</td></tr>`;
  });
  return h + '</tbody></table>';
}

/* One line: when the pipeline last published, how long ago, and STALE once a scheduled run has been missed. */
export function footer(status, source, now = Date.now()) {
  if (source === 'sample') return '<span class="kick">PIPELINE</span><span>SAMPLE BUNDLES · NO PIPELINE CONFIGURED</span>';
  if (!status) return '<span class="kick">PIPELINE</span><span>STATUS UNAVAILABLE</span>';
  const last = status.last_success ? new Date(status.last_success) : null;
  const ageH = last ? (now - last) / 3.6e6 : Infinity;
  const ago = !last ? '' : ageH < 1 ? `${Math.max(0, Math.round(ageH * 60))} MIN AGO` : ageH < 48 ? `${Math.round(ageH)} H AGO` : `${Math.round(ageH / 24)} D AGO`;
  const fails = status.consecutive_failures ? ` · ${status.consecutive_failures} FAILED RUN${status.consecutive_failures === 1 ? '' : 'S'} SINCE` : '';
  return `<span class="kick">PIPELINE</span>${ageH > STALE_H ? '<span class="ft-stale">STALE</span>' : ''}`
    + `<span>LAST PUBLISHED ${last ? `${utcDay(last)} ${utcTime(last)} UTC · ${ago}` : 'NEVER'}${fails}</span>`;
}
