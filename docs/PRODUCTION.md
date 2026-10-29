# Production pipeline (milestone 3)

Three times a day a systemd user timer on the match box runs `make produce`. That plays one five-minute match in the match image, validates the bundle, uploads it to Vercel Blob, and updates `index.json`. The viewer on Vercel reads `index.json` and the bundles straight from the Blob store's public URL.

```
timer ──> make produce ──> docker: tools/produce.py run
            disk check (>= min_free_gb) ─> next map in rotation ─> seed ─> engine.runner (once more on failure)
            ─> POV videos re-encoded at crf 32 ─> engine.validate_bundle ─> IWAD gate
            ─> bundles/<id>/* uploaded (never overwritten), match.json last ─> index.json (max-age 60)
            ─> local bundles beyond keep_local deleted ─> status.json (max-age 60)
          any failure ─> status.json (last_error, consecutive_failures + 1), exit 1, index.json untouched
```

| Piece | Where |
|---|---|
| Runner | `tools/produce.py` (`run`, `publish`, `reindex`) |
| Blob client | `tools/blob.py`, the REST API as `@vercel/blob` 2.8.2 speaks it (API version 13) |
| Config | `config/production.yaml` |
| Credentials | `~/.config/doom-bot-arena/env` (template: `env.example`) |
| Timer | `deploy/systemd/dba-produce.{service,timer}`, installed by `make install-timer` |
| Local bundles | `bundles/production/<id>/` (published copies), `bundles/production/raw/.capture/<id>/` (capture logs) |
| Local state | `bundles/production/status.json`, `bundles/production/index.json` (the last index this box wrote) |
| Viewer source | `web/.env.production` sets `VITE_BLOB_PUBLIC_BASE`; `web/src/season.js` has the match list, season table and footer |

## Store layout

```
index.json                     overwritten each run, Cache-Control max-age=60
status.json                    overwritten each run, Cache-Control max-age=60
bundles/<id>/match.json        written once (allowOverwrite false), Blob's default one-month cache
bundles/<id>/map.json, tics.jsonl, events.jsonl, spots.json, pov/slot1.mp4 … slot8.mp4
```

Pathnames have no random suffix. Within a bundle, `match.json` is uploaded last, and `index.json` only after every bundle file has landed and the store's listing shows its size. A reader never sees an index entry whose files are missing. Blob serves the JSON and JSONL files brotli-compressed, the MP4s with byte ranges, and everything with `Access-Control-Allow-Origin: *`.

`index.json` is `{index_version, updated, bundles: [...]}`, newest first. Each entry is `{id, map, iwad, date, duration_tics, ticrate, path, bytes, standings}`. `standings` holds displayed frags and deaths per bot, from `match.json` `score.rows`, best first. The viewer's match list and season table use only the index.

`status.json` is `{last_run, last_run_ok, last_success, last_bundle_id, consecutive_failures, free_disk_gb, last_error}`.

### What a run decides

- **Map:** the entry after the newest indexed bundle's map in `maps`. Entries with `if_present: true` are skipped while their IWAD file is missing.
- **Seed:** `YYYYMMDD * 100 + n` (UTC date), where `n` is 1 for the day's first published bundle. A failed run's retry gets the same seed.
- **Spot traffic:** the map entry's `traffic` directories (MAP10 and E1M5 use the eval-2c and eval-2d bundles, as eval-2e did). MAP01 has none, so spots use the route scorer.
- **Videos:** the capture encodes at crf 23. The published copy is re-encoded at `crf` (32). `match.json` `publish_reencode` keeps the originals' hashes and the exact command, so the copy still validates.
- **IWAD gate:** a bundle is published only if `match.json` `iwad.sha256` is a known Freedoom release: the `freedoom1.wad` and `freedoom2.wad` that ViZDoom ships, plus `freedoom_sha256` in the config. Anything else (original Doom, a modified Freedoom) is refused unless `allow_non_freedoom: true` is set or `--allow-non-freedoom` is passed. `run` checks the IWAD before playing too, so a refused IWAD costs no match.
- **Large files:** a file over 100 MB goes through Blob's multipart upload in 8 MiB parts. No bundle file is that large today (the largest is `tics.jsonl`, about 34 MB). The docs give no smaller single-request cap for callers off Vercel.

## Setup on a fresh box

1. Docker, with your user in the `docker` group. Then clone the repo and build the match image:
   ```
   git clone https://github.com/andrewluxem/doom-bot-arena && cd doom-bot-arena
   make image
   make docker-test && make docker-bench
   ```
2. If you keep the MAP10 and E1M5 spot traffic, copy `bundles/eval-2c` and `bundles/eval-2d` from the old box. Without them, those maps fall back to the route scorer: the runner logs "spot traffic: routes".
3. Create the env file (next section). Then run `make produce` once by hand and check the result:
   ```
   curl -s "$BLOB_PUBLIC_BASE/status.json"
   ```
4. Install the timer: `make install-timer`.

## Env file

`~/.config/doom-bot-arena/env`, mode 600, dotenv format. Values may be quoted.

