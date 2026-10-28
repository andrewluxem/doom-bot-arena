/* Milestone 1B acceptance checks, run in Google Chrome (Playwright's bundled Chromium has no H.264).

     npm run build && npm run acceptance                       # serves dist/ with python3 -m http.server
     node scripts/acceptance.mjs --url http://127.0.0.1:5173/  # or any running copy
     options: --bundle <id> (default: the first in index.json), --event <tic> (frag event to trace; default: first
              frag 5 s or more in), --headed, --out results.json

   1. Drift: play the whole sample; every video's presented frame stays within 2 frames of the master clock, and so does the map.
   2. Seek: paused and playing seeks to spread-out tics; all eight resync within 1 s.
   3. One frag event: map x, kill feed, killer's tile, and the killer's POV frame (pixels matched against ffmpeg's decode).
   4. Focus and return without reload: 1-8, ESC, M, tile click, map-marker click, return button.
   5. Phone 390x844: all eight tiles reachable, dock pinned, nothing covers a nameplate.
   6. Schema 2 bundles: the focused bot's telemetry shows its selected weapon by name, its target and its sector. */
import { spawn, spawnSync } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

const here = dirname(fileURLToPath(import.meta.url));
const args = Object.fromEntries(process.argv.slice(2).reduce((a, v, i, all) => {
  if (v.startsWith('--')) a.push([v.slice(2), all[i + 1] && !all[i + 1].startsWith('--') ? all[i + 1] : true]);
  return a;
}, []));
const results = { checks: [] };
const check = (name, pass, detail) => { results.checks.push({ name, pass, ...detail }); console.log(`${pass ? 'PASS' : 'FAIL'}  ${name}`); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let server = null, url = args.url;
if (!url) {
  const port = 8100 + Math.floor(Math.random() * 800);
  server = spawn('python3', ['-m', 'http.server', String(port), '--bind', '127.0.0.1', '-d', join(here, '..', 'dist')], { stdio: 'ignore' });
  url = `http://127.0.0.1:${port}/`;
  await sleep(800);
}
if (args.bundle) url = `${url.replace(/#.*$/, '')}#b=${args.bundle}`;
results.url = url;
console.log(`viewer: ${url}`);

const browser = await chromium.launch({ channel: 'chrome', headless: !args.headed, args: ['--autoplay-policy=no-user-gesture-required'] });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto(url);
  await page.waitForFunction(() => window.__dba && __dba.videos().every((v) => v.readyState >= 2), null, { timeout: 30000 });
  const info = await page.evaluate(() => ({ bundle: __dba.bundle, f0: __dba.f0, n: __dba.n, rate: __dba.rate, scoreCheck: __dba.scoreCheck }));
  Object.assign(results, info);
  const { f0, n, rate } = info, last = f0 + n - 1;
  check('score rule reproduces match.json score.rows at the last tic', info.scoreCheck.length === 0, { mismatches: info.scoreCheck });

  /* ---------- 1. drift over the full sample ---------- */
  await page.evaluate(async () => { await __dba.seek(__dba.f0, false); __dba.resetStats(); await __dba.play(); });
  const t0 = Date.now();
  await page.waitForFunction(() => !__dba.playing() && !__dba.busy(), null, { timeout: (n / rate) * 1000 + 20000, polling: 250 });
  const drift = await page.evaluate(() => ({ stats: __dba.stats(), map: __dba.mapMax(), stalls: __dba.stalls(), end: __dba.tic() }));
  const wall = (Date.now() - t0) / 1000;
  results.drift = drift.stats.map(({ slot, maxDrift, maxFrac, samples, resyncs }) => ({ slot, maxDrift, maxFrac: +maxFrac.toFixed(2), samples, resyncs }));
  results.mapMaxDrift = drift.map;
  console.table(results.drift);
  const worst = Math.max(...results.drift.map((d) => Math.abs(d.maxDrift)));
  check(`drift: 8 videos and the map within 2 frames for ${(n / rate).toFixed(0)} s (worst ${worst}, map ${drift.map}, stalls ${drift.stalls}, wall ${wall.toFixed(1)} s)`,
    worst <= 2 && drift.map <= 2 && results.drift.every((d) => d.samples > n * 0.8) && Math.floor(drift.end) === last,
    { worst, stalls: drift.stalls, wallSeconds: wall, endTic: drift.end });

  /* ---------- 2. seeks ---------- */
  const targets = [f0 + 700, f0 + 12, last - 3, f0 + 350, f0 + 999, f0 + 61, f0 + 530, f0 + 888];
  const seeks = [];
  for (const resume of [false, true]) {
    for (const tic of targets) {
      const before = await page.evaluate(() => __dba.seeks().length);
      await page.evaluate(([t, r]) => { __dba.seek(t, r); }, [tic, resume]);
      const got = await page.waitForFunction((k) => __dba.seeks().length > k && __dba.seeks()[k], before, { timeout: 5000 }).then((h) => h.jsonValue()).catch(() => null);
      seeks.push(got ? { tic, resume, ms: got.ms } : { tic, resume, ms: null });
      await sleep(resume ? 600 : 150);
    }
  }
  await page.evaluate(() => __dba.pause());
  results.seeks = seeks;
  const slowest = Math.max(...seeks.map((s) => s.ms ?? Infinity));
  check(`seek: ${seeks.length} seeks (paused and playing) resync all 8 within 1 s (slowest ${slowest} ms)`, slowest <= 1000, {});

  /* ---------- 3. one frag event, everywhere ---------- */
  const events = await page.evaluate(async () => (await (await fetch(new URL(`sample/${__dba.bundle}/events.jsonl`, location.href))).text()).trim().split('\n').map(JSON.parse));
  const ev = events.find((e) => e.type === 'frag' && (args.event ? e.tic === +args.event : e.tic >= f0 + 5 * rate));
  results.event = ev;
  await page.evaluate((t) => __dba.seek(t, false), ev.tic - 1);
  await sleep(500);
  const beforeKill = await page.evaluate((k) => document.querySelector(`article[data-slot="${k}"] .t-fr`).textContent, ev.actor);
  await page.evaluate((t) => __dba.seek(t, false), ev.tic);
  await page.waitForFunction((s) => { const x = __dba.stats()[s - 1].last; return x && x.frame === x.want; }, ev.actor, { timeout: 3000 }).catch(() => {});
  await sleep(300);
  const at = await page.evaluate(({ actor, target }) => {
    const tile = (s) => document.querySelector(`article[data-slot="${s}"]`);
    const v = tile(actor).querySelector('video'), c = document.createElement('canvas');
    c.width = 320; c.height = 240;
    const g = c.getContext('2d');
    g.drawImage(v, 0, 0, 320, 240);
    const px = g.getImageData(0, 0, 320, 240).data;
    let b = '';
    for (let i = 0; i < px.length; i += 4) b += String.fromCharCode(px[i], px[i + 1], px[i + 2]);
    const feed = document.querySelector('.kf-body li');
    return {
      tic: Math.floor(__dba.tic()), presented: __dba.stats()[actor - 1].last, currentTime: v.currentTime,
      marker: __dba.markers().find((m) => m.slot === target),
      feedTop: feed && { tic: +feed.dataset.tic, text: feed.textContent },
      killerTile: { cls: tile(actor).className, head: tile(actor).querySelector('.t-hd').textContent },
      victimTile: { cls: tile(target).className, resp: tile(target).querySelector('.vt-resp').textContent },
      pixels: btoa(b),
    };
  }, ev);
  // Reference: decode the frames around (tic - frame0) from the served MP4 and find the closest one.
  const k = ev.tic - f0, mp4 = await (await fetch(new URL(`sample/${results.bundle}/pov/slot${ev.actor}.mp4`, url))).arrayBuffer();
  const ref = spawnSync('ffmpeg', ['-v', 'error', '-i', 'pipe:0', '-vf', `select=between(n\\,${k - 3}\\,${k + 3})`, '-fps_mode', 'passthrough', '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'],
    { input: Buffer.from(mp4), maxBuffer: 1 << 26 });
  const shown = Buffer.from(at.pixels, 'base64'), F = 320 * 240 * 3, diffs = [];
  for (let i = 0; i * F < ref.stdout.length; i++) {
    let d = 0;
    for (let j = 0; j < F; j++) d += Math.abs(ref.stdout[i * F + j] - shown[j]);
    diffs.push({ frame: k - 3 + i, meanAbsDiff: +(d / F).toFixed(2) });
  }
  const best = diffs.reduce((a, b) => (b.meanAbsDiff < a.meanAbsDiff ? b : a));
  writeFileSync(join(args.out ? dirname(args.out) : here, `event-${ev.tic}-slot${ev.actor}.rgb`), shown);
  results.eventCheck = { ...at, pixels: undefined, beforeKill, expectedFrame: k, pixelMatch: diffs, bestFrame: best.frame };
  const K = String(ev.actor).padStart(2, '0'), V = String(ev.target).padStart(2, '0');
  check(`event tic ${ev.tic} (${ev.source}): map shows x for ${V} at the death`, !!at.marker && at.marker.dead, { marker: at.marker });
  check(`event tic ${ev.tic}: kill feed top row is ${K} x ${V}`, at.feedTop && at.feedTop.tic === ev.tic && at.feedTop.text.includes(K) && at.feedTop.text.includes(V), { feedTop: at.feedTop });
  check(`event tic ${ev.tic}: killer tile ${K} frags ${beforeKill} -> ${at.killerTile.head.match(/(\d+)F$/)?.[1]}, victim tile dead with countdown`,
    +beforeKill.replace(/\D/g, '') + 1 === +at.killerTile.head.match(/(\d+)F$/)[1] && at.victimTile.cls.includes('is-dead') && at.victimTile.resp.includes(`FRAGGED BY ${K}`), { killerTile: at.killerTile, victimTile: at.victimTile });
  check(`event tic ${ev.tic}: killer POV shows frame ${best.frame} (expected ${k}; presented ${at.presented?.frame})`,
    Math.abs(best.frame - k) <= 2 && Math.abs(at.presented.frame - k) <= 2, { diffs });

  /* ---------- 4. focus and return without reload ---------- */
  await page.evaluate(() => { window.__noReload = 1; window.__videosBefore = [...document.querySelectorAll('video')]; });
  await page.evaluate(() => __dba.play());
  await sleep(500);
  const modes = [];
  const mode = () => page.evaluate(() => __dba.mode());
  const staged = () => page.evaluate(() => document.querySelector('[data-put="stage"] article')?.dataset.slot);
  for (let s = 1; s <= 8; s++) {
    await page.keyboard.press(String(s));
    const m = await mode();
    modes.push({ key: String(s), ...m, stage: await staged() });
  }
  await page.keyboard.press('Escape'); modes.push({ key: 'Escape', ...(await mode()) });
  await page.keyboard.press('m'); modes.push({ key: 'm', ...(await mode()) });
  await page.keyboard.press('m'); modes.push({ key: 'm', ...(await mode()) });
  await page.keyboard.press('m'); modes.push({ key: 'm', ...(await mode()) });
  await page.keyboard.press('Escape'); modes.push({ key: 'Escape', ...(await mode()) });
  await page.click('article[data-slot="6"]'); modes.push({ key: 'click tile 6', ...(await mode()) });
  await page.keyboard.press('m'); modes.push({ key: 'm from focus', ...(await mode()) });
  await page.keyboard.press('Escape'); modes.push({ key: 'Escape back to focus', ...(await mode()) });
  await page.click('[data-act="return"]'); modes.push({ key: 'click Return', ...(await mode()) });
  const mk = await page.evaluate(() => { const m = __dba.markers().find((x) => !x.dead); const r = document.querySelector('.mm-host canvas').getBoundingClientRect(); return m && { slot: m.slot, x: r.left + m.x, y: r.top + m.y }; });
  await page.mouse.click(mk.x, mk.y); modes.push({ key: `click map marker ${mk.slot}`, ...(await mode()) });
  await page.keyboard.press('Escape');
  const after = await page.evaluate(() => ({
    noReload: window.__noReload === 1, sameVideos: [...document.querySelectorAll('video')].every((v, i) => v === window.__videosBefore[i]),
    playing: __dba.playing(), paused: __dba.videos().filter((v) => v.paused).length, hash: location.hash,
  }));
  await page.evaluate(() => __dba.pause());
  results.focus = { modes, after };
  const expect = [...[1, 2, 3, 4, 5, 6, 7, 8].map((s) => ['focus', s]), ['multi', null], ['map', null], ['multi', null], ['map', null], ['multi', null],
    ['focus', 6], ['map', 6], ['focus', 6], ['multi', null], ['focus', mk.slot]];
  const modesOk = modes.every((m, i) => m.mode === expect[i][0] && (expect[i][0] === 'map' || m.focus === expect[i][1]) && (m.mode !== 'focus' || !m.stage || +m.stage === m.focus));
  check('focus/return: 1-8, ESC, M, tile click, map marker click, Return button; no reload, same video elements, still playing',
    modesOk && after.noReload && after.sameVideos && after.playing && after.paused === 0, { after });

  /* ---------- 6. schema 2 telemetry ---------- */
  const m2 = await page.evaluate(async () => {
    const get = async (f) => (await fetch(new URL(`sample/${__dba.bundle}/${f}`, location.href))).text();
    const m = JSON.parse(await get('match.json'));
    if (m.schema_version < 2) return null;
    const rows = (await get('tics.jsonl')).trim().split('\n').map(JSON.parse);
    const r = rows.find((x) => x.tic >= m.frame0_tic + 35 && x.alive && x.target_slot && x.weapon_name && x.sector != null);
    return r ? { r, target: m.bots[r.target_slot - 1] } : { none: true };
  });
  if (m2) {
    let tele = '';
    if (!m2.none) {
      await page.evaluate((t) => __dba.seek(t, false), m2.r.tic);
      await sleep(400);
      await page.keyboard.press(String(m2.r.slot));
      await sleep(400);
      tele = await page.evaluate(() => document.querySelector('.tele')?.innerText || '');
      await page.keyboard.press('Escape');
    }
    const r = m2.r || {}, name = String(r.weapon_name).replace(/_/g, ' ').toUpperCase(), tid = String(r.target_slot).padStart(2, '0');
    results.schema2 = { row: r, telemetry: tele };
    check(`schema 2 telemetry at tic ${r.tic}, bot ${r.slot}: weapon ${name}, target ${tid} ${m2.target?.code}, sector ${r.sector}`,
      !m2.none && tele.includes(name) && new RegExp(`TARGET\\s+${tid}\\s+${m2.target.code}`).test(tele)
        && new RegExp(`SECTOR\\s+${r.sector}\\b`).test(tele), { telemetry: tele });
  }

  /* ---------- 5. phone ---------- */
  const phone = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 3 });
  await phone.goto(url);
  await phone.waitForFunction(() => window.__dba && __dba.videos().every((v) => v.readyState >= 2), null, { timeout: 30000 });
  await phone.evaluate(() => __dba.seek(__dba.f0 + 300, false));
  await sleep(400);
  const phoneCheck = async (label) => phone.evaluate((label) => {
    const dock = document.querySelector('.ph-dock').getBoundingClientRect(), scroller = document.querySelector('.ph-scroll');
    const out = { label, dock: { top: dock.top, bottom: dock.bottom }, tiles: [] };
    for (const t of document.querySelectorAll('article[data-slot]')) {
      t.scrollIntoView({ block: 'nearest', inline: 'nearest' });
      const r = t.getBoundingClientRect(), hd = t.querySelector('.t-hd').getBoundingClientRect();
      const pts = [[hd.left + 3, hd.top + 3], [hd.right - 3, hd.top + 3], [hd.left + 3, hd.bottom - 3], [hd.right - 3, hd.bottom - 3], [(hd.left + hd.right) / 2, (hd.top + hd.bottom) / 2]];
      const covered = pts.filter(([x, y]) => !t.contains(document.elementFromPoint(x, y))).length;
      out.tiles.push({ slot: +t.dataset.slot, visible: r.top >= 0 && r.bottom <= dock.top + 0.5 && r.left >= 0 && r.right <= innerWidth + 0.5, covered });
    }
    if (scroller) { scroller.scrollTop = scroller.scrollHeight; out.dockAfterScroll = document.querySelector('.ph-dock').getBoundingClientRect().bottom; }
    out.pageScrollX = document.documentElement.scrollWidth > innerWidth;
    return out;
  }, label);
  const p1 = await phoneCheck('multiview');
  await phone.locator('article[data-slot="4"]').tap();
  await sleep(300);
  const p2 = await phoneCheck('focus 04');
  await phone.screenshot({ path: join(args.out ? dirname(args.out) : here, 'phone-focus.png') });
  results.phone = [p1, p2];
  const phoneOk = [p1, p2].every((p) => p.tiles.length === 8 && p.tiles.every((t) => t.visible && t.covered === 0) && Math.abs(p.dock.bottom - 844) < 1
    && (p.dockAfterScroll == null || Math.abs(p.dockAfterScroll - 844) < 1) && !p.pageScrollX);
  check('phone 390x844: all 8 tiles reachable above a pinned dock, no nameplate covered (multiview and focus)', phoneOk, {});

  results.pageErrors = errors;
  check('no page errors', errors.length === 0, { errors });
} finally {
  await browser.close();
  if (server) server.kill();
}
if (args.out) writeFileSync(args.out, JSON.stringify(results, null, 2));
const failed = results.checks.filter((c) => !c.pass).length;
console.log(failed ? `${failed} check(s) failed` : 'all checks passed');
process.exit(failed ? 1 : 0);
