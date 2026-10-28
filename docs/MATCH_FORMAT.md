# Match bundle format

**Status: draft, schema_version 1 proposed. Not final.** The event-capture gate (section 6) did not pass on stock engine data. This draft assumes the proposed fix (6.3), which still needs approval. The runner, validator and `make bench` are not built yet.

A bundle is one match: eight bots, one map, one seed, fixed duration.

```
bundles/<id>/
  match.json
  map.json
  tics.jsonl
  events.jsonl
  pov/slot1.mp4 … pov/slot8.mp4
```

`id` = `<UTC yyyymmddThhmmssZ>-<map>-s<seed>`, e.g. `20261027T210000Z-MAP01-s1234`.

## 1. Engine and platform

| | Matches (Linux homelab) | Development (Intel macOS) |
|---|---|---|
| ViZDoom | `1.3.2` (`requirements-linux.txt`) | `1.2.4` (`requirements-mac.txt`), the last Intel-mac wheel |
| Bundled IWAD | `freedoom2.wad`, Freedoom 0.13, sha256 `a8772e08…` | `freedoom2.wad`, Freedoom 0.12, sha256 `729f1004…` |
| Notifications buffer | yes (added in 1.3.0) | **no** |

- 1.2.4 has no notifications buffer, so it cannot produce `events.jsonl`. The runner refuses to write a bundle on ViZDoom < 1.3.0.
- The two Freedoom versions have different MAP01 geometry (1069 vs 1274 linedefs). `map.json` must come from the same IWAD bytes as the run: `match.json.iwad.sha256 == map.json.source.iwad_sha256`.
- Seeding: the engine is seeded with `set_seed(seed)`, and each scripted bot RNG is seeded from `(seed, slot)`. No promise of identical results across ViZDoom versions or platforms.
- Never source-build ViZDoom on macOS.

## 2. Match setup

One process per slot. Slot 1 hosts and slots 2–8 join. All run synchronous `Mode.PLAYER`, headless, `RGB24` at 320x240 (frames are `(240, 320, 3)`).

Host:
```
-host 8 -port <p> -netmode 0 -deathmatch -nomonsters
+sv_forcerespawn 1 +sv_noautoaim 1 +sv_respawnprotect 1 +sv_spawnfarthest 1
+sv_nocrouch 1 +sv_noexit 1 +viz_nocheat 0 +viz_respawn_delay <s>
```
Clients: `-join 127.0.0.1 -port <p>`.
Every process: `+name <name> +colorset <n> +cl_bbannounce 0 +show_obituaries 1`, plus the obituary PWAD from 6.3 if approved.

- `viz_respawn_delay` is a `CVAR_SERVERINFO` int in seconds, set once on the host. Observed on 1.3.2 with delay 2: every death at tic `d` was followed by alive at `d + 71`, which is 70 tics of delay plus 1.
- `sv_respawnprotect` gives 3 s of invulnerability on every spawn, including the first (engine source, `p_mobj.cpp`).
- `DoomGame.respawn_player()` is never called: it steps tics internally. `sv_forcerespawn` respawns players inside the normal tic loop.
- `sv_cheats` stays off in matches. Only the controlled bench turns it on.

## 3. Time contract

- **Lockstep.** On every loop iteration, each process calls `make_action(action, 1)` once, then waits at a cross-process barrier. Without the barrier, ZDoom's `TryRunTics` lets a lagging client run two gametics in one step. Observed on 1.3.2 with 8 players over 1400 steps: the host skipped none and clients skipped 46–346 tics each. With the barrier, all eight slots showed 0 gaps.
- **Row and frame semantics.** The `tics.jsonl` row and the MP4 frame for bundle tic `t` both come from the same `get_state()` call. They show the state after the engine has simulated tic `t`, before the action chosen from that state is applied. That action runs during tic `t + 1`.
- **Origin.** Bundle tic = engine episode tic − 1, so every process's first observed state is tic 0. The validator requires every slot to start at engine episode tic 1.
- **frame0_tic.** MP4 frame `k` is bundle tic `frame0_tic + k`. Default `frame0_tic = 0`. All eight videos cover the same interval.
- **Duration.** `--duration-tics N` (default 10500 = 5 min) covers tics `0 … N−1`: exactly N rows per slot and N frames per video.
- **Death.** Rows and frames keep coming. The row has `alive: false` and `behavior: DEAD`, and the frame is whatever the engine renders (death cam).
- **Respawn.** The first row with `alive: true` after a death has `behavior: RESPAWNING`. It is detected from the slot's own `DEAD` flag only.
- **Dead at start.** A slot can be dead at tic 0 because spawning at level start can telefrag. This happened 3 times per 8-player run on MAP01. Rows and frames are written as normal.
- **Unavailable state or early exit.** If any process returns no state, finishes its episode, or stops before tic N−1, or if ffmpeg fails, the match fails. All children are killed and nothing is written as a bundle. Frames are never skipped, duplicated or synthesized, and telemetry is never filled in.
- **Termination.** All processes stop after tic N−1 at the same barrier, close their ffmpeg pipes, and exit.

