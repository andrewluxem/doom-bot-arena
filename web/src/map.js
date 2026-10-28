/* Overhead map. Linedefs from map.json are drawn once per size/theme to an offscreen canvas;
   each frame blits it and draws the eight markers from that tic's rows.
   Doom angles: 0 = east, counter-clockwise. The map is rotated 0 or 90 degrees, whichever fits the panel larger. */

/* oklch() -> rgb() so canvas gets a colour every engine parses (tokens.css uses oklch for bot colours). */
function oklchToRgb(L, C, H) {
  const a = C * Math.cos((H * Math.PI) / 180), b = C * Math.sin((H * Math.PI) / 180);
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  const lin = [4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s];
  const enc = (x) => Math.round(255 * Math.min(1, Math.max(0, x <= 0.0031308 ? 12.92 * x : 1.055 * x ** (1 / 2.4) - 0.055)));
  return `rgb(${lin.map(enc).join(', ')})`;
}
export function cssColor(name, el = document.documentElement) {
  const v = getComputedStyle(el).getPropertyValue(name).trim();
  const m = v.match(/^oklch\(\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)/);
  return m ? oklchToRgb(+m[1], +m[2], +m[3]) : v;
}

const CONE_HALF = (30 * Math.PI) / 180; // 60 degree view cone
const GRID = 256; // map units

export class MapView {
  constructor(B, host) {
    this.B = B;
    this.host = host;
    this.canvas = document.createElement('canvas');
    this.canvas.setAttribute('role', 'img');
    this.canvas.setAttribute('aria-label', `Arena map ${B.match.map}: bot positions and view cones`);
    host.appendChild(this.canvas);
    this.ctx = this.canvas.getContext('2d');
    this.off = document.createElement('canvas');
    this.opts = { ids: true, callsigns: false, r: 4, ls: 10 };
    this.hits = [];
    this.w = 0;
    this.h = 0;
    this.theme();
    new ResizeObserver(() => this.resize()).observe(host);
  }

  setOptions(o) {
    Object.assign(this.opts, o);
  }

  theme() {
    const c = (n) => cssColor(n);
    this.col = {
      bg: c('--surface-1'), grid: c('--rule-soft'), step: c('--rule'), wall: c('--ink-muted'), ink: c('--ink'),
      muted: c('--ink-muted'), halo: c('--surface-1'),
      bot: this.B.bots.map((b) => c(`--bot-${b.id}`)),
    };
    this.build();
  }

  resize() {
    const w = this.host.clientWidth, h = this.host.clientHeight;
    if (!w || !h || (w === this.w && h === this.h)) return;
    this.w = w;
    this.h = h;
    this.dpr = window.devicePixelRatio || 1;
    for (const cv of [this.canvas, this.off]) {
      cv.width = Math.round(w * this.dpr);
      cv.height = Math.round(h * this.dpr);
    }
    const { min_x, min_y, max_x, max_y } = this.B.map.bounds, pad = 8;
    let best = null;
    for (const deg of [0, 90]) {
      const t = (deg * Math.PI) / 180, cos = Math.cos(t), sin = Math.sin(t);
      const xs = [], ys = [];
      for (const [x, y] of [[min_x, min_y], [max_x, min_y], [min_x, max_y], [max_x, max_y]]) {
        xs.push(x * cos - y * sin);
        ys.push(x * sin + y * cos);
      }
      const bw = Math.max(...xs) - Math.min(...xs), bh = Math.max(...ys) - Math.min(...ys);
      const s = Math.min((w - 2 * pad) / bw, (h - 2 * pad) / bh);
      if (!best || s > best.s) {
        best = { t, cos, sin, s, minX: Math.min(...xs), maxY: Math.max(...ys), ox: (w - bw * s) / 2, oy: (h - bh * s) / 2 };
      }
    }
    this.xf = best;
    this.build();
  }

  pt(x, y) {
    const f = this.xf, X = x * f.cos - y * f.sin, Y = x * f.sin + y * f.cos;
    return [f.ox + (X - f.minX) * f.s, f.oy + (f.maxY - Y) * f.s];
  }

  build() {
    if (!this.xf) return;
    const g = this.off.getContext('2d'), { col } = this, f = this.xf, B = this.B.map.bounds;
    g.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    g.fillStyle = col.bg;
    g.fillRect(0, 0, this.w, this.h);
    g.lineWidth = 1;
    g.strokeStyle = col.grid;
    g.beginPath();
    for (let x = Math.ceil(B.min_x / GRID) * GRID; x <= B.max_x; x += GRID) { g.moveTo(...this.pt(x, B.min_y)); g.lineTo(...this.pt(x, B.max_y)); }
    for (let y = Math.ceil(B.min_y / GRID) * GRID; y <= B.max_y; y += GRID) { g.moveTo(...this.pt(B.min_x, y)); g.lineTo(...this.pt(B.max_x, y)); }
    g.stroke();
    for (const [twoSided, color, width] of [[true, col.step, 1], [false, col.wall, 1.5]]) {
      g.strokeStyle = color;
      g.lineWidth = width;
      g.lineCap = 'round';
      g.beginPath();
      for (const l of this.B.map.linedefs) {
        if (l.two_sided !== twoSided) continue;
        g.moveTo(...this.pt(l.x1, l.y1));
        g.lineTo(...this.pt(l.x2, l.y2));
      }
      g.stroke();
    }
    this.cone = Math.min(72, Math.max(14, 260 * f.s));
    if (this.last) this.draw(this.last); // the visible canvas was cleared by the resize
  }

