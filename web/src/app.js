/* Option A, Broadcast Multiviewer: one master clock drives eight POV videos, the map, standings,
   timeline, kill feed and telemetry. Tiles (and their videos) are created once and moved between layouts. */
import { fmtClock, loadBundle, loadIndex } from './bundle.js';
import { Clock } from './clock.js';
import { MapView } from './map.js';
import * as R from './render.js';
import * as S from './season.js';
import { VideoSync } from './sync.js';
import { Timeline } from './timeline.js';

const PHONE = '(max-width: 720px)';
const THEME_KEY = 'dba-theme';
const FOOT = 18 + 6; // pipeline footer height plus its gap, out of the desktop height budget
/* Production bundles live on Vercel Blob (index.json and status.json at the store root); without the build-time
   base, the viewer reads the committed sample index next to it. */
const BLOB_BASE = (import.meta.env.VITE_BLOB_PUBLIC_BASE || '').replace(/\/+$/, '');

async function loadStatus() {
  if (!BLOB_BASE) return null;
  try {
    const r = await fetch(`${BLOB_BASE}/status.json`, { cache: 'no-cache' });
    return r.ok ? await r.json() : null;
  } catch { return null; }
}

function readHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  const f = Number(p.get('f'));
  return { b: p.get('b'), f: f >= 1 && f <= 8 ? f : null, t: p.has('t') ? Number(p.get('t')) : null, m: p.get('m') === '1' };
}

/* Byte-range servers stream the MP4s; a server without ranges (python -m http.server) can't seek them,
   so the videos are loaded whole instead. */
async function videoSources(B) {
  const urls = B.match.pov.map((p) => new URL(p.path, B.base).href);
  let ranged = false;
  try {
    const r = await fetch(urls[0], { headers: { Range: 'bytes=0-1' } });
    ranged = r.status === 206;
    r.body?.cancel();
  } catch { /* fall through to whole-file loads */ }
  if (ranged) return urls;
  return Promise.all(urls.map(async (u) => URL.createObjectURL(await (await fetch(u)).blob())));
}

export async function boot(root) {
  try {
    const saved = localStorage.getItem(THEME_KEY);
    if (saved === 'paper' || saved === 'dark') document.documentElement.dataset.theme = saved;
  } catch { /* storage blocked: stay dark */ }
  root.innerHTML = '<div class="boot"><span class="kick">LOADING BUNDLE</span></div>';
  try {
    const indexUrl = BLOB_BASE ? new URL(`${BLOB_BASE}/index.json`) : new URL('index.json', location.href);
    const status = loadStatus();
    const index = await loadIndex(indexUrl);
    const hash = readHash();
    const entry = index.bundles.find((b) => b.id === hash.b) || index.bundles[0];
    const B = await loadBundle(new URL(entry.path, indexUrl));
    const app = new App(root, B, index, await videoSources(B), hash, { status: await status, source: BLOB_BASE ? 'blob' : 'sample' });
    window.__dba = app.debug();
  } catch (e) {
    console.error(e);
    root.innerHTML = `<div class="boot"><span class="kick">COULD NOT LOAD THE BUNDLE</span><p>${R.esc(e.message)}</p></div>`;
  }
}

class App {
  constructor(root, B, index, srcs, hash, pipeline) {
    this.root = root;
    this.B = B;
    this.index = index;
    this.pipeline = pipeline;
    this.sheetOpen = false;
    this.clock = new Clock(B.rate, B.f0, B.total - 1);
    this.ctx = { B, f0: B.f0, rate: B.rate, tic: B.f0, shownNow: [] };
    this.mode = 'multi';
    this.focus = null;
    this.mapFrom = 'multi';
    this.order = [...B.slots];
    this.drawerOpen = false;
    this.metrics = { mapMax: 0 };
    this.tiles = B.slots.map((slot, i) => this.makeTile(slot, srcs[i]));
    this.sync = new VideoSync(this.tiles.map((t) => ({ slot: t.slot, el: t.video })), this.clock, B.f0);
    this.timeline = new Timeline(B, {
      seek: (tic) => this.seek(tic, false),
      scrubStart: () => { this.resumeAfterScrub = this.clock.playing || this.sync.busy; this.sync.pause(); },
      scrubEnd: () => { if (this.resumeAfterScrub) this.play(); this.writeHash(); },
    });
    this.makeMap();
    this.media = matchMedia(PHONE);
    this.media.addEventListener('change', () => this.layout());
    addEventListener('resize', () => this.size());
    addEventListener('keydown', (e) => this.key(e));
    addEventListener('hashchange', () => this.fromHash());
    document.addEventListener('visibilitychange', () => {
      if (document.hidden && this.clock.playing) { this.hiddenPause = true; this.pause(); }
      else if (!document.hidden && this.hiddenPause) { this.hiddenPause = false; this.play(); }
    });
    this.applyHash(hash);
    root.replaceChildren();
    this.layout();
    if (B.scoreCheck.length) console.error('Displayed scores disagree with match.json score.rows:', B.scoreCheck);
    const loop = (now) => { this.frame(now); requestAnimationFrame(loop); };
    requestAnimationFrame(loop);
  }

