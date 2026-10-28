"""Production runner: play a match, validate it, publish it to Vercel Blob, update index.json (docs/PRODUCTION.md).

    python3 tools/produce.py run [--map MAP01] [--iwad PATH] [--allow-non-freedoom]
    python3 tools/produce.py publish bundles/production/<id> [--allow-non-freedoom]
    python3 tools/produce.py reindex [--dry-run]

Runs inside the match image (make produce). Credentials come only from the env file (--env), and the token is
never printed. A run: disk check, next map in rotation, seed from the UTC date and the day's sequence, the match
(run once more if it fails, as the Makefile does), the POV videos re-encoded at the configured crf, validation,
upload of every bundle file (never overwriting), index.json last, then local bundles beyond keep_local deleted.
Any failure is recorded in status.json and exits 1 without touching index.json.
"""
import argparse
import concurrent.futures
import contextlib
import datetime as dt
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import traceback
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml  # noqa: E402

from tools import blob, export_sample  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ID_RE = re.compile(r"^\d{8}T\d{6}Z-[A-Z0-9]+-s\d+$")
# Bundle files in upload order: match.json last, so a bundle with a match.json on Blob is complete.
DATA_FILES = ["tics.jsonl", "events.jsonl", "map.json", "spots.json"]
LIVE_MAX_AGE = 60  # index.json and status.json; bundle files keep Blob's one-month default (they never change)
INDEX_VERSION = 1


class Failure(Exception):
    pass


def log(msg):
    print(f"produce: {msg}", flush=True)


def now_iso():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_config(path):
    with open(path) as f:
        cfg = yaml.safe_load(f)
    for key in ("maps", "duration_tics", "crf", "keep_local", "min_free_gb", "out_dir"):
        if key not in cfg:
            raise Failure(f"{path}: missing {key}")
    cfg["out_dir"] = os.path.join(ROOT, cfg["out_dir"])
    return cfg


def free_gb(path):
    return round(shutil.disk_usage(path).free / 2**30, 1)


# ---------------------------------------------------------------- IWADs
def resolve_iwad(entry):
    name = entry["iwad"]
    if name in ("freedoom1", "freedoom2"):
        import vizdoom
        return os.path.join(os.path.dirname(vizdoom.__file__), f"{name}.wad")
    return name if os.path.isabs(name) else os.path.join(ROOT, name)


def freedoom_hashes(cfg):
    """sha256 of every IWAD that counts as Freedoom: the two ViZDoom ships, plus freedoom_sha256 in the config."""
    import vizdoom
    d = os.path.dirname(vizdoom.__file__)
    known = {export_sample.sha256(os.path.join(d, f"freedoom{n}.wad")) for n in (1, 2)}
    return known | {h.lower() for h in cfg.get("freedoom_sha256") or []}


def check_freedoom(sha, name, cfg, allow):
    if sha in freedoom_hashes(cfg):
        return
    if allow or cfg.get("allow_non_freedoom"):
        log(f"IWAD {name} ({sha[:12]}) is not a known Freedoom release; publishing it because the allow flag is set")
        return
    raise Failure(f"refusing to publish: IWAD {name} (sha256 {sha}) is not a known Freedoom release; "
                  "set allow_non_freedoom in the config or pass --allow-non-freedoom")


# ---------------------------------------------------------------- remote state
def fetch_json(url):
    """GET a public Blob URL past the CDN cache (the query string is a distinct cache key)."""
    sep = "&" if "?" in url else "?"
    with urllib.request.urlopen(f"{url}{sep}nocache={dt.datetime.now().timestamp():.0f}", timeout=60) as r:
        return json.loads(r.read())


def remote_json(store, pathname):
    """(found, data) for a root file such as index.json. A file that is there but unreadable raises Failure."""
    meta = store.head(pathname)
    if meta is None:
        return False, None
    try:
        return True, fetch_json(meta["url"])
    except (OSError, ValueError) as e:
        raise Failure(f"{pathname} on Blob is unreadable ({e}); see docs/PRODUCTION.md, re-publishing index.json")


