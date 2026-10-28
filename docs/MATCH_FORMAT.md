# Match bundle format

**schema_version 3.** One bundle = one match: eight bots, one map, one seed, fixed duration. Written by `engine/runner.py`, checked by `engine/validate_bundle.py`.

```
bundles/<id>/
  match.json
  map.json
  tics.jsonl
  events.jsonl
  spots.json          (schema 2)
  pov/slot1.mp4 … pov/slot8.mp4
```

Schema 2 (milestone 2A) adds `weapon_name`, `target_slot` and `sector` to every `tics.jsonl` row, `spots.json`, and per-bot `controller` and `weapons_resolved` in `match.json`. Schema 3 adds the damage counters `hitcount`, `damagecount` and `damage_taken` to every row. The validator still accepts schema 1 and 2 bundles, such as the viewer's samples.

`id` = `<UTC yyyymmddThhmmssZ>-<map>-s<seed>`, e.g. `20261028T041144Z-MAP01-s1234`.

The runner writes the bundle only if every process ran to the end and every check in section 9 passed. Capture output stays in `bundles/.capture/<id>/` either way: per-slot logs, raw notification lines, `runner.log` with warnings.

## 1. Engine, platform, IWAD

| | Matches (Linux) | Development (Intel macOS) |
|---|---|---|
| ViZDoom | `1.3.2` (`requirements-linux.txt`, `docker/Dockerfile`) | `1.2.4` (`requirements-mac.txt`), the last Intel-mac wheel |
| Bundled IWAD | `freedoom2.wad`, Freedoom 0.13, sha256 `a8772e08…` | `freedoom2.wad`, Freedoom 0.12, sha256 `729f1004…` |
| Notifications buffer | yes (added in 1.3.0) | no |

- **Refusal on old ViZDoom.** The runner refuses to start on ViZDoom < 1.3.0. 1.2.4 has no notifications buffer, so it cannot produce `events.jsonl`. It exits 2 with a message saying so.
- **One IWAD per bundle.** `map.json` must be extracted from the same IWAD file, and therefore the same Freedoom version, as the run. The two Freedoom versions have different MAP01 geometry (1069 vs 1274 linedefs). The IWAD sha256 is recorded twice, as `match.json.iwad.sha256` and `map.json.source.iwad_sha256`, and the validator requires them to match.
- **Determinism.** The engine is seeded with `set_seed(seed)`, and every bot RNG (placeholder and personality) is seeded from `(seed, slot)`. Runs are not reproducible tic for tic: join order (and so `player_number` and spawn spots) changes between runs with the same seed. Nothing is promised across ViZDoom versions or platforms.
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
Every process: `+name <name> +colorset <n> +cl_bbannounce 0 +show_obituaries 1 +neverswitchonpickup 1 +sb_deathmatch_enable 0 +cl_showsecretmessage 0 -file obituaries.wad`.
The exact argument lists are recorded in `match.json.host_args` and `client_args`.

- **Weapon selection.** `neverswitchonpickup` keeps the weapon a controller selected: the engine never swaps it on pickup. Every controller selects with `use <Class>` (`engine/weapons.py`). The engine still switches away from an empty weapon on its own.
- **Server settings in effect** (printed by the engine on 1.3.2): `dmflags` 4216196, so `sv_weaponstay` and `sv_itemrespawn` are on. Weapons stay where they are; items respawn.

- **Obituary WAD.** `obituaries.wad` is generated at run time by `engine/obituaries.py` (section 6.1), and its sha256 is recorded in `match.json.obituary_wad`.
- **Render settings.** No HUD, no crosshair, weapon visible, messages off (`set_render_messages(False)`), dead-player scoreboard off and secret message off on every capture client. Recorded per slot in `match.json.render.messages_per_slot` and `scoreboard_per_slot`.
  - **Dead-player scoreboard.** While a player's camera is dead, ZDoom draws the deathmatch scoreboard and ViZDoom's "Respawn in 0:0n" line over the view (`ct_chat.cpp`, `hu_scores.cpp`), with engine counters that include the pre-roll. `sb_deathmatch_enable 0` turns both off. The controlled bench checks it with the staged victims: slot 1 renders like a capture client and dies in stages 1 and 3; slot 2 renders the scoreboard as a positive control and dies in stages 2 and 4. A detector counts the board's player-colour swatches (24 pixels wide, stacked at one x) in the raw frames from 5 tics after each death until respawn: 8 rows in every frame of the control, at most 1 in the capture client's.
  - **Secret message.** "A secret is revealed!" is a mid-screen print that `set_render_messages` does not cover. `cl_showsecretmessage 0` turns it off. It was seen in a probe POV, not by a bench.
  - **Spawn-protection expiry.** When `sv_respawnprotect` runs out, 3 s after a spawn, the engine drew a 3x5 grey mark at x 271–273, y 15–19 for one dumped frame in one controlled-bench run. The message-area check now compares only frames after the observers' own spawn protection has ended. Capture POVs can contain this mark; it is not text.
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
  - These deaths fall in the pre-roll (section 3), before the displayed match starts.