  /* ---------- persistent pieces ---------- */
  makeTile(slot, src) {
    const bot = this.B.bot(slot), el = document.createElement('article');
    el.dataset.slot = slot;
    el.tabIndex = 0;
    el.setAttribute('role', 'button');
    el.setAttribute('aria-label', `${bot.id} ${bot.name}: focus this feed (key ${slot})`);
    el.style.setProperty('--c', R.col(bot.id));
    el.innerHTML = '<div class="vt-media"><video muted playsinline preload="auto" disablepictureinpicture disableremoteplayback></video>'
      + '<div class="vt-dim"></div><header class="t-hd"></header><div class="vt-resp"></div><footer class="t-ft"></footer></div>';
    const video = el.querySelector('video');
    video.muted = true;
    video.crossOrigin = 'anonymous'; // Blob-hosted feeds stay readable (frame checks, canvas) under its ACAO *
    video.src = src;
    el.addEventListener('click', () => this.focusBot(slot));
    el.addEventListener('keydown', (e) => { if (e.key === 'Enter') this.focusBot(slot); });
    return { slot, el, video, hd: el.querySelector('.t-hd'), ft: el.querySelector('.t-ft'), resp: el.querySelector('.vt-resp'), size: 'm' };
  }

  makeMap() {
    const p = document.createElement('div');
    p.className = 'mm-panel mm-panel--fill';
    p.dataset.pinned = 'bottom-right';
    p.innerHTML = `<div class="mm-hd"><span class="mm-title"></span><button class="mm-hint" type="button" data-act="map"></button></div><div class="mm-host"></div>`;
    this.mapPanel = p;
    this.map = new MapView(this.B, p.querySelector('.mm-host'));
    p.querySelector('.mm-host').addEventListener('click', (e) => {
      const slot = this.map.hit(e.clientX, e.clientY);
      if (slot) this.focusBot(slot);
      else if (this.mode !== 'map') this.toggleMap();
    });
    p.querySelector('.mm-host').addEventListener('mousemove', (e) => {
      e.currentTarget.style.cursor = this.map.hit(e.clientX, e.clientY) || this.mode !== 'map' ? 'pointer' : 'default';
    });
  }

  /* ---------- layout ---------- */
  topBar(phone) {
    const m = this.B.match, sub = `DEATHMATCH · 8 BOTS · ${R.esc(m.map)} · ${fmtClock(this.B.n / this.B.rate)}`;
    const buttons = '<div class="tb"><button class="tb-btn" type="button" data-act="play"></button>'
      + `<button class="tb-btn" type="button" data-act="matches" aria-expanded="${this.sheetOpen}">MATCHES <kbd>L</kbd></button>`
      + '<button class="tb-btn" type="button" data-act="theme"></button></div>';
    if (phone) {
      return `<header class="ph-top"><span class="brand-t">DOOM BOT ARENA</span><div class="clk"></div>${buttons}</header>`
        + `<div class="drawer" role="button" tabindex="0" data-act="drawer" aria-expanded="${this.drawerOpen}"><span class="kick">STANDINGS</span>`
        + `<span class="ldr"></span><span class="chev">${this.drawerOpen ? 'CLOSE ▴' : '+5 ▾'}</span></div>`
        + `<div class="drawer-body"${this.drawerOpen ? '' : ' hidden'}></div>`;
    }
    return `<div class="a-top"><div class="a-brand"><div class="brand"><span class="brand-t">DOOM BOT ARENA</span><span class="brand-s">${sub}</span></div>`
      + `<div class="clk"></div>${buttons}</div><ol class="sb-strip" aria-label="Standings by displayed frags"></ol></div>`;
  }

