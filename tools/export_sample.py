"""Copy a validated bundle into the web viewer's public folder.

    python3 tools/export_sample.py bundles/<id> [--dest web/public/sample] [--budget-mb 25] [--crf 32]

If the copy is over the size budget, every POV video is re-encoded with the
capture's own ffmpeg arguments (docs/MATCH_FORMAT.md section 8) except the CRF.
match.json then gets the new pov sha256 values and a `sample_reencode` record
holding the originals' hashes and the exact command, so the copy still passes
engine/validate_bundle.py. Finally web/public/index.json is rewritten from every
bundle under --dest.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys

FILES = ["match.json", "map.json", "tics.jsonl", "events.jsonl"]
SOURCE_CRF = 23


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_bytes(path):
    return sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(path) for f in fs)


def reencode_args(src, dst, crf):
    return ["ffmpeg", "-v", "error", "-y", "-i", src, "-an", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", str(crf), "-pix_fmt", "yuv420p", "-g", "35", "-r", "35", "-movflags", "+faststart", dst]


def write_index(dest, index_path):
    entries = []
    for name in sorted(os.listdir(dest)):
        mpath = os.path.join(dest, name, "match.json")
        if not os.path.isfile(mpath):
            continue
        with open(mpath) as f:
            m = json.load(f)
        rel = os.path.relpath(os.path.join(dest, name), os.path.dirname(index_path)).replace(os.sep, "/")
        entries.append({"id": m["id"], "map": m["map"], "iwad": m["iwad"]["name"], "date": m["date"],
                        "duration_tics": m["duration_tics"], "path": rel + "/"})
    entries.sort(key=lambda e: e["date"], reverse=True)
    with open(index_path, "w") as f:
        json.dump({"bundles": entries}, f, indent=2)
        f.write("\n")
    return entries


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("bundle")
    ap.add_argument("--dest", default="web/public/sample")
    ap.add_argument("--index", default="web/public/index.json")
    ap.add_argument("--budget-mb", type=float, default=25.0)
    ap.add_argument("--crf", type=int, default=32)
    a = ap.parse_args(argv)

    with open(os.path.join(a.bundle, "match.json")) as f:
        m = json.load(f)
    out = os.path.join(a.dest, m["id"])
    if os.path.exists(out):
        shutil.rmtree(out)
    os.makedirs(os.path.join(out, "pov"))
    for name in FILES + (["spots.json"] if m.get("schema_version", 1) >= 2 else []):
        shutil.copy2(os.path.join(a.bundle, name), os.path.join(out, name))
    for v in m["pov"]:
        shutil.copy2(os.path.join(a.bundle, v["path"]), os.path.join(out, v["path"]))

    budget = int(a.budget_mb * 1_000_000)
    before = tree_bytes(out)
    print(f"copied {m['id']}: {before} bytes (budget {budget})")
    if before > budget:
        originals = []
        for v in m["pov"]:
            src = os.path.join(a.bundle, v["path"])
            dst = os.path.join(out, v["path"])
            subprocess.run(reencode_args(src, dst, a.crf), check=True)
            originals.append({"slot": v["slot"], "sha256": v["sha256"], "bytes": os.path.getsize(src)})
            v["sha256"] = sha256(dst)
        ffmpeg = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True).stdout.splitlines()[0]
        m["sample_reencode"] = {
            "reason": f"web sample copy kept under {a.budget_mb:g} MB",
            "crf": a.crf,
            "source_crf": SOURCE_CRF,
            "command": " ".join(reencode_args("<bundle>/pov/slot<n>.mp4", "pov/slot<n>.mp4", a.crf)),
            "ffmpeg": ffmpeg,
            "original_pov": originals,
            "note": "re-encoded from the capture's decoded frames; frame count, rate and size unchanged",
        }
        with open(os.path.join(out, "match.json"), "w") as f:
            json.dump(m, f, indent=2)
            f.write("\n")
        print(f"re-encoded at crf {a.crf}: {tree_bytes(out)} bytes")
    after = tree_bytes(out)
    entries = write_index(a.dest, a.index)
    print(f"{out}: {after} bytes; index lists {len(entries)} bundle(s)")
    return 0 if after <= budget else 1


if __name__ == "__main__":
    sys.exit(main())
