/* Keeps the eight POV videos on the master clock.
   Target media time for tic t is (t - frame0_tic) / ticrate (MP4 frame 0 is frame0_tic).
   Playing: drift over 0.35 frames is trimmed with playbackRate; over 2 frames the video is re-seeked.
   Paused: every video is parked on the clock's frame.
   Drift is measured on presented frames (requestVideoFrameCallback), against the clock at display time. */
const HARD = 2;
const SOFT = 0.35;

function settle(el, t, tol) {
  return new Promise((resolve) => {
    const ok = () => !el.seeking && el.readyState >= 3 && Math.abs(el.currentTime - t) < tol;
    if (ok()) return resolve();
    const done = () => { clearTimeout(timer); el.removeEventListener('seeked', check); el.removeEventListener('canplay', check); resolve(); };
    const check = () => { if (ok()) done(); };
    const timer = setTimeout(done, 4000);
    el.addEventListener('seeked', check);
    el.addEventListener('canplay', check);
    if (Math.abs(el.currentTime - t) >= tol) el.currentTime = t;
  });
}

export class VideoSync {
  constructor(videos, clock, frame0) {
    this.videos = videos; // [{ slot, el }]
    this.clock = clock;
    this.frame0 = frame0;
    this.rate = clock.rate;
    this.busy = false; // starting playback: the clock waits for all eight
    this.token = 0;
    this.stalls = 0;
    this.seekLead = 0.04;
    this.resetStats();
    this.pendingSeek = null;
    this.seekLog = [];
    videos.forEach((v, i) => this.watch(v.el, i));
  }

  resetStats() {
    this.stats = this.videos.map(({ slot }) => ({ slot, maxDrift: 0, maxFrac: 0, samples: 0, resyncs: 0, last: null }));
  }

  mediaTime(tic) { return (tic - this.frame0) / this.rate; }

  watch(el, i) {
    if (!el.requestVideoFrameCallback) return;
    const cb = (_, meta) => { this.presented(i, meta); el.requestVideoFrameCallback(cb); };
    el.requestVideoFrameCallback(cb);
  }

  presented(i, meta) {
    const frame = Math.round(meta.mediaTime * this.rate);
    const clockTic = this.clock.now(meta.expectedDisplayTime);
    const want = Math.floor(clockTic) - this.frame0;
    const s = this.stats[i];
    s.last = { frame, want, at: meta.expectedDisplayTime };
    if (this.clock.playing && !this.busy) {
      const drift = frame - want;
      const frac = frame + 0.5 - (clockTic - this.frame0);
      s.samples++;
      if (Math.abs(drift) > Math.abs(s.maxDrift)) s.maxDrift = drift;
      if (Math.abs(frac) > Math.abs(s.maxFrac)) s.maxFrac = frac;
    }
    this.checkSeek();
  }

  /* Seek timing: from the request until all eight have presented a frame within 2 of the clock. */
  checkSeek() {
    const p = this.pendingSeek;
    if (!p) return;
    const ok = this.stats.every((s) => s.last && s.last.at >= p.t0 && Math.abs(s.last.frame - s.last.want) <= HARD);
    if (!ok || (p.resume && (!this.clock.playing || this.busy))) return;
    const ms = Math.max(...this.stats.map((s) => s.last.at)) - p.t0;
    this.seekLog.push({ tic: p.tic, resume: p.resume, ms: Math.round(ms) });
    this.pendingSeek = null;
  }

  tick(wall) {
    if (this.busy) return;
    const tic = this.clock.now(wall);
    if (this.clock.playing && this.videos.some(({ el }) => el.readyState < 3 && !el.seeking)) {
      this.stalls++;
      this.play(); // hold the clock until every video can play again
      return;
    }
    for (let i = 0; i < this.videos.length; i++) {
      const el = this.videos[i].el;
      if (el.seeking || el.readyState < 1) continue;
      if (!this.clock.playing) {
        if (!el.paused) el.pause();
        const t = this.mediaTime(tic);
        if (Math.abs(el.currentTime - t) > 0.3 / this.rate) el.currentTime = t;
        continue;
      }
      if (el.paused) el.play().catch(() => {});
      const drift = (el.currentTime - this.mediaTime(tic)) * this.rate;
      if (Math.abs(drift) > HARD) {
        el.playbackRate = 1;
        el.currentTime = this.mediaTime(tic) + this.seekLead;
        this.stats[i].resyncs++;
      } else {
        const r = Math.abs(drift) < SOFT ? 1 : Math.min(1.1, Math.max(0.9, 1 - drift * 0.05));
        if (Math.abs(el.playbackRate - r) > 0.004) el.playbackRate = r;
      }
    }
  }

  /* Park every video on the clock, start them together, then start the clock from where they actually are. */
  async play() {
    const my = ++this.token;
    this.busy = true;
    const tic = this.clock.now();
    this.clock.stop();
    this.clock.tic = this.clock.clamp(tic >= this.clock.max ? this.clock.min : this.clock.tic);
    const t = this.mediaTime(this.clock.tic);
    this.videos.forEach(({ el }) => { el.pause(); el.playbackRate = 1; });
    await Promise.all(this.videos.map(({ el }) => settle(el, t, 0.3 / this.rate)));
    if (my !== this.token) return false;
    await Promise.all(this.videos.map(({ el }) => el.play().catch((e) => e)));
    if (my !== this.token) return false;
    const mean = this.videos.reduce((a, { el }) => a + el.currentTime, 0) / this.videos.length;
    this.clock.start(this.frame0 + mean * this.rate);
    this.busy = false;
    return true;
  }

  pause() {
    ++this.token;
    this.busy = false;
    this.clock.stop();
    this.videos.forEach(({ el }) => { el.pause(); el.playbackRate = 1; });
  }

  /* Every seek and scrub goes through here: the clock moves first, the videos follow. */
  seek(tic, resume = this.clock.playing || this.busy) {
    ++this.token;
    this.busy = false;
    this.clock.stop();
    this.clock.set(tic);
    this.videos.forEach(({ el }) => { if (!el.paused) el.pause(); });
    this.pendingSeek = { tic: Math.floor(this.clock.tic), resume, t0: performance.now() };
    if (resume) return this.play();
    return Promise.resolve(true);
  }
}