  skeleton(phone, mode) {
    const tl = '<div class="panel tl-panel" data-put="tl"></div>', kf = '<div class="panel kf-panel" data-put="kf"></div>';
    if (phone) {
      if (mode === 'map') {
        return '<div class="ph-strip ph-strip--map" data-put="wall" aria-label="Feeds"></div><div class="ph-mapx" data-put="map"></div>'
          + `<section class="ph-dock ph-dock--solo">${tl}</section>`;
      }
      const body = mode === 'focus'
        ? '<div class="ph-stage" data-put="stage"></div><div class="ph-strip" data-put="wall" aria-label="Other feeds"></div>'
          + `<section class="tele tele--compact" data-put="tele"></section>${kf}`
        : '<section class="pa-grid" data-put="wall" aria-label="Eight POV feeds"></section>';
      return `<div class="ph-scroll">${body}</div><section class="ph-dock">${tl}<div data-put="map"></div></section>`;
    }
    if (mode === 'focus') {
      return '<section class="af"><div class="af-l"><div data-put="stage"></div><div class="af-thumbs" data-put="wall" aria-label="Other feeds, one click to focus"></div></div>'
        + `<div class="af-r"><section class="tele" data-put="tele"></section>${tl}<div class="af-bot">${kf}<div data-put="map"></div></div></div></section>`;
    }
    if (mode === 'map') {
      return `<section class="am"><div class="am-l"><div class="am-grid" data-put="wall" aria-label="Feeds, one click to focus"></div>${tl}</div><div data-put="map"></div></section>`;
    }
    return '<section class="a-wall" data-put="wall" aria-label="Multiview: eight POV feeds"></section>'
      + `<section class="a-dock">${tl}${kf}<div data-put="map"></div></section>`;
  }

  layout() {
    const phone = this.media.matches, mode = this.mode;
    this.phone = phone;
    const L = document.createElement('div');
    L.className = `L L-${phone ? 'phone' : 'desk'} L-${mode}`;
    const foot = `<footer class="a-foot" aria-label="Pipeline status">${S.footer(this.pipeline.status, this.pipeline.source)}</footer>`;
    L.innerHTML = this.topBar(phone) + this.skeleton(phone, mode) + (phone ? '' : foot) + this.sheet(phone);
    if (phone) L.querySelector('.ph-scroll')?.insertAdjacentHTML('beforeend', foot);
    this.root.appendChild(L);
    const put = (k) => L.querySelector(`[data-put="${k}"]`);
    const thumb = mode !== 'multi' || phone ? 'xs' : 'm';
    for (const t of this.tiles) {
      if (mode === 'focus' && t.slot === this.focus) { put('stage').appendChild(t.el); t.size = phone ? 'm' : 'l'; }
      else { put('wall').appendChild(t.el); t.size = thumb; }
    }
    put('map').appendChild(this.mapPanel);
    const expanded = mode === 'map';
    this.map.setOptions(expanded ? { ids: true, callsigns: true, r: 5, ls: 12 } : phone ? { ids: false, callsigns: false, r: 4, ls: 9 } : { ids: true, callsigns: false, r: 4, ls: 10 });
    this.mapPanel.querySelector('.mm-title').textContent = phone && !expanded ? this.B.match.map : `ARENA · ${this.B.match.map}${expanded ? ' · EXPANDED' : ''}`;
    this.mapPanel.querySelector('.mm-hint').innerHTML = expanded ? 'COLLAPSE ↘ <kbd>M</kbd>' : phone ? 'TAP ⤢' : '⤢ <kbd>M</kbd>';
    this.mapPanel.querySelector('.mm-hint').setAttribute('aria-label', expanded ? 'Collapse map (M)' : 'Expand map (M)');
    this.timeline.mount(put('tl'), phone ? { compact: true, noHead: true, buckets: 50, stack: 3 } : mode === 'multi' ? { buckets: 120, stack: 7 } : { buckets: 90, compact: mode === 'map' });
    if (put('kf')) put('kf').innerHTML = '<span class="kick">KILL FEED</span><div class="kf-body"></div>';
    if (put('tl') && !phone && mode === 'multi') put('tl').insertAdjacentHTML('beforeend', '<p class="feat"></p>');
    if (put('tele') && this.focus) put('tele').style.setProperty('--c', R.col(this.B.bot(this.focus).id));
    L.addEventListener('click', (e) => this.act(e));
    L.querySelector('.drawer')?.addEventListener('keydown', (e) => { if (e.key === 'Enter') this.act(e); });
    if (this.L) this.L.remove();
    this.L = L;
    this.size();
    this.dirty = true;
    this.writeHash();
  }

