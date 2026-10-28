"""Cut POV clips for a blind identification test, with a separate answer key.

    python3 tools/blind_clips.py bundles/eval-2a/2* --bots VEX,HEX,NUL --per-bot 2 --seconds 30 \\
        --out out/blind-2a --seed 2

For each bot, clips come from different bundles where possible. With --per-map, each bot gets
exactly one clip from each map among the bundles (milestone 2B: one per bot per map). A window qualifies if
it lies in the displayed match, the bot is alive for at least 90% of it, and it holds at
least one of the bot's engagement tics (target_slot set), so there is something to judge.
Among qualifying windows on a 1 s grid, one is drawn per bundle with a seeded RNG. The
windows are not chosen for looking characteristic.

Clips go to <out>/clips/clip_NN.mp4 in shuffled order, scaled 3x with nearest-neighbour
and no audio. The answer key goes to <out>/key/answer_key.json and answer_key.md, so the
clips folder can be handed out on its own. Needs ffmpeg.
"""

import argparse
import json
import os
import random
import subprocess
import sys

TICRATE = 35
SCALE = 3


def read_jsonl(path):
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def windows(bundle_dir, code, frames):
    with open(os.path.join(bundle_dir, "match.json")) as f:
        m = json.load(f)
    slot = next(b["slot"] for b in m["bots"] if b["code"] == code)
    f0, n = m["frame0_tic"], m["duration_tics"]
    rows = [r for r in read_jsonl(os.path.join(bundle_dir, "tics.jsonl")) if r["slot"] == slot]
    alive = [r["alive"] for r in rows]
    engaged = [r["target_slot"] is not None for r in rows]
    out = []
    for start in range(f0, f0 + n - frames + 1, TICRATE):
        span = range(start, start + frames)
        if sum(alive[t] for t in span) >= 0.9 * frames and any(engaged[t] for t in span):
            out.append(start)
    return m, slot, out


def cut(src, dst, first_frame, frames):
    """Frame-exact cut: decode from the frame's time, keep `frames` frames, re-encode."""
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{first_frame / TICRATE:.6f}", "-i", src,
           "-frames:v", str(frames), "-vf", f"scale={320 * SCALE}:{240 * SCALE}:flags=neighbor",
           "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "22", "-pix_fmt", "yuv420p",
           "-r", str(TICRATE), "-movflags", "+faststart", dst]
    subprocess.run(cmd, check=True)


def count_frames(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
                          "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", path],
                         capture_output=True, text=True, check=True)
    return int(out.stdout.strip())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("bundles", nargs="+")
    ap.add_argument("--bots", default="VEX,HEX,NUL")
    ap.add_argument("--per-bot", type=int, default=2)
    ap.add_argument("--per-map", action="store_true", help="one clip per bot from each map (ignores --per-bot)")
    ap.add_argument("--seconds", type=int, default=30)
    ap.add_argument("--out", default="out/blind-2a")
    ap.add_argument("--seed", type=int, default=2)
    args = ap.parse_args(argv)
    rng = random.Random(args.seed)
    frames = args.seconds * TICRATE
    bundles = sorted(b for b in args.bundles if os.path.isfile(os.path.join(b, "match.json")))
    groups = {}
    for b in bundles:
        with open(os.path.join(b, "match.json")) as f:
            groups.setdefault(json.load(f)["map"] if args.per_map else "all", []).append(b)
    picks = []
    for code in [c.strip().upper() for c in args.bots.split(",")]:
        for group in sorted(groups):
            want = 1 if args.per_map else args.per_bot
            order = groups[group][:]
            rng.shuffle(order)
            chosen = []
            for b in order:
                m, slot, wins = windows(b, code, frames)
                if wins:
                    chosen.append({"bot": code, "bundle": b, "match_id": m["id"], "map": m["map"], "seed": m["seed"],
                                   "slot": slot, "start_tic": rng.choice(wins), "frame0_tic": m["frame0_tic"],
                                   "qualifying_windows": len(wins)})
                if len(chosen) == want:
                    break
            if len(chosen) < want:
                print(f"blind_clips: only {len(chosen)} qualifying windows for {code} ({group})", file=sys.stderr)
                return 1
            picks += chosen
    rng.shuffle(picks)

    clips_dir, key_dir = os.path.join(args.out, "clips"), os.path.join(args.out, "key")
    os.makedirs(clips_dir, exist_ok=True)
    os.makedirs(key_dir, exist_ok=True)
    key = []
    for i, p in enumerate(picks, 1):
        name = f"clip_{i:02d}.mp4"
        first = p["start_tic"] - p["frame0_tic"]
        dst = os.path.join(clips_dir, name)
        cut(os.path.join(p["bundle"], "pov", f"slot{p['slot']}.mp4"), dst, first, frames)
        got = count_frames(dst)
        if got != frames:
            print(f"blind_clips: {name} has {got} frames, expected {frames}", file=sys.stderr)
            return 1
        key.append({"clip": name, "bot": p["bot"], "match_id": p["match_id"], "map": p["map"], "seed": p["seed"], "slot": p["slot"],
                    "start_tic": p["start_tic"], "end_tic": p["start_tic"] + frames - 1,
                    "video_frames": [first, first + frames - 1],
                    "match_clock": [round((p["start_tic"] - p["frame0_tic"]) / TICRATE, 2),
                                    round((p["start_tic"] - p["frame0_tic"] + frames) / TICRATE, 2)],
                    "qualifying_windows": p["qualifying_windows"]})
    meta = {"selection": __doc__.split("\n\n")[1].replace("\n", " "), "seed": args.seed, "seconds": args.seconds,
            "scale": SCALE, "clips": key}
    with open(os.path.join(key_dir, "answer_key.json"), "w") as f:
        json.dump(meta, f, indent=1)
    with open(os.path.join(key_dir, "answer_key.md"), "w") as f:
        f.write("# Blind test answer key\n\nDo not share with participants.\n\n| clip | bot | map | match | slot | match clock |\n|---|---|---|---|---|---|\n")
        for k in key:
            f.write(f"| {k['clip']} | {k['bot']} | {k['map']} | {k['match_id']} | {k['slot']} | "
                    f"{k['match_clock'][0]:.0f}–{k['match_clock'][1]:.0f} s |\n")
    print(f"wrote {len(key)} clips to {clips_dir} and the key to {key_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
