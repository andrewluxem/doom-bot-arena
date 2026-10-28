/* Loads a schema 1 to 5 match bundle (docs/MATCH_FORMAT.md) into per-slot typed arrays.
   The viewer shows only what the bundle records: no positions, targets or sectors are guessed.
   Schema 2 rows add weapon_name, target_slot and sector, and spots.json lists the spots bots chose. */

export const BEHAVIORS = ['IN_COMBAT', 'HUNTING', 'STALKING', 'HOLDING_ANGLE', 'ROAMING', 'COLLECTING_ARMOR',
  'COLLECTING_HEALTH', 'FLEEING', 'KITING', 'DEAD', 'RESPAWNING'];
export const DEATH_TYPES = new Set(['frag', 'telefrag', 'suicide', 'environmental_death']);

/* characters/README.md: engine weapon slot -> weapons. Slots 1 and 3 hold two, so the slot alone does not say which. */
const WEAPONS = { 1: 'FIST / CHAINSAW', 2: 'PISTOL', 3: 'SHOTGUN / SSG', 4: 'CHAINGUN', 5: 'ROCKET LAUNCHER', 6: 'PLASMA RIFLE', 7: 'BFG' };
const WEAPONS_SHORT = { 1: 'FIST/SAW', 2: 'PISTOL', 3: 'SG/SSG', 4: 'CHAINGUN', 5: 'ROCKET', 6: 'PLASMA', 7: 'BFG' };
export function weaponLabel(slot, short) {
  return (short ? WEAPONS_SHORT : WEAPONS)[slot] || 'SLOT ' + slot;
}
/* Schema 2 weapon_name values (characters/README.md), stored per tic as an index; -1 is null. */
export const WEAPON_NAMES = ['fist', 'chainsaw', 'pistol', 'shotgun', 'super_shotgun', 'chaingun', 'rocket_launcher', 'plasma_rifle', 'bfg'];
const NAME_LABEL = { fist: 'FIST', chainsaw: 'CHAINSAW', pistol: 'PISTOL', shotgun: 'SHOTGUN', super_shotgun: 'SUPER SHOTGUN',
  chaingun: 'CHAINGUN', rocket_launcher: 'ROCKET LAUNCHER', plasma_rifle: 'PLASMA RIFLE', bfg: 'BFG' };
const NAME_SHORT = { fist: 'FIST', chainsaw: 'SAW', pistol: 'PISTOL', shotgun: 'SHOTGUN', super_shotgun: 'SSG',
  chaingun: 'CHAINGUN', rocket_launcher: 'ROCKET', plasma_rifle: 'PLASMA', bfg: 'BFG' };
export function weaponNameLabel(name, short) {
  return (short ? NAME_SHORT : NAME_LABEL)[name] || String(name).toUpperCase();
}

/* Obituary key (engine/obituaries.py) -> what did the killing, for the kill feed. */
const KEY_LABEL = {
  OB_MPFIST: 'FIST', OB_MPCHAINSAW: 'CHAINSAW', OB_MPPISTOL: 'PISTOL', OB_MPSHOTGUN: 'SHOTGUN',
  OB_MPSSHOTGUN: 'SUPER SHOTGUN', OB_MPCHAINGUN: 'CHAINGUN', OB_MPROCKET: 'ROCKET', OB_MPR_SPLASH: 'ROCKET SPLASH',
  OB_MPPLASMARIFLE: 'PLASMA', OB_MPBFG_BOOM: 'BFG', OB_MPBFG_SPLASH: 'BFG SPLASH', OB_MPBFG_MBF: 'BFG',
  OB_RAILGUN: 'RAILGUN', OB_GRENADE: 'GRENADE', OB_BARREL: 'BARREL', OB_MPTELEFRAG: 'TELEFRAG',
  OB_MONTELEFRAG: 'TELEFRAG', OB_KILLEDSELF: 'OWN SPLASH', OB_EXIT: 'EXIT', OB_SUICIDE: 'SUICIDE',
  OB_FALLING: 'FALL', OB_CRUSH: 'CRUSHED', OB_WATER: 'WATER', OB_SLIME: 'SLIME', OB_LAVA: 'LAVA', OB_DEFAULT: 'UNKNOWN',
};
export function keyLabel(key) {
  return KEY_LABEL[key] || String(key || '').replace(/^OB_(MP)?/, '').replace(/_/g, ' ');
}

async function get(url, as) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return as === 'json' ? r.json() : r.text();
}

export async function loadIndex(url) {
  const index = await get(url, 'json');
  if (!index.bundles || !index.bundles.length) throw new Error('index.json lists no bundles');
  return index;
}