  /* Match list and season table, over the layout below the top bar; picking a match loads it via the hash. */
  sheet(phone) {
    if (!this.sheetOpen) return '';
    return `<div class="ml-sheet${phone ? ' ml-sheet--phone' : ''}" role="dialog" aria-label="Matches and season standings">`
      + '<div class="ml-hd"><span class="kick">MATCHES</span><button class="tb-btn" type="button" data-act="matches">CLOSE <kbd>ESC</kbd></button></div>'
      + `<div class="ml-body"><section class="panel ml-panel">${S.matchList(this.index, this.B.match.id)}</section>`
      + `<section class="panel ml-panel ss-panel">${S.seasonTable(this.index)}</section></div>`
      + `<footer class="a-foot">${S.footer(this.pipeline.status, this.pipeline.source)}</footer></div>`;
  }

  /* Desktop sizes that CSS can't derive: the 4x2 wall fills the width unless the dock would drop under 180px. */
  size() {
    if (!this.L || this.phone) return;
    const W = innerWidth, H = innerHeight - FOOT, s = this.L.style, top = 56, frame = 16, gap = 6;
    if (this.mode === 'multi') {
      const budget = H - frame - top - 2 * gap - 180;
      const tile = Math.max(100, Math.min((W - frame - 12) / 4, ((budget - 4) / 2 - 2) * (4 / 3) + 2));
      const wallH = 2 * ((tile - 2) * 0.75 + 2) + 4, dockH = H - frame - top - 2 * gap - wallH;
      s.setProperty('--wall-w', `${4 * tile + 12}px`);
      s.setProperty('--dock-h', `${Math.round(dockH)}px`);
      s.setProperty('--map-w', `${Math.round(Math.min(W * 0.3, Math.max(180, (dockH - 22) * (4 / 3))))}px`);
    } else if (this.mode === 'focus') {
      const avail = H - frame - top - gap;
      s.setProperty('--stage-w', `${Math.round(Math.min((W - frame) * 0.64, (avail - 6 - 4) / (0.75 + 0.75 / 7)))}px`);
    } else {
      s.setProperty('--rail-w', `${Math.round(Math.min(420, Math.max(260, W * 0.24)))}px`);
    }
  }

