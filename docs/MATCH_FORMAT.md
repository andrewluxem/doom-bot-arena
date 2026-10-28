# Match bundle format

**schema_version 1.** One bundle = one match: eight bots, one map, one seed, fixed duration. Written by `engine/runner.py`, checked by `engine/validate_bundle.py`.

```
bundles/<id>/
  match.json
  map.json
  tics.jsonl
  events.jsonl
  pov/slot1.mp4 … pov/slot8.mp4
```

`id` = `<UTC yyyymmddThhmmssZ>-<map>-s<seed>`, e.g. `20261028T034723Z-MAP01-s1234`.

The runner writes the bundle only if every process ran to the end and every check in section 9 passed. Capture output stays in `bundles/.capture/<id>/` either way: per-slot logs, raw notification lines, `runner.log` with warnings.

## 1. Engine, platform, IWAD

| | Matches (Linux) | Development (Intel macOS) |
|---|---|---|
| ViZDoom | `1.3.2` (`requirements-linux.txt`, `docker/Dockerfile`) | `1.2.4` (`requirements-mac.txt`), the last Intel-mac wheel |
| Bundled IWAD | `freedoom2.wad`, Freedoom 0.13, sha256 `a8772e08…` | `freedoom2.wad`, Freedoom 0.12, sha256 `729f1004…` |
| Notifications buffer | yes (added in 1.3.0) | no |

- **Refusal on old ViZDoom.** The runner refuses to start on ViZDoom < 1.3.0. 1.2.4 has no notifications buffer, so it cannot produce `events.jsonl`. It exits 2 with a message saying so.
- **One IWAD per bundle.** `map.json` must be extracted from the same IWAD file, and therefore the same Freedoom version, as the run. The two Freedoom versions have different MAP01 geometry (1069 vs 1274 linedefs). The IWAD sha256 is recorded twice, as `match.json.iwad.sha256` and `map.json.source.iwad_sha256`, and the validator requires them to match.
- **Determinism.** The engine is seeded with `set_seed(seed)`, and each scripted bot RNG is seeded from `(seed, slot)`. Runs are not reproducible tic for tic: join order (and so `player_number` and spawn spots) changes between runs with the same seed. Nothing is promised across ViZDoom versions or platforms.
- Never source-build ViZDoom on macOS.

## 2. Match setup

One process per slot. Slot 1 hosts and slots 2–8 join. All run synchronous `Mode.PLAYER` at skill 3, headless, `RGB24` at 320x240 (frames are `(240, 320, 3)`).

Host:
```
-host 8 -port <p> -netmode 0 -deathmatch -nomonsters
+sv_forcerespawn 1 +sv_noautoaim 1 +sv_respawnprotect 1 +sv_spawnfarthest 1
+sv_nocrouch 1 +sv_noexit 1 +viz_nocheat 0 +viz_respawn_delay <s>
```
Clients: `-join 127.0.0.1 -port <p>`.
Every process: `+name <name> +colorset <n> +cl_bbannounce 0 +show_obituaries 1 -file obituaries.wad`.
The exact argument lists are recorded in `match.json.host_args` and `client_args`.

- **Obituary WAD.** `obituaries.wad` is generated at run time by `engine/obituaries.py` (section 6.1), and its sha256 is recorded in `match.json.obituary_wad`.
- **Render settings.** No HUD, no crosshair, weapon visible, messages off (`set_render_messages(False)`) on every capture client. Recorded per slot in `match.json.render.messages_per_slot`.
  - The controlled bench checks this with two static observers facing walls. The messages-off observer's top 64 rows changed by 0 pixels during each of 5 messages, including 4 obituaries. The messages-on control changed by 976–3679 pixels each time, all inside x 0–269, y 0–24.