def load_index(store):
    found, index = remote_json(store, "index.json")
    if not found:
        return {"index_version": INDEX_VERSION, "bundles": []}
    if not isinstance(index, dict) or not isinstance(index.get("bundles"), list):
        raise Failure("index.json on Blob has no bundles list; see docs/PRODUCTION.md, re-publishing index.json")
    return index


def put_live(store, pathname, data):
    body = (json.dumps(data, indent=1) + "\n").encode()
    return store.put(pathname, body, overwrite=True, max_age=LIVE_MAX_AGE)


def record_public_base(env_path, env, url, pathname):
    """BLOB_PUBLIC_BASE comes from the first upload's returned url; written to the env file once."""
    base = url[: -len(pathname)].rstrip("/") if url.endswith(pathname) else None
    if base and env.get("BLOB_PUBLIC_BASE") != base:
        blob.set_env(env_path, "BLOB_PUBLIC_BASE", base)
        env["BLOB_PUBLIC_BASE"] = base
        log(f"BLOB_PUBLIC_BASE={base} written to the env file")


# ---------------------------------------------------------------- index entries
def index_entry(m, bytes_total):
    rows = {r["slot"]: r for r in m["score"]["rows"]}
    standings = [{"slot": b["slot"], "code": b["code"], "name": b["name"],
                  "frags": rows[b["slot"]]["displayed_frags"], "deaths": rows[b["slot"]]["displayed_deaths"]}
                 for b in m["bots"]]
    standings.sort(key=lambda s: (-s["frags"], s["deaths"], s["slot"]))
    return {"id": m["id"], "map": m["map"], "iwad": m["iwad"]["name"], "date": m["date"],
            "duration_tics": m["duration_tics"], "ticrate": m["ticrate"], "path": f"bundles/{m['id']}/",
            "bytes": bytes_total, "standings": standings}


def with_entry(index, entry):
    bundles = [entry] + [e for e in index["bundles"] if e.get("id") != entry["id"]]
    bundles.sort(key=lambda e: e["date"], reverse=True)
    return {"index_version": INDEX_VERSION, "updated": now_iso(), "bundles": bundles}


# ---------------------------------------------------------------- bundle steps
def bundle_files(bundle_dir, m):
    """Relative paths in upload order: videos, data files, match.json last."""
    files = [v["path"] for v in m["pov"]] + [f for f in DATA_FILES if os.path.isfile(os.path.join(bundle_dir, f))]
    return files + ["match.json"]


def reencode(raw_dir, out_dir, crf):
    """Copy a capture-crf bundle to out_dir with its POV videos at crf; match.json records the originals."""
    with open(os.path.join(raw_dir, "match.json")) as f:
        m = json.load(f)
    os.makedirs(os.path.join(out_dir, "pov"))
    for name in DATA_FILES:
        if os.path.isfile(os.path.join(raw_dir, name)):
            shutil.copy2(os.path.join(raw_dir, name), os.path.join(out_dir, name))

    def one(v):
        src, dst = os.path.join(raw_dir, v["path"]), os.path.join(out_dir, v["path"])
        subprocess.run(export_sample.reencode_args(src, dst, crf), check=True)
        return {"slot": v["slot"], "sha256": v["sha256"], "bytes": os.path.getsize(src)}, export_sample.sha256(dst)

    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        done = list(pool.map(one, m["pov"]))
    for v, (_, sha) in zip(m["pov"], done):
        v["sha256"] = sha
    ffmpeg = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True).stdout.splitlines()[0]
    m["publish_reencode"] = {
        "reason": "published POV videos re-encoded to keep the bundle small",
        "crf": crf, "source_crf": export_sample.SOURCE_CRF,
        "command": " ".join(export_sample.reencode_args("<capture>/pov/slot<n>.mp4", "pov/slot<n>.mp4", crf)),
        "ffmpeg": ffmpeg, "original_pov": [o for o, _ in done],
        "note": "re-encoded from the capture's decoded frames; frame count, rate and size unchanged",
    }
    with open(os.path.join(out_dir, "match.json"), "w") as f:
        json.dump(m, f, indent=1)
        f.write("\n")
    return m