  /* ---------- state ---------- */
  renderTic(t) {
    const B = this.B, ctx = this.ctx;
    ctx.tic = t;
    ctx.shownNow = B.shownUpTo(t);
    const score = B.scoreAt(t);
    this.order = [...this.order].sort((a, b) => score[b].frags - score[a].frags); // stable: ties keep the last order
    const rank = {};
    this.order.forEach((s, i) => { rank[s] = i + 1; });
    const states = B.slots.map((s) => R.botState(B, s, t, score, rank[s]));
    this.states = states;
    const set = (el, html) => { if (el && el._h !== html) { el.innerHTML = html; el._h = html; } };
    for (const tile of this.tiles) {
      const b = states[tile.slot - 1], cls = R.tileClass(b, tile.size);
      if (tile.el.className !== cls) tile.el.className = cls;
      set(tile.hd, R.tileHead(b));
      set(tile.ft, R.tileFoot(b));
      set(tile.resp, R.tileResp(b, ctx));
    }
    const L = this.L, playing = this.clock.playing || this.sync.busy;
    set(L.querySelector('.sb-strip'), R.scoreStrip(states, this.order, ctx));
    set(L.querySelector('.ldr'), R.leaders(states, this.order));
    if (this.drawerOpen) set(L.querySelector('.drawer-body'), R.scoreTable(states, this.order, ctx));
    set(L.querySelector('.kf-body'), R.killfeed(ctx.shownNow, 5, ctx));
    const tele = L.querySelector('[data-put="tele"]');
    if (tele && this.focus) set(tele, R.telemetry(states[this.focus - 1], ctx, { compact: this.phone }));
    const feat = L.querySelector('.feat');
    if (feat) {
      const hot = states.filter((b) => b.hot).map((b) => `${b.id} ${R.esc(b.code)}`);
      const down = states.filter((b) => b.dead).map((b) => `${b.id} ${R.esc(b.code)} ${b.remain.toFixed(1)}s`);
      set(feat, `<b>IN COMBAT</b> ${hot.join(' · ') || 'NONE'} &nbsp; <b>DOWN</b> ${down.join(' · ') || 'NONE'}`);
    }
    L.querySelectorAll('.clk').forEach((c) => set(c, R.clock(ctx, playing)));
    L.querySelectorAll('[data-act="play"]').forEach((b) => set(b, playing ? 'PAUSE <kbd>SPACE</kbd>' : 'PLAY <kbd>SPACE</kbd>'));
    L.querySelectorAll('[data-act="theme"]').forEach((b) => set(b, document.documentElement.dataset.theme === 'paper' ? 'DARK' : 'PAPER'));
  }

  frame(now) {
    this.sync.tick(now);
    let tic = this.clock.now(now);
    if (this.clock.playing && tic >= this.clock.max) {
      this.pause();
      this.logDrift();
      tic = this.clock.now();
    }
    const t = Math.floor(tic);
    if (t !== this.lastT || this.dirty) {
      this.renderTic(t);
      this.map.draw(this.states);
      this.mapTic = t;
      this.lastT = t;
      this.dirty = false;
    }
    this.metrics.mapMax = Math.max(this.metrics.mapMax, Math.abs(t - this.mapTic));
    this.timeline.update(tic);
    if (this.clock.playing && now - (this.hashAt || 0) > 1000) this.writeHash();
  }

  logDrift() {
    console.info('Max drift per video while playing (frames; presented frame minus clock frame):');
    console.table(this.sync.stats.map((s) => ({ slot: s.slot, maxDrift: s.maxDrift, maxDriftFrac: +s.maxFrac.toFixed(2), samples: s.samples, resyncs: s.resyncs })));
    console.info(`Map: max ${this.metrics.mapMax} frames between the clock frame and the drawn frame; stalls ${this.sync.stalls}`);
  }

  /* ---------- transport ---------- */
  play() {
    this.sync.play().then(() => { this.dirty = true; this.writeHash(); });
    this.dirty = true;
  }
  pause() {
    this.sync.pause();
    this.dirty = true;
    this.writeHash();
  }
  togglePlay() {
    if (this.clock.playing || this.sync.busy) this.pause();
    else this.play();
  }
  seek(tic, resume) {
    this.sync.seek(tic, resume).then(() => { this.dirty = true; });
    this.dirty = true;
    if (!this.clock.playing) this.writeHash();
  }

  /* ---------- modes ---------- */
  focusBot(slot) {
    if (!this.B.slots.includes(slot) || (this.mode === 'focus' && this.focus === slot)) return;
    this.mode = 'focus';
    this.focus = slot;
    this.layout();
  }
  back() {
    if (this.mode === 'map') this.mode = this.mapFrom === 'focus' && this.focus ? 'focus' : 'multi';
    else if (this.mode === 'focus') { this.mode = 'multi'; this.focus = null; }
    else return;
    this.layout();
  }
  toggleMap() {
    if (this.mode === 'map') return this.back();
    this.mapFrom = this.mode;
    this.mode = 'map';
    this.layout();
  }