## 4. match.json

```jsonc
{
  "schema_version": 1,
  "id": "20261027T210000Z-MAP01-s1234",
  "date": "2026-10-27T21:00:00Z",
  "iwad": {"name": "freedoom2.wad", "sha256": "…"},
  "map": "MAP01",
  "seed": 1234,
  "ticrate": 35,
  "vizdoom_version": "1.3.2",
  "platform": {"os": "Linux-…-x86_64", "python": "3.12.x", "ffmpeg": "…"},
  "duration_tics": 10500,
  "frame0_tic": 0,
  "respawn_delay_s": 2,
  "skill": 3,
  "bots": [{"slot": 1, "code": "VEX", "name": "Vex", "archetype": "rusher", "colorset": 0, "player_number": 0}],
  "weapons_present": ["chaingun", "chainsaw", "plasma_rifle", "rocket_launcher", "shotgun", "super_shotgun"],
  "engine": [{"slot": 1, "fragcount": -1, "deathcount": 2}],
  "score": {
    "rule": "displayed = engine FRAGCOUNT - credited telefrags",
    "rows": [{"slot": 1, "displayed": -1, "frags_raw": -1, "telefrags": 0, "suicides": 1, "environmental_deaths": 0, "deaths": 3}]
  }
}
```

- `player_number` is the engine `PLAYER_NUMBER` (0-based) seen by that slot's own process. It must be constant for the whole match and distinct across slots. It follows connection order, not slot order: it changed between identical runs. `PLAYER<n>_FRAGCOUNT` is indexed by `player_number + 1`.
- `engine` holds the raw counters, copied untouched from the final tic: `fragcount` = `PLAYER<player_number+1>_FRAGCOUNT`, `deathcount` = the slot's `DEATHCOUNT`.
- Engine definition of FRAGCOUNT (from `AActor::Die`, confirmed on 1.3.2):
  - +1 to the credited player for each player they kill. This includes telefrags and barrels, but not kills at level start, while `level.maptime == 0`.
  - −1 for a self-kill (`OB_KILLEDSELF`).
  - No change for environmental deaths.
- `DEATHCOUNT` leaves out tic-0 deaths. A slot telefragged at level start reads 0 while dead.
- `score.rows`:
  - `telefrags`: credited telefrags only. Tic-0 telefrags are never credited, so they are not subtracted.
  - `suicides`: self-kills.
  - `environmental_deaths`: deaths with no player attacker.
  - `deaths`: all death events with that slot as target, including tic 0.
  - `displayed` = `engine.fragcount − telefrags`.

## 5. tics.jsonl

One line per `(tic, slot)`, sorted by `tic`, then `slot`:

```json
{"tic": 0, "slot": 1, "x": 896.0, "y": 720.0, "angle": 270.0, "health": 100, "armor": 0, "weapon": 2, "alive": true, "behavior": "ROAMING", "utility_scores": null}
```

| field | source | notes |
|---|---|---|
| `x`, `y` | `POSITION_X/Y` | map units, float |
| `angle` | `ANGLE` | degrees [0, 360), Doom convention: 0 = east, counter-clockwise |
| `health`, `armor` | `HEALTH`, `ARMOR` | int; health ≤ 0 while dead (−999900 after a telefrag) |
| `weapon` | `SELECTED_WEAPON` | engine weapon slot 0–9. Slots 1 and 3 hold two weapons each; v1 does not say which one |
| `alive` | `not DEAD` | |
| `behavior` | bot brain | only the values in `characters/README.md`; `DEAD`/`RESPAWNING` as defined in section 3 |
| `utility_scores` | bot brain | `{BEHAVIOR: float}` or `null` for placeholder bots |

Each process writes only its own slot's rows. The merge rejects missing `(tic, slot)` rows, duplicates, slots outside 1–8, and per-slot tic ranges that differ from `0 … N−1`.

## 6. events.jsonl

```json
{"tic": 1017, "type": "frag", "actor": 3, "target": 2, "source": "obituary", "key": "OB_BARREL", "credited": true}
```