```
BLOB_READ_WRITE_TOKEN="vercel_blob_rw_..."
BLOB_STORE_ID="store_..."
BLOB_PUBLIC_BASE="https://<hash>.public.blob.vercel-storage.com"   # written by the first upload
```

`make produce` mounts this file's folder at `/config` in the container. The token is read only from this file, sent only in the `authorization` header, and never printed. The file is never committed: `.env` and `.env.*` are ignored, and only `env.example` and the public `web/.env.production` are tracked.

## Store setup

1. In Vercel: Storage → Create → Blob, public access, connected to the `doom-bot-arena` project. Copy the store's read-write token and its id into the env file.
2. The runner works off Vercel, so it uses the static read-write token, not OIDC. Rotating the token in the Vercel dashboard means updating the env file. Nothing else holds it.
3. After the first `make produce`, the env file has `BLOB_PUBLIC_BASE`. Put the same value in `web/.env.production` and in the Vercel project's environment variables (`VITE_BLOB_PUBLIC_BASE`, Production and Preview), then redeploy. The viewer reads it at build time. With it unset or empty, the viewer shows the committed sample in `web/public/`.

## Timer

```
make install-timer           # installs the units, enables the timer, turns on lingering (loginctl enable-linger)
systemctl --user list-timers dba-produce.timer
```

Runs are at 03:20, 11:20 and 19:20 local time, each with up to 10 minutes of random delay. `Persistent=true` runs a missed slot once at boot. Lingering keeps the user's systemd running without a login session. Check it with `loginctl show-user $USER -p Linger`. The service runs whatever is checked out in the repo, so keep the box on `main`.

### Logs

```
journalctl --user -u dba-produce              # every run
journalctl --user -u dba-produce -b -n 200    # this boot, last 200 lines
journalctl --user -u dba-produce -f           # follow a run in progress
systemctl --user status dba-produce.service   # result of the last run
```

Lines from the runner start with `produce:`. A failure ends with `produce: FAILED (<n> in a row): <reason>`. Per-slot logs of each capture are in `bundles/production/raw/.capture/<id>/`, for the newest `keep_local` captures.

### Pause and resume

```
systemctl --user stop dba-produce.timer       # pause until the next boot
systemctl --user disable --now dba-produce.timer   # pause across reboots
systemctl --user enable --now dba-produce.timer    # resume
make uninstall-timer                          # remove the units
```

A run in progress finishes unless you stop it with `systemctl --user stop dba-produce.service`. Stopping it mid-upload is safe: nothing reaches `index.json` until every file has landed. The next run starts over with a new match. The partly uploaded bundle stays on Blob but is never indexed (see the clean-up below).

Pausing does not change `status.json`. Within ten hours the footer turns STALE. That is expected while paused.

## Re-publishing index.json

If `index.json` is ever corrupted or lost, rebuild it from the bundles on Blob:

```
make produce-reindex PRODUCE_ARGS=--dry-run   # print the rebuilt index
make produce-reindex                          # upload it and replace the local copy
```

`reindex` lists `bundles/` on the store and keeps every bundle that has a `match.json` and all the files it lists. It reads standings from each `match.json` and writes the index newest first. Incomplete bundles are logged as skipped. The CDN picks up the new index within a minute.

To drop a bundle from the site, delete its `bundles/<id>/` files from the store in the Vercel dashboard (Storage, then the store), then run `make produce-reindex`. Reindex also replaces the local copy, so the next run won't re-add the bundle.

To publish a local bundle by hand: `make produce-publish BUNDLE=bundles/production/<id>`. It validates the bundle, applies the IWAD gate, uploads it (files already on Blob with the same size are kept) and updates the index.

## The footer shows a stale time

The footer shows `status.json` `last_success`. It reads STALE when that is more than ten hours old, meaning at least one scheduled run has produced nothing. Check, in order:

1. **Did the run fail?** `curl -s "$BLOB_PUBLIC_BASE/status.json"`. Look at `last_run_ok`, `last_error` and `consecutive_failures`. The footer also shows "N FAILED RUNS SINCE". Then `journalctl --user -u dba-produce -n 200` for the full output.
   - `refusing to start: N GB free`: free disk under `min_free_gb`. Clear old evaluation bundles under `bundles/` or `out/`, or lower `keep_local`.
   - `match failed twice`: see the capture logs in `bundles/production/raw/.capture/<id>/` (`runner.log`, `slot*/log.txt`).
   - `failed validation`: the run log lists each failed check. The re-encoded bundle is in `bundles/production/<id>/`. Run `make docker-validate BUNDLE=bundles/production/<id>` to see it again.
   - `blob API 401/403`: the token was rotated or revoked. Update the env file.
   - `index.json on Blob is unreadable`: re-publish it (above).
2. **Did the run happen?** `systemctl --user list-timers dba-produce.timer` (LAST and NEXT), and `loginctl show-user $USER -p Linger` (must be `yes`). Also check that Docker is running: `docker info`.
3. **Is the box up and online?** If `last_run` in `status.json` is old too, the box was off or offline. `Persistent=true` runs one catch-up at boot.
4. **Is the footer itself stale?** `status.json` is cached for 60 s at the CDN. The viewer fetches it with `cache: no-cache` on every page load, so reloading shows the new value.