def validate(bundle_dir):
    from engine import validate_bundle
    rep, _ = validate_bundle.validate(bundle_dir)
    if not rep.ok:
        failed = [name for name, problems in rep.results if problems]
        raise Failure(f"{os.path.basename(bundle_dir)} failed validation: {'; '.join(failed)}")


def upload_bundle(store, bundle_dir, m):
    """Upload every bundle file without overwriting. A file already there with the same size (a re-run of a
    publish that stopped part way) is kept; one with another size is an error. Returns total bytes."""
    prefix = f"bundles/{m['id']}/"
    files = bundle_files(bundle_dir, m)
    present = {b["pathname"]: b["size"] for b in store.list(prefix)}
    total = 0
    for rel in files:
        path, pathname = os.path.join(bundle_dir, rel), prefix + rel
        size = os.path.getsize(path)
        total += size
        if pathname in present:
            if present[pathname] != size:
                raise Failure(f"{pathname} is on Blob with {present[pathname]} bytes, local is {size}; bundle files are never overwritten")
            log(f"  {pathname}: already on Blob")
            continue
        with open(path, "rb") as f:
            data = f.read()
        try:
            store.put(pathname, data, overwrite=False)
        except blob.BlobError as e:
            if "already exists" not in str(e):
                raise
            meta = store.head(pathname)  # a retried put whose first attempt landed
            if not meta or meta["size"] != size:
                raise Failure(f"{pathname} already exists on Blob with different content") from None
        log(f"  {pathname}: {size} bytes")
    landed = {b["pathname"]: b["size"] for b in store.list(prefix)}
    missing = [rel for rel in files if landed.get(prefix + rel) != os.path.getsize(os.path.join(bundle_dir, rel))]
    if missing:
        raise Failure(f"after upload, Blob lists {prefix} without (or with the wrong size for) {missing}")
    return total


def publish(store, env_path, env, cfg, bundle_dir, allow_non_freedoom):
    """Validate, gate on the IWAD, upload the bundle, then index.json. Returns the index entry."""
    with open(os.path.join(bundle_dir, "match.json")) as f:
        m = json.load(f)
    check_freedoom(m["iwad"]["sha256"], m["iwad"]["name"], cfg, allow_non_freedoom)
    log(f"validating {bundle_dir}")
    validate(bundle_dir)
    index = load_index(store)  # read before any upload: an unreadable index stops the run here
    log(f"uploading {m['id']}")
    total = upload_bundle(store, bundle_dir, m)
    entry = index_entry(m, total)
    new_index = with_entry(index, entry)
    r = put_live(store, "index.json", new_index)
    record_public_base(env_path, env, r["url"], "index.json")
    log(f"index.json: {len(new_index['bundles'])} bundle(s), newest {new_index['bundles'][0]['id']}")
    return entry


# ---------------------------------------------------------------- run
def next_map(cfg, index):
    maps = []
    for e in cfg["maps"]:
        if e.get("if_present") and not os.path.isfile(resolve_iwad(e)):
            log(f"skipping {e['map']}: {e['iwad']} not present")
            continue
        maps.append(e)
    if not maps:
        raise Failure("no enabled maps")
    last = next((b for b in index["bundles"]), None)
    names = [e["map"] for e in maps]
    if last is None or last["map"] not in names:
        return maps[0]
    return maps[(names.index(last["map"]) + 1) % len(maps)]


def day_seed(index, today):
    """YYYYMMDD * 100 + the day's sequence (1 for the day's first published bundle). Fits a 32-bit int."""
    stamp = today.strftime("%Y%m%d")
    seq = 1 + sum(1 for b in index["bundles"] if b["id"].startswith(stamp))
    return int(stamp) * 100 + seq