`actor` and `target` are slots (1–8) or `null`. `source` is `obituary` (notifications buffer) or `telemetry` (the slot's own `DEAD` flag).

| type | actor | target | source | rule |
|---|---|---|---|---|
| `frag` | credited killer | victim | obituary | player killer ≠ victim, key ≠ `OB_MPTELEFRAG` |
| `telefrag` | telefragger | victim | obituary | key `OB_MPTELEFRAG`; `credited: false` at tic 0 |
| `suicide` | victim | victim | obituary | key `OB_KILLEDSELF` |
| `environmental_death` | `null` | victim | obituary | no player attacker (killer field equals victim, key not `OB_KILLEDSELF`) |
| `respawn` | slot | `null` | telemetry | `DEAD` 1→0 in the slot's own rows |

**Deferred, not captured in v1:**
- **Shots:** an `ATTACK` input is not a confirmed shot.
- **Hits:** `HITCOUNT`/`DAMAGECOUNT` are counters with no target.
- **Pickups:** pickup messages ("Got the pump-action shotgun!") appear only in the picking player's own buffer. That is a candidate source, not yet validated.

### 6.1 Source

`set_notifications_buffer_enabled(True)` with `set_notifications_buffer_size(1)` (1.3.0+). The engine logs every console print with its gametic. With size 1, each state's buffer holds exactly the lines printed during the tic just simulated. Observed on 1.3.2 with 8 players:

- Every obituary appeared once, in all 8 processes, at the same tic.
- That tic was also the victim's `DEAD` 0→1 tic and the tic the credited player's `FRAGCOUNT` changed.

Obituaries are matched to slots by player name from `+name`. Lines that are not obituaries are ignored: bot-list notices, cheat notices, pickup messages, "left the game".

### 6.2 Gate result: stock obituaries are not enough

| case (1.3.2, MAP01) | stock obituary | engine credit |
|---|---|---|
| super shotgun kill | `Vex was permeated by Kronos's double-barreled shotgun.` | Kronos +1 |
| teleporter telefrag (tic 569) | `Kronos stood where Null was teleporting.` | Null +1 |
| level-start telefrag (tic 0) | `Vex stood where Kronos was teleporting.` | none |
| rocket self-kill | `Vex killed himself.` | Vex −1 |
| barrel set off by Null | `Kronos went boom.` | **Null +1, not named** |

- **Barrels break attribution.** `OB_BARREL` names only the victim. The engine still credits the player who detonated the barrel (`target` chain through `A_Explode`). MAP01 has 6 barrels in deathmatch, and barrels respawn.
- **The text is not stable.** Freedoom's own `DEHACKED` rewrites the multiplayer obituaries, differently in 0.12 and 0.13 (killer first in some strings). Parsing stock text would mean rebuilding the effective string table for each IWAD.

### 6.3 Proposed fix (needs approval)

A generated one-lump PWAD, loaded with `-file` on every process. It contains a `DEHACKED` `[STRINGS]` section that rewrites the 31 obituary keys `ClientObituary` can produce for a player victim:

```
OB_BARREL = OBIT|OB_BARREL|%o|%k
OB_MPTELEFRAG = OBIT|OB_MPTELEFRAG|%o|%k
…
```

- **Data only.** No engine patch, no ACS, nothing about the simulation changes. PWAD `DEHACKED` lumps load after the IWAD's (`D_LoadDehLumps`), so they win.
- **Fields.** `%k` is the credited player. For deaths with no player attacker, the engine sets `%k` to the victim.
- **Demonstrated on 1.3.2.** The barrel case printed `OBIT|OB_BARREL|Kronos|Null`, and the other four cases printed the matching keys.
- **Smallest variant.** Rewrite only `OB_BARREL` to include `%k`, and keep parsing stock text everywhere else. Not recommended, for the reason in 6.2.

**Cross-checks the validator would enforce.** These use engine counters to verify attribution, never to create it:

- Each `DEAD` 0→1 in a slot's rows matches exactly one obituary event targeting that slot at the same tic.
- A slot dead at tic 0 has a tic-0 obituary.
- For every slot, final `FRAGCOUNT` = credited frags + credited telefrags − suicides.
- For every slot, final `DEATHCOUNT` = death events with `tic > 0`.

## 7. map.json

Written by `tools/extract_map.py` from the run's IWAD. It reads the Doom-format map lumps directly and rejects Hexen and UDMF maps. Contents:

- `source`: IWAD name, sha256, map, skill.
- `bounds`.
- `linedefs`: endpoints, flags, `two_sided`, `special`, `tag`, front and back sector.
- `sectors`: floor, ceiling, light, special, tag.
- `deathmatch_starts` (thing type 11) and `player_starts`.
- `weapons`, with raw flags, `multiplayer_only` (flag 0x10) and `in_deathmatch` for the match skill.
- `weapons_present`.
- `barrels`.

## 8. POV video

`pov/slot<n>.mp4`: 320x240, 35 fps, H.264, `yuv420p`, one keyframe per second (`-g 35`). Raw `rgb24` frames are piped to ffmpeg:

```
ffmpeg -f rawvideo -pix_fmt rgb24 -s 320x240 -r 35 -i - -c:v libx264 -pix_fmt yuv420p -g 35 -r 35 pov/slot<n>.mp4
```

The decoded frame count must equal `duration_tics`, and the duration must equal `duration_tics / 35` s. Both are checked with ffprobe.

## 9. Validation (`engine/validate_bundle.py`, not built)

- All files present.
- `match.json` fields and types. `iwad.sha256` matches `map.json`.
- `tics.jsonl`: complete `(tic, slot)` grid, no duplicates, valid slots, values within the field rules.
- `player_number`: distinct across slots and constant per slot.
- Events: valid types, every death paired with its `DEAD` flip, every respawn paired with a `DEAD` 1→0.
- Score reconciliation as in 6.3. Displayed scores are recomputed from the rule.
- Every video: ffprobe codec `h264`, 320x240, 35 fps, decoded frame count = `duration_tics`.