## 2a. Fairness

What a bot may know. Tests enforce the identity rule; review enforces the rest.

- **Enemies.** Enemy positions come only from the bot's own labels buffer: what its own POV renders, inside its 90-degree view. `state.objects` is read only to find the player's own actor (its engine sector); it is never used for other players.
- **The map.** Geometry, items, weapons and spawn points from `map.json`, and live floor and ceiling heights from sectors info (doors, lifts), are fair: a player learns the map.
- **Identity.** ViZDoom label object ids differ between processes, so naming a visible player goes through a shared table (`engine/peers.py`). The rule:
  - The table holds only requests (the positions of labels a process can see) and answers (slot ids). No process publishes its own position.
  - Only `engine/personality/perception.py` may query it, and only to name a `DoomPlayer` label present in the current tic's labels buffer. Nothing may use it for the position of a player who is not in that buffer.
  - `engine/player.py` creates each process's reader and writer and runs the writer, and `engine/match.py` allocates the table. No other module imports `engine/peers.py`.
  - `tests/test_peers.py` checks that a process reads only its own request and answer rows, that no answer names a slot absent from its labels, that a player's position never reaches the table, and the import and call rule (`make test`).
- **Protocol.** A label a process cannot name yet is posted at tic `t`. Every other process compares open requests with its own position at the previous two tics and answers within 20 units: labels report an actor's position at its own tic or the one before (81% exactly the previous tic, probed on 1.3.2). The asker reads the answer at tic `t + 2` and caches it per label object id. So `target_slot` for a newly seen player starts two tics after the label first appears. ViZDoom can give a new actor an id an old actor had: two evaluation matches named a player dead for over a second. So every cached name is asked again every 4 tics and dropped if nobody answers, a corpse label drops its id at once, and a contact's name always mirrors the latest answer.

## 3. Time contract

- **Lockstep.** On every loop iteration, each process calls `make_action(action, 1)` once, then waits at a cross-process barrier. Without the barrier, ZDoom's `TryRunTics` lets a lagging client run two gametics in one step: 46–346 skipped tics per client over 1400 steps in the 8-player probe. `match.json.sync` records `barrier: true` and the gap count, which must be 0. A process that sees a gap aborts the match.
- **Row and frame semantics.** The `tics.jsonl` row and the MP4 frame for bundle tic `t` both come from the same `get_state()` call. They show the state after the engine has simulated tic `t`, before the action chosen from that state is applied. That action runs during tic `t + 1`. The obituary for a death at tic `t` appears in that same state.
- **Origin.** Bundle tic = engine episode tic − 1, so every process's first observed state is tic 0. All processes must start at the same engine tic.
- **Pre-roll and `frame0_tic`.** The displayed match starts at `frame0_tic`, once the level-start deaths have respawned, plus a margin:
  - `frame0_tic = R + M`, where `R = 35·respawn_delay_s + 1` and `M = 35·respawn_delay_s + 35`.
  - `R` is the tic at which a slot killed at tic 0 respawns, since a death at tic `d` respawns at `d + 35·delay + 1` (section 2). `M` is the respawn delay plus one second.
  - With the default 2 s delay, `R = 71`, `M = 105` and `frame0_tic = 176`, which is 5.03 s of pre-roll.
  - `frame0_tic` depends only on `respawn_delay_s`. The runner knows it before tic 0, and every bundle with the same delay has the same pre-roll.
  - Every slot dead at tic 0 must be alive again by tic `R`. If one is not, no bundle is written.
- **Match clock.** The displayed match clock reads 00:00 at `frame0_tic`: clock = `(tic − frame0_tic) / 35` s. Pre-roll tics come before 00:00.
- **What the pre-roll keeps.**
  - Pre-roll telemetry stays in the bundle: `tics.jsonl` rows and `events.jsonl` events start at tic 0.
  - The POV videos start at `frame0_tic`, so MP4 frame `k` is bundle tic `frame0_tic + k`.
  - Displayed score columns count only events at `tic ≥ frame0_tic` (section 4).
