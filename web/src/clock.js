/* The master clock. Everything on screen reads its tic; nothing else keeps time.
   Tics are continuous; the shown frame is floor(tic). Paused and seeked positions sit mid-frame (k + 0.5)
   so every video lands on the same frame regardless of timestamp rounding. */
export class Clock {
  constructor(ticrate, first, last) {
    this.rate = ticrate;
    this.min = first + 0.5;
    this.max = last + 0.5;
    this.tic = this.min;
    this.playing = false;
    this.wall = 0;
  }
  clamp(tic) { return Math.min(this.max, Math.max(this.min, tic)); }
  now(wall = performance.now()) {
    return this.playing ? this.clamp(this.tic + ((wall - this.wall) * this.rate) / 1000) : this.tic;
  }
  start(tic, wall = performance.now()) {
    this.tic = this.clamp(tic);
    this.wall = wall;
    this.playing = true;
  }
  stop(wall = performance.now()) {
    this.tic = Math.floor(this.now(wall)) + 0.5;
    this.tic = this.clamp(this.tic);
    this.playing = false;
  }
  set(tic) {
    this.tic = this.clamp(Math.floor(tic) + 0.5);
    this.wall = performance.now();
  }
}
