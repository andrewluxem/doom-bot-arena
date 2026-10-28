/* Doom Bot Arena — shared spectator renderer. Plain functions returning HTML strings; assigns window.DBA. Ground-truth snapshot frozen at 02:14 of 05:00. */
(function () {
  var NOW = 134, LEN = 300;
  var BOTS = [
    { id: '01', cs: 'VEX', name: 'Vex', role: 'Rusher', f: 14, d: 4, hp: 22, ar: 15, wp: 'Shotgun', st: 'combat', lab: 'IN COMBAT', tag: 'DUEL ▸ 02 KRN', beh: 'ENGAGING 02 KRN', zone: 'Central Hall', x: 182, y: 150, a: 0, opp: '02' },
    { id: '02', cs: 'KRN', name: 'Kronos', role: 'Tactician', f: 11, d: 3, hp: 38, ar: 50, wp: 'Rocket Launcher', st: 'combat', lab: 'IN COMBAT', tag: 'DUEL ▸ 01 VEX', beh: 'ENGAGING 01 VEX', zone: 'Central Hall', x: 220, y: 150, a: 180, opp: '01' },
    { id: '03', cs: 'NUL', name: 'Null', role: 'Ambusher', f: 9, d: 6, hp: 100, ar: 100, wp: 'Rocket Launcher', st: 'stalk', lab: 'STALKING', tag: 'STALKING', beh: 'STALKING N DOOR', zone: 'North Corridor', x: 176, y: 76, a: 68 },
    { id: '04', cs: 'ASH', name: 'Ash', role: 'Berserker', f: 8, d: 9, hp: 0, ar: 0, wp: '—', st: 'dead', lab: 'DEAD', tag: 'DEAD', beh: 'RESPAWN IN 2.4s', zone: 'West Door', x: 122, y: 170, a: 0, resp: 2.4, killer: '01', killedAt: '02:11' },
    { id: '05', cs: 'ORB', name: 'Orbit', role: 'Kiter', f: 7, d: 5, hp: 85, ar: 25, wp: 'Plasma Rifle', st: 'roam', lab: 'ROAMING', tag: 'ROAMING', beh: 'ROAMING NE DECK', zone: 'NE Deck', x: 332, y: 58, a: 205 },
    { id: '06', cs: 'HEX', name: 'Hex', role: 'Camper', f: 6, d: 2, hp: 100, ar: 75, wp: 'Chaingun', st: 'hold', lab: 'HOLDING ANGLE', tag: 'HOLDING ANGLE', beh: 'HOLDING ANGLE → HALL', zone: 'SE Nest', x: 360, y: 246, a: -150 },
    { id: '07', cs: 'PYR', name: 'Pyre', role: 'Flanker', f: 5, d: 11, hp: 64, ar: 40, wp: 'Super Shotgun', st: 'armor', lab: 'COLLECTING ARMOR', tag: 'ARMOR RUN', beh: 'COLLECTING ARMOR', zone: 'NW Armory', x: 56, y: 58, a: 120 },
    { id: '08', cs: 'ZED', name: 'Zed', role: 'Erratic', f: 2, d: 12, hp: 90, ar: 0, wp: 'Chainsaw', st: 'hunt', lab: 'HUNTING', tag: 'HUNTING', beh: 'HUNTING — NO TARGET', zone: 'South Corridor', x: 214, y: 258, a: -75 }
  ];
  var BY = {}; BOTS.forEach(function (b) { BY[b.id] = b; });
  function col(id) { return 'var(--bot-' + id + ')'; }
  function hot(b) { return b.st === 'combat'; }
  function crit(b) { return b.hp > 0 && b.hp <= 25; }
  function dead(b) { return b.st === 'dead'; }
  function fmt(t) { var m = Math.floor(t / 60), s = Math.floor(t % 60); return (m < 10 ? '0' : '') + m + ':' + (s < 10 ? '0' : '') + s; }

  /* ---------- frag events (deterministic) ---------- */
  var FIXED = [
    { t: 131.6, k: '01', v: '04', w: 'Shotgun' }, { t: 123.2, k: '02', v: '08', w: 'Chaingun' },
    { t: 118.4, k: '06', v: '07', w: 'Chaingun' }, { t: 116.1, k: '03', v: '08', w: 'Rocket' },
    { t: 112.7, k: '01', v: '07', w: 'Shotgun' }
  ];
  var EVENTS = (function () {
    var seed = 0x2b7e1516;
    function rnd() { seed |= 0; seed = seed + 0x6D2B79F5 | 0; var t = Math.imul(seed ^ seed >>> 15, 1 | seed); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }
    var pool = [];
    BOTS.forEach(function (b) { var n = b.f - FIXED.filter(function (e) { return e.k === b.id; }).length; for (var i = 0; i < n; i++) pool.push(b.id); });
    for (var i = pool.length - 1; i > 0; i--) { var j = Math.floor(rnd() * (i + 1)); var tmp = pool[i]; pool[i] = pool[j]; pool[j] = tmp; }
    var bursts = [[19, 3.5, 9], [46, 4, 11], [71, 3, 8], [96, 4.5, 13], [111, 2.5, 9]];
    var times = [];
    bursts.forEach(function (bu) { for (var i = 0; i < bu[2]; i++) times.push(bu[0] + (rnd() * 2 - 1) * bu[1]); });
    while (times.length < pool.length) times.push(4 + rnd() * 122);
    var ev = pool.map(function (k, i) { var v; do { v = BOTS[Math.floor(rnd() * 8)].id; } while (v === k); return { t: times[i], k: k, v: v, w: BY[k].wp }; });
    return ev.concat(FIXED).sort(function (a, b) { return a.t - b.t; });
  })();

  /* ---------- feed placeholder ---------- */
  function feedSvg(markHot) {
    var s = '<svg class="xh" viewBox="0 0 320 240" preserveAspectRatio="none" aria-hidden="true">';
    for (var x = 40; x < 320; x += 40) s += '<line class="xh-grid" x1="' + x + '" y1="0" x2="' + x + '" y2="240"/>';
    for (var y = 40; y < 240; y += 40) s += '<line class="xh-grid" x1="0" y1="' + y + '" x2="320" y2="' + y + '"/>';
    s += '<g class="xh-mark">';
    s += '<path d="M138 120H153M167 120H182M160 98V113M160 127V142"/>';
    s += '<path d="M12 28V12H28M292 12H308V28M308 212V228H292M28 228H12V212"/>';
    s += '</g><circle class="xh-dot" cx="160" cy="120" r="1.6"/></svg>';
    return s;
  }
  function feed(b, o) {
    o = o || {};
    if (dead(b)) {
      var pct = Math.round((1 - b.resp / 4.8) * 100);
      return '<div class="feed feed--dead">' + feedSvg() +
        '<div class="resp"><span class="resp-k">NO SIGNAL · RESPAWN IN</span><span class="resp-v">' + b.resp.toFixed(1) + '<small>s</small></span>' +
        '<span class="resp-bar"><i style="width:' + pct + '%"></i></span><span class="resp-by">FRAGGED BY ' + b.killer + ' ' + BY[b.killer].cs + ' · ' + b.killedAt + '</span></div></div>';
    }
    if (o.onstage) return '<div class="feed feed--onstage">' + feedSvg() + '<span class="onstage">ON STAGE ↗</span></div>';
    return '<div class="feed">' + feedSvg() + '<span class="f-meta f-tl">CAM ' + b.id + ' · POV</span><span class="f-meta f-br">320×240</span></div>';
  }
  function hpBar(b) { return '<span class="hp"><i style="width:' + b.hp + '%"></i></span>'; }

  /* ---------- tile (A / B) ---------- */
  function tile(id, o) {
    o = o || {}; var b = BY[id], size = o.size || 'm';
    var cls = 'tile tile--' + size + (hot(b) ? ' is-hot' : '') + (crit(b) ? ' is-crit' : '') + (dead(b) ? ' is-dead' : '') + (o.onstage ? ' is-onstage' : '') + (o.cls ? ' ' + o.cls : '');
    var h = '<article class="' + cls + '" style="--c:' + col(id) + '" data-bot="' + id + '" tabindex="0" aria-label="' + id + ' ' + b.name + '">';
    h += '<header class="t-hd"><span class="sw"></span><span class="t-id">' + id + '</span><span class="t-nm">' + b.name + '</span>';
    if (o.sector) h += '<span class="t-sec">' + o.sector + '</span>';
    h += '<span class="t-st">' + (dead(b) ? 'DEAD' : (o.onstage ? 'ON STAGE' : b.tag)) + '</span>';
    h += '<span class="t-fr">' + b.f + '<em>F</em></span></header>';
    h += feed(b, o);
    h += '<footer class="t-ft">';
    if (dead(b)) h += '<span class="t-hpv">HP 0</span><span class="hp"></span><span class="t-wp">RESPAWNING</span>';
    else h += '<span class="t-hpv">HP ' + b.hp + '</span>' + hpBar(b) + (crit(b) ? '<span class="crit">CRIT</span>' : '') + '<span class="t-wp">' + b.wp + '</span>';
    h += '</footer></article>';
    return h;
  }

  /* ---------- tile (C editorial) ---------- */
  function ctile(id, o) {
    o = o || {}; var b = BY[id];
    var cls = 'ctile' + (o.stack ? ' ctile--stack' : '') + (hot(b) ? ' is-hot' : '') + (crit(b) ? ' is-crit' : '') + (dead(b) ? ' is-dead' : '');
    var h = '<article class="' + cls + '" style="--c:' + col(id) + '" data-bot="' + id + '" tabindex="0">';
    h += '<div class="c-lab"><div class="c-top"><span class="c-num">' + id + '</span><span class="c-fr"><b>' + b.f + '<small>F</small></b><i>' + b.d + ' D</i></span></div>';
    h += '<div class="c-nm"><span class="sw"></span>' + b.name + '<span class="c-role">' + b.role + '</span></div>';
    h += '<div class="c-st">' + (dead(b) ? 'DEAD · ' + b.resp.toFixed(1) + 's' : b.tag) + '</div>';
    h += '<div class="c-hp">' + (dead(b) ? '<span class="hp"></span><span class="c-hpv">0</span>' : hpBar(b) + '<span class="c-hpv">' + b.hp + (crit(b) ? ' <span class="crit">CRIT</span>' : '') + '</span>') + '</div>';
    if (!o.stack) h += '<div class="c-wp">' + b.wp + '</div>';
    h += '</div>' + feed(b) + '</article>';
    return h;
  }

  /* ---------- minimap ---------- */
  var WALLS = [
    [110, 10, 110, 48], [110, 74, 110, 110], [10, 110, 46, 110], [74, 110, 110, 110],
    [140, 40, 260, 40],
    [290, 10, 290, 46], [290, 72, 316, 98], [316, 98, 346, 98], [364, 98, 390, 98],
    [165, 110, 186, 110], [214, 110, 235, 110], [235, 110, 265, 140], [265, 160, 235, 190], [235, 190, 214, 190], [186, 190, 165, 190], [165, 190, 135, 160], [135, 140, 165, 110],
    [10, 210, 52, 210], [52, 210, 52, 236], [86, 246, 120, 246],
    [150, 228, 188, 228], [212, 228, 250, 228], [150, 228, 150, 262], [250, 228, 250, 262],
    [300, 206, 342, 206], [342, 206, 342, 222], [300, 206, 284, 222]
  ];
  var SECTORS = [[36, 26, 'NW ARMORY'], [146, 30, 'N CORRIDOR'], [298, 26, 'NE DECK'], [200, 182, 'CENTRAL HALL', 'middle'], [18, 128, 'W PILLARS'], [382, 128, 'E PILLARS', 'end'], [36, 282, 'SW YARD'], [200, 282, 'S CORRIDOR', 'middle'], [362, 282, 'SE NEST', 'end']];
  function cone(b, R, half) {
    var a1 = (b.a - half) * Math.PI / 180, a2 = (b.a + half) * Math.PI / 180;
    return 'M' + b.x + ' ' + b.y + 'L' + (b.x + R * Math.cos(a1)).toFixed(1) + ' ' + (b.y + R * Math.sin(a1)).toFixed(1) +
      'A' + R + ' ' + R + ' 0 0 1 ' + (b.x + R * Math.cos(a2)).toFixed(1) + ' ' + (b.y + R * Math.sin(a2)).toFixed(1) + 'Z';
  }
  function minimap(o) {
    o = o || {};
    var ls = o.ls || 12, r = o.r || 5, R = o.cone || 46, ids = o.ids !== false, cs = !!o.callsigns;
    var s = '<svg class="mm" viewBox="0 0 400 300" role="img" aria-label="Arena map: 7 living bots, 01 and 02 dueling in the central hall">';
    s += '<rect class="mm-bg" width="400" height="300"/>';
    for (var x = 20; x < 400; x += 20) s += '<line class="mm-grid" x1="' + x + '" y1="0" x2="' + x + '" y2="300"/>';
    for (var y = 20; y < 300; y += 20) s += '<line class="mm-grid" x1="0" y1="' + y + '" x2="400" y2="' + y + '"/>';
    s += '<polygon class="mm-wall" points="30,10 370,10 390,30 390,270 370,290 30,290 10,270 10,30"/>';
    s += '<path class="mm-wall" d="' + WALLS.map(function (w) { return 'M' + w[0] + ' ' + w[1] + 'L' + w[2] + ' ' + w[3]; }).join('') + 'M62 140H84V162H62ZM316 140H338V162H316Z"/>';
    if (o.sectors) SECTORS.forEach(function (q) { s += '<text class="mm-sec" x="' + q[0] + '" y="' + q[1] + '" text-anchor="' + (q[3] || 'start') + '" style="font-size:' + (o.secSize || 7) + 'px">' + q[2] + '</text>'; });
    s += '<path class="mm-pickup" d="M40 78L45 84L40 90L35 84Z"/>';
    BOTS.forEach(function (b) { if (!dead(b)) s += '<path class="mm-cone" style="fill:' + col(b.id) + ';stroke:' + col(b.id) + '" d="' + cone(b, R, 30) + '"/>'; });
    BOTS.forEach(function (b) {
      if (dead(b)) {
        var k = r * 1.1;
        s += '<g class="mm-x" data-dot="' + b.id + '" transform="translate(' + b.x + ' ' + b.y + ')"><path d="M' + (-k) + ' ' + (-k) + 'L' + k + ' ' + k + 'M' + k + ' ' + (-k) + 'L' + (-k) + ' ' + k + '"/></g>';
        if (ids) s += '<text class="mm-id mm-id--dead" x="' + (b.x - r - 3) + '" y="' + (b.y + ls * 0.35) + '" text-anchor="end" style="font-size:' + ls + 'px">' + b.id + (cs ? ' ' + b.cs : '') + ' ×</text>';
        return;
      }
      s += '<circle class="mm-dot" data-dot="' + b.id + '" cx="' + b.x + '" cy="' + b.y + '" r="' + r + '" style="fill:' + col(b.id) + '"/>';
      if (ids) {
        var up = b.id === '01' || b.id === '02' || b.y > 240;
        var lx = b.id === '01' ? b.x - 2 : b.id === '02' ? b.x + 2 : b.x + r + 3;
        var anchor = b.id === '01' ? 'end' : 'start';
        var ly = up ? b.y - r - 5 : b.y + ls * 0.35;
        if (b.id === '01' || b.id === '02') ly = b.y - r - 5;
        if (b.x > 340) { anchor = 'end'; lx = b.x - r - 3; }
        s += '<text class="mm-id" x="' + lx + '" y="' + ly + '" text-anchor="' + anchor + '" style="font-size:' + ls + 'px;fill:' + col(b.id) + '">' + b.id + (cs ? ' ' + b.cs : '') + '</text>';
      }
    });
    s += '</svg>';
    return s;
  }
  function mapPanel(o) {
    o = o || {};
    var h = '<div class="mm-panel' + (o.cls ? ' ' + o.cls : '') + '" data-pinned="bottom-right">';
    h += '<div class="mm-hd"><span>ARENA' + (o.expanded ? ' · EXPANDED' : '') + '</span>' + (o.hint === false ? '' : '<span class="mm-hint">' + (o.expanded ? 'COLLAPSE ↘ <kbd>M</kbd>' : (o.tap ? 'TAP ⤢' : '⤢ <kbd>M</kbd>')) + '</span>') + '</div>';
    h += minimap(o) + '</div>';
    return h;
  }

  /* ---------- scoreboard ---------- */
  function scoreStrip() {
    return '<ol class="sb-strip" aria-label="Standings by frags">' + BOTS.map(function (b, i) {
      return '<li class="sb-cell' + (hot(b) ? ' is-hot' : '') + (dead(b) ? ' is-dead' : '') + '" style="--c:' + col(b.id) + '"><span class="sb-rk">' + (i + 1) + '</span><span class="sw"></span><span class="sb-id">' + b.id + '</span><span class="sb-nm">' + b.name + '</span><span class="sb-f">' + b.f + '<em>F</em></span><span class="sb-d">' + b.d + '<em>D</em></span></li>';
    }).join('') + '</ol>';
  }
  function scoreTable(o) {
    o = o || {};
    var h = '<table class="sb-table' + (o.cls ? ' ' + o.cls : '') + '"><thead><tr><th class="c-rk">#</th><th class="c-idh">ID</th><th class="c-nmh">Name</th><th class="c-n">F</th><th class="c-n">D</th></tr></thead><tbody>';
    BOTS.forEach(function (b, i) {
      h += '<tr class="' + (hot(b) ? 'is-hot' : '') + (dead(b) ? ' is-dead' : '') + '" style="--c:' + col(b.id) + '"><td class="c-rk">' + (i + 1) + '</td><td class="c-idc"><span class="sw"></span>' + b.id + '</td><td class="c-nmc">' + b.name + (o.state ? '<span class="sb-state">' + b.lab + '</span>' : '') + '</td><td class="c-n c-f">' + b.f + '</td><td class="c-n">' + b.d + '</td></tr>';
    });
    return h + '</tbody></table>';
  }
  function scoreColumn() {
    var h = '<ol class="sb-col">';
    BOTS.forEach(function (b, i) {
      h += '<li class="' + (hot(b) ? 'is-hot' : '') + (dead(b) ? ' is-dead' : '') + '" style="--c:' + col(b.id) + '"><span class="scl-rk">' + (i + 1) + '</span><span class="scl-who"><span class="scl-id"><span class="sw"></span>' + b.id + ' ' + b.cs + '</span><span class="scl-nm">' + b.name + '</span><span class="scl-role">' + b.role + ' · ' + b.lab + '</span></span><span class="scl-f">' + b.f + '</span><span class="scl-d">' + b.d + '</span></li>';
    });
    return h + '</ol>';
  }

  /* ---------- timeline scrubber ---------- */
  function timeline(o) {
    o = o || {};
    var WIN = o.window || 150, N = o.buckets || 100, stackH = o.stack || 4, maxStack = 0, buckets = {};
    var h = '<div class="tl' + (o.compact ? ' tl--compact' : '') + '">';
    if (!o.noHead) h += '<div class="tl-hd"><span class="tl-k">FRAG TIMELINE</span><span class="tl-sub">' + EVENTS.length + ' FRAGS · WINDOW 00:00–' + fmt(WIN) + '</span></div>';
    h += '<div class="tl-lane" style="--sh:' + stackH + 'px">';
    EVENTS.forEach(function (e) {
      var k = Math.round(e.t / WIN * N); buckets[k] = (buckets[k] || 0) + 1; var lvl = buckets[k] - 1; if (lvl > maxStack) maxStack = lvl;
      h += '<i class="tl-tick" style="left:' + (k / N * 100).toFixed(2) + '%;bottom:' + (lvl * stackH) + 'px;width:calc(' + (100 / N).toFixed(3) + '% - 1px);background:' + col(e.k) + '" title="' + fmt(e.t) + ' ' + BY[e.k].cs + ' ✕ ' + BY[e.v].cs + '"></i>';
    });
    h += '<span class="tl-future" style="left:' + (NOW / WIN * 100).toFixed(2) + '%"></span>';
    h += '<span class="tl-head" style="left:' + (NOW / WIN * 100).toFixed(2) + '%"><b>' + fmt(NOW) + '</b></span>';
    h += '</div><div class="tl-axis">';
    for (var t = 0; t <= WIN; t += 30) h += '<span style="left:' + (t / WIN * 100) + '%">' + fmt(t) + '</span>';
    h += '</div>';
    if (!o.noOverview) {
      h += '<div class="tl-ov" aria-label="Full match 00:00 to 05:00"><span class="tl-ov-fill" style="width:' + (NOW / LEN * 100).toFixed(2) + '%"></span><span class="tl-ov-win" style="width:' + (WIN / LEN * 100).toFixed(2) + '%"></span>';
      for (var m = 1; m * 60 < LEN; m++) h += '<span class="tl-ov-m" style="left:' + (m * 60 / LEN * 100) + '%"></span>';
      h += '<span class="tl-ov-head" style="left:' + (NOW / LEN * 100).toFixed(2) + '%"></span></div><div class="tl-ov-axis"><span style="left:0">00:00</span><span style="left:' + (NOW / LEN * 100).toFixed(2) + '%">LIVE</span><span style="left:100%">05:00</span></div>';
    }
    return h + '</div>';
  }

  /* ---------- kill feed ---------- */
  function killfeed(n) {
    var list = EVENTS.slice(-n).reverse();
    return '<ol class="kf">' + list.map(function (e) {
      return '<li><span class="kf-t">' + fmt(e.t) + '</span><span class="kf-k" style="--c:' + col(e.k) + '"><span class="sw"></span>' + e.k + ' ' + BY[e.k].cs + '</span><span class="kf-x">✕</span><span class="kf-v" style="--c:' + col(e.v) + '"><span class="sw"></span>' + e.v + ' ' + BY[e.v].cs + '</span><span class="kf-w">' + e.w + '</span></li>';
    }).join('') + '</ol>';
  }

  /* ---------- telemetry ---------- */
  function telemetry(id, o) {
    o = o || {}; var b = BY[id], opp = b.opp ? BY[b.opp] : null;
    var btn = '<button class="btn-ret' + (o.compact ? ' btn-ret--sm' : '') + '" type="button">\u2190 Return to Multiview <kbd>ESC</kbd></button>';
    var h = '<section class="tele' + (o.compact ? ' tele--compact' : '') + '" style="--c:' + col(id) + '" aria-label="Telemetry ' + id + ' ' + b.name + '">';
    h += '<header class="tele-hd"><span class="sw"></span><span class="tele-id">' + id + '</span><span class="tele-nm">' + b.name + '</span><span class="tele-role">' + b.role + '</span>' + (o.compact ? btn : '<span class="tele-rank">RANK ' + (BOTS.indexOf(b) + 1) + '/8</span>') + '</header>';
    h += '<div class="tele-beh"><span class="tele-k">BEHAVIOR</span><span class="tele-chip">' + b.beh + '</span></div>';
    h += '<dl class="tele-grid">';
    h += '<div class="tg tg-hp"><dt>HP</dt><dd>' + b.hp + '<small>/100</small>' + (crit(b) ? ' <span class="crit">CRIT</span>' : '') + '</dd>' + hpBar(b) + '</div>';
    h += '<div class="tg"><dt>ARMOR</dt><dd>' + b.ar + '<small>/100</small></dd><span class="hp hp--ar"><i style="width:' + b.ar + '%"></i></span></div>';
    h += '<div class="tg"><dt>WEAPON</dt><dd>' + b.wp + '</dd></div>';
    h += '<div class="tg"><dt>FRAGS / DEATHS</dt><dd>' + b.f + ' <small>/</small> ' + b.d + '</dd></div>';
    h += '<div class="tg"><dt>PERSONALITY</dt><dd>' + b.role + '</dd></div>';
    h += '<div class="tg"><dt>ZONE</dt><dd>' + b.zone + '</dd></div>';
    if (opp) h += '<div class="tg tg-wide"><dt>TARGET</dt><dd><span class="sw" style="--c:' + col(opp.id) + '"></span>' + opp.id + ' ' + opp.name + ' · HP ' + opp.hp + ' · ' + opp.wp + ' · 38u</dd></div>';
    h += '</dl>';
    if (!o.compact) {
      var mine = EVENTS.filter(function (e) { return e.k === id; }).slice(-4).reverse();
      h += '<div class="tele-log"><span class="tele-k">RECENT FRAGS \u00B7 ' + b.cs + '</span><ol class="kf">' + mine.map(function (e) {
        return '<li><span class="kf-t">' + fmt(e.t) + '</span><span class="kf-k" style="--c:' + col(e.k) + '"><span class="sw"></span>' + e.k + ' ' + BY[e.k].cs + '</span><span class="kf-x">\u2715</span><span class="kf-v" style="--c:' + col(e.v) + '"><span class="sw"></span>' + e.v + ' ' + BY[e.v].cs + '</span><span class="kf-w">' + e.w + '</span></li>';
      }).join('') + '</ol></div>';
      h += btn;
    }
    return h + '</section>';
  }

  /* ---------- header bits ---------- */
  function clock(o) {
    o = o || {};
    return '<div class="clk' + (o.cls ? ' ' + o.cls : '') + '"><span class="live"><i></i>LIVE</span><span class="clk-t">' + fmt(NOW) + '</span><span class="clk-of">/ 05:00</span></div>';
  }

  /* B: draw tethers from each flank tile to its map dot (call after layout) */
  function tethers(root) {
    var svg = root.querySelector('.tethers'); if (!svg) return;
    var rr = root.getBoundingClientRect(), sc = rr.width / root.offsetWidth || 1, out = '';
    svg.setAttribute('viewBox', '0 0 ' + root.offsetWidth + ' ' + root.offsetHeight);
    root.querySelectorAll('[data-tether]').forEach(function (t) {
      var id = t.getAttribute('data-bot'), dot = root.querySelector('[data-dot="' + id + '"]'); if (!dot) return;
      var tr = t.querySelector('.feed').getBoundingClientRect(), dr = dot.getBoundingClientRect();
      var left = t.getAttribute('data-tether') === 'l';
      var x1 = ((left ? tr.right : tr.left) - rr.left) / sc, y1 = (tr.top + tr.height / 2 - rr.top) / sc;
      var x2 = (dr.left + dr.width / 2 - rr.left) / sc, y2 = (dr.top + dr.height / 2 - rr.top) / sc;
      var xe = x1 + (left ? 18 : -18);
      out += '<path class="teth' + (dead(BY[id]) ? ' teth--dead' : '') + '" style="stroke:' + col(id) + '" d="M' + x1.toFixed(1) + ' ' + y1.toFixed(1) + 'H' + xe.toFixed(1) + 'L' + x2.toFixed(1) + ' ' + y2.toFixed(1) + '"/><circle class="teth-end" style="fill:' + col(id) + '" cx="' + x1.toFixed(1) + '" cy="' + y1.toFixed(1) + '" r="2.5"/>';
    });
    svg.innerHTML = out;
  }

  window.DBA = { NOW: NOW, BOTS: BOTS, BY: BY, EVENTS: EVENTS, fmt: fmt, tile: tile, ctile: ctile, feed: feed, minimap: minimap, mapPanel: mapPanel, scoreStrip: scoreStrip, scoreTable: scoreTable, scoreColumn: scoreColumn, timeline: timeline, killfeed: killfeed, telemetry: telemetry, clock: clock, tethers: tethers };
})();