- **Duration.** `--duration-tics N` (default 10500 = 5 min) is the displayed match: the clock runs from 00:00 to `N / 35` s.
  - The capture runs `frame0_tic + N` tics.
  - That gives `frame0_tic + N` rows per slot (tics `0 … frame0_tic + N − 1`) and `N` frames per video (tics `frame0_tic … frame0_tic + N − 1`).
  - The last state is recorded and no further action is sent.
- **Death.** Rows and frames keep coming. The row has `alive: false` and `behavior: DEAD`, and the frame is whatever the engine renders.
- **Respawn.** The first row with `alive: true` after a death has `behavior: RESPAWNING`. It is detected from the slot's own `DEAD` flag.
- **Dead at start.** A slot can be dead at tic 0 because of a level-start telefrag. Its rows are written as normal, and it respawns at tic `R`, inside the pre-roll.
- **Failure.** If a process returns no state, finishes early, sees a tic gap, or ffmpeg exits non-zero, the runner kills every slot's process group and exits 1. Each group holds the slot process, its ViZDoom engine and its ffmpeg. The startup timeout is 120 s, and the progress timeout is 60 s without the slowest slot advancing. No bundle is written. Frames are never skipped, duplicated or synthesized, and telemetry is never filled in.

## 4. match.json

```jsonc
{
  "schema_version": 1,
  "id": "20261028T041144Z-MAP01-s1234",
  "date": "2026-10-28T04:11:44Z",
  "iwad": {"name": "freedoom2.wad", "sha256": "a8772e08…"},
  "map": "MAP01", "seed": 1234, "ticrate": 35, "skill": 3,
  "vizdoom_version": "1.3.2",
  "platform": {"os": "Linux-…-x86_64-with-glibc2.36", "python": "3.12.16", "ffmpeg": "ffmpeg version 5.1.9-0+deb12u1 …"},
  "duration_tics": 2100, "frame0_tic": 176, "respawn_delay_s": 2,
  "bots": [{"slot": 1, "code": "VEX", "name": "Vex", "archetype": "rusher", "colorset": 0, "player_number": 0,
            "controller": "engine.personality.controller:PersonalityBot", "weapons_resolved": ["super_shotgun", "shotgun"]}],
  "weapons_present": ["chaingun", "chainsaw", "plasma_rifle", "rocket_launcher", "shotgun", "super_shotgun"],
  "engine": [{"slot": 1, "player_number": 0, "fragcount": 0, "deathcount": 1}],
  "score": {
    "rule": "displayed columns count events at tic >= frame0_tic; displayed_frags = kills - suicides; displayed_deaths = deaths; frags_raw and deaths_raw are engine counters at the last tic",
    "rows": [{"slot": 1, "displayed_frags": 0, "displayed_deaths": 1, "frags_raw": 0, "deaths_raw": 1,
              "kills": 0, "telefrags": 0, "suicides": 0, "environmental_deaths": 0}]
  },
  "event_counts": {"frag": 14, "telefrag": 3, "suicide": 1, "environmental_death": 0, "respawn": 18},
  "obituary_wad": {"sha256": "74149d45…", "keys": 31, "format": "OBIT|<key>|<victim>|<killer>"},
  "notifications": {"unknown_lines": 95, "unattributed_obituaries": 0},
  "sync": {"barrier": true, "steps": 2276, "gaps": 0, "frag_vector_mismatches": 0},
  "render": {"hud": false, "crosshair": false, "weapon": true, "messages": false, "scoreboard": false, "secret_message": false,
             "messages_per_slot": [false, false, false, false, false, false, false, false],
             "scoreboard_per_slot": [false, false, false, false, false, false, false, false]},
  "pov": [{"slot": 1, "path": "pov/slot1.mp4", "sha256": "…"}],
  "host_args": ["-host 8", "…"], "client_args": ["-join 127.0.0.1", "…"]
}
```

- **`bots[].controller`** is the import path that drove the slot. In milestone 2A, VEX, HEX and NUL run `engine.personality.controller:PersonalityBot` and the other five `engine.bots:PlaceholderBot` (`--personalities` on the runner).
- **`bots[].weapons_resolved`** is the sheet's `weapons.allowed`, ordered by `weapons.weights` and kept only if the map has the weapon (`weapons_present`, plus the fist and pistol every player spawns with). It is `null` for placeholders, which do not follow their sheet's weapons. Schema 1 had one top-level `controller` instead.
- **`player_number`** is the engine `PLAYER_NUMBER` (0-based), recorded as each slot's own process observed it.
  - It must be constant per slot and distinct across slots.
  - It follows join order, not slot order, and differs between runs.
  - `PLAYER<n>_FRAGCOUNT` is indexed by `player_number + 1`.