/* Last index i with list[i].tic <= tic, or -1. */
function lastAtOrBefore(list, tic) {
  let lo = 0, hi = list.length - 1, at = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (list[mid].tic <= tic) { at = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return at;
}

export async function loadBundle(base) {
  const url = (p) => new URL(p, base).href;
  const [match, map, ticsText, eventsText] = await Promise.all([
    get(url('match.json'), 'json'), get(url('map.json'), 'json'), get(url('tics.jsonl')), get(url('events.jsonl')),
  ]);
  if (![1, 2, 3, 4, 5].includes(match.schema_version)) throw new Error(`schema_version ${match.schema_version} is not supported`);
  const spots = match.schema_version >= 2 ? await get(url('spots.json'), 'json') : null;
  const f0 = match.frame0_tic, n = match.duration_tics, total = f0 + n, rate = match.ticrate;
  const respawnAfter = rate * match.respawn_delay_s + 1; // a death at tic d respawns at d + 35·delay + 1
  const bots = match.bots.map((b) => ({ ...b, id: String(b.slot).padStart(2, '0') }));
  const slots = bots.map((b) => b.slot);

  const tel = {};
  for (const s of slots) {
    tel[s] = {
      x: new Float32Array(total), y: new Float32Array(total), angle: new Float32Array(total),
      health: new Int32Array(total), armor: new Int32Array(total), weapon: new Int8Array(total),
      alive: new Uint8Array(total), behavior: new Uint8Array(total), weaponName: null, target: null, sector: null,
    };
  }
  const recorded = { weaponName: false, target: false, sector: false };
  let rows = 0;
  for (const line of ticsText.split('\n')) {
    if (!line) continue;
    const r = JSON.parse(line), t = tel[r.slot];
    if (!t || !(r.tic >= 0 && r.tic < total)) throw new Error(`tics.jsonl row out of range: ${line}`);
    const beh = BEHAVIORS.indexOf(r.behavior);
    if (beh < 0) throw new Error(`tics.jsonl behavior ${r.behavior} is not in characters/README.md`);
    t.x[r.tic] = r.x; t.y[r.tic] = r.y; t.angle[r.tic] = r.angle;
    t.health[r.tic] = r.health; t.armor[r.tic] = r.armor; t.weapon[r.tic] = r.weapon;
    t.alive[r.tic] = r.alive ? 1 : 0; t.behavior[r.tic] = beh;
    if ('weapon_name' in r) {
      recorded.weaponName = true;
      (t.weaponName ||= new Int8Array(total).fill(-1))[r.tic] = r.weapon_name == null ? -1 : WEAPON_NAMES.indexOf(r.weapon_name);
    }
    if ('target_slot' in r) {
      recorded.target = true;
      (t.target ||= new Uint8Array(total))[r.tic] = r.target_slot || 0; // 0 = no target
    }
    if ('sector' in r) {
      recorded.sector = true;
      (t.sector ||= new Int16Array(total).fill(-1))[r.tic] = r.sector == null ? -1 : r.sector;
    }
    rows++;
  }
  if (rows !== total * slots.length) throw new Error(`tics.jsonl has ${rows} rows, expected ${total * slots.length}`);

  const events = eventsText.split('\n').filter(Boolean).map(JSON.parse);
  const deaths = events.filter((e) => DEATH_TYPES.has(e.type)).map((e, i) => ({
    ...e, i, victim: e.target, killer: e.type === 'environmental_death' ? null : e.actor,
    respawnTic: e.tic + respawnAfter, x: tel[e.target].x[e.tic], y: tel[e.target].y[e.tic],
  }));
  const deathsBySlot = Object.fromEntries(slots.map((s) => [s, deaths.filter((d) => d.victim === s)]));
  // Pre-roll events stay in the bundle but are never shown (docs/MATCH_FORMAT.md section 3).
  const shown = deaths.filter((d) => d.tic >= f0);

  // Score columns per match.json.score.rule, applied cumulatively so the board is right at every tic.
  const zero = () => Object.fromEntries(slots.map((s) => [s, { kills: 0, telefrags: 0, suicides: 0, env: 0, deaths: 0 }]));
  const snaps = [zero()];
  for (const d of shown) {
    const next = structuredClone(snaps[snaps.length - 1]);
    if (d.type === 'frag' && d.credited) next[d.actor].kills++;
    if (d.type === 'telefrag' && d.credited) next[d.actor].telefrags++;
    if (d.type === 'suicide') next[d.actor].suicides++;
    if (d.type === 'environmental_death') next[d.victim].env++;
    next[d.victim].deaths++;
    snaps.push(next);
  }
  const scoreAt = (tic) => {
    const s = snaps[lastAtOrBefore(shown, tic) + 1];
    return Object.fromEntries(slots.map((k) => [k, { ...s[k], frags: s[k].kills - s[k].suicides }]));
  };
  const final = scoreAt(total - 1);
  const scoreCheck = match.score.rows.flatMap((r) => {
    const f = final[r.slot];
    const want = { displayed_frags: f.frags, displayed_deaths: f.deaths, kills: f.kills, telefrags: f.telefrags,
      suicides: f.suicides, environmental_deaths: f.env };
    return Object.entries(want).filter(([k, v]) => r[k] !== v).map(([k, v]) => `slot ${r.slot} ${k}: bundle ${r[k]}, viewer ${v}`);
  });
  if (scoreCheck.length) console.error('score rule mismatch', scoreCheck);

  const engine = Object.fromEntries(match.score.rows.map((r) => [r.slot, { frags: r.frags_raw, deaths: r.deaths_raw }]));

  /* Chosen spots (spots.json) per slot, in pick order; spotAt gives the one held at a tic. */
  const chosen = Object.fromEntries(slots.map((s) => [s, (spots?.chosen || []).filter((c) => c.slot === s)]));

  return {
    base, match, map, bots, slots, tel, recorded, f0, n, total, rate, respawnAfter, spots,
    deaths, shown, deathsBySlot, scoreAt, scoreCheck, engine,
    bot: (slot) => bots[slot - 1],
    /* Shown death events at or before tic (the kill feed and recent-frags lists). */
    shownUpTo: (tic) => shown.slice(0, lastAtOrBefore(shown, tic) + 1),
    spotAt(slot, tic) {
      return chosen[slot].find((c) => c.from_tic <= tic && (c.to_tic == null || tic <= c.to_tic)) || null;
    },
    /* The death that put slot down, if it is dead at tic. */
    deathAt(slot, tic) {
      if (tel[slot].alive[tic]) return null;
      const list = deathsBySlot[slot], i = lastAtOrBefore(list, tic);
      return i < 0 ? null : list[i];
    },
  };
}

export function fmtClock(sec) {
  const s = Math.max(0, Math.floor(sec + 1e-6));
  return String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0');
}