  /* Screen angle for a Doom angle: rotate with the map, then flip for canvas y-down. */
  heading(deg) {
    return -((deg * Math.PI) / 180 + this.xf.t);
  }

  draw(states) {
    this.last = states;
    if (!this.xf) return;
    const c = this.ctx, { col, opts } = this, R = this.cone, r = opts.r;
    c.setTransform(1, 0, 0, 1, 0, 0);
    c.drawImage(this.off, 0, 0);
    c.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    this.hits = [];
    const live = states.filter((b) => b.alive), dead = states.filter((b) => b.dead && b.death);
    for (const b of live) {
      const [x, y] = this.pt(b.x, b.y), a = this.heading(b.angle);
      c.beginPath();
      c.moveTo(x, y);
      c.arc(x, y, R, a - CONE_HALF, a + CONE_HALF);
      c.closePath();
      c.fillStyle = col.bot[b.slot - 1];
      c.strokeStyle = col.bot[b.slot - 1];
      c.globalAlpha = 0.2;
      c.fill();
      c.globalAlpha = 0.75;
      c.lineWidth = 1;
      c.stroke();
      c.globalAlpha = 1;
    }
    for (const b of live) {
      if (!b.spot) continue; // spots.json: a held perch or ambush spot, with its watch direction
      const [x, y] = this.pt(b.spot.x, b.spot.y), a = this.heading(b.spot.angle), k = r + 3;
      c.strokeStyle = col.bot[b.slot - 1];
      c.lineWidth = 1.5;
      c.setLineDash([2, 2]);
      c.strokeRect(x - k, y - k, 2 * k, 2 * k);
      c.setLineDash([]);
      c.beginPath();
      c.moveTo(x + Math.cos(a) * k, y + Math.sin(a) * k);
      c.lineTo(x + Math.cos(a) * (k + 8), y + Math.sin(a) * (k + 8));
      c.stroke();
    }
    c.font = `600 ${opts.ls}px "IBM Plex Mono", ui-monospace, Menlo, monospace`;
    c.textBaseline = 'middle';
    c.lineJoin = 'round';
    for (const b of dead) {
      const [x, y] = this.pt(b.death.x, b.death.y), k = r * 1.1;
      c.strokeStyle = col.ink;
      c.lineWidth = 2;
      c.beginPath();
      c.moveTo(x - k, y - k); c.lineTo(x + k, y + k);
      c.moveTo(x + k, y - k); c.lineTo(x - k, y + k);
      c.stroke();
      if (opts.ids) this.label(b.id + (opts.callsigns ? ' ' + b.code : '') + ' ×', x, y, k, col.muted);
      this.hits.push({ slot: b.slot, x, y, dead: true });
    }
    for (const b of live) {
      const [x, y] = this.pt(b.x, b.y);
      c.beginPath();
      c.arc(x, y, r, 0, Math.PI * 2);
      c.fillStyle = col.bot[b.slot - 1];
      c.fill();
      c.lineWidth = 1.5;
      c.strokeStyle = col.halo;
      c.stroke();
      if (opts.ids) this.label(b.id + (opts.callsigns ? ' ' + b.code : ''), x, y, r, col.bot[b.slot - 1]);
      this.hits.push({ slot: b.slot, x, y, dead: false });
    }
  }

  label(text, x, y, r, fill) {
    const c = this.ctx, w = c.measureText(text).width, right = x + r + 3 + w <= this.w - 2;
    c.textAlign = right ? 'left' : 'right';
    const lx = right ? x + r + 3 : x - r - 3;
    c.lineWidth = 3;
    c.strokeStyle = this.col.halo;
    c.strokeText(text, lx, y);
    c.fillStyle = fill;
    c.fillText(text, lx, y);
  }

  /* Marker under a client point (within 12px), or null. */
  hit(clientX, clientY) {
    const rect = this.canvas.getBoundingClientRect(), x = clientX - rect.left, y = clientY - rect.top;
    let best = null, bd = 144;
    for (const h of this.hits) {
      const d = (h.x - x) ** 2 + (h.y - y) ** 2;
      if (d < bd) { bd = d; best = h.slot; }
    }
    return best;
  }
}