def run_match(cfg, entry, iwad, seed, raw_root):
    cmd = [sys.executable, "-u", "-m", "engine.runner", "--map", entry["map"], "--iwad", iwad, "--seed", str(seed),
           "--duration-tics", str(cfg["duration_tics"]), "--out", raw_root]
    if entry.get("traffic"):
        cmd += ["--traffic"] + [os.path.join(ROOT, t) for t in entry["traffic"]]
    for attempt in (1, 2):  # a capture that fails its checks is run once more (docs/MATCH_FORMAT.md section 9)
        log(f"match attempt {attempt}: {entry['map']} seed {seed}")
        written = None
        with subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as p:
            for line in p.stdout:
                print(line, end="", flush=True)
                if line.startswith("bundle written: "):
                    written = line.removeprefix("bundle written: ").strip()
        if p.returncode == 0 and written:
            return written
        if p.returncode == 2:
            raise Failure(f"runner refused to start (exit 2) for {entry['map']}")
        log(f"runner exit {p.returncode}")
    raise Failure(f"match failed twice for {entry['map']} seed {seed}")


def prune(cfg):
    """Keep the newest keep_local published bundles; delete older ones, raw copies and old captures."""
    out, keep = cfg["out_dir"], cfg["keep_local"]
    removed = []
    published = sorted(d for d in os.listdir(out) if ID_RE.match(d))
    for d in published[:-keep] if keep else published:
        shutil.rmtree(os.path.join(out, d))
        removed.append(d)
    raw = os.path.join(out, "raw")
    if os.path.isdir(raw):
        for d in os.listdir(raw):
            if ID_RE.match(d):
                shutil.rmtree(os.path.join(raw, d))
        cap = os.path.join(raw, ".capture")
        if os.path.isdir(cap):
            captures = sorted(d for d in os.listdir(cap) if ID_RE.match(d))
            for d in captures[:-keep] if keep else captures:
                shutil.rmtree(os.path.join(cap, d))
    if removed:
        log(f"deleted local bundles {removed}")
    return removed


def run(args, cfg, env, store):
    out = cfg["out_dir"]
    free = free_gb(out)
    args.status["free_disk_gb"] = free
    if free < cfg["min_free_gb"]:
        raise Failure(f"refusing to start: {free} GB free under {out}, under {cfg['min_free_gb']} GB")
    published = []
    for _ in range(cfg.get("matches_per_run", 1)):
        index = load_index(store)
        entry = dict(next_map(cfg, index))
        if args.map:
            entry = next((e for e in cfg["maps"] if e["map"] == args.map.upper()), {"map": args.map.upper(), "iwad": "freedoom2"})
        if args.iwad:
            entry["iwad"] = args.iwad
        iwad = resolve_iwad(entry)
        if not os.path.isfile(iwad):
            raise Failure(f"IWAD not found: {iwad} (map {entry['map']})")
        sha = export_sample.sha256(iwad)
        check_freedoom(sha, os.path.basename(iwad), cfg, args.allow_non_freedoom)  # before spending a match on it
        seed = day_seed(index, dt.datetime.now(dt.timezone.utc))
        raw_dir = run_match(cfg, entry, iwad, seed, os.path.join(out, "raw"))
        bundle_dir = os.path.join(out, os.path.basename(raw_dir))
        log(f"re-encoding POV videos at crf {cfg['crf']}")
        reencode(raw_dir, bundle_dir, cfg["crf"])
        shutil.rmtree(raw_dir)
        e = publish(store, args.env, env, cfg, bundle_dir, args.allow_non_freedoom)
        published.append(e)
        args.status["last_bundle_id"] = e["id"]
    prune(cfg)
    return published