- **Respawn delay.** `viz_respawn_delay` is a `CVAR_SERVERINFO` int in seconds, set once on the host (default 2). The bench verifies it: every death at tic `d` respawns at `d + 35·delay + 1`, which is +71 for 2 s.
- **Spawn protection.** `sv_respawnprotect` gives 3 s of invulnerability on every spawn, including the first.
- **No `respawn_player()`.** `DoomGame.respawn_player()` is never called because it steps tics internally. `sv_forcerespawn` respawns players inside the normal loop.
- **Exit switch.** `sv_noexit` kills a player who uses the exit (`OB_EXIT`, counted as a self-kill; see section 6).
- **Cheats.** `sv_cheats` is off in matches. Only the controlled bench turns it on, for `warp` and `give`.
- **Spawn telefrags at tic 0.** At level start ZDoom always picks a random deathmatch start and ignores `sv_spawnfarthest` (`G_DeathMatchSpawnPlayer`), so several players can land on one start.
  - `make bench-spawn` ran 10 seeds with `sv_spawnfarthest` on from the start, and the same 10 with it off at spawn and switched on by the host at tic 1.
  - Both modes gave 23 tic-0 telefrags (1–3 per run), identical per seed.
  - The setting stays on from the start, since it governs respawns.

## 3. Time contract

- **Lockstep.** On every loop iteration, each process calls `make_action(action, 1)` once, then waits at a cross-process barrier. Without the barrier, ZDoom's `TryRunTics` lets a lagging client run two gametics in one step: 46–346 skipped tics per client over 1400 steps in the 8-player probe. `match.json.sync` records `barrier: true` and the gap count, which must be 0. A process that sees a gap aborts the match.
- **Row and frame semantics.** The `tics.jsonl` row and the MP4 frame for bundle tic `t` both come from the same `get_state()` call. They show the state after the engine has simulated tic `t`, before the action chosen from that state is applied. That action runs during tic `t + 1`. The obituary for a death at tic `t` appears in that same state.
- **Origin.** Bundle tic = engine episode tic − 1, so every process's first observed state is tic 0. All processes must start at the same engine tic.
- **frame0_tic.** MP4 frame `k` is bundle tic `frame0_tic + k`. Schema 1 only defines `frame0_tic = 0`. All eight videos cover tics `0 … N−1`.
- **Duration.** `--duration-tics N` (default 10500 = 5 min) gives exactly N rows per slot and N frames per video. The last state is recorded and no further action is sent.
- **Death.** Rows and frames keep coming. The row has `alive: false` and `behavior: DEAD`, and the frame is whatever the engine renders.
- **Respawn.** The first row with `alive: true` after a death has `behavior: RESPAWNING`. It is detected from the slot's own `DEAD` flag.
- **Dead at start.** A slot can be dead at tic 0 because of a level-start telefrag. Rows and frames are written as normal.
- **Failure.** If a process returns no state, finishes early, sees a tic gap, or ffmpeg exits non-zero, the runner kills every slot's process group and exits 1. Each group holds the slot process, its ViZDoom engine and its ffmpeg. The startup timeout is 120 s, and the progress timeout is 60 s without the slowest slot advancing. No bundle is written. Frames are never skipped, duplicated or synthesized, and telemetry is never filled in.

## 4. match.json

