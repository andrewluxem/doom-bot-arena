/* Frag timeline (design/components/Timeline.md): one tick per shown death event in the killer's colour,
   stacked per bucket, a playhead, click a tick to seek, drag to scrub. Matches longer than the detail
   window get the overview bar. Every seek goes through the caller (and so through the clock). */
import { fmtClock, keyLabel } from './bundle.js';
import { col, esc } from './render.js';

const WINDOW_S = 150;

export class Timeline {
  constructor(B, { seek, scrubStart, scrubEnd }) {
    this.B = B;
    this.cb = { seek, scrubStart, scrubEnd };
    this.len = B.n / B.rate;
    this.win = Math.min(WINDOW_S, this.len);
  }

  sec(tic) { return (tic - this.B.f0) / this.B.rate; }

  mount(host, o = {}) {
    const B = this.B, N = o.buckets || 120, bucket = this.win / N, stackH = o.stack || 4;
    this.o = o;
    const levels = {}, ticks = [];
    for (const d of B.shown) {
      const k = Math.floor(this.sec(d.tic) / bucket), lvl = (levels[k] = (levels[k] ?? -1) + 1);
      const who = d.killer ? B.bot(d.killer) : null, v = B.bot(d.victim);
      const label = `${fmtClock(this.sec(d.tic))} ${who ? who.code : 'ENV'} ✕ ${d.type === 'suicide' ? 'SELF' : v.code} · ${keyLabel(d.key)}`;
      ticks.push(`<i class="tl-tick" data-tic="${d.tic}" style="left:${((k * bucket) / this.len) * 100}%;bottom:${lvl * stackH}px;`
        + `width:calc(${(bucket / this.len) * 100}% - 1px);background:${who ? col(who.id) : 'var(--ink-muted)'}" title="${esc(label)}"></i>`);
    }
    const frags = B.shown.filter((d) => d.type === 'frag').length, other = B.shown.length - frags;
    const step = this.win <= 30 ? 5 : this.win <= 60 ? 10 : 30;
    let h = `<div class="tl${o.compact ? ' tl--compact' : ''}">`;
    if (!o.noHead) {
      h += `<div class="tl-hd"><span class="tl-k">FRAG TIMELINE</span><span class="tl-sub">${frags} FRAGS`
        + (other ? ` · ${other} OTHER DEATH${other > 1 ? 'S' : ''}` : '') + ` · ${fmtClock(0)}–${fmtClock(this.len)}</span></div>`;
    }
    h += `<div class="tl-lane" style="--sh:${stackH}px" role="slider" tabindex="0" aria-label="Match timeline: click a tick to seek, drag to scrub" `
      + `aria-valuemin="0" aria-valuemax="${Math.round(this.len)}"><div class="tl-clip"><div class="tl-track">${ticks.join('')}</div></div>`
      + '<span class="tl-future"></span><span class="tl-head"><b></b></span></div><div class="tl-axis"><div class="tl-axis-in">';
    for (let t = 0; t <= this.len + 1e-6; t += step) h += `<span style="left:${(t / this.len) * 100}%">${fmtClock(t)}</span>`;
    h += '</div></div>';
    const overview = this.len > this.win && !o.noOverview;
    if (overview) {
      h += `<div class="tl-ov" aria-hidden="true"><span class="tl-ov-fill"></span><span class="tl-ov-win" style="width:${(this.win / this.len) * 100}%"></span>`;
      for (let m = 1; m * 60 < this.len; m++) h += `<span class="tl-ov-m" style="left:${((m * 60) / this.len) * 100}%"></span>`;
      h += `<span class="tl-ov-head"></span></div><div class="tl-ov-axis"><span style="left:0">00:00</span><span style="left:100%">${fmtClock(this.len)}</span></div>`;
    }
    host.innerHTML = h + '</div>';
    const q = (s) => host.querySelector(s);
    this.el = { lane: q('.tl-lane'), track: q('.tl-track'), axis: q('.tl-axis-in'), future: q('.tl-future'), head: q('.tl-head'),
      label: q('.tl-head b'), ovFill: q('.tl-ov-fill'), ovWin: q('.tl-ov-win'), ovHead: q('.tl-ov-head'), ov: q('.tl-ov') };
    this.lastLabel = null;
    this.start = -1;
    this.drag(this.el.lane, false);
    if (this.el.ov) this.drag(this.el.ov, true);
  }

  /* Detail-window start (seconds): follows the playhead once the match is longer than the window. */
  windowStart(sec) {
    return Math.min(Math.max(0, sec - this.win / 2), this.len - this.win);
  }

  ticAt(el, clientX, overview) {
    const r = el.getBoundingClientRect(), f = Math.min(1, Math.max(0, (clientX - r.left) / r.width));
    const sec = overview ? f * this.len : this.start + f * this.win;
    return this.B.f0 + sec * this.B.rate;
  }

  /* A tick within 5px of the pointer, or null. Ticks are a few pixels wide, so clicks get some slack. */
  tickNear(clientX) {
    let best = null, bd = 6;
    for (const t of this.el.track.children) {
      const r = t.getBoundingClientRect(), d = clientX < r.left ? r.left - clientX : clientX > r.right ? clientX - r.right : 0;
      if (d < bd) { bd = d; best = +t.dataset.tic; }
    }
    return best;
  }

  drag(el, overview) {
    let down = null;
    el.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      down = { x: e.clientX, moved: false };
      el.setPointerCapture(e.pointerId);
      this.cb.scrubStart();
    });
    el.addEventListener('pointermove', (e) => {
      if (!down) return;
      if (Math.abs(e.clientX - down.x) > 3) down.moved = true;
      if (down.moved) this.cb.seek(this.ticAt(el, e.clientX, overview));
    });
    const up = (e) => {
      if (!down) return;
      const tick = !down.moved && !overview ? this.tickNear(e.clientX) : null;
      this.cb.seek(tick ?? this.ticAt(el, e.clientX, overview));
      down = null;
      this.cb.scrubEnd();
    };
    el.addEventListener('pointerup', up);
    el.addEventListener('pointercancel', up);
  }

  update(tic) {
    if (!this.el) return;
    const sec = this.sec(tic), start = this.windowStart(sec);
    if (start !== this.start) {
      this.start = start;
      const scale = this.len / this.win, shift = (-start / this.len) * 100;
      for (const el of [this.el.track, this.el.axis]) {
        el.style.width = scale * 100 + '%';
        el.style.transform = `translateX(${shift}%)`;
      }
      if (this.el.ovWin) this.el.ovWin.style.left = (start / this.len) * 100 + '%';
    }
    const p = ((sec - start) / this.win) * 100 + '%';
    this.el.head.style.left = p;
    this.el.future.style.left = p;
    const label = fmtClock(sec);
    if (label !== this.lastLabel) {
      this.el.label.textContent = label;
      this.el.lane.setAttribute('aria-valuenow', Math.floor(sec));
      this.el.lane.setAttribute('aria-valuetext', label);
      this.lastLabel = label;
    }
    if (this.el.ovFill) {
      this.el.ovFill.style.width = (sec / this.len) * 100 + '%';
      this.el.ovHead.style.left = (sec / this.len) * 100 + '%';
    }
  }
}