def reindex(args, cfg, env, store):
    """Rebuild index.json from the bundles on Blob: every bundle whose match.json and listed files are all there."""
    by_id = {}
    for b in store.list("bundles/"):
        parts = b["pathname"].split("/", 2)
        if len(parts) == 3:
            by_id.setdefault(parts[1], {})[parts[2]] = b
    entries = []
    for bid, files in sorted(by_id.items()):
        if "match.json" not in files:
            log(f"  {bid}: no match.json, incomplete, skipped")
            continue
        m = fetch_json(files["match.json"]["url"])
        need = [v["path"] for v in m["pov"]] + DATA_FILES[:3] + (["spots.json"] if m.get("schema_version", 1) >= 2 else [])
        missing = [f for f in need if f not in files]
        if missing:
            log(f"  {bid}: missing {missing}, skipped")
            continue
        entries.append(index_entry(m, sum(f["size"] for f in files.values())))
    index = {"index_version": INDEX_VERSION, "updated": now_iso(),
             "bundles": sorted(entries, key=lambda e: e["date"], reverse=True)}
    log(f"rebuilt index: {len(entries)} bundle(s)")
    if args.dry_run:
        print(json.dumps(index, indent=1))
        return []
    r = put_live(store, "index.json", index)
    record_public_base(args.env, env, r["url"], "index.json")
    return []


def write_status(store, cfg, status):
    path = os.path.join(cfg["out_dir"], "status.json")
    with open(path, "w") as f:
        json.dump(status, f, indent=1)
        f.write("\n")
    try:
        put_live(store, "status.json", status)
    except blob.BlobError as e:
        log(f"could not upload status.json: {e}")


def previous_status(store, cfg):
    path = os.path.join(cfg["out_dir"], "status.json")
    if os.path.isfile(path):
        with open(path) as f:
            return json.load(f)
    with contextlib.suppress(Failure, blob.BlobError):
        found, data = remote_json(store, "status.json")
        if found and isinstance(data, dict):
            return data
    return {}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", default=os.path.join(ROOT, "config/production.yaml"))
    ap.add_argument("--env", default="/config/env", help="dotenv file with BLOB_READ_WRITE_TOKEN (default %(default)s)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="play, validate and publish the next match(es) in rotation")
    r.add_argument("--map", help="play this map instead of the next in rotation")
    r.add_argument("--iwad", help="use this IWAD path instead of the map's configured one")
    r.add_argument("--allow-non-freedoom", action="store_true")
    p = sub.add_parser("publish", help="validate and publish one local bundle")
    p.add_argument("bundle")
    p.add_argument("--allow-non-freedoom", action="store_true")
    x = sub.add_parser("reindex", help="rebuild index.json from the bundles on Blob")
    x.add_argument("--dry-run", action="store_true", help="print the index instead of uploading it")
    args = ap.parse_args(argv)

    env = blob.read_env(args.env)
    store = blob.Blob(env.get("BLOB_READ_WRITE_TOKEN", ""), env.get("BLOB_STORE_ID"))
    cfg = load_config(args.config)
    os.makedirs(cfg["out_dir"], exist_ok=True)
    with open(os.path.join(cfg["out_dir"], ".lock"), "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log("another produce.py holds the lock; exiting")
            return 1
        if args.cmd == "reindex":
            reindex(args, cfg, env, store)
            return 0
        if args.cmd == "publish":  # a hand-run tool: it reports, but status.json tracks scheduled runs only
            try:
                e = publish(store, args.env, env, cfg, args.bundle, args.allow_non_freedoom)
            except (Failure, blob.BlobError, OSError) as e:
                log(f"FAILED: {e}")
                return 1
            log(f"published {e['id']}")
            return 0
        status = previous_status(store, cfg)
        status.update({"status_version": 1, "last_run": now_iso(), "free_disk_gb": free_gb(cfg["out_dir"])})
        args.status = status
        try:
            published = run(args, cfg, env, store)
        except (Failure, blob.BlobError, subprocess.CalledProcessError, OSError, KeyError, ValueError) as e:
            if not isinstance(e, Failure):
                traceback.print_exc()
            status.update({"last_run_ok": False, "last_error": str(e),
                           "consecutive_failures": status.get("consecutive_failures", 0) + 1})
            write_status(store, cfg, status)
            log(f"FAILED ({status['consecutive_failures']} in a row): {e}")
            return 1
        status.update({"last_run_ok": True, "last_error": None, "last_success": now_iso(), "consecutive_failures": 0,
                       "free_disk_gb": free_gb(cfg["out_dir"])})
        write_status(store, cfg, status)
        log(f"published {[e['id'] for e in published]}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