```jsonc
{
  "schema_version": 1,
  "id": "20261028T034723Z-MAP01-s1234",
  "date": "2026-10-28T03:47:23Z",
  "iwad": {"name": "freedoom2.wad", "sha256": "a8772e08…"},
  "map": "MAP01", "seed": 1234, "ticrate": 35, "skill": 3,
  "vizdoom_version": "1.3.2",
  "platform": {"os": "Linux-…-x86_64-with-glibc2.36", "python": "3.12.16", "ffmpeg": "ffmpeg version 5.1.9-0+deb12u1 …"},
  "duration_tics": 2100, "frame0_tic": 0, "respawn_delay_s": 2,
  "controller": "engine.bots:PlaceholderBot",
  "bots": [{"slot": 1, "code": "VEX", "name": "Vex", "archetype": "rusher", "colorset": 0, "player_number": 0}],
  "weapons_present": ["chaingun", "chainsaw", "plasma_rifle", "rocket_launcher", "shotgun", "super_shotgun"],
  "engine": [{"slot": 1, "player_number": 0, "fragcount": 2, "deathcount": 1}],
  "score": {
    "rule": "displayed_frags = engine FRAGCOUNT - credited telefrags; displayed_deaths = deaths after tic 0",
    "rows": [{"slot": 1, "displayed_frags": 2, "displayed_deaths": 1, "frags_raw": 2, "deaths_raw": 1,
              "kills": 2, "telefrags": 0, "suicides": 0, "environmental_deaths": 0, "tic0_deaths": 1}]
  },
  "event_counts": {"frag": 8, "telefrag": 3, "suicide": 1, "environmental_death": 0, "respawn": 12},
  "obituary_wad": {"sha256": "74149d45…", "keys": 31, "format": "OBIT|<key>|<victim>|<killer>"},
  "notifications": {"unknown_lines": 78, "unattributed_obituaries": 0},
  "sync": {"barrier": true, "steps": 2100, "gaps": 0, "frag_vector_mismatches": 0},
  "render": {"hud": false, "crosshair": false, "weapon": true, "messages": false, "messages_per_slot": [false, false, false, false, false, false, false, false]},
  "pov": [{"slot": 1, "path": "pov/slot1.mp4", "sha256": "…"}],
  "host_args": ["-host 8", "…"], "client_args": ["-join 127.0.0.1", "…"]
}
```

- **`player_number`** is the engine `PLAYER_NUMBER` (0-based), recorded as each slot's own process observed it.
  - It must be constant per slot and distinct across slots.
  - It follows join order, not slot order, and differs between runs.
  - `PLAYER<n>_FRAGCOUNT` is indexed by `player_number + 1`.