- **`engine`** holds the raw counters, copied untouched from the last tic (`frame0_tic + duration_tics − 1`): `fragcount` (the slot's `FRAGCOUNT`) and `deathcount` (its `DEATHCOUNT`).
- **How the engine moves FRAGCOUNT** (`AActor::Die`):

  | Event | Change |
  |---|---|
  | Credited kill of another player, including telefrags and barrels | +1 |
  | Self-kill (damage source is the victim) | −1 |
  | Environmental death (no source) | 0 |
  | Any frag at level start (`level.maptime == 0`) | 0 |

- **Tic-0 deaths.**
  - `DEATHCOUNT` leaves them out: a slot telefragged at level start reads 0 while dead.
  - They fall in the pre-roll, so no displayed column counts them.
  - They are still recorded as events.
- **`score.rows` columns.**
  - **Displayed match only.** `displayed_frags`, `displayed_deaths`, `kills`, `telefrags`, `suicides` and `environmental_deaths` count events at `tic ≥ frame0_tic`. Pre-roll events stay in `events.jsonl` but are not in these columns.
  - `kills`: credited non-telefrag frags.
  - `telefrags`: credited telefrags.
  - `suicides`: self-kills.
  - `environmental_deaths`: deaths with no source.
  - `displayed_frags` = `kills − suicides`. Telefrags are not displayed frags. This equals the change in `FRAGCOUNT` over the displayed match, minus its telefrags.
  - `displayed_deaths` = all deaths.
  - `frags_raw` and `deaths_raw` are the engine `FRAGCOUNT` and `DEATHCOUNT` at the last tic. They include the pre-roll, so they can differ from the displayed columns. They are not displayed.
- **`event_counts`** counts every event in `events.jsonl`, pre-roll included.

## 5. tics.jsonl

One line per `(tic, slot)`, sorted by `tic`, then `slot`:

```json
{"tic":585,"slot":3,"x":2063.964,"y":-226.008,"angle":245.215,"health":100,"armor":0,"weapon":3,"alive":true,"behavior":"STALKING","utility_scores":{"IN_COMBAT":0.5,"HUNTING":0.015,"ROAMING":0.09,"STALKING":0.75,"HOLDING_ANGLE":0.0,"COLLECTING_ARMOR":0.049,"COLLECTING_HEALTH":0.0,"FLEEING":0.0},"weapon_name":"rocket_launcher","target_slot":1,"sector":60}
```

NUL has just picked up the rocket launcher and selected it; the engine still holds the shotgun (slot 3) while it switches.

```
```

| field | source | notes |
|---|---|---|
| `x`, `y` | `POSITION_X/Y` | map units, 3 decimals |
| `angle` | `ANGLE` | degrees [0, 360), Doom convention: 0 = east, counter-clockwise |
| `health`, `armor` | `HEALTH`, `ARMOR` | int; health ≤ 0 while dead (−999900 after a telefrag) |
| `weapon` | `SELECTED_WEAPON` | engine weapon slot 0–9. Slots 1 and 3 hold two weapons each, so it does not say which |
| `alive` | `not DEAD` | |
| `behavior` | controller | only the values in `characters/README.md`; `DEAD`/`RESPAWNING` set by the runner as in section 3 |
| `utility_scores` | controller | `{BEHAVIOR: float}` or `null`. Personality bots write the eight chooseable behaviors' weighted scores from their last 10 Hz decision; placeholders write `null` |
| `weapon_name` | controller | schema 2. The weapon the bot itself last selected (`characters/README.md` names), or `null` before it has selected one or while it holds no usable allowed weapon. It can lead `weapon` by the switch time (about 15 tics) and differ from it after the engine switches away from an empty weapon. For personality bots it is always one of `bots[].weapons_resolved` |
| `target_slot` | controller | schema 2. The slot the bot is engaging: the enemy it is aiming at, from the first tic it is in view and named (two tics after a new label appears; section 2a) until 0.6 s after it leaves view, or `null`. Personality bots name a target only while they may fight it (an ambusher's fire window, `engage_health_min`). A placeholder names the nearest visible player it is shooting at |
| `sector` | engine | schema 2. Engine sector id of the player's own actor (`state.objects`, used for nothing else). Sector ids are `map.json` `sectors` indices |
| `hitcount` | `HITCOUNT` | schema 3. Hits this player has landed on other players since the match began |
| `damagecount` | `DAMAGECOUNT` | schema 3. Damage this player has dealt to other players since the match began |
| `damage_taken` | `DAMAGE_TAKEN` | schema 3. Damage this player has taken since the match began, from anyone: other players, its own splash, the world |

Each process writes only its own slot's rows.

**Damage counters** (ViZDoom `VIZ_LogDmg`, read in the 1.3.0 source and probed on 1.3.2):
- They are cumulative over the whole match and do not reset on death or respawn.
- Every hit on a player counts its damage after armor, not clamped to the health left, so overkill counts. Hits over 1000 (telefrags) are ignored.
- `damage_taken` counts all of them. `damagecount` and `hitcount` count only damage this player dealt to another player: its own splash is in its `damage_taken` and nowhere else.
- A barrel's blast counts for the player who set the barrel off: in the controlled bench's barrel stage, the shooter's `damagecount` rose 324 over 3 hits at the blast tic, 128 of it the staged victim's.
- The counter moves at the tic the damage lands: for a rocket, at impact, not at the shot.
- **Open finding.** In the five milestone 2A evaluation matches, 1,300 of 51,036 damage points dealt (2.5%, in 32 tics) were matched by no damage taken, and no health change, by any player in the same tic. Only half of those tics had a player under spawn protection; the cause is not identified. Match totals still satisfy dealt ≤ taken.

**Target identity** follows the fairness rule in section 2a. The merge rejects missing `(tic, slot)` rows, duplicates, slots outside 1–8, and per-slot tic ranges other than `0 … frame0_tic + duration_tics − 1`.

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
- Since `map_format_version` 2: linedef `exit` (Doom specials 11, 51, 52, 124; navigation treats them as walls), `items` (health, armor, ammo, megasphere and berserk things with their `class`, which the sheets' `items.weights` use), `teleport_destinations`, and `obstacles` (solid decorations and barrels with their radius; Doom collides them as boxes, and navigation keeps clear of them).

The IWAD rule from section 1 applies: same file as the run, sha256 recorded in both places.

## 7a. spots.json (schema 2)

Spot candidates for the stationary styles and the spots bots chose (`engine/personality/spots.py`).

```jsonc
{"spots_version": 1, "map": "MAP01", "iwad_sha256": "a8772e08…",
 "params": [{"mode": "sightlines_max", "range": [500, 1500], "rays": 72, "ray_max": 2048.0, "lattice": 64, "candidates_scored": 584},
            {"mode": "sightlines_min_near_traffic", "range": [200, 600], "rays": 72, "ray_max": 2048.0, "lattice": 64, "candidates_scored": 584}],
 "candidates": {"sightlines_max": [{"id": 1309, "x": 1112.0, "y": -312.0, "sector": 165, "floor": -64, "score": 1.0, "watch_angle": 295.0}, "…"],
                "sightlines_min_near_traffic": [{"id": 1993, "x": 1560.0, "y": -1080.0, "sector": 178, "floor": -96, "score": 1.0, "watch_angle": 140.0}, "…"]},
 "chosen": [{"slot": 3, "code": "NUL", "mode": "sightlines_min_near_traffic", "spot": 1507, "x": 1240.0, "y": -1080.0, "angle": 45.0, "from_tic": 0, "to_tic": 233},
            {"slot": 6, "code": "HEX", "mode": "sightlines_max", "spot": 1452, "x": 1176.0, "y": 1096.0, "angle": 185.0, "from_tic": 71, "to_tic": 2275}]}
```

- Candidates are nav-graph nodes on a 64-unit lattice. Sight is 72 rays from eye height against `map.json` linedefs: a one-sided line, a closed door, or a two-sided line whose far floor is above (or ceiling below) eye height stops a ray. `score` is normalised to 1 for the best candidate; at most 40 are kept per mode, no two closer than 128 units.
  - `sightlines_max`: mean ray length clipped at the far end of the bot's `preferred_range`.
  - `sightlines_min_near_traffic`: `sqrt(covered traffic) × (1 − exposure)² × sqrt(1 − own traffic)`. Traffic counts how often a node lies on the shortest path between two of the deathmatch starts, weapons and items; covered traffic is the traffic visible from the spot inside the bot's `preferred_range`; exposure is mean ray length clipped at 1024.
- `chosen` lists every spot a bot picked, from the tic it picked it to the tic it left it (`to_tic` is the last tic, inclusive). `angle` is the candidate's `watch_angle`.
- Tables are cached by IWAD sha256, map, nav-graph version and parameters in `<out>/.cache/`.

## 8. POV video

`pov/slot<n>.mp4`: 320x240, 35 fps, H.264, `yuv420p`, one keyframe per second, with the sha256 recorded in `match.json.pov`. Each video holds `duration_tics` frames, starting at `frame0_tic`. Pre-roll frames are never piped to ffmpeg. Raw `rgb24` frames are piped to ffmpeg:

```
ffmpeg -f rawvideo -pix_fmt rgb24 -s 320x240 -r 35 -i - -an -c:v libx264 -preset veryfast -crf 23 \
       -pix_fmt yuv420p -g 35 -r 35 -movflags +faststart pov/slot<n>.mp4
```

## 9. Checks

The runner applies these before writing the bundle (exit 4 and no bundle if any fails), and `engine/validate_bundle.py` re-derives them from the bundle files alone:

- **Files.** Every file is present (`spots.json` from schema 2). `match.json` fields and types are valid. `frame0_tic` follows the section 3 formula for `respawn_delay_s`, and `sync.steps` = `frame0_tic + duration_tics`. The IWAD sha256 matches `map.json`. The obituary WAD sha256 matches what `engine/obituaries.py` generates. `sync` shows the barrier with 0 gaps. Message rendering is off for every slot.
- **Telemetry.** `tics.jsonl` is a complete `(tic, slot)` grid over tics `0 … frame0_tic + duration_tics − 1` with no duplicates, and its values follow section 5. `DEAD`/`RESPAWNING` are consistent with `alive`. The behavior vocabulary matches `characters/README.md`.
- **Schema 2 fields.**
  - `weapon_name` is a weapon name or `null`, and for a bot with `weapons_resolved` one of those.
  - `target_slot` is `null` or another slot, only on alive rows, and that slot was alive at some tic in the last 35.
  - `sector` is a `map.json` sector index. It is re-derived from `x`, `y` and the `map.json` sector polygons and must contain the point, except within 1.5 units of a sector boundary (the validator prints how many such rows it allowed).
  - The scoreboard is off for every slot, every bot has a `controller` and `weapons_resolved`, and `spots.json` candidates and chosen spots are well formed.
- **Schema 3 fields.** The damage counters are non-negative integers that never fall. Over the match, the damage all players dealt to other players is at most the damage all players took.
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
- **Pre-roll.** Every slot dead at tic 0 is alive again by tic `R`.
- **Runner only.** All processes logged identical frag vectors at every tic.
- **Scores.** Score rows equal the rule applied to the events at `tic ≥ frame0_tic` and the engine counts.
- **Videos.** Each passes ffprobe: `h264`, 320x240, `35/1`, `yuv420p`, decoded frame count = `duration_tics` (the pre-roll is not in the video), duration = `duration_tics / 35` s. Its sha256 matches `match.json.pov`.

## 10. Commands

| | Linux | Intel mac (pinned Docker image) |
|---|---|---|
| Parser unit tests | `make test` | `make test` (in `.venv`) or `make docker-test` |
| Image | | `make image` |
| Smoke, controlled and personality benches | `make bench` | `make docker-bench` |
| Spawn-telefrag experiment | `make bench-spawn` | |
| One match | `make match TICS=2100 SEED=1234` | `make docker-match TICS=2100 SEED=1234` |
| Validate | `make validate BUNDLE=bundles/<id>` | `make docker-validate BUNDLE=…` |

- **Parser tests** (`tests/test_obituaries.py`) need `pytest` from `requirements-dev.txt`, not ViZDoom. They cover:
  - all 31 keys, each with killer = victim and killer ≠ victim, against an expected table written out in the test;
  - `OB_EXIT` and `OB_SUICIDE` as self-kills that cost the victim one frag;
  - malformed `OBIT` lines;
  - stock obituary text, which must be rejected and never guessed: the lines observed in section 6.2, and every stock English string of the installed engine and IWAD.
- **Smoke bench:** 8 placeholder bots to tic 300. Frag vectors must agree across all processes at every tic.
- **Controlled bench:** the staged weapon kill, teleporter telefrag, self-kill and barrel kill, plus the message-area and dead-player scoreboard checks. It needs Freedoom 0.13's MAP01, because the teleporter coordinates are specific to it.