  act(e) {
    const a = e.target.closest('[data-act]'), cell = e.target.closest('.sb-cell, .sb-table tr[data-slot]');
    if (cell) return this.focusBot(+cell.dataset.slot);
    if (!a) return;
    const k = a.dataset.act;
    if (k === 'play') this.togglePlay();
    else if (k === 'theme') this.toggleTheme();
    else if (k === 'map') this.toggleMap();
    else if (k === 'return') this.back();
    else if (k === 'drawer') { this.drawerOpen = !this.drawerOpen; this.layout(); }
    else if (k === 'matches') this.toggleSheet();
  }

  key(e) {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const tag = e.target.tagName;
    if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
    if (/^[1-8]$/.test(e.key)) { e.preventDefault(); this.focusBot(+e.key); }
    else if (e.key === 'Escape') { if (this.sheetOpen) this.toggleSheet(); else this.back(); }
    else if (e.key === 'l' || e.key === 'L') this.toggleSheet();
    else if (e.key === 'm' || e.key === 'M') this.toggleMap();
    else if (e.key === ' ' && tag !== 'BUTTON') { e.preventDefault(); this.togglePlay(); }
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
      e.preventDefault();
      const step = (e.shiftKey ? this.B.rate : 1) * (e.key === 'ArrowLeft' ? -1 : 1);
      this.seek(Math.floor(this.clock.now()) + step, false);
      if (this.clock.playing) this.pause();
    }
  }

  toggleSheet() {
    this.sheetOpen = !this.sheetOpen;
    this.layout();
  }

  toggleTheme() {
    const next = document.documentElement.dataset.theme === 'paper' ? 'dark' : 'paper';
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem(THEME_KEY, next); } catch { /* not persisted */ }
    this.map.theme();
    this.dirty = true;
  }

  /* ---------- URL hash: #b=<bundle id>&f=<slot>&t=<bundle tic>&m=1 ---------- */
  writeHash() {
    const p = new URLSearchParams();
    p.set('b', this.B.match.id);
    if (this.focus && (this.mode === 'focus' || this.mapFrom === 'focus')) p.set('f', this.focus);
    p.set('t', Math.floor(this.clock.now()));
    if (this.mode === 'map') p.set('m', '1');
    const h = '#' + p.toString();
    this.hashAt = performance.now();
    if (h !== location.hash) { this.ownHash = h; history.replaceState(null, '', h); }
  }
  applyHash(h) {
    if (h.t != null && Number.isFinite(h.t)) this.clock.set(h.t);
    this.focus = h.f;
    this.mode = h.f ? 'focus' : 'multi';
    if (h.m) { this.mapFrom = this.mode; this.mode = 'map'; }
  }
  fromHash() {
    if (location.hash === this.ownHash) return;
    const h = readHash();
    if (h.b && h.b !== this.B.match.id) return location.reload();
    this.applyHash(h);
    if (h.t != null) this.seek(h.t, false);
    this.layout();
  }

  /* Read-only hooks for scripts/acceptance.mjs. */
  debug() {
    const app = this;
    return {
      bundle: app.B.match.id, base: String(app.B.base), f0: app.B.f0, n: app.B.n, rate: app.B.rate,
      tic: () => app.clock.now(), playing: () => app.clock.playing, busy: () => app.sync.busy,
      play: () => app.sync.play(), pause: () => app.pause(), seek: (t, resume) => app.sync.seek(t, resume),
      stats: () => app.sync.stats.map((s) => ({ ...s, last: s.last && { ...s.last } })),
      resetStats: () => { app.sync.resetStats(); app.sync.stalls = 0; app.metrics.mapMax = 0; },
      seeks: () => [...app.sync.seekLog], stalls: () => app.sync.stalls, mapMax: () => app.metrics.mapMax,
      markers: () => app.map.hits.map((h) => ({ ...h })), mode: () => ({ mode: app.mode, focus: app.focus }),
      videos: () => app.tiles.map((t) => ({ slot: t.slot, currentTime: t.video.currentTime, paused: t.video.paused, readyState: t.video.readyState })),
      logDrift: () => app.logDrift(), scoreCheck: app.B.scoreCheck, source: app.pipeline.source,
      openSheet: () => { if (!app.sheetOpen) app.toggleSheet(); }, closeSheet: () => { if (app.sheetOpen) app.toggleSheet(); },
    };
  }
}