- **`engine`** holds the raw counters, copied untouched from tic N−1: `fragcount` (the slot's `FRAGCOUNT`) and `deathcount` (its `DEATHCOUNT`).
- **How the engine moves FRAGCOUNT** (`AActor::Die`):

  | Event | Change |
  |---|---|
  | Credited kill of another player, including telefrags and barrels | +1 |
  | Self-kill (damage source is the victim) | −1 |
  | Environmental death (no source) | 0 |
  | Any frag at level start (`level.maptime == 0`) | 0 |

- **Tic-0 deaths.**
  - `DEATHCOUNT` leaves them out: a slot telefragged at level start reads 0 while dead.
  - Displayed deaths leave them out too.
  - They are still recorded as events and counted in `tic0_deaths`.
- **`score.rows` columns.**
  - `telefrags`: credited telefrags only (tic > 0).
  - `kills`: credited non-telefrag frags.
  - `suicides`: self-kills.
  - `environmental_deaths`: deaths with no source.
  - `displayed_frags` = `frags_raw − telefrags`.
  - `displayed_deaths` = deaths with `tic > 0`, which equals `deaths_raw`.

## 5. tics.jsonl

One line per `(tic, slot)`, sorted by `tic`, then `slot`:

```json
{"tic":0,"slot":3,"x":-144.0,"y":-1024.0,"angle":0.0,"health":100,"armor":0,"weapon":2,"alive":true,"behavior":"ROAMING","utility_scores":null}
```

| field | source | notes |
|---|---|---|
| `x`, `y` | `POSITION_X/Y` | map units, 3 decimals |
| `angle` | `ANGLE` | degrees [0, 360), Doom convention: 0 = east, counter-clockwise |
| `health`, `armor` | `HEALTH`, `ARMOR` | int; health ≤ 0 while dead (−999900 after a telefrag) |
| `weapon` | `SELECTED_WEAPON` | engine weapon slot 0–9. Slots 1 and 3 hold two weapons each; schema 1 does not say which one |
| `alive` | `not DEAD` | |
| `behavior` | controller | only the values in `characters/README.md`; `DEAD`/`RESPAWNING` set by the runner as in section 3 |
| `utility_scores` | controller | `{BEHAVIOR: float}` or `null`. Milestone 1A placeholder bots always write `null` |

Each process writes only its own slot's rows. The merge rejects missing `(tic, slot)` rows, duplicates, slots outside 1–8, and per-slot tic ranges other than `0 … N−1`.

## 6. events.jsonl

```json
{"tic":1018,"type":"frag","actor":3,"target":2,"key":"OB_BARREL","credited":true,"source":"OBIT|OB_BARREL|Kronos|Null"}
```

`actor` and `target` are slots (1–8) or `null`. For death events, `source` is the raw notification line, verbatim.

| type | actor | target | `credited` | from |
|---|---|---|---|---|
| `frag` | killer | victim | `tic > 0` | obituary, weapon key or `OB_BARREL`/`OB_DEFAULT`, killer ≠ victim |
| `telefrag` | telefragger | victim | `tic > 0` | obituary, `OB_MPTELEFRAG` |
| `suicide` | victim | victim | `tic > 0` | obituary, `OB_KILLEDSELF` (own splash), `OB_EXIT` (exit used with `sv_noexit`), `OB_SUICIDE` (console `kill`) |
| `environmental_death` | `null` | victim | `null` | obituary, `OB_FALLING`, `OB_CRUSH`, `OB_WATER`, `OB_SLIME`, `OB_LAVA`, `OB_MONTELEFRAG`, or `OB_DEFAULT` with killer = victim |
| `respawn` | slot | `null` | `null` | `tics.jsonl`, `alive` false→true; `source` is `"tics.jsonl:alive false->true"` |

**Deferred, not captured in schema 1:**
- **Shots:** an `ATTACK` input is not a confirmed shot.
- **Hits:** `HITCOUNT`/`DAMAGECOUNT` are counters with no target.
- **Pickups:** pickup messages ("Got the minigun!") appear only in the picking player's own notifications buffer. That is a candidate source, not validated.

### 6.1 Source: structured obituaries

Every process loads a one-lump PWAD that `engine/obituaries.py` generates from its table of the 31 obituary keys ZDoom can print for a player victim. The lump is a `DEHACKED` `[STRINGS]` section:

```
OB_BARREL = OBIT|OB_BARREL|%o|%k
OB_MPTELEFRAG = OBIT|OB_MPTELEFRAG|%o|%k
…
```

ZDoom fills `%o` with the victim and `%k` with the credited player, or with the victim when no player is credited. The PWAD's `DEHACKED` loads after the IWAD's, so these strings replace Freedoom's. Strings only: the simulation is unchanged.

The buffer is read with `set_notifications_buffer_enabled(True)` and `set_notifications_buffer_size(1)`. Each state then carries exactly the lines printed during the tic just simulated.

Parsing:
- **Only the `OBIT|<key>|<victim>|<killer>` format is accepted.**
  - Every other line is unknown: pickups, cheat notices, "15 bots read from…", and stock obituary text if the WAD failed to load.
  - Unknown lines are logged as warnings in `runner.log` and counted in `match.json.notifications.unknown_lines`. They never become events.
- **Rejected `OBIT` lines.** A line with a wrong field count, an unknown key, or a name not in the match is rejected with a warning. So is a key/killer combination that cannot be attributed (`OB_VOODOO`, `OB_FRIENDLY*`, or a self-kill key with a different killer).
- **Agreement across processes.** All eight processes must log identical `OBIT` lines at identical tics. Events are built from that agreed list.

### 6.2 Why the override (gate record)

Stock obituaries were not enough. Probe on ViZDoom 1.3.2, MAP01, 8 players:

| case | stock obituary | engine credit |
|---|---|---|
| super shotgun kill | `Vex was permeated by Kronos's double-barreled shotgun.` | Kronos +1 |
| teleporter telefrag | `Kronos stood where Null was teleporting.` | Null +1 |
| level-start telefrag (tic 0) | `Vex stood where Kronos was teleporting.` | none |
| rocket self-kill | `Vex killed himself.` | Vex −1 |
| barrel set off by Null | `Kronos went boom.` | **Null +1, not named** |

- **Barrels break attribution.** `OB_BARREL` names only the victim, and MAP01 has 6 barrels in deathmatch.
- **The text is not stable.** Freedoom's own `DEHACKED` rewrites the multiplayer strings, differently in 0.12 and 0.13.
- **Decision.** The full 31-key override was approved on 2026-10-27.

## 7. map.json

Written by `tools/extract_map.py` from the run's IWAD, which reads Doom-format map lumps directly. Hexen and UDMF maps are rejected. Contents:

- `source`: IWAD name, `iwad_sha256`, map, skill.
- `bounds`.
- `linedefs`: endpoints, flags, `two_sided`, `impassable`, `special`, `tag`, front and back sector.
- `sectors`: floor, ceiling, light, special, tag.
- `deathmatch_starts` (thing type 11) and `player_starts`.
- `weapons`, with raw flags, `multiplayer_only` (flag 0x10) and `in_deathmatch` at the match skill.
- `weapons_present`.
- `barrels`.

The IWAD rule from section 1 applies: same file as the run, sha256 recorded in both places.

## 8. POV video

`pov/slot<n>.mp4`: 320x240, 35 fps, H.264, `yuv420p`, one keyframe per second, with the sha256 recorded in `match.json.pov`. Raw `rgb24` frames are piped to ffmpeg:

```
ffmpeg -f rawvideo -pix_fmt rgb24 -s 320x240 -r 35 -i - -an -c:v libx264 -preset veryfast -crf 23 \
       -pix_fmt yuv420p -g 35 -r 35 -movflags +faststart pov/slot<n>.mp4
```

## 9. Checks

The runner applies these before writing the bundle (exit 4 and no bundle if any fails), and `engine/validate_bundle.py` re-derives them from the bundle files alone:

- **Files.** Every file is present. `match.json` fields and types are valid. The IWAD sha256 matches `map.json`. The obituary WAD sha256 matches what `engine/obituaries.py` generates. `sync` shows the barrier with 0 gaps. Message rendering is off for every slot.
- **Telemetry.** `tics.jsonl` is a complete `(tic, slot)` grid with no duplicates, and its values follow section 5. `DEAD`/`RESPAWNING` are consistent with `alive`. The behavior vocabulary matches `characters/README.md`.
- **Events.** Every death event's `source` re-parses to the same event. Event counts match.
- **Pairing.**
  - Every `alive` true→false in a slot's rows has exactly one death event for that slot at that tic.
  - A slot dead at tic 0 has a tic-0 death event.
  - No death event lacks a matching death in telemetry.
  - Respawn events equal the false→true transitions.
- **Reconciliation.**
  - For every slot, FRAGCOUNT = credited kills + credited telefrags − credited self-kills.
  - For every slot, DEATHCOUNT = deaths after tic 0.
  - Engine counters only verify attribution; they never create it.
- **Runner only.** All processes logged identical frag vectors at every tic.
- **Scores.** Score rows equal the rule applied to events and engine counts.
- **Videos.** Each passes ffprobe: `h264`, 320x240, `35/1`, `yuv420p`, decoded frame count = `duration_tics`, duration = `duration_tics / 35` s. Its sha256 matches `match.json.pov`.

## 10. Commands

| | Linux | Intel mac (pinned Docker image) |
|---|---|---|
| Image | | `make image` |
| Smoke and controlled benches | `make bench` | `make docker-bench` |
| Spawn-telefrag experiment | `make bench-spawn` | |
| One match | `make match TICS=2100 SEED=1234` | `make docker-match TICS=2100 SEED=1234` |
| Validate | `make validate BUNDLE=bundles/<id>` | `make docker-validate BUNDLE=…` |

- **Smoke bench:** 8 placeholder bots to tic 300. Frag vectors must agree across all processes at every tic.
- **Controlled bench:** the staged weapon kill, teleporter telefrag, self-kill and barrel kill, plus the message-area check. It needs Freedoom 0.13's MAP01, because the teleporter coordinates are specific to it.
